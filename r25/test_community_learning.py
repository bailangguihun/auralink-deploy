import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from community_migration import LEARNING_SHA, learning_schema_sql
from community_bundle import manifest, LEARNING_FILES, REQUIRED
import test_community_runtime as legacy_runtime
from r25_storage import StorageRefused


class CommunityLearningMigrationTest(unittest.TestCase):
    def test_new_bundle_requires_complete_learning_inventory_and_known_native_target(self):
        expected = dict(commit='a'*40,run_id='1',run_attempt='1')
        value = dict(schema='artlive-community-bundle/v1',repository='bailangguihun/auralink-core-api',ref='refs/heads/release',
                     contract_version=1,database_schema_version=1,learning_schema_version=2,runtime_node_min='22.13.0',
                     runtime_platform=dict(os='linux',arch='x64',libc='glibc',sharp='0.34.3'),
                     files=[dict(path=name,bytes=1,sha256='b'*64) for name in sorted(REQUIRED | LEARNING_FILES)],**expected)
        self.assertEqual(manifest(json.dumps(value).encode(),expected)['learning_schema_version'],2)
        value['files']=[item for item in value['files'] if item['path']!='src/learning-media.mjs']
        with self.assertRaisesRegex(Exception,'inventory_scope'):manifest(json.dumps(value).encode(),expected)
        value['runtime_platform']['os']='windows'
        with self.assertRaisesRegex(Exception,'native_image_target'):manifest(json.dumps(value).encode(),expected)

    def test_learning_migration_requires_exact_reviewed_bytes(self):
        with self.assertRaisesRegex(Exception, 'reviewed_learning'):
            learning_schema_sql(b'BEGIN;\nSELECT 1;\nCOMMIT;', 'a' * 32)

    def test_migration_renderer_is_transactional_and_preserves_historical_rows(self):
        import community_migration
        from unittest.mock import patch
        source = b'-- fixture\nBEGIN;\nALTER TABLE community.posts ADD COLUMN title text;\nINSERT INTO community.schema_version VALUES(2);\nCOMMIT;\n'
        with patch.object(community_migration, 'LEARNING_SHA', hashlib.sha256(source).hexdigest()):
            sql = learning_schema_sql(source, 'a' * 32).decode()
        self.assertEqual(sql.count('BEGIN;'), 1)
        self.assertEqual(sql.count('COMMIT;'), 1)
        self.assertIn('IS DISTINCT FROM ARRAY[1]', sql)
        self.assertIn('learning_publication_receipt', sql)
        for forbidden in ('DROP ', 'TRUNCATE ', 'DELETE ', 'CREATE ROLE', 'CREATE DATABASE'):
            self.assertNotIn(forbidden, sql)
        self.assertEqual(len(LEARNING_SHA), 64)


class CommunityLearningRuntimeTest(unittest.TestCase):
    def fixture(self):
        f = legacy_runtime.CommunityRuntimeTest('runTest')
        f.setUp()
        self.addCleanup(f.doCleanups)
        for name in LEARNING_FILES:
            path = f.release/name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_text('fixture')
            path.chmod(0o600)
        import subprocess
        target = json.loads(subprocess.check_output([f.binding['node'], '-p', "JSON.stringify({os:process.platform,arch:process.arch,libc:process.report.getReport().header.glibcVersionRuntime?'glibc':'musl'})"], env={}))
        f.manifest.update(learning_schema_version=2, runtime_platform={**target, 'sharp':'0.34.3'},
            files=[dict(path=name,bytes=len((f.release/name).read_bytes()),sha256=legacy_runtime.digest((f.release/name).read_bytes())) for name in sorted(REQUIRED | LEARNING_FILES)])
        (f.config/'media').mkdir(mode=0o700)
        self.save_manifest(f)
        self.save_config(f, str(f.config/'media'))
        return f

    def save_manifest(self, f):
        raw=json.dumps(f.manifest).encode()
        (f.release/'manifest.json').write_bytes(raw)
        f.binding['manifest_sha256']=legacy_runtime.digest(raw)

    def save_config(self, f, media):
        path=f.config/'env.json'
        value=json.loads(path.read_bytes());value['COMMUNITY_MEDIA_ROOT']=media
        raw=json.dumps(value).encode();path.write_bytes(raw)
        f.binding['config_sha256']=legacy_runtime.digest(raw)

    def test_learning_runtime_requires_exact_private_media_root(self):
        f=self.fixture()
        self.assertEqual(f.role.verify()['COMMUNITY_MEDIA_ROOT'],str(f.config/'media'))
        self.save_config(f,str(f.config/'other'))
        with self.assertRaisesRegex(StorageRefused,'media_root_binding'):f.role.verify()
        self.save_config(f,str(f.config/'media'))
        (f.config/'media').chmod(0o755)
        with self.assertRaisesRegex(StorageRefused,'mode'):f.role.verify()

    def test_learning_runtime_rejects_native_platform_drift_before_start(self):
        f=self.fixture()
        f.manifest['runtime_platform']['arch']='arm64' if f.manifest['runtime_platform']['arch']=='x64' else 'x64'
        self.save_manifest(f)
        with self.assertRaisesRegex(StorageRefused,'native_platform_changed'):f.role.verify()
        self.assertFalse((f.root/'current.json').exists())


if __name__ == '__main__':
    unittest.main()
