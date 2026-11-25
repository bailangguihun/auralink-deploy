import hashlib
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import web_artifact as artifact


COMMIT = "a" * 40
RUN = f"{COMMIT}-123-1"
BUILD = "next-BUILD_ID"


class OfflineWebArtifactTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.releases = self.root / "releases"
        self.releases.mkdir(mode=0o700)
        self.incoming = self.root / "incoming"
        self.incoming.mkdir(mode=0o700)
        self.run = self.incoming / RUN
        self.run.mkdir(mode=0o700)
        self.archive = self.run / "web.tar.gz"
        self.content = {
            "server.js": b"console.log('isolated candidate');\n",
            ".commit-sha": f"{COMMIT}\n".encode(),
            ".next/BUILD_ID": f"{BUILD}\n".encode(),
            ".next/static/chunk.js": b"console.log('static asset');\n",
            "public/health.txt": b"ready\n",
        }

    def write_archive(self):
        with tarfile.open(self.archive, "w:gz") as output:
            for name, data in self.content.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                output.addfile(info, io.BytesIO(data))
        self.archive.chmod(0o600)
        return hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def materialize(self, digest, build=BUILD):
        return artifact.materialize(self.root, RUN, digest, build, owner=(os.getuid(), os.getgid()))

    def test_verified_release_is_materialized_without_running_git_or_npm(self):
        digest = self.write_archive()
        with patch.object(subprocess, "run", side_effect=AssertionError("unexpected process")):
            release = self.materialize(digest)
            self.assertEqual(release, self.releases / COMMIT[:12])
            self.assertEqual(self.materialize(digest), release)
        self.assertEqual((release / "server.js").read_bytes(), self.content["server.js"])
        self.assertEqual((release / "public/health.txt").read_bytes(), b"ready\n")

    def test_wrong_checksum_build_or_commit_does_not_publish_a_directory(self):
        digest = self.write_archive()
        for checksum, build in (("f" * 64, BUILD), (digest, "OTHER")):
            with self.subTest(checksum=checksum, build=build):
                with self.assertRaises(ValueError):
                    self.materialize(checksum, build)
                self.assertEqual(list(self.releases.iterdir()), [])
        self.content[".commit-sha"] = b"wrong source\n"
        with self.assertRaises(ValueError):
            self.materialize(self.write_archive())
        self.assertEqual(list(self.releases.iterdir()), [])

    def test_existing_release_must_match_the_verified_archive(self):
        digest = self.write_archive()
        release = self.materialize(digest)
        (release / "server.js").write_text("different content")
        with self.assertRaisesRegex(ValueError, "existing_release"):
            self.materialize(digest)
        self.assertEqual((release / "server.js").read_text(), "different content")

    def test_artifact_with_a_dotenv_file_is_refused(self):
        self.content[".env.production"] = b"LOCAL_FIXTURE=1\n"
        with self.assertRaises(ValueError):
            self.materialize(self.write_archive())
        self.assertEqual(list(self.releases.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
