from dataclasses import asdict
import hashlib
import unittest
from unittest.mock import patch

import ai_publish
import ai_release_candidate
import ai_release_guard
import ai_runtime
import assistant_rollout_binding as binding
import core_runtime
import test_assistant_ai_runtime as ai_fixture
import test_core_publish_repository as core_stage_fixture
import test_core_runtime as core_fixture


BEFORE = {
    'commit_sha': '1' * 40,
    'source_tree_sha256': '2' * 64,
    'record_sha256': '3' * 64,
}
AFTER = {
    'commit_sha': '4' * 40,
    'source_tree_sha256': '5' * 64,
    'record_sha256': '6' * 64,
}
CORE_BEFORE = {
    'commit_sha': '7' * 40,
    'artifact_sha256': '8' * 64,
    'accepted_record_sha256': '9' * 64,
}
CORE_AFTER = {
    'commit_sha': 'a' * 40,
    'artifact_sha256': 'b' * 64,
    'accepted_record_sha256': 'c' * 64,
}
BEFORE_TX = 'd' * 32
AFTER_TX = 'e' * 32
SNAPSHOT_SHA = 'f' * 64
BEFORE_CURRENT = {
    'schema': 'R25_AI_CURRENT_V1',
    'release': BEFORE,
    'previous': dict(AFTER, commit_sha='0' * 40),
    'transaction_id': BEFORE_TX,
}
AFTER_CURRENT = {
    'schema': 'R25_AI_CURRENT_V1',
    'release': AFTER,
    'previous': BEFORE,
    'transaction_id': AFTER_TX,
}
BEFORE_ACTIVE = {'schema': 'R25_AI_ACTIVE_V1', 'transaction_id': BEFORE_TX}
AFTER_ACTIVE = {'schema': 'R25_AI_ACTIVE_V1', 'transaction_id': AFTER_TX}
CORE_BEFORE_CURRENT = {
    'schema': 'R25_CORE_CURRENT_V1',
    'release': CORE_BEFORE,
    'previous': None,
    'transaction_id': BEFORE_TX,
}
CORE_AFTER_CURRENT = {
    'schema': 'R25_CORE_CURRENT_V1',
    'release': CORE_AFTER,
    'previous': CORE_BEFORE,
    'transaction_id': AFTER_TX,
}
CORE_BEFORE_ACTIVE = {'schema': 'R25_CORE_ACTIVE_V1', 'transaction_id': BEFORE_TX}
CORE_AFTER_ACTIVE = {'schema': 'R25_CORE_ACTIVE_V1', 'transaction_id': AFTER_TX}
OLD_JARS = {'release-jar': CORE_BEFORE['artifact_sha256'], 'preview-jar': CORE_BEFORE['artifact_sha256']}
NEW_JARS = {'release-jar': CORE_AFTER['artifact_sha256'], 'preview-jar': CORE_AFTER['artifact_sha256']}
MIXED_JARS = {'release-jar': CORE_AFTER['artifact_sha256'], 'preview-jar': CORE_BEFORE['artifact_sha256']}


