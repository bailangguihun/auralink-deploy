"""Validate and materialize a prebuilt Next.js release for the R25 Web publisher."""

import hashlib
import os
from pathlib import PurePosixPath
import re
import shutil
import stat
import tarfile
import tempfile


MAX_ARCHIVE_BYTES = 2 * 1024**3
MAX_EXPANDED_BYTES = 4 * 1024**3
MAX_MEMBERS = 100_000
TOP_LEVEL = {".commit-sha", ".next", "node_modules", "package.json", "public", "server.js"}
ARTIFACT_MARKER = ".artifact-sha256"


class ArtifactRefused(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise ArtifactRefused(reason)


def commit_for(identity):
    match = re.fullmatch(r"([0-9a-f]{40})-[1-9][0-9]{0,19}-[1-9][0-9]{0,19}", identity)
    require(match is not None, "invalid_github_run_identity")
    return match[1]


def private_directory(path, owner):
    value = path.lstat()
    require(stat.S_ISDIR(value.st_mode) and stat.S_IMODE(value.st_mode) == 0o700
            and (value.st_uid, value.st_gid) == owner, "web_artifact_directory_identity")


def prepare(root, identity, *, owner=(0, 0)):
    commit_for(identity)
    private_directory(root, owner)
    incoming = root / "incoming"
    incoming.mkdir(mode=0o700, exist_ok=True)
    private_directory(incoming, owner)
    folder = incoming / identity
    folder.mkdir(mode=0o700, exist_ok=True)
    private_directory(folder, owner)
    return folder


def checksum(stream):
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def extract(archive, staging):
    seen = set()
    expanded = 0
    with tarfile.open(fileobj=archive, mode="r|gz") as package:
        for member in package:
            name = PurePosixPath(member.name)
            require(name.parts and not name.is_absolute() and ".." not in name.parts
                    and name.parts[0] in TOP_LEVEL
                    and not any(part == ".git" or part == ".npmrc" or part.startswith(".env")
                                for part in name.parts),
                    "web_artifact_member_path")
            require(name not in seen and len(seen) < MAX_MEMBERS, "web_artifact_member_count_or_duplicate")
            seen.add(name)
            require(member.isdir() or member.type in (tarfile.REGTYPE, tarfile.AREGTYPE),
                    "web_artifact_member_type")
            expanded += member.size
            require(member.size >= 0 and expanded <= MAX_EXPANDED_BYTES, "web_artifact_expanded_size")
            destination = staging.joinpath(*name.parts)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True, mode=0o700)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            mode = 0o600 | (member.mode & 0o100)
            with package.extractfile(member) as source, destination.open("xb") as output:
                os.fchmod(output.fileno(), mode)
                shutil.copyfileobj(source, output)


def fingerprints(root, owner):
    result = {}
    private_directory(root, owner)
    for path in root.rglob("*"):
        value = path.lstat()
        mode = stat.S_IMODE(value.st_mode)
        require((value.st_uid, value.st_gid) == owner and mode in (0o600, 0o700),
                "existing_release_identity")
        if stat.S_ISDIR(value.st_mode):
            result[path.relative_to(root)] = (mode, None)
        else:
            require(stat.S_ISREG(value.st_mode) and value.st_nlink == 1, "existing_release_identity")
            with path.open("rb") as stream:
                result[path.relative_to(root)] = (mode, checksum(stream))
    return result


def materialize(root, identity, digest, build, *, owner=(0, 0)):
    commit = commit_for(identity)
    require(re.fullmatch(r"[0-9a-f]{64}", digest) and re.fullmatch(r"[A-Za-z0-9._-]{1,80}", build)
            and build not in (".", ".."),
            "web_artifact_expected_identity")
    releases = root / "releases"
    private_directory(root, owner)
    private_directory(releases, owner)
    incoming = root / "incoming"
    private_directory(incoming, owner)
    folder = incoming / identity
    private_directory(folder, owner)
    fd = os.open(folder / "web.tar.gz", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as archive:
        value = os.fstat(archive.fileno())
        require(stat.S_ISREG(value.st_mode) and value.st_nlink == 1
                and (value.st_uid, value.st_gid, stat.S_IMODE(value.st_mode)) == (*owner, 0o600)
                and 0 < value.st_size <= MAX_ARCHIVE_BYTES, "web_artifact_archive_identity")
        require(checksum(archive) == digest, "web_artifact_checksum")
        archive.seek(0)
        with tempfile.TemporaryDirectory(prefix=".artifact-", dir=releases) as directory:
            staging = releases / directory
            previous_umask = os.umask(0o077)
            try:
                extract(archive, staging)
                # Verify the same open archive before any filesystem promotion.
                archive.seek(0)
                require(checksum(archive) == digest, "web_artifact_changed")
                require((staging / "server.js").is_file() and (staging / ".next/static").is_dir()
                        and (staging / ".commit-sha").read_text(encoding="ascii").strip() == commit
                        and (staging / ".next/BUILD_ID").read_text(encoding="ascii").strip() == build,
                        "web_artifact_runtime_identity")
                (staging / ARTIFACT_MARKER).write_text(digest + "\n", encoding="ascii")
                release = releases / commit[:12]
                if os.path.lexists(release):
                    require(fingerprints(release, owner) == fingerprints(staging, owner),
                            "existing_release_mismatch")
                else:
                    staging.rename(release)
                return release
            finally:
                os.umask(previous_umask)
