from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import tempfile
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

    def test_derives_a_second_reviewed_successor_from_the_active_published_core(self):
        installed = guard.render_assistant_rollout_successor_policy(self.old, self.plan)
        prior = deepcopy(installed)
        plan = deepcopy(self.plan)
        plan['current_release'] = {
            'commit_sha': self.candidate['commit_sha'],
            'artifact_sha256': self.candidate['artifact_sha256'],
            'accepted_record_sha256': '7' * 64,
        }
        proof = plan['successor']
        proof['ai_snapshot_core'] = deepcopy(self.plan['current_release'])
        proof['candidate'].update(commit_sha='6'*40, artifact_sha256='6'*64, payload_fingerprint='5'*64)
        proof['web']['commit_sha'] = '4'*40
        plan['operational']['policy']['before_sha256'] = '8' * 64

        value = guard.render_assistant_rollout_successor_policy(installed, plan)

        self.assertEqual(installed, prior)
        self.assertEqual(value['upgrade']['baseline_release'], plan['current_release'])
        self.assertEqual(value['upgrade']['candidate'], proof['candidate'])
        self.assertEqual(value['upgrade']['assistant']['compatible_recovery_jar_sha256'], self.candidate['artifact_sha256'])
        self.assertEqual(value['upgrade']['core_followup']['predecessor_policy_sha256'], '8'*64)
        self.assertEqual(value['upgrade']['assistant']['artifacts']['ai'], prior['upgrade']['assistant']['artifacts']['ai'])

    def test_rejects_wrong_predecessor_or_repackaging(self):
        value = guard.render_assistant_rollout_successor_policy(self.old, self.plan)
        with self.assertRaisesRegex(guard.Refused, 'successor_predecessor'):
            guard.render_assistant_rollout_successor_policy(value, self.plan)
        self.candidate['payload_fingerprint'] = 'b' * 64
        with self.assertRaisesRegex(guard.Refused, 'successor_candidate'):
            guard.render_assistant_rollout_successor_policy(self.old, self.plan)


