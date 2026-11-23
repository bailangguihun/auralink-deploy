"""Verify the finite planner against actual package emitters and retained files."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import assistant_rollout_prepare as prepare
import core_release_guard as guard


ROOT = Path(__file__).resolve().parents[1]
IDENTITY = {'repository': 'bailangguihun/auralink-deploy', 'ref': 'refs/heads/release',
            'commit': 'ab'*20, 'run_id': '1234567', 'run_attempt': '2'}


def emitter(name):
    path = ROOT/'scripts'/name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FormalInputs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.owner = self.folder.stat().st_uid, self.folder.stat().st_gid

    def run_verify(self, path, archive, manifest, expected):
        archive_path = path/archive
        manifest_name = 'r25-controller-manifest.json' if archive=='r25-controller.zip' else 'manifest.json'
        manifest_path = path/manifest_name
        archive_path.chmod(0o600)
        manifest_path.chmod(0o600)
        reader = guard.read_file

        def read(path, *, expected=None, limit=1024*1024):
            return reader(path, owner=self.owner, expected=expected, limit=limit)

        class Runtime:
            def read(self, target, digest):
                return guard.strict_json(read(target, expected=digest))

        identity = dict(IDENTITY, archive_sha256=prepare.sha(archive_path.read_bytes()),
                        manifest_sha256=prepare.sha(manifest))
        with patch.object(guard, 'read_file', side_effect=read):
            result = prepare.approval_input(Runtime(), path, identity, archive, expected,
                                            controller_manifest=archive=='r25-controller.zip')
            with self.assertRaisesRegex(guard.Refused, 'illustration_formal_manifest'):
                prepare.approval_input(Runtime(), path, dict(identity, run_attempt='1'), archive, expected,
                                       controller_manifest=archive=='r25-controller.zip')
        return result

    def test_controller_inventory_and_source_match_the_formal_emitter(self):
        pack = emitter('package-r25-release.py')
        path = self.folder/'controller'
        pack.create(ROOT/'r25', path, *(IDENTITY[key] for key in prepare.REF_FIELDS))
        manifest = (path/pack.MANIFEST_NAME).read_bytes()
        result = self.run_verify(path, pack.ARCHIVE_NAME, manifest, sorted(prepare.CONTROLLER_FILES))
        self.assertEqual(result['assistant_rollout_prepare.py'], Path(prepare.__file__).read_bytes())

    def test_vmm_and_community_inventory_match_formal_emitter(self):
        for filename, archive, expected in (
                ('package-vmm-extension.py', 'vmm-extension.zip', prepare.VMM_FILES),
                ('package-community-extension.py', 'community-controller.zip', prepare.COMMUNITY_FILES)):
            with self.subTest(archive=archive):
                path = self.folder/filename
                path.mkdir()
                raw, manifest = emitter(filename).materialize(ROOT, IDENTITY)
                (path/archive).write_bytes(raw)
                (path/'manifest.json').write_bytes(manifest)
                files = self.run_verify(path, archive, manifest, expected)
                self.assertEqual(tuple(files), expected)


if __name__ == '__main__':
    unittest.main()
