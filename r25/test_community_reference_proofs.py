"""Retained Community proofs and live Core identity stay independently verifiable."""
import unittest
from unittest.mock import patch

import community_host as community
import test_reference_publication as fixtures


class ReferenceProofs(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ReferenceCoreRecords('runTest')
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()

    def test_retained_proofs_are_independent_of_live_policy_and_current(self):
        f = self.f
        expected = community.reference_core_record(f.host, f.core_folder / 'approval.json')
        successor = {'commit_sha': 'a' * 40, 'artifact_sha256': 'b' * 64}
        f.put(f.base / 'POLICY.json', {'upgrade': {'candidate': successor}})
        f.current(successor)

        self.assertEqual(
            community.reference_package_proofs(f.host, f.reference_id),
            (expected, f.expected['predecessor'], f.expected['previous_context']),
        )
        with self.assertRaisesRegex(ValueError, 'community_reference_context_policy'):
            community.reference_package_context(f.host, f.reference_id)
        f.retained()

    def test_retained_proofs_reject_changed_approval_marker_or_journal(self):
        f = self.f
        for path in f.old_receipts:
            with self.subTest(path=path.name):
                raw = path.read_bytes()
                f.put(path, dict(f.host.read(path), changed=True))
                try:
                    with self.assertRaises(ValueError):
                        community.reference_package_proofs(f.host, f.reference_id)
                finally:
                    path.write_bytes(raw)
        f.put(f.core_folder / 'journal.json', {'approval_sha256': f.reference_id, 'phase': 'PREPARED'})
        with self.assertRaisesRegex(ValueError, 'community_reference_context_not_installed'):
            community.reference_package_proofs(f.host, f.reference_id)
        f.retained()

    def test_committed_core_journal_binds_the_exact_record_and_current_release(self):
        f = self.f
        current = {'transaction_id': f.tx, 'release': f.candidate}
        path = f.base / 'transactions' / f.tx / 'journal.json'
        expected = {'transaction_id': f.tx, 'candidate': f.candidate, 'phase': 'COMMITTED'}
        digest = community.sha(community.encoded(expected))

        self.assertEqual(community.committed_core_journal(f.host, current, expected_sha256=digest), expected)
        with self.assertRaisesRegex(ValueError, 'record_sha256_mismatch'):
            community.committed_core_journal(f.host, current, expected_sha256='d' * 64)
        with self.assertRaisesRegex(ValueError, 'community_reference_core_transaction'):
            community.committed_core_journal(f.host, dict(current, transaction_id='../unexpected'))
        f.put(path, dict(expected, candidate=f.old_release))
        with self.assertRaisesRegex(ValueError, 'community_reference_core_not_committed'):
            community.committed_core_journal(f.host, current)

    def test_assistant_reference_context_uses_only_the_bound_predecessor_proofs(self):
        f = self.f
        f.current(f.candidate)
        rollout_sha = 'e' * 64
        before = f.base / 'controller-upgrades' / rollout_sha / 'before'
        policy = community.reference_core_record(f.host, f.base/'POLICY.json')
        current = community.reference_core_record(f.host, f.base/'CURRENT.json')
        saved_policy = before/'policy'
        saved_current = before/'ILLUSTRATION_CORE_CURRENT.json'
        f.put(saved_policy, policy)
        f.put(saved_current, current)
        runtime = {'current_sha256': community.identity(saved_current)['sha256'],
                   'transaction_sha256': community.identity(f.base/'transactions'/f.tx/'journal.json')['sha256'],
                   'jar_sha256': f.candidate['artifact_sha256']}
        rollout = {'operational': {'policy': {'before_sha256': community.identity(saved_policy)['sha256']}},
                   'assistant_rollout': {'core_current_sha256': runtime['current_sha256']}}
        f.put(f.base/'POLICY.json', {'upgrade': {'candidate': {'commit_sha': 'a' * 40}}})
        f.current({'commit_sha': 'a' * 40, 'artifact_sha256': 'b' * 64})

        def context():
            return community.assistant_reference_package_context(f.host, f.reference_id, rollout_sha, rollout,
                                                                 {'core_runtime': runtime})

        self.assertEqual(f.expected, context())
        journal = f.base/'transactions'/f.tx/'journal.json'
        for path in (saved_policy, saved_current, journal):
            with self.subTest(path=path.name):
                raw = path.read_bytes()
                path.write_bytes(raw+b' ')
                try:
                    with self.assertRaisesRegex(ValueError, 'record_sha256_mismatch'):
                        context()
                finally:
                    path.write_bytes(raw)
        f.retained()

    def test_assistant_controller_requires_exact_active_approval_and_scopes_verify_intent(self):
        f = self.f
        code_sha, manifest_sha = 'a'*64, 'b'*64
        approval = {'schema': community.ASSISTANT_ROLLOUT_SCHEMA,
                    'assistant_rollout': {'community': {'code_controller_sha256': code_sha,
                        'code_plan_sha256': 'c'*64, 'deploy': {'manifest_sha256': manifest_sha}}}}
        rollout_sha = community.sha(community.encoded(approval))
        folder = f.base/'controller-upgrades'/rollout_sha
        pointer = f.base/'CONTROL_UPGRADE_ACTIVE.json'
        marker = {'schema': 'R25_CORE_CONTROLLER_UPGRADE_ACTIVE_V1', 'approval_sha256': rollout_sha}
        receipt = {'schema': approval['schema'], 'approval_sha256': rollout_sha, 'phase': 'VERIFY_INTENT'}
        f.put(pointer, marker)
        f.put(folder/'approval.json', approval)
        f.put(folder/'journal.json', receipt)
        storage = community.R25Storage
        def approved(*, code=code_sha, manifest=manifest_sha, preserve_only=False):
            return community.assistant_community_approval(rollout_sha, code, manifest, preserve_only=preserve_only)
        with patch.object(community, 'R25Storage', side_effect=lambda roots, *_: storage(roots, *f.host.owner)):
            self.assertEqual(approved(preserve_only=True), approval)
            with self.assertRaisesRegex(ValueError, 'community_assistant_controller_not_installed'):
                approved()
            f.put(folder/'journal.json', dict(receipt, phase='INSTALLED'))
            self.assertEqual(approved(), approval)
            for key in ('code','manifest'):
                with self.subTest(binding=key), self.assertRaisesRegex(ValueError, 'community_assistant_controller_binding'):
                    approved(**{key:'d'*64})
            f.put(pointer, dict(marker, approval_sha256='d'*64))
            with self.assertRaisesRegex(ValueError, 'community_assistant_controller_pointer'):
                approved()

    def test_code_upgrade_still_requires_both_live_core_digests(self):
        f = self.f
        f.current(f.candidate)
        current = f.base / 'CURRENT.json'
        journal = f.base / 'transactions' / f.tx / 'journal.json'
        reference = community.ReferencePackageUpgrade(f.host, None, f.expected)
        runtime = {'current_sha256': community.identity(current)['sha256'],
                   'transaction_sha256': community.identity(journal)['sha256']}
        code = community.CodePackageUpgrade(reference, {'core_runtime': runtime})
        self.assertIsNone(code.core())
        for key in runtime:
            with self.subTest(missing_digest=key):
                unbound = community.CodePackageUpgrade(reference, {'core_runtime': dict(runtime, **{key: None})})
                with self.assertRaisesRegex(ValueError, 'sha256_identity'):
                    unbound.core()
        for path in (current, journal):
            with self.subTest(path=path.name):
                raw = path.read_bytes()
                path.write_bytes(raw + b' ')
                try:
                    with self.assertRaises(ValueError):
                        code.core()
                finally:
                    path.write_bytes(raw)


if __name__ == '__main__':
    unittest.main()
