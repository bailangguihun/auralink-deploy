"""Community artifact checks only; no extraction, migration or activation."""
import hashlib
import io
from pathlib import PurePosixPath
import re
import stat
import zipfile
from r25_storage import must, strict_json

REQUIRED = {'package.json', 'package-lock.json', 'db/001_foundation.sql',
            'src/main.mjs', 'src/server.mjs', 'src/store.mjs', 'src/database.mjs',
            'src/core.mjs', 'src/errors.mjs', 'src/media.mjs'}


def manifest(raw, expected):
    value = strict_json(raw)
    must(value.get('schema') == 'artlive-community-bundle/v1', 'community_bundle_schema')
    must(value.get('repository') == 'bailangguihun/auralink-core-api' and
         value.get('ref') == 'refs/heads/release', 'community_release_origin')
    must(set(expected) == {'commit', 'run_id', 'run_attempt'}, 'community_expected_provenance')
    must(re.fullmatch('[a-f0-9]{40}', expected['commit']) is not None, 'community_commit')
    for key in ('run_id', 'run_attempt'):
        must(re.fullmatch('[1-9][0-9]{0,19}', expected[key]) is not None, 'community_run')
    must(all(value.get(k) == v for k, v in expected.items()), 'community_release_mismatch')
    must(value.get('contract_version') == value.get('database_schema_version') == 1,
         'community_contract_version')
    must(value.get('runtime_node_min') == '22.13.0', 'community_runtime_contract')
    files = value.get('files')
    must(type(files) is list and 10 <= len(files) <= 5000, 'community_file_inventory')
    names = set()
    total = 0
    for item in files:
        must(type(item) is dict and set(item) == {'path', 'bytes', 'sha256'}, 'community_file_record')
        name = item['path']
        must(type(name) is str and not any(ord(c) < 32 for c in name) and '\\' not in name,
             'community_file_name')
        path = PurePosixPath(name)
        must(not path.is_absolute() and path.as_posix() == name and '..' not in path.parts,
             'community_file_path')
        must(name in REQUIRED or (name.startswith('node_modules/') and len(path.parts) >= 3),
             'community_file_scope')
        must(name not in names, 'community_duplicate_file'); names.add(name)
        must(type(item['bytes']) is int and 0 <= item['bytes'] <= 32*1024*1024, 'community_file_size')
        must(type(item['sha256']) is str and re.fullmatch('[a-f0-9]{64}', item['sha256']), 'community_file_digest')
        total += item['bytes']
    must(REQUIRED <= names and total <= 64*1024*1024, 'community_inventory_scope')
    return value


def verify_archive(raw, manifest_raw, expected):
    value = manifest(manifest_raw, expected)
    must(len(raw) <= 64*1024*1024, 'community_archive_size')
    must(hashlib.sha256(raw).hexdigest() == value.get('archive_sha256'), 'community_archive_digest')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        records = {item['path']: item for item in value['files']}
        must(len(archive.infolist()) == len(records) and set(archive.namelist()) == set(records),
             'community_archive_inventory')
        for info in archive.infolist():
            must(info.create_system == 3 and (info.external_attr >> 16) == stat.S_IFREG | 0o600,
                 'community_archive_file_type')
            item = records[info.filename]
            must(not info.flag_bits & 1 and info.file_size == item['bytes'], 'community_archive_file_size')
            content = archive.read(info)
            must(hashlib.sha256(content).hexdigest() == item['sha256'], 'community_archive_file_digest')
    return value