class SuccessorParentTests(unittest.TestCase):
    def setUp(self):
        self.original_core = {'commit_sha': '1'*40, 'artifact_sha256': '2'*64, 'accepted_record_sha256': '3'*64}
        self.ai = {'commit_sha': '4'*40, 'source_tree_sha256': '5'*64, 'record_sha256': '6'*64}
        self.prior_id = 'a'*64
        self.journal_id = 'b'*64
        self.original_id, self.original_receipt = '1'*64, '2'*64
        self.old_web = {'commit_sha': 'c'*40, 'selection_sha256': 'd'*64, 'reference_sha256': 'e'*64}
        self.rollout = {
            'ai_candidate': {'commit_sha': '4'*40},
            'ai_current_sha256': 'f'*64,
            'ai_active_sha256': '7'*64,
            'community': {'code_controller_sha256': '8'*64, 'code_plan_sha256': '9'*64},
        }
        self.parent = {
            'schema': guard.ASSISTANT_ILLUSTRATION_SUCCESSOR_APPROVAL,
            'current_release': self.original_core,
            'current_ai_release': self.ai,
            'operational': {'policy': {'before_sha256': '2'*64, 'after_sha256': '3'*64}},
            'installed_control': {'approval_sha256': self.original_id, 'journal_sha256': self.original_receipt},
            'successor': {'web': self.old_web, 'ai_journal_sha256': '4'*64, 'ai_snapshot_sha256': '5'*64},
            'assistant_rollout': {**self.rollout, 'web_commit': '6'*40},
        }
        self.plan = {
            'current_release': {'commit_sha': '9'*40, 'artifact_sha256': 'a'*64, 'accepted_record_sha256': 'b'*64},
            'installed_control': {'approval_sha256': self.prior_id, 'journal_sha256': self.journal_id},
            'operational': {'policy': {'before_sha256': '3'*64}},
            'current_ai_release': self.ai,
            'frontend': {key: self.old_web[key] for key in ('selection_sha256', 'reference_sha256')},
            'assistant_rollout': {**self.rollout, 'web_commit': self.old_web['commit_sha']},
            'successor': {'ai_snapshot_core': deepcopy(self.original_core),
                          'ai_journal_sha256': '4'*64, 'ai_snapshot_sha256': '5'*64},
        }
        self.receipt = {'schema': self.parent['schema'], 'approval_sha256': self.prior_id, 'phase': 'INSTALLED'}

    def read(self, file, expected):
        if expected == self.original_id:
            return {
                'schema': guard.ASSISTANT_ILLUSTRATION_APPROVAL_SCHEMA,
                'operational': {'policy': {'after_sha256': '2'*64}},
                'assistant_rollout': {**self.rollout, 'web_commit': self.parent['assistant_rollout']['web_commit']},
            }
        if expected == self.original_receipt:
            return {'schema': guard.ASSISTANT_ILLUSTRATION_APPROVAL_SCHEMA, 'phase': 'INSTALLED',
                    'approval_sha256': self.original_id}
        if file.name == 'approval.json':
            self.assertEqual(expected, self.prior_id)
            return self.parent
        self.assertEqual(expected, self.journal_id)
        self.assertEqual(file.name, 'journal.json')
        return self.receipt

    def test_second_successor_preserves_published_web_rollback_and_original_ai_binding(self):
        parent, folder = guard.load_assistant_rollout_successor_parent(self.plan, read=self.read)
        self.assertEqual(parent, self.parent)
        self.assertEqual(folder.name, self.prior_id)
        self.assertEqual(self.plan['successor']['ai_snapshot_core'], parent['current_release'])

    def test_live_ai_verifier_keeps_the_snapshot_bound_to_the_original_core(self):
        tx = '1' * 32
        documents = {
            'CURRENT.json': {'schema': 'R25_AI_CURRENT_V1', 'release': self.ai},
            'ACTIVE.json': {'schema': 'R25_AI_ACTIVE_V1', 'transaction_id': tx},
            'journal.json': {'schema': 'R25_AI_TRANSACTION_V1', 'phase': 'COMMITTED',
                             'candidate': self.ai, 'transaction_id': tx, 'snapshot_sha256': '5'*64},
            'snapshot.json': {'schema': 'R25_AI_SNAPSHOT_V1', 'candidate': self.ai,
                              'transaction_id': tx, 'core': self.original_core},
            'POLICY.json': {'source_owner': [0, 0]},
            'record.json': {},
        }
        policy = {'upgrade': {'core_followup': {'ai_policy_sha256': 'a'*64, 'ai_configuration_sha256': 'b'*64}},
                  'installation': {'private_owner': [0, 0]}}
        parent_folder = guard.STORE/'controller-upgrades'/self.prior_id
        with patch.object(guard, 'private_record', side_effect=lambda path, expected: documents[path.name]), \
             patch.object(guard, 'load_assistant_rollout_successor_parent', return_value=(self.parent, parent_folder)), \
             patch.object(guard, 'handoff_values', return_value={'R25_AI_RELEASE_POLICY_SHA256': 'a'*64}), \
             patch.object(guard, 'read_file'), patch.object(guard, '_rollout_ai_inventory', return_value={}), \
             patch.object(guard, '_verify_rollout_live_ai'), \
             patch.object(guard, 'verify_assistant_rollout_core', return_value=self.plan['current_release']), \
             patch.object(guard, 'verify_assistant_rollout_successor_web') as web:
            self.assertEqual(guard.verify_assistant_rollout_successor_ai(policy, self.plan, parent_folder, for_start=True),
                             self.ai)
            web.assert_called_once_with(policy, self.plan, None)
            documents['snapshot.json']['core'] = self.plan['current_release']
            with self.assertRaisesRegex(guard.Refused, 'illustration_successor_ai_provenance'):
                guard.verify_assistant_rollout_successor_ai(policy, self.plan, parent_folder, for_start=True)

    def test_refuses_rebound_ai_snapshot_or_web_rollback(self):
        for field, bad in (
            ('ai_snapshot_core', {**self.original_core, 'commit_sha': '9'*40}),
            ('ai_snapshot_sha256', '9'*64),
        ):
            changed = deepcopy(self.plan)
            changed['successor'][field] = bad
            with self.assertRaisesRegex(guard.Refused, 'illustration_successor_ai_parent'):
                guard.load_assistant_rollout_successor_parent(changed, read=self.read)
        changed = deepcopy(self.plan)
        changed['assistant_rollout']['web_commit'] = self.parent['assistant_rollout']['web_commit']
        with self.assertRaisesRegex(guard.Refused, 'illustration_successor_web_parent'):
            guard.load_assistant_rollout_successor_parent(changed, read=self.read)

    def test_third_successor_keeps_the_original_ai_anchor_and_checks_historical_receipts(self):
        first, second, third = (deepcopy(item) for item in (self.parent, self.plan, self.plan))
        original_id, original_receipt = '1'*64, '2'*64
        second_id, second_receipt = '6'*64, '7'*64
        first['installed_control'] = {'approval_sha256': original_id, 'journal_sha256': original_receipt}
        first['operational']['policy']['before_sha256'] = '2'*64
        original = {
            'schema': guard.ASSISTANT_ILLUSTRATION_APPROVAL_SCHEMA,
            'operational': {'policy': {'after_sha256': '2'*64}},
            'assistant_rollout': {**self.rollout, 'web_commit': first['assistant_rollout']['web_commit']},
        }
        second['schema'] = first['schema']
        second['operational']['policy']['after_sha256'] = '8'*64
        web = {'commit_sha': '8'*40, 'selection_sha256': '7'*64, 'reference_sha256': '6'*64}
        second['successor']['web'] = web
        third['installed_control'] = {'approval_sha256': second_id, 'journal_sha256': second_receipt}
        third['operational']['policy']['before_sha256'] = '8'*64
        third['frontend'] = {key: web[key] for key in ('selection_sha256', 'reference_sha256')}
        third['assistant_rollout']['web_commit'] = web['commit_sha']
        records = {
            original_id: original,
            original_receipt: {'schema': original['schema'], 'phase': 'INSTALLED', 'approval_sha256': original_id},
            self.prior_id: first,
            self.journal_id: self.receipt,
            second_id: second,
            second_receipt: {'schema': second['schema'], 'phase': 'INSTALLED', 'approval_sha256': second_id},
        }

        def read(_path, expected):
            return records[expected]

        parent, folder = guard.load_assistant_rollout_successor_parent(third, read=read)
        self.assertEqual(parent, second)
        self.assertEqual(folder.name, second_id)
        self.assertEqual(
            [(doc, path.name) for doc, path in guard.load_assistant_rollout_ancestry(third, read=read)],
            [(second, second_id), (first, self.prior_id), (original, original_id)],
        )
        for record in (original_receipt, self.journal_id, second_receipt):
            with self.subTest(record=record):
                saved = records[record]
                records[record] = dict(saved, phase='RESTORED')
                try:
                    with self.assertRaisesRegex(guard.Refused, 'illustration_successor_controller_parent'):
                        guard.load_assistant_rollout_successor_parent(third, read=read)
                finally:
                    records[record] = saved
        second['successor']['ai_snapshot_core'] = second['current_release']
        third['successor']['ai_snapshot_core'] = second['current_release']
        with self.assertRaisesRegex(guard.Refused, 'illustration_successor_ai_parent'):
            guard.load_assistant_rollout_successor_parent(third, read=read)

    def test_refuses_missing_terminal_receipt_and_invalid_predecessor(self):
        self.receipt['phase'] = 'VERIFY_INTENT'
        with self.assertRaisesRegex(guard.Refused, 'illustration_successor_controller_parent'):
            guard.load_assistant_rollout_successor_parent(self.plan, read=self.read)
        self.receipt['phase'] = 'INSTALLED'
        self.parent['successor']['ai_snapshot_core'] = self.original_core
        with self.assertRaisesRegex(guard.Refused, 'illustration_successor_controller_parent'):
            guard.load_assistant_rollout_successor_parent(self.plan, read=self.read)


