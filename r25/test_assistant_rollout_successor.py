from copy import deepcopy
import unittest
from unittest.mock import patch

import core_release_guard as guard


class SuccessorPolicyTests(unittest.TestCase):
    def setUp(self):
        self.old = {
            'schema': guard.BUSINESS_POLICY_SCHEMA,
            'controllers': {'core_release_guard.py': 'a' * 64, 'lifecycle_adapter.py': 'e' * 64},
            'runtime_modules': {'core_release_guard.py': 'a' * 64, 'vmm_controller.py': 'b' * 64},
            'upgrade': {
                'candidate': {'commit_sha': 'a' * 40, 'artifact_sha256': 'a' * 64, 'payload_fingerprint': 'b' * 64},
                'core_followup': {'schema': guard.ASSISTANT_ILLUSTRATION_CORE_SCHEMA},
                'assistant': {'artifacts': {'core': {}, 'deploy': {}, 'ai': {'sha256': 'e' * 64}}, 'migrations': []},
                'configuration': {'before_sha256': 'c' * 64, 'after_sha256': 'c' * 64},
                'compatibility': {'ledger': []},
            },
        }
        self.candidate = {
            'repository': 'bailangguihun/auralink-core-api', 'commit_sha': 'd' * 40,
            'artifact_sha256': 'd' * 64, 'payload_fingerprint': 'e' * 64,
            'artifact_bytes': 7000, 'github_run_id': '11', 'github_run_attempt': '1',
        }
        self.plan = {
            'current_release': {'commit_sha': 'a' * 40, 'artifact_sha256': 'a' * 64, 'accepted_record_sha256': 'c' * 64},
            'files': {'core_release_guard.py': {'after_sha256': 'f' * 64}},
            'operational': {'policy': {'before_sha256': '3' * 64}, 'guard': {'after_sha256': 'f' * 64}},
            'deploy': {'repository': 'bailangguihun/auralink-deploy', 'commit': 'b' * 40, 'archive_sha256': 'c' * 64},
            'review_evidence_sha256': 'd' * 64,
            'successor': {'candidate': self.candidate, 'web': {'commit_sha': 'c' * 40}},
        }

    def test_derives_policy_without_mutating_predecessor(self):
        before = deepcopy(self.old)
        value = guard.render_assistant_rollout_successor_policy(self.old, self.plan)
        self.assertEqual(self.old, before)
        self.assertTrue(guard.is_assistant_rollout_successor(value))
        self.assertEqual(value['upgrade']['candidate'], self.candidate)
        self.assertEqual(value['upgrade']['baseline_release'], self.plan['current_release'])
        self.assertEqual(value['upgrade']['compatibility'], before['upgrade']['compatibility'])
        self.assertEqual(value['upgrade']['configuration'], before['upgrade']['configuration'])
        self.assertEqual(value['upgrade']['assistant']['artifacts']['ai'], before['upgrade']['assistant']['artifacts']['ai'])
        self.assertEqual(value['controllers']['lifecycle_adapter.py'], before['controllers']['lifecycle_adapter.py'])
        self.assertEqual(value['controllers']['core_release_guard.py'], 'f' * 64)

    def test_rejects_wrong_predecessor_or_repackaging(self):
        value = guard.render_assistant_rollout_successor_policy(self.old, self.plan)
        with self.assertRaisesRegex(guard.Refused, 'successor_predecessor'):
            guard.render_assistant_rollout_successor_policy(value, self.plan)
        self.candidate['payload_fingerprint'] = 'b' * 64
        with self.assertRaisesRegex(guard.Refused, 'successor_candidate'):
            guard.render_assistant_rollout_successor_policy(self.old, self.plan)


class SuccessorWebTests(unittest.TestCase):
    def check_web(self, selected, candidate_committed=False):
        before = {'selection_sha256': 'a' * 64, 'reference_sha256': 'b' * 64}
        after = {'selection_sha256': 'c' * 64, 'reference_sha256': 'd' * 64, 'commit_sha': 'e' * 40}
        approval = {'frontend': before, 'assistant_rollout': {'web_commit': 'f' * 40},
                    'successor': {'web': after}}
        with patch.object(guard, 'frontend_digests', return_value=selected), \
             patch.object(guard, '_rollout_web_commit_receipt') as receipt, \
             patch.object(guard, '_verify_rollout_live_web', return_value=True) as live:
            result = guard.verify_assistant_rollout_successor_web({}, approval, 100 if candidate_committed else None)
            return result, receipt, live

    def test_old_web_remains_startable_with_either_core(self):
        old = {'selection_sha256': 'a' * 64, 'reference_sha256': 'b' * 64}
        for candidate_committed in (False, True):
            with self.subTest(candidate_committed=candidate_committed):
                result, receipt, live = self.check_web(old, candidate_committed)
                self.assertTrue(result)
                receipt.assert_not_called()
                self.assertEqual(live.call_args.args[1], 'f' * 40)

    def test_new_web_needs_a_publication_after_the_new_core_commit(self):
        new = {'selection_sha256': 'c' * 64, 'reference_sha256': 'd' * 64}
        with self.assertRaisesRegex(guard.Refused, 'successor_web_core_not_committed'):
            self.check_web(new)
        result, receipt, live = self.check_web(new, True)
        self.assertTrue(result)
        self.assertEqual(receipt.call_args.kwargs['core_at'], 100)
        self.assertEqual(live.call_args.args[1], 'e' * 40)

    def test_refuses_mixed_or_foreign_web_selection(self):
        with self.assertRaisesRegex(guard.Refused, 'successor_web_pair'):
            self.check_web({'selection_sha256': 'a' * 64, 'reference_sha256': 'd' * 64}, True)


if __name__ == '__main__':
    unittest.main()
