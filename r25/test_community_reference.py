import hashlib
import json
import unittest
from unittest.mock import patch
import community_migration
from community_migration import reference_schema_sql
from community_bundle import manifest, REQUIRED, LEARNING_FILES, REFERENCE_FILES


class ReferencePublicationTest(unittest.TestCase):
    def test_pinned_additive_schema_and_no_receipt_delete_privilege(self):
        source=b'-- fixture\nBEGIN;\nALTER TABLE community.posts ADD COLUMN reference_title text;\nINSERT INTO community.schema_version VALUES(3);\nCOMMIT;\n'
        with patch.object(community_migration,'REFERENCE_SHA',hashlib.sha256(source).hexdigest()):
            sql=reference_schema_sql(source,'a'*32).decode()
        self.assertIn('ARRAY[1,2]',sql)
        self.assertIn('reference_publication_receipt',sql)
        self.assertIn('GRANT INSERT,UPDATE ON community.v2_operations',sql)
        self.assertIn('GRANT INSERT ON community.v2_coin_ledger',sql)
        self.assertNotIn('GRANT INSERT,UPDATE,DELETE ON community.v2_operations',sql)
        self.assertNotIn('TRUNCATE',sql)
        with self.assertRaises(Exception):reference_schema_sql(source,'a'*32)

    def test_v3_requires_complete_inventory_and_matching_native_target(self):
        expected=dict(commit='a'*40,run_id='1',run_attempt='1')
        files=[dict(path=name,bytes=1,sha256='b'*64) for name in sorted(REQUIRED|LEARNING_FILES|REFERENCE_FILES)]
        value=dict(schema='artlive-community-bundle/v1',repository='bailangguihun/auralink-core-api',ref='refs/heads/release',**expected,
                   contract_version=1,database_schema_version=1,learning_schema_version=2,reference_schema_version=3,
                   runtime_node_min='22.13.0',runtime_platform=dict(os='linux',arch='x64',libc='glibc',sharp='0.35.5'),files=files)
        self.assertEqual(manifest(json.dumps(value).encode(),expected)['reference_schema_version'],3)
        value['runtime_platform']['sharp']='0.34.3'
        with self.assertRaises(Exception):manifest(json.dumps(value).encode(),expected)
        value['runtime_platform']['sharp']='0.35.5';value['files']=files[:-1]
        with self.assertRaises(Exception):manifest(json.dumps(value).encode(),expected)


if __name__=='__main__':unittest.main()