class SuccessorWebTests(unittest.TestCase):
    def test_old_web_remains_startable_with_either_core(self):
        before = {'selection_sha256': 'a' * 64, 'reference_sha256': 'b' * 64}
        after = {'selection_sha256': 'c' * 64, 'reference_sha256': 'd' * 64, 'commit_sha': 'e' * 40}
        approval = {'frontend': before, 'assistant_rollout': {'web_commit': 'f' * 40},
                    'successor': {'web': after}}
        for core_at in (None, 100):
            with self.subTest(core_at=core_at), patch.object(guard, 'frontend_digests', return_value=before), \
                 patch.object(guard, '_verify_independent_web_history') as receipts, \
                 patch.object(guard, '_verify_rollout_live_web', return_value=True) as live:
                self.assertTrue(guard.verify_assistant_rollout_successor_web({}, approval, core_at))
                receipts.assert_not_called()
                live.assert_called_once_with({}, 'f' * 40, before['selection_sha256'])


class IndependentWebReceiptTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.runtime = Path(temp.name)
        self.paths = self.runtime / 'selection.json', self.runtime / 'selection.reference'
        self.before = self.pair('a')
        self.anchor = self.pair('b')
        self.intermediate = self.pair('c')
        self.current = self.pair('d')
        self.core_at = 1790000000
        self.archive = 'f' * 64
        self.approval = {'frontend': self.hashes(self.before), 'assistant_rollout': {'web_commit': 'a' * 40},
                         'successor': {'web': self.hashes(self.anchor) | {
                             'commit_sha': 'b' * 40, 'github_run_id': '1', 'github_run_attempt': '1',
                             'archive_sha256': self.archive, 'build_id': 'build-b'}}}
        for name, value in [('LOCK', self.runtime / 'lock'), ('frontend_paths', lambda: self.paths),
                            ('read_file', self.read)]:
            mocked = patch.object(guard, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        live = patch.object(guard, '_verify_rollout_live_web', return_value=True)
        self.verify_live = live.start()
        self.addCleanup(live.stop)
        self.receipt(1, self.before, self.anchor, 'b')
        self.receipt(2, self.anchor, self.intermediate, 'c')
        self.receipt(3, self.intermediate, self.current, 'd')
        self.select(self.current)

    def select(self, pair):
        for path, data in zip(self.paths, pair, strict=True):
            path.write_bytes(data)

    @staticmethod
    def hashes(pair):
        return dict(zip(('selection_sha256', 'reference_sha256'),
                        (sha256(item).hexdigest() for item in pair), strict=True))

    @staticmethod
    def pair(identity):
        commit, build = identity * 40, 'build-' + identity
        selection = guard.encoded({'commit_sha': commit, 'build_id': build, 'provenance': {'kind': 'git_commit'}})
        reference = (f'schema=ARTLIVE_FRONTEND_SELECTION_REFERENCE_V1\nselection_sha256={sha256(selection).hexdigest()}'
                     f'\ncommit_sha={commit}\nbuild_id={build}\n').encode()
        return selection, reference

    @staticmethod
    def read(path, *, expected=None, **kwargs):
        raw = path.read_bytes()
        guard.must(expected in (None, sha256(raw).hexdigest()), 'fixture_file_digest')
        return raw

    def receipt(self, index, before, after, commit, **overrides):
        stage = self.runtime / f'frontend-selection-transition-{(self.core_at + index) * 10**9}'
        stage.mkdir(exist_ok=True)
        state = {'state': 'COMMITTED', 'stage': str(stage), 'selection': str(self.paths[0]),
                 'reference': str(self.paths[1]), 'expected_commit': commit * 40,
                 'requested_ref': f'{commit * 40}-{index}-1', 'artifact_sha256': self.archive,
                 'expected_build_id': 'build-' + commit}
        for side, pair in (('old', before), ('new', after)):
            for name, data in zip(('selection', 'reference'), pair, strict=True):
                (stage / f'{side}.{name}').write_bytes(data)
                state[f'{side}_{name}_sha'] = sha256(data).hexdigest()
        (stage / 'state.json').write_bytes(guard.encoded(state | overrides))
        return stage

    def check_live(self, core_at):
        return guard.verify_assistant_rollout_successor_web({}, self.approval, core_at)

    def test_later_artifacts_are_allowed_after_the_reviewed_web_commit(self):
        self.assertTrue(self.check_live(self.core_at))
        self.verify_live.assert_called_once_with({}, 'd' * 40, self.hashes(self.current)['selection_sha256'])

    def test_later_web_release_cannot_run_before_the_core_commit(self):
        with self.assertRaises(guard.Refused):
            self.check_live(None)

    def test_receipts_cannot_skip_the_reviewed_anchor_or_intermediate_commit(self):
        self.receipt(2, self.before, self.intermediate, 'c')
        with self.assertRaises(guard.Refused):
            self.check_live(self.core_at)

    def test_newest_uncommitted_receipt_is_refused(self):
        self.receipt(3, self.intermediate, self.current, 'd', state='CONFIG_INSTALLED')
        with self.assertRaises(guard.Refused):
            self.check_live(self.core_at)

    def test_foreign_artifact_identity_or_tampered_snapshot_is_refused(self):
        self.receipt(2, self.anchor, self.intermediate, 'c', expected_build_id='other')
        with self.assertRaises(guard.Refused):
            self.check_live(self.core_at)
        stage = self.receipt(2, self.anchor, self.intermediate, 'c')
        (stage / 'new.reference').write_bytes(b'tampered')
        with self.assertRaises(guard.Refused):
            self.check_live(self.core_at)

    def test_reviewed_anchor_still_requires_the_exact_approved_artifact(self):
        self.receipt(1, self.before, self.anchor, 'b', artifact_sha256='e' * 64)
        with self.assertRaises(guard.Refused):
            self.check_live(self.core_at)

    def test_reviewed_anchor_without_later_releases_is_startable(self):
        stages = sorted(self.runtime.glob('frontend-selection-transition-*'))
        for stage in stages[1:]:
            for file in stage.iterdir():
                file.unlink()
            stage.rmdir()
        self.select(self.anchor)
        self.assertTrue(self.check_live(self.core_at))

    def test_baseline_retained_receipt_keeps_the_last_committed_release(self):
        self.receipt(4, self.current, self.pair('e'), 'e', state='BASELINE_RETAINED')
        self.assertTrue(self.check_live(self.core_at))

    def test_managed_rollback_to_reviewed_anchor_reuses_the_exact_chain(self):
        self.receipt(4, self.current, self.anchor, 'b')
        self.select(self.anchor)
        self.assertTrue(self.check_live(self.core_at))

    def test_mixed_selection_and_reference_are_refused(self):
        self.select((self.current[0], self.anchor[1]))
        with self.assertRaisesRegex(guard.Refused, 'successor_web_pair'):
            self.check_live(self.core_at)


if __name__ == '__main__':
    unittest.main()