class RolloutBindingTest(unittest.TestCase):
    def transition(self, phase, **overrides):
        snapshot = {
            'schema': 'R25_AI_SNAPSHOT_V1',
            'transaction_id': AFTER_TX,
            'current': BEFORE,
            'candidate': AFTER,
            'core': CORE_AFTER,
            'before_current': BEFORE_CURRENT,
            'changed_roles': ['critic'],
            'created_directories': [],
        }
        journal = {
            'schema': 'R25_AI_TRANSACTION_V1',
            'transaction_id': AFTER_TX,
            'current': BEFORE,
            'candidate': AFTER,
            'phase': phase,
            'snapshot_sha256': SNAPSHOT_SHA,
        }
        evidence = {
            'before_current': BEFORE_CURRENT,
            'before_active': BEFORE_ACTIVE,
            'candidate': AFTER,
            'core_before': CORE_BEFORE,
            'core_after': CORE_AFTER,
            'core_current': CORE_AFTER,
            'current': BEFORE_CURRENT,
            'active': AFTER_ACTIVE,
            'journal': journal,
            'snapshot': snapshot,
            'snapshot_sha256': SNAPSHOT_SHA,
            'web_approved': True,
        }
        return binding.select_ai_reference(**(evidence | overrides))

    def test_candidate_requires_independently_approved_provenance(self):
        approval = {
            'commit_sha': AFTER['commit_sha'],
            'github_run_id': '123456',
            'github_run_attempt': '2',
            'source_tree_sha256': AFTER['source_tree_sha256'],
            'candidate_manifest_sha256': 'a' * 64,
            'archive_sha256': 'b' * 64,
            'policy_sha256': 'c' * 64,
        }
        record = approval | {
            'schema': 'R25_AI_ACCEPTED_SOURCE_V2',
            'repository': 'bailangguihun/auralink-ai-service',
        }
        self.assertEqual(
            binding.bind_ai_candidate(approval, record, record_sha256=AFTER['record_sha256']),
            AFTER,
        )
        for key, value in (
            ('github_run_id', '123457'),
            ('github_run_attempt', '3'),
            ('source_tree_sha256', 'd' * 64),
            ('candidate_manifest_sha256', 'e' * 64),
            ('archive_sha256', 'f' * 64),
            ('policy_sha256', 'd' * 64),
            ('schema', 'R25_AI_ACCEPTED_SOURCE_V1'),
            ('repository', 'other/auralink-ai-service'),
        ):
            with self.subTest(key=key), self.assertRaises(binding.BindingRefused):
                binding.bind_ai_candidate(
                    approval, record | {key: value}, record_sha256=AFTER['record_sha256'],
                )
        with self.assertRaises(binding.BindingRefused):
            binding.bind_ai_candidate(
                approval | {'record_sha256': '0' * 64},
                record,
                record_sha256=AFTER['record_sha256'],
            )

    def test_stable_predecessor_survives_core_and_web_release_before_ai(self):
        journal = {
            'schema': 'R25_AI_TRANSACTION_V1',
            'transaction_id': BEFORE_TX,
            'phase': 'COMMITTED',
            'candidate': BEFORE,
        }
        for core in (CORE_BEFORE, CORE_AFTER):
            with self.subTest(core=core['commit_sha']):
                self.assertEqual(
                    self.transition(
                        'COMMITTED', candidate=None, active=BEFORE_ACTIVE, journal=journal,
                        snapshot=None, snapshot_sha256=None, core_current=core, web_approved=False,
                    ),
                    BEFORE,
                )
        with self.assertRaises(binding.BindingRefused):
            self.transition(
                'COMMITTED', active=BEFORE_ACTIVE, journal=journal,
                current=BEFORE_CURRENT | {'previous': AFTER},
            )

    def test_unchanged_ai_can_start_before_core_candidate_is_staged(self):
        journal = {
            'schema': 'R25_AI_TRANSACTION_V1',
            'transaction_id': BEFORE_TX,
            'phase': 'COMMITTED',
            'candidate': BEFORE,
        }
        self.assertEqual(self.transition(
            'COMMITTED', candidate=None, active=BEFORE_ACTIVE, journal=journal,
            snapshot=None, snapshot_sha256=None, core_after=None, core_current=None,
            web_approved=False,
        ), BEFORE)

    def test_restored_ai_can_start_while_core_recovers(self):
        self.assertEqual(self.transition('RESTORED', core_current=None, web_approved=False), BEFORE)

    def test_core_candidate_binds_manifest_payload_and_approval(self):
        approval = {
            'repository': 'bailangguihun/auralink-core-api',
            'commit_sha': CORE_AFTER['commit_sha'],
            'github_run_id': '123450',
            'github_run_attempt': '1',
            'artifact_sha256': CORE_AFTER['artifact_sha256'],
            'artifact_bytes': 1000000,
            'payload_fingerprint': 'd' * 64,
            'candidate_manifest_sha256': 'e' * 64,
            'policy_sha256': 'f' * 64,
        }
        record = approval | {'schema': 'R25_CORE_BUSINESS_RELEASE_V2'}
        self.assertEqual(
            binding.bind_core_candidate(
                approval, record, record_sha256=CORE_AFTER['accepted_record_sha256'],
            ),
            CORE_AFTER,
        )
        for key, value in (
            ('artifact_bytes', 1000001),
            ('payload_fingerprint', '0' * 64),
            ('candidate_manifest_sha256', '0' * 64),
            ('policy_sha256', '0' * 64),
            ('schema', 'R25_CORE_ACCEPTED_RELEASE_V1'),
        ):
            with self.subTest(key=key), self.assertRaises(binding.BindingRefused):
                binding.bind_core_candidate(
                    approval, record | {key: value},
                    record_sha256=CORE_AFTER['accepted_record_sha256'],
                )

    def test_new_source_starts_only_after_core_and_web(self):
        self.assertEqual(self.transition('START_INTENT'), AFTER)
        for overrides in (
            {'core_current': CORE_BEFORE},
            {'core_current': CORE_AFTER | {'accepted_record_sha256': '0' * 64}},
            {'web_approved': False},
            {'web_approved': 1},
            {'candidate': None},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(binding.BindingRefused):
                self.transition('START_INTENT', **overrides)

    def test_only_complete_source_side_is_eligible_to_start(self):
        for phase, current, expected in (
            ('PREPARED', BEFORE_CURRENT, BEFORE),
            ('STOPPED', BEFORE_CURRENT, BEFORE),
            ('ACCEPTED', BEFORE_CURRENT, AFTER),
            ('CURRENT_COMMIT_INTENT', BEFORE_CURRENT, AFTER),
            ('CURRENT_COMMIT_INTENT', AFTER_CURRENT, AFTER),
            ('COMMITTED', AFTER_CURRENT, AFTER),
            ('RESTORE_START_INTENT', AFTER_CURRENT, BEFORE),
            ('RESTORED', BEFORE_CURRENT, BEFORE),
        ):
            with self.subTest(phase=phase, current=current):
                self.assertEqual(self.transition(phase, current=current), expected)
        for phase, current in (
            ('STOPPED', AFTER_CURRENT),
            ('START_INTENT', AFTER_CURRENT),
            ('COMMITTED', BEFORE_CURRENT),
            ('RESTORED', AFTER_CURRENT),
        ):
            with self.subTest(phase=phase), self.assertRaises(binding.BindingRefused):
                self.transition(phase, current=current)

    def test_mixed_source_is_inspectable_for_recovery_but_may_not_start(self):
        for phase, current in (
            ('INSTALL_INTENT', BEFORE_CURRENT),
            ('INSTALLED', BEFORE_CURRENT),
            ('RECOVERY_REQUIRED', AFTER_CURRENT),
            ('RESTORE_STOP_INTENT', AFTER_CURRENT),
            ('RESTORE_STOPPED', AFTER_CURRENT),
            ('RESTORE_INTENT', AFTER_CURRENT),
        ):
            with self.subTest(phase=phase):
                with self.assertRaises(binding.BindingRefused):
                    self.transition(phase, current=current)
                self.assertIsNone(self.transition(phase, current=current, for_start=False))
        with self.assertRaises(binding.BindingRefused):
            self.transition('UNRECOGNIZED', for_start=False)

    def test_restored_critic_survives_later_web_and_core_rollback(self):
        self.assertEqual(
            self.transition('RESTORED', core_current=CORE_BEFORE, web_approved=False),
            BEFORE,
        )

    def test_transaction_rejects_unbound_snapshot_or_source_configuration_change(self):
        with self.assertRaises(binding.BindingRefused):
            self.transition('START_INTENT', snapshot_sha256='0' * 64)
        with self.assertRaises(binding.BindingRefused):
            self.transition('START_INTENT', active=AFTER_ACTIVE | {'transaction_id': '0' * 32})
        with self.assertRaises(binding.BindingRefused):
            self.transition('START_INTENT', current=AFTER_CURRENT | {'transaction_id': '0' * 32})
        for key, value in (
            ('candidate', BEFORE),
            ('core', CORE_BEFORE),
            ('before_current', AFTER_CURRENT),
            ('changed_roles', ['critic', 'guide']),
            ('created_directories', ['critic-service/app/assistant']),
            ('configuration_transition', None),
        ):
            with self.subTest(key=key), self.assertRaises(binding.BindingRefused):
                normal = {
                    'schema': 'R25_AI_SNAPSHOT_V1',
                    'transaction_id': AFTER_TX,
                    'current': BEFORE,
                    'candidate': AFTER,
                    'core': CORE_AFTER,
                    'before_current': BEFORE_CURRENT,
                    'changed_roles': ['critic'],
                    'created_directories': [],
                }
                self.transition('START_INTENT', snapshot=normal | {key: value})


class CorePhaseBindingTest(unittest.TestCase):
    def transition(self, phase, **overrides):
        candidate_jars = {
            'sha256': CORE_BEFORE['artifact_sha256'],
            'after_sha256': CORE_AFTER['artifact_sha256'],
        }
        snapshot = {
            'schema': 'R25_CORE_SNAPSHOT_V1',
            'transaction_id': AFTER_TX,
            'current': CORE_BEFORE,
            'candidate': CORE_AFTER,
            'before_current': CORE_BEFORE_CURRENT,
            'files': {'release-jar': candidate_jars, 'preview-jar': candidate_jars},
        }
        journal = {
            'schema': 'R25_CORE_TRANSACTION_V1',
            'transaction_id': AFTER_TX,
            'snapshot_id': AFTER_TX,
            'current': CORE_BEFORE,
            'candidate': CORE_AFTER,
            'phase': phase,
            'snapshot_sha256': SNAPSHOT_SHA,
        }
        evidence = {
            'before_current': CORE_BEFORE_CURRENT,
            'before_active': CORE_BEFORE_ACTIVE,
            'candidate': CORE_AFTER,
            'current': CORE_BEFORE_CURRENT,
            'active': CORE_AFTER_ACTIVE,
            'journal': journal,
            'jar_sha256': OLD_JARS,
            'snapshot': snapshot,
            'snapshot_sha256': SNAPSHOT_SHA,
        }
        return binding.select_core_reference(**(evidence | overrides))

    def test_normal_runtime_requires_terminal_proof_and_both_matching_jars(self):
        predecessor = {
            'schema': 'R25_CORE_TRANSACTION_V1',
            'transaction_id': BEFORE_TX,
            'snapshot_id': BEFORE_TX,
            'phase': 'COMMITTED',
            'candidate': CORE_BEFORE,
        }
        self.assertEqual(
            self.transition('COMMITTED', active=CORE_BEFORE_ACTIVE, journal=predecessor,
                            snapshot=None, snapshot_sha256=None, candidate=None),
            CORE_BEFORE,
        )
        self.assertEqual(
            self.transition('COMMITTED', current=CORE_AFTER_CURRENT, jar_sha256=NEW_JARS),
            CORE_AFTER,
        )
        self.assertEqual(self.transition('RESTORED'), CORE_BEFORE)
        for phase, jars, current in (
            ('START_INTENT', NEW_JARS, CORE_BEFORE_CURRENT),
            ('RESTORE_INTENT', OLD_JARS, CORE_AFTER_CURRENT),
            ('COMMITTED', MIXED_JARS, CORE_AFTER_CURRENT),
            ('RESTORED', NEW_JARS, CORE_BEFORE_CURRENT),
        ):
            with self.subTest(phase=phase), self.assertRaises(binding.BindingRefused):
                self.transition(phase, jar_sha256=jars, current=current)

    def test_preserve_selects_verified_bytes_before_current_cas(self):
        for phase, current, jars, expected in (
            ('STOP_INTENT', CORE_BEFORE_CURRENT, OLD_JARS, CORE_BEFORE),
            ('START_INTENT', CORE_BEFORE_CURRENT, NEW_JARS, CORE_AFTER),
            ('ACCEPTED', CORE_BEFORE_CURRENT, NEW_JARS, CORE_AFTER),
            ('CURRENT_COMMIT_INTENT', CORE_AFTER_CURRENT, NEW_JARS, CORE_AFTER),
            ('RESTORE_INTENT', CORE_AFTER_CURRENT, OLD_JARS, CORE_BEFORE),
        ):
            with self.subTest(phase=phase, current=current):
                self.assertEqual(
                    self.transition(phase, current=current, jar_sha256=jars, preserve_only=True),
                    expected,
                )

    def test_preserve_can_inspect_known_partial_jars_without_authorizing_a_release(self):
        for phase in ('INSTALL_INTENT', 'RECOVERY_REQUIRED', 'RESTORE_STOP_INTENT',
                      'RESTORE_STOPPED', 'RESTORE_INTENT'):
            with self.subTest(phase=phase):
                self.assertIsNone(self.transition(phase, jar_sha256=MIXED_JARS, preserve_only=True))
        for phase, jars in (
            ('UNKNOWN', OLD_JARS),
            ('START_INTENT', MIXED_JARS),
            ('INSTALL_INTENT', MIXED_JARS | {'preview-jar': '0' * 64}),
            ('RESTORE_INTENT', {'release-jar': CORE_BEFORE['artifact_sha256']}),
        ):
            with self.subTest(phase=phase), self.assertRaises(binding.BindingRefused):
                self.transition(phase, jar_sha256=jars, preserve_only=True)

    def test_preserve_requires_exact_transaction_and_snapshot(self):
        for overrides in (
            {'active': CORE_AFTER_ACTIVE | {'transaction_id': '0' * 32}},
            {'current': CORE_BEFORE_CURRENT | {'previous': CORE_AFTER}},
            {'snapshot_sha256': '0' * 64},
            {'candidate': CORE_AFTER | {'accepted_record_sha256': '0' * 64}},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(binding.BindingRefused):
                self.transition('RESTORE_INTENT', preserve_only=True, **overrides)


class EmittedRecordBindingTest(unittest.TestCase):
    def test_core_stage_record_retains_approved_manifest_and_policy_digests(self):
        fixture = core_stage_fixture.CoreStageRepositoryTests('runTest')
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        release = fixture.stage('bailangguihun/auralink-core-api')
        approval = {
            **fixture.identity,
            'candidate_manifest_sha256': hashlib.sha256(fixture.manifest.read_bytes()).hexdigest(),
            'policy_sha256': hashlib.sha256((fixture.store / 'POLICY.json').read_bytes()).hexdigest(),
        }
        self.assertEqual(
            binding.bind_core_candidate(
                approval, fixture.record(release), record_sha256=release.accepted_record_sha256,
            ),
            asdict(release),
        )

    def test_core_phase_selection_uses_emitted_current_active_and_snapshot(self):
        fixture = core_fixture.RuntimeMethodTests('runTest')
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        rt = fixture.runtime
        first, _ = fixture.prepared()
        rt.install_candidate(fixture.new, first)
        rt.commit_current(fixture.new, fixture.old)
        rt.record(first, 'COMMITTED')
        before = rt.read(fixture.store / 'CURRENT.json')
        before_active = rt.read(fixture.store / 'ACTIVE.json')

        with patch.object(core_runtime.secrets, 'token_hex', return_value='2' * 32):
            second = rt.snapshot(fixture.new, fixture.old)
        rt.begin(fixture.new, fixture.old, second)
        rt.install_candidate(fixture.old, second)
        rt.record(second, 'START_INTENT')
        folder = rt.transaction_folder(second)
        observed = {
            'before_current': before,
            'before_active': before_active,
            'candidate': asdict(fixture.old),
            'current': rt.read(fixture.store / 'CURRENT.json'),
            'active': rt.read(fixture.store / 'ACTIVE.json'),
            'journal': rt.read(folder / 'journal.json'),
            'snapshot': rt.read(folder / 'snapshot.json'),
            'snapshot_sha256': rt.identity(folder / 'snapshot.json')['sha256'],
            'jar_sha256': {
                name: rt.identity(fixture.live[name])['sha256']
                for name in ('release-jar', 'preview-jar')
            },
        }
        with self.assertRaises(binding.BindingRefused):
            binding.select_core_reference(**observed)
        self.assertEqual(binding.select_core_reference(**observed, preserve_only=True), asdict(fixture.old))

    def test_ai_source_only_rollout_consumes_emitted_v2_stage_and_transaction(self):
        transition = ai_fixture.AssistantSourceTransition('runTest')
        self.addCleanup(transition.doCleanups)
        transition.setUp()
        fixture = transition.f
        rt = fixture.rt
        self.assertEqual(ai_runtime.publish(rt, transition.after)['outcome'], 'AI_SOURCE_PUBLISHED')
        before = rt.read(fixture.store / 'CURRENT.json')
        before_active = rt.read(fixture.store / 'ACTIVE.json')
        source = {
            name: (fixture.root / 'release' / name).read_bytes()
            for name in ai_release_candidate.ASSISTANT_PATHS
        }
        source['critic-service/app/assistant/execution.py'] += b'# reviewed rollout fixture\n'
        commit, run_id, attempt = 'a' * 40, '24680', '3'
        schema = ai_release_candidate.ASSISTANT_SCHEMA
        provenance = ai_release_candidate.provenance(
            ai_release_candidate.CURRENT_REPOSITORY, ai_release_candidate.REF, commit, run_id, attempt, schema,
        )
        manifest, archive = ai_release_candidate.build(source, provenance, schema)
        raw_manifest = ai_release_candidate.canonical(manifest)
        incoming = ai_publish.incoming_path(commit, run_id, attempt)
        incoming.mkdir()
        (incoming / ai_release_candidate.MANIFEST).write_bytes(raw_manifest)
        (incoming / ai_release_candidate.ARCHIVE).write_bytes(archive)
        staged = ai_publish.stage(rt, commit, run_id, attempt)
        record = rt.read(ai_release_guard.release_folder(staged) / 'record.json')
        approval = {
            'commit_sha': commit,
            'github_run_id': run_id,
            'github_run_attempt': attempt,
            'source_tree_sha256': manifest['source_tree_sha256'],
            'candidate_manifest_sha256': hashlib.sha256(raw_manifest).hexdigest(),
            'archive_sha256': hashlib.sha256(archive).hexdigest(),
            'policy_sha256': rt.identity(fixture.store / 'POLICY.json')['sha256'],
        }
        candidate = binding.bind_ai_candidate(approval, record, record_sha256=staged['record_sha256'])
        self.assertEqual(candidate, staged)

        result = ai_runtime.publish(rt, candidate)
        self.assertEqual(result['outcome'], 'AI_SOURCE_PUBLISHED')
        folder = rt.transaction(result['transaction_id'])
        paired_core = asdict(rt.core.current())
        self.assertEqual(
            binding.select_ai_reference(
                before_current=before, before_active=before_active, candidate=candidate,
                core_before=CORE_BEFORE, core_after=paired_core, core_current=paired_core,
                current=rt.read(fixture.store / 'CURRENT.json'), active=rt.read(fixture.store / 'ACTIVE.json'),
                journal=rt.read(folder / 'journal.json'), snapshot=rt.read(folder / 'snapshot.json'),
                snapshot_sha256=rt.identity(folder / 'snapshot.json')['sha256'], web_approved=True,
            ),
            candidate,
        )


if __name__ == '__main__':
    unittest.main()
