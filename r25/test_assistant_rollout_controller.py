from copy import deepcopy
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import core_release_guard as guard
import upgrade_core_controller
from publisher_transaction import Refused
from r25_storage import R25Storage


@dataclass
class CoreRelease:
    commit_sha: str
    artifact_sha256: str
    accepted_record_sha256: str


class Engine:
    def __init__(self):
        self.running = True
        self.stops = 0
        self.starts = 0

    def active(self, name, port):
        return {'pid': port, 'ticks': str(port + 1)}

    def frontend(self, command, fd):
        if command != 'status' or fd < 3:
            raise AssertionError('Expected owned read-only Web status')

    def community_action(self, command, fd):
        if command != 'preserve' or fd < 3:
            raise AssertionError('Expected Community preservation under the original lease')
        return {'state': 'READY_PRESERVED', 'process': {'pid': 18090, 'ticks': '561'}}

    def watchdog_identity(self):
        return {'pid': 9000} if self.running else None

    def watchdog_start_locked(self):
        self.starts += 1
        self.running = True

    def watchdog_stop_locked(self):
        self.stops += 1
        self.running = False


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux descriptor and flock verification')
class AssistantRolloutControllerTest(unittest.TestCase):
    def setUp(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('assistant_rollout_controller'),
                             'The rollout needs an installation and recovery controller')
        import assistant_rollout_controller as controller
        self.controller = controller
        tmp = tempfile.TemporaryDirectory(prefix='r25-assistant-rollout-controller-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = self.root / 'runtime/core-publications'
        self.ai = self.root / 'runtime/ai-publications'
        self.lock = self.root / 'frontend-deployment.lock'
        self.owner = (os.geteuid(), os.getegid())
        self.storage = R25Storage((str(self.root),), *self.owner)
        self.fd = None
        self.app_terminal = True
        self.service = Engine()
        self.write(self.lock, b'original shared lease')
        self.live = {name: self.root / 'live' / name for name in
                     ('release-jar', 'preview-jar', 'manifest', 'application', 'handoff')}
        for name, path in self.live.items():
            self.write(path, b'Core JAR' if name.endswith('-jar') else name.encode())
        for module, name, value in (
            (guard, 'ROOT', self.root), (guard, 'STORE', self.store),
            (controller, 'LIVE_FILES', self.live), (upgrade_core_controller, 'LOCK', self.lock),
        ):
            change = patch.object(module, name, value)
            change.start()
            self.addCleanup(change.stop)
        previous = self.write(self.store / 'prior-plan.json', b'prior approved plan')
        prior_journal = self.record(
            self.store / 'controller-upgrades' / previous / 'journal.json',
            {'schema': 'R25_REFERENCE_CORE_PUBLICATION_BINDING_V1', 'approval_sha256': previous, 'phase': 'INSTALLED'},
        )
        self.core = {'commit_sha': 'a' * 40, 'artifact_sha256': self.identity(self.live['release-jar'])['sha256'],
                     'accepted_record_sha256': 'b' * 64}
        ai = {'commit_sha': 'c' * 40, 'source_tree_sha256': 'd' * 64, 'record_sha256': 'e' * 64}
        preimages = {
            'core_current_sha256': self.record(self.store / 'CURRENT.json', {'release': self.core}),
            'core_active_sha256': self.record(self.store / 'ACTIVE.json', {'transaction_id': '1' * 32}),
            'ai_current_sha256': self.record(self.ai / 'CURRENT.json', {'release': ai}),
            'ai_active_sha256': self.record(self.ai / 'ACTIVE.json', {'transaction_id': '2' * 32}),
            'control_active_sha256': self.record(self.store / 'CONTROL_UPGRADE_ACTIVE.json', {
                'schema': 'R25_CORE_CONTROLLER_UPGRADE_ACTIVE_V1', 'approval_sha256': previous,
            }),
        }
        self.new_plan = {
            'schema': 'R25_ASSISTANT_ILLUSTRATION_CORE_PUBLICATION_BINDING_V1',
            'from_policy_schema': 'R25_CORE_BUSINESS_POLICY_V2',
            'deploy': self.deploy('f'),
            'current_release': self.core,
            'current_ai_release': ai,
            'installed_control': {'approval_sha256': previous, 'journal_sha256': prior_journal},
            'frontend': {},
            'preserved': {name: self.identity(path)['sha256'] for name, path in self.live.items() if name != 'handoff'},
            'services': {name: self.service.active(name, port) for name, port in
                         (('java', 19080), ('guide', 19084), ('critic', 19085))},
            'next_owner': 'c5ea', 'controller_owner': list(self.owner), 'private_owner': list(self.owner),
            'review_evidence_sha256': hashlib.sha256(guard.encoded({'reviewed': True})).hexdigest(),
            'assistant_rollout': {
                **preimages, 'core_candidate_manifest_sha256': '1' * 64, 'web_commit': '2' * 40,
                'ai_candidate': {'commit_sha': '3' * 40, 'source_tree_sha256': '4' * 64,
                                 'candidate_manifest_sha256': '5' * 64, 'archive_sha256': '6' * 64,
                                 'policy_sha256': '7' * 64, 'github_run_id': '42', 'github_run_attempt': '1'},
                'community': {'code_controller_sha256': '8' * 64, 'code_plan_sha256': '9' * 64,
                              'deploy': {key: value for key, value in self.deploy('f').items() if key != 'tool_sha256'},
                              'cli_sha256': 'b' * 64},
            },
            'files': {}, 'operational': {},
        }
        config = self.root / 'runtime/config'
        for name, path in (('selection_sha256', config / 'frontend-selection.json'),
                           ('reference_sha256', config / 'frontend-selection.reference')):
            self.new_plan['frontend'][name] = self.write(path, name.encode())
        self.targets = controller.targets(self.new_plan)
        for name, (path, owner, mode) in self.targets.items():
            before = None if name == 'assistant_rollout_binding.py' else self.write(path, f'old {name}'.encode(), mode)
            after = hashlib.sha256(self.after(name)).hexdigest()
            key = 'files' if name.endswith('.py') else 'operational'
            self.new_plan[key][name] = {'before_sha256': before, 'after_sha256': after}
        self.approval_sha = hashlib.sha256(guard.encoded(self.new_plan)).hexdigest()
        self.folder = controller.transaction_path(self.approval_sha)
        self.record(self.folder / 'approval.json', self.new_plan)
        self.record(self.folder / 'review.json', {'reviewed': True})
        for name in self.targets:
            self.write(self.folder / 'after' / name, self.after(name))

    @staticmethod
    def deploy(sha):
        return {'repository': 'bailangguihun/auralink-deploy', 'ref': 'refs/heads/release',
                'commit': sha * 40, 'run_id': '321', 'run_attempt': '1', 'archive_sha256': sha * 64,
                'manifest_sha256': sha * 64, 'tool_sha256': sha * 64}

    @staticmethod
    def after(name):
        return f'reviewed new {name}'.encode()

    def write(self, path, raw, mode=0o600):
        for parent in reversed(path.parents):
            if parent.is_relative_to(self.root) and not parent.exists():
                parent.mkdir(mode=0o700)
        before = self.identity(path)['sha256'] if path.exists() and mode == 0o600 else None
        if mode == 0o700 and path.exists():
            before = self.identity(path, expected_mode=mode)['sha256']
        result = self.storage.atomic_write_bytes(
            path, raw, mode=mode, replace=before is not None, expected_existing_sha256=before,
            existing_mode=mode,
        )
        return result['sha256']

    def record(self, path, value):
        return self.write(path, guard.encoded(value))

    def identity(self, path, **kwargs):
        return self.storage.file_identity(path, **kwargs)

    def read(self, path, expected=None):
        return self.storage.read_private_record(path, expected_sha256=expected)

    def require_lease(self):
        if self.fd is None:
            raise Refused('original_lease_required')

    def require_terminal_application_transactions(self):
        self.require_lease()
        if not self.app_terminal:
            raise Refused('unfinished_application_transaction')

    def current(self):
        return CoreRelease(**self.read(self.store / 'CURRENT.json')['release'])

    def verify_current(self, release):
        self.assertEqual(release, self.current())

    def require_no_active_work(self):
        self.require_lease()

    def engine(self):
        self.require_lease()
        return self.service

    def install(self):
        return self.controller.install(self, approval_sha256=self.approval_sha)

    def recover(self):
        return self.controller.recover(self, approval_sha256=self.approval_sha)

    def set_successor_plan(self):
        prior = deepcopy(self.new_plan)
        plan = self.new_plan = deepcopy(self.new_plan)
        plan['schema'] = self.controller.SUCCESSOR
        for name in ('assistant_rollout_binding.py', 'core_publish.py'):
            path, _, mode = self.controller.targets(plan)[name]
            before = self.write(path, f'old {name}'.encode(), mode)
            plan['files'][name] = {'before_sha256': before, 'after_sha256': hashlib.sha256(self.after(name)).hexdigest()}
        prior['operational']['policy']['after_sha256'] = plan['operational']['policy']['before_sha256']
        prior_id = hashlib.sha256(guard.encoded(prior)).hexdigest()
        prior_folder = self.controller.transaction_path(prior_id)
        self.record(prior_folder/'approval.json', prior)
        plan['installed_control'] = {
            'approval_sha256': prior_id,
            'journal_sha256': self.record(prior_folder/'journal.json', {
                'schema': self.controller.SCHEMA, 'approval_sha256': prior_id, 'phase': 'INSTALLED',
            }),
        }
        rollout = plan['assistant_rollout']
        rollout['control_active_sha256'] = self.record(
            self.store/'CONTROL_UPGRADE_ACTIVE.json', self.controller._active_record(prior_id))
        evidence = plan['successor'] = {
            'candidate': {'repository': 'bailangguihun/auralink-core-api', 'commit_sha': 'd'*40,
                          'artifact_sha256': 'f'*64, 'artifact_bytes': 100,
                          'payload_fingerprint': 'e'*64, 'github_run_id': '4', 'github_run_attempt': '1'},
            'web': {'commit_sha': 'd'*40, 'selection_sha256': '9'*64, 'reference_sha256': '8'*64,
                    'github_run_id': '15', 'github_run_attempt': '1', 'archive_sha256': '7'*64,
                    'build_id': 'next-build', 'receipt_stamp': '1800000000000000000', 'receipt_sha256': 'a'*64},
        }
        for component, base, tx, ref in (('core', self.store, '1'*32, self.core),
                                         ('ai', self.ai, '2'*32, plan['current_ai_release'])):
            folder = base/'transactions'/tx
            evidence[component+'_journal_sha256'] = self.record(folder/'journal.json', {
                'phase': 'COMMITTED', 'transaction_id': tx, 'candidate': ref,
            })
            if component == 'ai':
                evidence['ai_snapshot_sha256'] = self.record(folder/'snapshot.json', {'candidate': ref})
        self.store_successor_fixture(plan)

    def store_successor_fixture(self, plan):
        self.targets = self.controller.targets(plan)
        self.approval_sha = hashlib.sha256(guard.encoded(plan)).hexdigest()
        self.folder = self.controller.transaction_path(self.approval_sha)
        self.record(self.folder/'approval.json', plan)
        self.record(self.folder/'review.json', {'reviewed': True})
        for name in self.targets:
            self.write(self.folder/'after'/name, self.after(name))

    def test_second_successor_installs_without_rewriting_parent_or_current_pointers(self):
        self.set_successor_plan()
        plan = self.new_plan
        parent = deepcopy(plan)
        parent['current_release'] = {**plan['current_release'], 'commit_sha': '5'*40}
        parent['operational']['policy']['after_sha256'] = plan['operational']['policy']['before_sha256']
        parent_web = parent['successor']['web']
        parent_web.update({**plan['frontend'], 'commit_sha': '6'*40})
        plan['assistant_rollout']['web_commit'] = parent_web['commit_sha']
        plan['successor']['ai_snapshot_core'] = parent['current_release']
        parent_id = hashlib.sha256(guard.encoded(parent)).hexdigest()
        folder = self.controller.transaction_path(parent_id)
        self.record(folder/'approval.json', parent)
        journal = {'schema': self.controller.SUCCESSOR, 'approval_sha256': parent_id, 'phase': 'INSTALLED'}
        plan['installed_control'] = {'approval_sha256': parent_id,
                                     'journal_sha256': self.record(folder/'journal.json', journal)}
        plan['assistant_rollout']['control_active_sha256'] = self.record(
            self.store/'CONTROL_UPGRADE_ACTIVE.json', self.controller._active_record(parent_id))
        self.store_successor_fixture(plan)
        paths = (folder/'approval.json', folder/'journal.json', self.store/'CURRENT.json', self.ai/'CURRENT.json')
        before = {path: path.read_bytes() for path in paths}

        with patch.object(self.controller, 'verify_successor_web_preimage'):
            self.assertEqual(self.install(), 'INSTALLED')

        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertEqual(self.read(self.store/'CONTROL_UPGRADE_ACTIVE.json'),
                         self.controller._active_record(self.approval_sha))

    def test_successor_recovers_known_partial_install_without_rewriting_parent_receipts(self):
        self.set_successor_plan()
        original = {name: path.read_bytes() for name, (path, _, _) in self.targets.items()}
        parent = self.controller.transaction_path(self.new_plan['installed_control']['approval_sha256'])
        parent_receipt = (parent/'journal.json').read_bytes()
        old_pointer = (self.store/'CONTROL_UPGRADE_ACTIVE.json').read_bytes()
        atomic_copy = self.storage.atomic_copy_file

        def fail_second_target(source, target, *args, **kwargs):
            if source == self.folder/'after/core_runtime.py':
                raise OSError('interrupted successor copy')
            return atomic_copy(source, target, *args, **kwargs)

        with patch.object(self.controller, 'verify_successor_web_preimage'), \
             patch.object(self.storage, 'atomic_copy_file', side_effect=fail_second_target):
            with self.assertRaises(OSError):
                self.install()
            journal = self.read(self.folder/'journal.json')
            self.assertEqual((journal['schema'], journal['phase']), (self.controller.SUCCESSOR, 'RECOVERY_REQUIRED'))
            self.assertNotEqual(self.targets['core_business.py'][0].read_bytes(), original['core_business.py'])
            with patch.object(self.controller, '_recovery_engine', return_value=self.service):
                self.assertEqual(self.recover(), 'RESTORED')
        self.assertEqual((parent/'journal.json').read_bytes(), parent_receipt)
        self.assertEqual((self.store/'CONTROL_UPGRADE_ACTIVE.json').read_bytes(), old_pointer)
        self.assertEqual({name: path.read_bytes() for name, (path, _, _) in self.targets.items()}, original)

    def test_successor_installs_once_and_refuses_missing_community_preservation(self):
        self.set_successor_plan()
        with patch.object(self.controller, 'verify_successor_web_preimage'):
            with patch.object(self.service, 'community_action', return_value={'state': 'UNKNOWN_START_RECEIPT'}):
                with self.assertRaisesRegex(Refused, 'community_not_preserved'):
                    self.install()
            self.assertEqual(self.service.stops, 0)
            self.assertFalse((self.folder/'journal.json').exists())
            self.assertEqual(self.install(), 'INSTALLED')
            self.assertEqual(self.install(), 'INSTALLED_VERIFIED_NOOP')
            self.assertEqual((self.service.stops, self.service.starts), (1, 1))

    def test_installed_receipt_ignores_an_exact_retry_without_stopping_watchdog(self):
        self.assertEqual(self.install(), 'INSTALLED')
        self.assertEqual(self.install(), 'INSTALLED_VERIFIED_NOOP')
        self.assertEqual((self.service.stops, self.service.starts), (1, 1))
        self.assertEqual(self.read(self.folder / 'journal.json')['phase'], 'INSTALLED')

    def test_foreign_target_and_unfinished_application_refuse_before_old_watchdog_stop(self):
        self.app_terminal = False
        with self.assertRaises(Refused):
            self.install()
        self.app_terminal = True
        target, _, mode = self.targets['core_business.py']
        self.write(target, b'foreign live controller', mode)
        with self.assertRaises(Refused):
            self.install()
        self.assertEqual(self.service.stops, 0)
        self.assertEqual(target.read_bytes(), b'foreign live controller')

    def test_community_bundle_must_come_from_the_same_formal_run(self):
        self.new_plan['assistant_rollout']['community']['deploy']['run_attempt'] = '2'
        with self.assertRaises(Refused):
            self.controller.validate(self.new_plan)

    def test_explicit_recovery_restores_partial_install_and_original_pointer(self):
        original_pointer = (self.store / 'CONTROL_UPGRADE_ACTIVE.json').read_bytes()
        original = {name: path.read_bytes() if path.exists() else None
                    for name, (path, _, _) in self.targets.items()}
        copy = self.storage.atomic_copy_file
        count = 0

        def interrupt(source, target, *args, **kwargs):
            nonlocal count
            if source.parent == self.folder / 'after':
                count += 1
                if count == 2:
                    raise OSError('interrupted reviewed controller copy')
            return copy(source, target, *args, **kwargs)

        with patch.object(self.storage, 'atomic_copy_file', side_effect=interrupt):
            with self.assertRaises(OSError):
                self.install()
        self.assertEqual(self.read(self.folder / 'journal.json')['phase'], 'RECOVERY_REQUIRED')
        self.assertEqual(self.read(self.folder / 'journal.json')['history'][-1]['failure_class'], 'OSError')
        with self.assertRaises(Refused):
            self.install()
        (self.folder / 'after/core_business.py').unlink()
        with patch.object(self.controller, '_recovery_engine', return_value=self.service):
            self.assertEqual(self.recover(), 'RESTORED')
        self.assertTrue(self.service.running)
        self.assertEqual((self.store / 'CONTROL_UPGRADE_ACTIVE.json').read_bytes(), original_pointer)
        for name, (path, _, _) in self.targets.items():
            self.assertEqual(path.read_bytes() if path.exists() else None, original[name])

    def test_interruption_after_durable_install_only_resumes_owned_watchdog(self):
        with patch.object(self.service, 'watchdog_start_locked', side_effect=OSError('lost watchdog start')):
            with self.assertRaises(OSError):
                self.install()
        self.assertFalse(self.service.running)
        self.assertEqual(self.read(self.folder / 'journal.json')['phase'], 'INSTALLED')
        self.assertEqual(self.recover(), 'INSTALLED_VERIFIED_NOOP')
        self.assertTrue(self.service.running)
        self.assertEqual((self.service.stops, self.service.starts), (1, 1))
