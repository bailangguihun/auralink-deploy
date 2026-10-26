"""Verify the full actual controller package before resolving any imports."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
from community_host_cli import verify_controller


class ControllerPackage(unittest.TestCase):
    def setUp(self):
        repo=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('community_packager',repo/'scripts/package-community-extension.py')
        pack=importlib.util.module_from_spec(spec);spec.loader.exec_module(pack)
        self.archive,self.manifest=pack.materialize(repo,dict(repository='bailangguihun/auralink-deploy',
            ref='refs/heads/release',commit='a'*40,run_id='1',run_attempt='1'))
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.root.chmod(0o700)
        with zipfile.ZipFile(io.BytesIO(self.archive)) as z:
            for name in z.namelist():
                path=self.root/name;path.write_bytes(z.read(name));path.chmod(0o600)
        path=self.root/'manifest.json';path.write_bytes(self.manifest);path.chmod(0o600)
        self.digest=hashlib.sha256(self.manifest).hexdigest()
        self.owner=(os.geteuid(),os.getegid())

    def check(self):return verify_controller(self.root,self.digest,self.owner)

    def test_actual_package_and_inventory_and_private_metadata(self):
        self.check()
        path=self.root/'community_host.py';raw=path.read_bytes()
        path.write_bytes(raw+b'\n')
        with self.assertRaisesRegex(ValueError,'controller_identity'):self.check()
        path.write_bytes(raw);path.chmod(0o700)
        with self.assertRaisesRegex(ValueError,'controller_identity'):self.check()
        path.chmod(0o600);os.link(path,self.root/'extra.py')
        with self.assertRaisesRegex(ValueError,'controller_identity'):self.check()
        (self.root/'extra.py').unlink();(self.root/'__pycache__').mkdir()
        with self.assertRaisesRegex(ValueError,'controller_cache'):self.check()

    def test_manifest_duplicate_and_unapproved_digest(self):
        self.digest='0'*64
        with self.assertRaisesRegex(ValueError,'manifest_digest'):self.check()
        value=json.loads(self.manifest);value['files'].append(value['files'][0])
        raw=json.dumps(value).encode();(self.root/'manifest.json').write_bytes(raw)
        self.digest=hashlib.sha256(raw).hexdigest()
        with self.assertRaisesRegex(ValueError,'controller_inventory'):self.check()

    def test_manifest_and_directory_links(self):
        original=self.root/'manifest.json';saved=self.root/'saved.json'
        original.rename(saved);original.symlink_to(saved)
        with self.assertRaisesRegex(ValueError,'private_identity'):self.check()


if __name__=='__main__':unittest.main()
