"""Exercise the new AI source proof with private temporary release records."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ai_release_guard as ai
import ai_runtime
import assistant_rollout_binding as binding
import core_release_guard as guard
import test_ai_runtime as legacy
import test_assistant_rollout_binding as fixture
from publisher_transaction import Release

sha = legacy.sha


class IllustrationAIReleaseTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = self.root / 'runtime/ai-publications'
        self.core = self.root / 'runtime/core-publications'
        self.folder = self.core / 'controller-upgrades' / ('a' * 64)
        inventory = ai.ASSISTANT_PATHS
        self.old_source = {path: b'# original\n' for path in inventory}
        self.new_source = self.old_source | {'critic-service/app/assistant/execution.py': b'# replacement\n'}
        self.dependencies = {path: sha(self.old_source[path]) for path in ai.DEPENDENCIES}
        self.ai_policy_sha = self.put(self.store / 'POLICY.json', {
            'schema': 'R25_AI_SOURCE_POLICY_V2', 'repository': 'bailangguihun/auralink-ai-service',
            'source_paths': list(inventory), 'source_owner': [0, 0], 'dependency_sha256': self.dependencies,
            'legacy_policy_sha256': '0' * 64, 'review_evidence_sha256': '9' * 64,
        })
        self.old, self.old_record = self.release('1' * 40, self.old_source)
        self.new, self.new_record = self.release('2' * 40, self.new_source)
        self.before_current = fixture.BEFORE_CURRENT | {'release': self.old}
        self.current(self.before_current, fixture.BEFORE_ACTIVE)
        self.configuration = self.root / 'runtime/config/assistant-private/assistant-runtime.json'
        self.policy = {'schema': guard.BUSINESS_POLICY_SCHEMA,
                       'installation': {'private_owner': [0, 0], 'controller_owner': [0, 0]}, 'upgrade': {
            'baseline_release': fixture.CORE_BEFORE, 'candidate': fixture.CORE_AFTER,
            'core_followup': {'schema': guard.ASSISTANT_ILLUSTRATION_CORE_SCHEMA,
                'ai_policy_sha256': self.ai_policy_sha, 'ai_release': self.old,
                'ai_current_sha256': self.put(self.folder / 'before/ILLUSTRATION_AI_CURRENT.json', self.before_current),
                'ai_active_sha256': self.put(self.folder / 'before/ILLUSTRATION_AI_ACTIVE.json', fixture.BEFORE_ACTIVE),
                'ai_configuration_sha256': self.put(self.configuration, {'scope': 'configuration'}),
                'ai_journal_sha256': self.put(self.store / 'transactions' / fixture.BEFORE_TX / 'journal.json', {
                    'schema': 'R25_AI_TRANSACTION_V1', 'transaction_id': fixture.BEFORE_TX,
                    'phase': 'COMMITTED', 'candidate': self.old,
                }),
            },
        }}
        self.approval = {'assistant_rollout': {'ai_candidate': {
            key: self.new_record[key] for key in binding.AI_PROVENANCE
        }}, 'frontend': {}}
        self.install(self.old_source)
        for name, replacement in (
            ('ROOT', self.root), ('STORE', self.core), ('read_file', self.read_file),
            ('assistant_rollout_binding_module', lambda approval: binding),
            ('load_assistant_rollout_approval', lambda value: (self.approval, self.folder, 'INSTALLED')),
            ('verify_core_followup_history', lambda value: self.old),
            ('handoff_values', lambda: {'R25_AI_RELEASE_POLICY_SHA256': self.ai_policy_sha}),
            ('verify_assistant_rollout_core', lambda *args: self.live_core),
            ('verify_assistant_rollout_web', lambda *args: self.web),
        ):
            patcher = patch.object(guard, name, replacement, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.live_core = fixture.CORE_AFTER
        self.web = True

    def put(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = value if isinstance(value, bytes) else guard.encoded(value)
        path.write_bytes(raw)
        return sha(raw)

    def read_file(self, path, *, expected=None, **kwargs):
        path = Path(path)
        raw = path.read_bytes()
        guard.must(not path.is_symlink() and expected in (None, sha(raw)), 'fixture_file_identity')
        return raw

    def release(self, commit, source):
        files = [{'path': path, 'sha256': sha(raw), 'bytes': len(raw)} for path, raw in sorted(source.items())]
        tree = sha(guard.encoded(files).rstrip(b'\n'))
        folder = self.store / 'releases' / (tree + '-' + commit)
        for path, raw in source.items():
            self.put(folder / 'source' / path, raw)
        manifest_sha = self.put(folder / 'candidate.json', {'evidence': commit})
        archive_sha = self.put(folder / 'ai-source.zip', b'archive:' + commit.encode())
        record = {'schema': 'R25_AI_ACCEPTED_SOURCE_V2', 'repository': 'bailangguihun/auralink-ai-service',
                  'policy_sha256': self.ai_policy_sha, 'github_run_id': '18261', 'github_run_attempt': '1',
                  'files': files, 'dependency_sha256': self.dependencies, 'commit_sha': commit,
                  'source_tree_sha256': tree, 'candidate_manifest_sha256': manifest_sha, 'archive_sha256': archive_sha}
        return {'commit_sha': commit, 'source_tree_sha256': tree,
                'record_sha256': self.put(folder / 'record.json', record)}, record

    def current(self, current, active):
        self.put(self.store / 'CURRENT.json', current)
        self.put(self.store / 'ACTIVE.json', active)

    def install(self, source):
        for path, raw in source.items():
            self.put(self.root / 'release' / path, raw)

    def transition(self, phase, *, current=None):
        self.current(current or self.before_current, fixture.AFTER_ACTIVE)
        path = self.store / 'transactions' / fixture.AFTER_TX
        snapshot = {'schema': 'R25_AI_SNAPSHOT_V1', 'transaction_id': fixture.AFTER_TX,
                    'current': self.old, 'candidate': self.new, 'core': fixture.CORE_AFTER,
                    'before_current': self.before_current, 'changed_roles': ['critic'], 'created_directories': [],
                    'source_owner': [0, 0], 'files': {item['path']: {'before': old, 'after': item}
                                   for old, item in zip(self.old_record['files'], self.new_record['files'])}}
        self.put(path / 'journal.json', {'schema': 'R25_AI_TRANSACTION_V1', 'transaction_id': fixture.AFTER_TX,
                 'current': self.old, 'candidate': self.new, 'phase': phase,
                 'snapshot_sha256': self.put(path / 'snapshot.json', snapshot)})

    def verify(self, **kwargs):
        return guard.verify_assistant_rollout_ai(self.policy, **kwargs)

    def test_live_old_source_can_start_without_a_newly_staged_core(self):
        self.live_core = None
        self.assertEqual(self.verify(), self.old)
        self.put(self.root / 'release/critic-service/app/main.py', b'unknown source')
        with self.assertRaises(guard.Refused):
            self.verify()

    def test_prepare_requires_exact_staged_ai_provenance_and_committed_predecessors(self):
        self.assertEqual(self.verify(candidate=self.new), self.old)
        self.web = False
        with self.assertRaises(guard.Refused):
            self.verify(candidate=self.new)
        self.web = True
        self.live_core = fixture.CORE_BEFORE
        with self.assertRaises(guard.Refused):
            self.verify(candidate=self.new)
        self.live_core = fixture.CORE_AFTER
        self.approval['assistant_rollout']['ai_candidate']['github_run_attempt'] = '2'
        with self.assertRaises(guard.Refused):
            self.verify(candidate=self.new)

    def test_even_approved_v2_provenance_cannot_change_guide(self):
        candidate, record = self.release('3' * 40, self.new_source | {'guide_service/app.py': b'# change\n'})
        self.approval['assistant_rollout']['ai_candidate'] = {key: record[key] for key in binding.AI_PROVENANCE}
        with self.assertRaisesRegex(guard.Refused, 'illustration_critic_source_only'):
            self.verify(candidate=candidate)

    def test_candidate_start_checks_live_source_and_unmodified_configuration(self):
        self.transition('START_INTENT')
        self.install(self.new_source)
        self.assertEqual(self.verify(), self.new)
        self.put(self.configuration, {'scope': 'drift'})
        with self.assertRaises(guard.Refused):
            self.verify()

    def test_partial_install_permits_recovery_but_refuses_start_and_foreign_source(self):
        self.transition('INSTALL_INTENT')
        self.install(self.new_source)
        self.assertIsNone(self.verify(for_start=False))
        with self.assertRaises(RuntimeError):
            self.verify()
        self.put(self.root / 'release/critic-service/app/main.py', b'unknown source')
        with self.assertRaises(guard.Refused):
            self.verify(for_start=False)

    def bind_ai_guard(self):
        from contextlib import ExitStack
        stack = ExitStack()
        self.addCleanup(stack.close)
        core_guard = self.root / 'operations/core_release_guard.py'
        core_guard_sha = self.put(core_guard, b'from core_release_guard import verify_assistant_rollout_ai\n')
        policy_sha = self.put(self.core / 'POLICY.json', self.policy)
        handoff = {
            'R25_AI_RELEASE_POLICY_SHA256': self.ai_policy_sha, 'R25_CORE_RELEASE_POLICY_SHA256': policy_sha,
            'R25_CORE_RELEASE_GUARD': str(core_guard), 'R25_CORE_RELEASE_GUARD_SHA256': core_guard_sha,
        }
        self.put(self.root / 'runtime/handoff-control.env', ''.join(f'{key}={value}\n' for key, value in handoff.items()).encode())
        for key, value in (('ROOT', self.root), ('STORE', self.store), ('read_file', self.read_file),
                           ('private_record', guard.private_record)):
            stack.enter_context(patch.object(ai, key, value))
        return core_guard

    def test_direct_critic_identity_cannot_bypass_rollout_guard(self):
        core_guard = self.bind_ai_guard()
        self.transition('START_INTENT')
        self.install(self.new_source)
        self.assertEqual(ai.validate_critic_identity(self.root, 'main.py'), sha(self.new_source['critic-service/app/main.py']))
        self.web = False
        with self.assertRaises(guard.Refused):
            ai.validate_critic_identity(self.root, 'main.py')
        self.web = True
        self.put(core_guard, b'foreign guard')
        with self.assertRaises(guard.Refused):
            ai.validate_critic_identity(self.root, 'main.py')

    def test_successor_critic_start_keeps_original_ai_receipt_across_new_core(self):
        self.policy['upgrade']['core_followup']['schema'] = guard.ASSISTANT_ILLUSTRATION_SUCCESSOR_SCHEMA
        self.bind_ai_guard()
        self.transition('COMMITTED', current=self.before_current | {
            'release': self.new, 'previous': self.old, 'transaction_id': fixture.AFTER_TX,
        })
        self.install(self.new_source)
        path = self.store/'transactions'/fixture.AFTER_TX
        self.approval['current_release'] = fixture.CORE_AFTER
        self.approval['current_ai_release'] = self.new
        self.approval['assistant_rollout'].update({
            f'ai_{name.lower()}_sha256': sha((self.store/(name + '.json')).read_bytes())
            for name in ('ACTIVE', 'CURRENT')
        })
        self.approval['successor'] = {f'ai_{name}_sha256': sha((path/(name + '.json')).read_bytes())
                                      for name in ('journal', 'snapshot')}
        core = fixture.CORE_AFTER | {'commit_sha': '6'*40}
        tx = '7'*32
        self.put(self.core/'CURRENT.json', {'transaction_id': tx, 'release': core})
        self.put(self.core/'transactions'/tx/'journal.json', {
            'phase': 'COMMITTED', 'candidate': core, 'history': [{'phase': 'COMMITTED', 'at_unix': 100}],
        })
        with patch.object(guard, 'verify_assistant_rollout_core', return_value=core), \
             patch.object(guard, 'verify_assistant_rollout_successor_web') as web:
            self.assertEqual(ai.validate_critic_identity(self.root, 'main.py'), sha(self.new_source['critic-service/app/main.py']))
            self.assertEqual(web.call_args.args[2], 100)
            with self.assertRaisesRegex(guard.Refused, 'ai_is_frozen'):
                self.verify(candidate=self.old)
            self.put(path/'snapshot.json', {'foreign': True})
            with self.assertRaisesRegex(guard.Refused, 'fixture_file_identity'):
                ai.validate_critic_identity(self.root, 'main.py')

    def test_managed_publication_and_rollback_restore_exact_critic_source(self):
        self.bind_ai_guard()
        self.put(self.root / 'runtime/config/internal-token', b'a' * 72)
        self.events, self.commands = [], []
        self.busy, self.foreign, self.hook_enabled = False, None, False
        self.watchdog_running = True
        self.processes = {name: {'pid': idx, 'ticks': idx + 10}
                          for idx, name in enumerate(('guide', 'critic', 'java'), 10)}
        runtime = ai_runtime.AIRuntime.__new__(ai_runtime.AIRuntime)
        runtime.core = legacy.Core(self)
        runtime.core.release = Release(**fixture.CORE_AFTER)
        runtime.storage = legacy.Files()
        with patch.object(ai_runtime, 'ROOT', self.root), patch.object(ai_runtime, 'STORE', self.store), \
                patch.object(guard, 'policy', return_value=self.policy), \
                patch.object(ai_runtime, 'get_json', lambda *args, **kwargs: legacy.AdapterTests.http(self, *args, **kwargs)), \
                patch.object(ai_runtime, 'verify_http', return_value={'gallery_total': 40}):
            self.web = False
            with self.assertRaises(guard.Refused):
                ai_runtime.publish(runtime, self.new)
            self.assertEqual(self.events, ['lease:acquire', 'lease:release'])
            self.web = True
            result = ai_runtime.publish(runtime, self.new)
            self.assertEqual(result['outcome'], 'AI_SOURCE_PUBLISHED')
            self.assertEqual(result['changed_roles'], ['critic'])
            self.assertEqual(self.verify(), self.new)
            with runtime.core.lease():
                ai_runtime.recover_locked(runtime, result['transaction_id'])
            self.assertEqual(self.verify(), self.old)
            self.assertNotIn('stop:guide', self.events)


class IllustrationApprovalTest(unittest.TestCase):
    def test_rollout_policy_refuses_missing_vmm_controller_binding(self):
        value = {'schema': guard.BUSINESS_POLICY_SCHEMA, 'upgrade': {'core_followup': {
                     'schema': guard.ASSISTANT_ILLUSTRATION_CORE_SCHEMA}}}

        class Validator:
            def validate_policy(self, observed):
                if observed != value:
                    raise AssertionError('unexpected policy')

        with patch.object(guard, 'handoff_values', return_value={'R25_CORE_RELEASE_POLICY_SHA256': '1' * 64}), \
                patch.object(guard, 'private_record', return_value=value), \
                patch.object(guard, 'business_module', return_value=Validator()), \
                patch.object(guard, 'validate_configuration_context', return_value=None), \
                self.assertRaises(guard.Refused):
            guard.policy()

    def test_private_installed_approval_binds_exact_policy_receipt_and_pointer(self):
        store = Path('/fixture/core-publications')
        value = {'schema': guard.BUSINESS_POLICY_SCHEMA, 'installation': {'private_owner': [0, 0],
                 'controller_owner': [1000, 1000]}, 'upgrade': {'core_followup': {
                     'schema': guard.ASSISTANT_ILLUSTRATION_CORE_SCHEMA}}}
        review = guard.encoded({'fixture': True})
        approval = {'schema': guard.ASSISTANT_ILLUSTRATION_APPROVAL_SCHEMA,
                    'private_owner': [0, 0], 'controller_owner': [1000, 1000],
                    'review_evidence_sha256': sha(review),
                    'operational': {'policy': {'after_sha256': sha(guard.encoded(value))}}}
        digest = sha(guard.encoded(approval))
        folder = store / 'controller-upgrades' / digest
        receipt = {'schema': approval['schema'], 'approval_sha256': digest, 'phase': 'INSTALLED'}
        records = {store / 'POLICY.json': guard.encoded(value), folder / 'approval.json': guard.encoded(approval),
                   folder / 'journal.json': guard.encoded(receipt), folder / 'review.json': review,
                   store / 'CONTROL_UPGRADE_ACTIVE.json': guard.encoded({
                       'schema': 'R25_CORE_CONTROLLER_UPGRADE_ACTIVE_V1', 'approval_sha256': digest})}

        def read(path, *, expected=None, **kwargs):
            raw = records[path]
            guard.must(expected in (None, sha(raw)), 'fixture_file_identity')
            return raw

        with patch.object(guard, 'STORE', store), patch.object(guard, 'read_file', read):
            self.assertEqual(guard.load_assistant_rollout_approval(value, digest), (approval, folder, 'INSTALLED'))
            with self.assertRaises(guard.Refused):
                guard.load_assistant_rollout_approval(value, 'f' * 64)
            records[folder / 'journal.json'] = guard.encoded(receipt | {'phase': 'RESTORE_INTENT'})
            with self.assertRaises(guard.Refused):
                guard.load_assistant_rollout_approval(value)

    def test_web_commit_receipt_binds_the_live_selection_and_occurs_after_core(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp)
            selection, reference = runtime / 'selection.json', runtime / 'selection.reference'
            selection.write_bytes(b'new selection')
            reference.write_bytes(b'new reference')
            stage = runtime / 'frontend-selection-transition-1790000000000000000'
            stage.mkdir()
            commit = 'f' * 40
            state = {'state': 'COMMITTED', 'expected_commit': commit, 'stage': str(stage),
                     'selection': str(selection), 'reference': str(reference)}
            old = {}
            for name, path in (('selection', selection), ('reference', reference)):
                before = ('old ' + name).encode()
                old[name + '_sha256'] = state['old_' + name + '_sha'] = sha(before)
                (stage / ('old.' + name)).write_bytes(before)
                (stage / ('new.' + name)).write_bytes(path.read_bytes())
                state['new_' + name + '_sha'] = sha(path.read_bytes())
            (stage / 'state.json').write_bytes(guard.encoded(state))

            def read(path, *, expected=None, **kwargs):
                raw = path.read_bytes()
                guard.must(expected in (None, sha(raw)), 'fixture_file_identity')
                return raw

            with patch.object(guard, 'LOCK', runtime / 'lock'), patch.object(guard, 'read_file', read):
                self.assertEqual(guard._rollout_web_commit_receipt(selection, reference, commit=commit,
                                 before=old, core_at=1789999999), sha(selection.read_bytes()))
                with self.assertRaises(guard.Refused):
                    guard._rollout_web_commit_receipt(selection, reference, commit=commit,
                                                      before=old, core_at=1790000000)
                (stage / 'new.selection').write_bytes(b'foreign selection')
                with self.assertRaises(guard.Refused):
                    guard._rollout_web_commit_receipt(selection, reference, commit=commit,
                                                      before=old, core_at=1789999999)
