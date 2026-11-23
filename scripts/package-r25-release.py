#!/usr/bin/env python3
"""Create or verify an installation artifact. Never import or install its payload."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import zipfile

REPOSITORY = 'bailangguihun/auralink-deploy'
HISTORICAL_REPOSITORY = 'Hang-666-star/auralink-deploy'
# Preserve the recorded origin of already built artifacts; never normalize it
# to the current owner or accept an arbitrary fork with the same project name.
VERIFIABLE_REPOSITORIES = (REPOSITORY, HISTORICAL_REPOSITORY)
REF = 'refs/heads/release'
FILES_V1 = tuple(sorted((
    'assistant_release.py','assistant_runtime.py','assistant_runtime_overlay.py','assistant_acceptance.py','assistant_backup.py','upgrade_assistant_controller.py',
    'ai_publish.py', 'ai_release_candidate.py', 'ai_release_guard.py', 'ai_runtime.py',
    'ai-baseline.json', 'control_overlay.py', 'core_acceptance.py', 'core_publish.py',
    'core_release_guard.py', 'core_runtime.py', 'core-release-candidate.py',
    'core_business.py', 'core_asset_sandbox.py', 'upgrade_core_controller.py',
    'install_ai_controller.py', 'install_core_controller.py', 'publisher_transaction.py',
    'r25_storage.py', 'verify-r25-artifact.py',
)))
FILES = tuple(sorted((*FILES_V1, 'assistant_rollout_binding.py', 'assistant_rollout_controller.py',
                      'assistant_rollout_prepare.py')))
MAX_FILE_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = len(FILES) * (MAX_FILE_BYTES + 512)
ARCHIVE_NAME = 'r25-controller.zip'
MANIFEST_NAME = 'r25-controller-manifest.json'


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def identity(repository, ref, commit, run_id, run_attempt):
    require(repository in VERIFIABLE_REPOSITORIES and ref == REF, 'release_repository_or_ref')
    require(isinstance(commit, str) and re.fullmatch('[0-9a-f]{40}', commit), 'commit')
    for value in (run_id, run_attempt):
        require(isinstance(value, str) and re.fullmatch('[1-9][0-9]{0,19}', value), 'run_identity')
    return dict(repository=repository, ref=ref, commit=commit, run_id=run_id, run_attempt=run_attempt)


def read_regular(path, limit):
    path = Path(path).absolute()
    require(not any(parent.is_symlink() for parent in (path, *path.parents)), 'symlink_path')
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_size <= limit, 'file_type_or_size')
    data = path.read_bytes()
    require(len(data) == info.st_size, 'file_changed_during_read')
    return data


def digest(data):
    return hashlib.sha256(data).hexdigest()


def archive_bytes(payload):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(payload):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, payload[name])
    return output.getvalue()


def manifest_for(payload, archive, expected):
    return dict(schema=1, **expected, status='NOT_DEPLOYED', installation_performed=False,
                archive=dict(name=ARCHIVE_NAME, bytes=len(archive), sha256=digest(archive)),
                files=[dict(path=name, bytes=len(payload[name]), sha256=digest(payload[name]))
                       for name in sorted(payload)])


def create(source, output, repository, ref, commit, run_id, run_attempt):
    require(repository == REPOSITORY, 'new_release_repository')
    expected = identity(repository, ref, commit, run_id, run_attempt)
    payload = {name: read_regular(Path(source) / name, MAX_FILE_BYTES) for name in FILES}
    archive = archive_bytes(payload)
    manifest = manifest_for(payload, archive, expected)
    output = Path(output).absolute()
    require(not any(parent.is_symlink() for parent in (output, *output.parents)), 'symlink_output')
    # A new directory avoids overwriting artifacts or mixing this run with a prior run.
    output.mkdir(parents=True, exist_ok=False)
    with (output / ARCHIVE_NAME).open('xb') as stream:
        stream.write(archive)
    with (output / MANIFEST_NAME).open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
    return manifest


def strict_json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, 'duplicate_manifest_key')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('nonfinite_manifest_value')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def verify(archive_path, manifest_path, repository, ref, commit, run_id, run_attempt):
    expected = identity(repository, ref, commit, run_id, run_attempt)
    raw = read_regular(archive_path, MAX_ARCHIVE_BYTES)
    manifest = strict_json(read_regular(manifest_path, MAX_FILE_BYTES))
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        require(tuple(entry.filename for entry in entries) in (FILES_V1, FILES), 'archive_allowlist')
        payload = {}
        for entry in entries:
            require(entry.file_size <= MAX_FILE_BYTES and entry.compress_type == zipfile.ZIP_STORED,
                    'archive_size_or_compression')
            payload[entry.filename] = archive.read(entry)
    # Rebuilding also rejects duplicate entries, symlink metadata, trailing content,
    # alternate permissions/timestamps and inconsistent ZIP headers.
    require(raw == archive_bytes(payload), 'archive_not_canonical')
    require(json.dumps(manifest, sort_keys=True) == json.dumps(manifest_for(payload, raw, expected), sort_keys=True),
            'manifest_or_expected_identity_mismatch')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('create', 'verify'))
    parser.add_argument('--source', type=Path, default=Path('r25'))
    parser.add_argument('--output', type=Path, default=Path('dist/r25-controller'))
    parser.add_argument('--repository', required=True)
    parser.add_argument('--ref', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--run-attempt', required=True)
    args = parser.parse_args()
    common = (args.repository, args.ref, args.commit, args.run_id, args.run_attempt)
    if args.operation == 'create':
        report = create(args.source, args.output, *common)
    else:
        report = verify(args.output / ARCHIVE_NAME, args.output / MANIFEST_NAME, *common)
    status = 'BUNDLE_CREATED_NOT_DEPLOYED' if args.operation == 'create' else 'BUNDLE_VERIFIED_NOT_DEPLOYED'
    print(json.dumps(dict(status=status, commit=report['commit'],
                          files=len(report['files']), archive_sha256=report['archive']['sha256'])))


if __name__ == '__main__':
    main()
