#!/usr/bin/env python3
"""Package-only optional VMM extension; no server, credentials or activation."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'r25'))
from vmm_controller import MODULES, MANIFEST_SCHEMA


def materialize(root, provenance):
    assert provenance['repository'] == 'bailangguihun/auralink-deploy'
    assert provenance['ref'] == 'refs/heads/release'
    assert re.fullmatch('[a-f0-9]{40}', provenance['commit'])
    assert all(re.fullmatch('[1-9][0-9]{0,19}', provenance[k]) for k in ('run_id', 'run_attempt'))
    output, files = io.BytesIO(), []
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name in MODULES:
            path = root / 'r25' / name
            assert path.is_file() and not path.is_symlink()
            raw = path.read_bytes()
            compile(raw, name, 'exec')
            item = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            item.create_system = 3
            item.external_attr = 0o100600 << 16
            archive.writestr(item, raw)
            files.append(dict(path=name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
    raw = output.getvalue()
    manifest = dict(schema=MANIFEST_SCHEMA, **provenance, archive_sha256=hashlib.sha256(raw).hexdigest(), files=files)
    return raw, (json.dumps(manifest, sort_keys=True, separators=(',', ':')) + '\n').encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['create', 'verify'])
    for name in ('repository', 'ref', 'commit', 'run-id', 'run-attempt'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    raw, manifest = materialize(ROOT, {k: getattr(args, k) for k in ('repository', 'ref', 'commit', 'run_id', 'run_attempt')})
    folder = ROOT / 'dist/r25-vmm-extension'
    if args.mode == 'create':
        folder.mkdir(parents=True, exist_ok=True)
        for path, content in ((folder / 'vmm-extension.zip', raw), (folder / 'manifest.json', manifest)):
            with path.open('xb') as output:
                output.write(content)
    else:
        assert (folder / 'vmm-extension.zip').read_bytes() == raw, 'archive differs from reviewed sources/provenance'
        assert (folder / 'manifest.json').read_bytes() == manifest, 'manifest differs from reviewed sources/provenance'
    print('VMM_EXTENSION=NOT_DEPLOYED PACKAGE_' + args.mode.upper() + '_PASS')


if __name__ == '__main__':
    main()
