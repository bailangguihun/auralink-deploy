"""Finite schema-1/schema-2 renderer contract; native SQL is rehearsed separately."""
import hashlib
import unittest
from unittest.mock import patch

import community_migration as migration
from r25_storage import StorageRefused


class ReferenceTransition(unittest.TestCase):
    def setUp(self):
        self.learning = (b'BEGIN;\nCREATE TABLE community.learning_fixture(id integer);\n'
                         b'INSERT INTO community.schema_version VALUES(2);\nCOMMIT;\n')
        self.reference = (b'BEGIN;\nCREATE TABLE community.reference_fixture(id integer);\n'
                          b'INSERT INTO community.schema_version VALUES(3);\nCOMMIT;\n')
        self.operation = 'a' * 32
        for name, raw in [('LEARNING_SHA', self.learning), ('REFERENCE_SHA', self.reference)]:
            item = patch.object(migration, name, hashlib.sha256(raw).hexdigest())
            item.start()
            self.addCleanup(item.stop)

    def render(self, versions, *, learning=None, reference=None, operation=None):
        return migration.reference_transition_sql(
            self.learning if learning is None else learning,
            self.reference if reference is None else reference,
            self.operation if operation is None else operation, versions)

    def test_schema_one_has_one_transaction_and_both_original_receipts(self):
        sql = self.render([1]).decode()
        self.assertEqual(sql.count('BEGIN;'), 1)
        self.assertEqual(sql.count('COMMIT;'), 1)
        self.assertIn('IS DISTINCT FROM ARRAY[1]', sql)
        self.assertIn('IS DISTINCT FROM ARRAY[1,2]', sql)
        self.assertIn('CREATE TABLE community.learning_fixture', sql)
        self.assertIn('CREATE TABLE community.reference_fixture', sql)
        self.assertIn("VALUES('" + self.operation + "','" + migration.LEARNING_SHA + "',2)", sql)
        self.assertIn("VALUES('" + self.operation + "','" + migration.REFERENCE_SHA + "',3)", sql)
        self.assertLess(sql.index('community.learning_fixture'), sql.index('community.reference_fixture'))

    def test_schema_two_does_not_recreate_learning_tables_or_receipt(self):
        sql = self.render([1, 2]).decode()
        self.assertEqual(sql.count('BEGIN;'), 1)
        self.assertEqual(sql.count('COMMIT;'), 1)
        self.assertNotIn('community.learning_fixture', sql)
        self.assertNotIn('CREATE TABLE community.learning_publication_receipt', sql)
        self.assertIn('community.reference_publication_receipt', sql)

    def test_only_exact_integer_predecessor_lists_are_accepted(self):
        invalid = [None, (), (1,), '1', {}, [], [0], [2], [1, 1], [2, 1],
                   [1, 2, 3], [True], [1.0], [True, 2], [1, True], [1, 2.0]]
        for versions in invalid:
            with self.subTest(versions=repr(versions)):
                with self.assertRaisesRegex(StorageRefused, 'exact_predecessor'):
                    self.render(versions)

    def test_both_sources_are_bound_even_when_learning_is_already_installed(self):
        for versions in [[1], [1, 2]]:
            for which in ['learning', 'reference']:
                with self.subTest(versions=versions, source=which):
                    with self.assertRaisesRegex(StorageRefused, 'reviewed_.*_migration_digest'):
                        self.render(versions, **{which: getattr(self, which) + b'-- drift\n'})

    def test_operation_must_be_a_freshly_bound_lowercase_hex_string(self):
        for operation in [None, True, 1, b'a' * 32, '', 'a' * 31, 'a' * 33,
                          'A' * 32, 'g' * 32, 'a' * 31 + "'"]:
            with self.subTest(operation=repr(operation)):
                with self.assertRaisesRegex(StorageRefused, 'reference_operation'):
                    migration.reference_transition_sql(
                        self.learning, self.reference, operation, [1])

    def test_renderer_never_adds_database_restore_or_role_provisioning(self):
        for versions in [[1], [1, 2]]:
            sql = self.render(versions).decode()
            self.assertIn("SET LOCAL lock_timeout='5s'", sql)
            self.assertIn("SET LOCAL statement_timeout='60s'", sql)
            for command in ['CREATE DATABASE', 'DROP DATABASE', 'CREATE ROLE', 'TRUNCATE']:
                self.assertNotIn(command, sql)
            self.assertNotIn('GRANT INSERT,UPDATE,DELETE ON community.v2_operations', sql)
            self.assertNotIn('GRANT INSERT,UPDATE ON community.v2_coin_ledger', sql)


if __name__ == '__main__':
    unittest.main()
