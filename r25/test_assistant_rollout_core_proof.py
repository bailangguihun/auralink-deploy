from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

import assistant_rollout_binding as binding
from r25_storage import R25Storage


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux descriptor verification')
class AssistantRolloutCoreProofTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='r25-rollout-core-proof-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = self.directory('core-publications')
        self.preview = self.directory('preview')
        release = self.directory('release')
        self.jars = {'release-jar': release / 'backend.jar', 'preview-jar': self.preview / 'backend.jar'}
        self.storage = R25Storage(
            (self.root.as_posix(),), os.geteuid(), os.getegid(), private_roots=(self.store.as_posix(),),
        )
        self.old_jar, self.new_jar = b'previous reviewed Core JAR', b'new reviewed Core JAR'
        self.previous = {
            'commit_sha': '1' * 40,
            'artifact_sha256': sha256(self.old_jar).hexdigest(),
            'accepted_record_sha256': '2' * 64,
        }
        self.old_tx, self.new_tx = 'a' * 32, 'b' * 32
        self.before_current = {
            'schema': 'R25_CORE_CURRENT_V1', 'release': self.previous,
            'previous': None, 'transaction_id': self.old_tx,
        }
        self.before_active = {'schema': 'R25_CORE_ACTIVE_V1', 'transaction_id': self.old_tx}
        self.candidate = {
            'repository': 'bailangguihun/auralink-core-api',
            'commit_sha': '3' * 40, 'github_run_id': '1234', 'github_run_attempt': '1',
            'artifact_sha256': sha256(self.new_jar).hexdigest(), 'artifact_bytes': len(self.new_jar),
            'payload_fingerprint': '4' * 64,
        }
        self.policy = {
            'schema': 'R25_CORE_BUSINESS_POLICY_V2',
            'upgrade': {
                'candidate': self.candidate,
                'baseline_release': self.previous,
                'core_followup': {'schema': 'R25_ASSISTANT_ILLUSTRATION_CORE_PUBLICATION_V1'},
            },
        }
        self.policy_sha = self.json(self.store / 'POLICY.json', self.policy)
        self.approval = {
            'schema': 'R25_ASSISTANT_ILLUSTRATION_CORE_PUBLICATION_BINDING_V1',
            'current_release': self.previous,
            'operational': {'policy': {'after_sha256': self.policy_sha}},
            'assistant_rollout': {
                'core_current_sha256': sha256(self.encode(self.before_current)).hexdigest(),
                'core_active_sha256': sha256(self.encode(self.before_active)).hexdigest(),
                'core_candidate_manifest_sha256': '5' * 64,
            },
        }
        self.folder = self.directory(
            'core-publications/controller-upgrades/' + sha256(self.encode(self.approval)).hexdigest(),
        )
        before = self.directory(self.folder.relative_to(self.root) / 'before')
        self.json(self.folder / 'approval.json', self.approval)
        self.saved_current = before / 'ILLUSTRATION_CORE_CURRENT.json'
        self.saved_active = before / 'ILLUSTRATION_CORE_ACTIVE.json'
        self.json(self.saved_current, self.before_current)
        self.json(self.saved_active, self.before_active)
        self.old_transaction = self.directory('core-publications/transactions/' + self.old_tx)
        self.new_transaction = self.directory('core-publications/transactions/' + self.new_tx)
        self.new_record = self.directory(
            'core-publications/releases/' + self.candidate['artifact_sha256'] + '-' + self.candidate['commit_sha'],
        ) / 'record.json'
        self.record = self.candidate | {
            'schema': 'R25_CORE_BUSINESS_RELEASE_V2',
            'candidate_manifest_sha256': '5' * 64,
            'policy_sha256': self.policy_sha,
        }
        self.set_successor()

    @staticmethod
    def encode(value):
        return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()

    def directory(self, path):
        path = self.root / path
        for parent in reversed((path, *path.parents)):
            if parent.is_relative_to(self.root) and not parent.exists():
                parent.mkdir(mode=0o700)
        return path

    @staticmethod
    def write(path, raw):
        path.write_bytes(raw)
        path.chmod(0o600)
        return sha256(raw).hexdigest()

    def json(self, path, value):
        return self.write(path, self.encode(value))

    def set_successor(self, *, phase='COMMITTED', record=None):
        digest = self.json(self.new_record, self.record if record is None else record)
        self.successor = {
            'commit_sha': self.candidate['commit_sha'], 'artifact_sha256': self.candidate['artifact_sha256'],
            'accepted_record_sha256': digest,
        }
        self.new_current = {
            'schema': 'R25_CORE_CURRENT_V1', 'release': self.successor,
            'previous': self.previous, 'transaction_id': self.new_tx,
        }
        current = self.new_current if phase == 'COMMITTED' else self.before_current
        self.json(self.store / 'CURRENT.json', current)
        self.json(self.store / 'ACTIVE.json', {'schema': 'R25_CORE_ACTIVE_V1', 'transaction_id': self.new_tx})
        snapshot = {
            'schema': 'R25_CORE_SNAPSHOT_V1', 'transaction_id': self.new_tx,
            'current': self.previous, 'candidate': self.successor, 'before_current': self.before_current,
            'files': {
                name: {'path': str(path), 'sha256': self.previous['artifact_sha256'],
                       'after_sha256': self.successor['artifact_sha256']}
                for name, path in self.jars.items()
            },
        }
        self.snapshot_path = self.new_transaction / 'snapshot.json'
        snapshot_sha = self.json(self.snapshot_path, snapshot)
        self.json(self.new_transaction / 'journal.json', {
            'schema': 'R25_CORE_TRANSACTION_V1', 'transaction_id': self.new_tx, 'snapshot_id': self.new_tx,
            'current': self.previous, 'candidate': self.successor, 'phase': phase, 'snapshot_sha256': snapshot_sha,
        })
        for path in self.jars.values():
            self.write(path, self.new_jar)

    def verify(self, *, preserve_only=False):
        return binding.verify_assistant_rollout_core(
            self.policy, self.approval, self.folder,
            preserve_only=preserve_only, storage=self.storage, _layout=(self.store, self.jars),
        )

    def test_committed_successor_requires_both_live_jars_and_bound_pointers(self):
        self.assertEqual(self.verify(), self.successor)
        self.write(self.jars['preview-jar'], self.old_jar)
        with self.assertRaises(binding.BindingRefused):
            self.verify()

    def test_stable_baseline_uses_approved_current_and_active_preimages(self):
        self.json(self.store / 'CURRENT.json', self.before_current)
        self.json(self.store / 'ACTIVE.json', self.before_active)
        self.json(self.old_transaction / 'journal.json', {
            'schema': 'R25_CORE_TRANSACTION_V1', 'transaction_id': self.old_tx, 'snapshot_id': self.old_tx,
            'phase': 'COMMITTED', 'candidate': self.previous,
        })
        self.new_record.unlink()
        for path in self.jars.values():
            self.write(path, self.old_jar)
        self.assertEqual(self.verify(), self.previous)

    def test_rewritten_but_consistent_canonical_records_cannot_change_approved_provenance(self):
        for key in ('candidate_manifest_sha256', 'policy_sha256', 'payload_fingerprint', 'github_run_id'):
            with self.subTest(key=key):
                self.set_successor(record=self.record | {key: '9' * 64 if key != 'github_run_id' else '9876'})
                with self.assertRaises(binding.BindingRefused):
                    self.verify()

    def test_saved_current_active_and_snapshot_are_bound_to_original_bytes(self):
        for path in (self.saved_current, self.saved_active, self.snapshot_path, self.store / 'POLICY.json'):
            raw = path.read_bytes()
            with self.subTest(path=path.name):
                self.write(path, raw + b'\n')
                with self.assertRaises(binding.BindingRefused):
                    self.verify()
                self.write(path, raw)

    def test_approval_must_supply_a_digest_for_every_historical_preimage(self):
        before = self.folder / 'before'
        for key in ('core_current_sha256', 'core_active_sha256', 'core_candidate_manifest_sha256'):
            with self.subTest(key=key):
                approval = self.approval | {'assistant_rollout': self.approval['assistant_rollout'] | {key: None}}
                folder = self.directory(
                    'core-publications/controller-upgrades/' + sha256(self.encode(approval)).hexdigest(),
                )
                self.json(folder / 'approval.json', approval)
                target = self.directory(folder.relative_to(self.root) / 'before')
                for path in before.iterdir():
                    self.write(target / path.name, path.read_bytes())
                with self.assertRaises(binding.BindingRefused):
                    binding.verify_assistant_rollout_core(
                        self.policy, approval, folder, storage=self.storage, _layout=(self.store, self.jars),
                    )

    def test_descriptor_refuses_symlink_in_live_jar_and_preserves_known_partial_install(self):
        self.set_successor(phase='INSTALL_INTENT')
        self.write(self.jars['preview-jar'], self.old_jar)
        self.assertIsNone(self.verify(preserve_only=True))
        with self.assertRaises(binding.BindingRefused):
            self.verify()
        self.jars['preview-jar'].unlink()
        self.jars['preview-jar'].symlink_to(self.jars['release-jar'])
        with self.assertRaises(binding.BindingRefused):
            self.verify(preserve_only=True)
