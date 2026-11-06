"""Real owned Node/lease observations for the finite Core maintenance contract.

Only disposable fixture processes and files are created. No production service,
restart-budget refill, database, external provider, or network origin is used.
"""
from contextlib import ExitStack
import copy
import os
from pathlib import Path
import signal
import socket
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import community_host
import community_host_cli as cli
import community_lifecycle
import core_runtime
from r25_storage import R25Storage
import test_community_supervision as fixture


class KnowledgeCommunityGuardRaces(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='knowledge-guard-race-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_successor_record_change_between_identity_and_parse_is_rejected(self):
        marker = self.root / 'runtime/vmm-controller/KNOWLEDGE_PUBLICATION_ACTIVE.json'
        marker.parent.mkdir(parents=True)
        original = {'schema': 'R25_KNOWLEDGE_PUBLICATION_ACTIVE_V1', 'approval_sha256': 'a' * 64}
        marker.write_bytes(community_host.encoded(original)); marker.chmod(0o600)
        identity = community_host.identity

        def replace_after_identity(path):
            observed = identity(path)
            # Only the fixture owner is relocated; hash/read both inspect a
            # real file which is replaced between the two actual reads.
            observed.update(uid=0, gid=0)
            marker.write_bytes(community_host.encoded(dict(original, approval_sha256='b' * 64)))
            return observed

        with patch.object(community_host, 'ROOT', self.root), \
             patch.object(community_host, 'identity', side_effect=replace_after_identity), \
             patch.object(community_host, 'strict_json', side_effect=AssertionError('changed bytes must not be parsed')):
            with self.assertRaisesRegex(ValueError, 'community_knowledge_file_changed'):
                community_host.knowledge_lease_binding({}, 'c' * 64, 'd' * 64, 'a' * 64)

    def test_guard_change_after_match_is_rejected_before_executing_code(self):
        path = self.root / 'operations/core_release_guard.py'
        path.parent.mkdir()
        path.write_bytes(b"raise AssertionError('original code must not execute during this race')\n")
        path.chmod(0o700)
        binding = {'guard': community_host.identity(path), 'lock': {'path': str(community_host.LOCK)}}
        match = community_host.match

        def replace_after_match(actual, expected):
            match(actual, expected)
            path.write_bytes(b"raise AssertionError('unverified replacement was executed')\n")

        with patch.object(community_host, 'ROOT', self.root), \
             patch.object(community_host, 'match', side_effect=replace_after_match), \
             patch.dict(sys.modules):
            sys.modules.pop('core_release_guard', None)
            with self.assertRaisesRegex(ValueError, 'community_guard_changed'):
                community_host.production_lease(binding)
            self.assertNotIn('core_release_guard', sys.modules)


class KnowledgeCommunityPreserve(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.Persistent('runTest')
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.h = self.fixture.h
        self.lease = self.fixture.f.lease
        # Spend the same durable lifetime budget through real original ticks;
        # only the final owned operation remains running.
        for attempt in range(3):
            self.assertEqual(self.fixture.tick(), 'STARTING')
            self.fixture.wait('READY')
            record = self.h.role().current()[1]
            if attempt < 2:
                child = self.h.role().children[record['process']['pid']]
                child.send_signal(signal.SIGTERM)
                child.wait(timeout=5)
        self.assertEqual(len(self.fixture.s.load()['attempts']), 3)
        self.record_path, self.record = self.h.role().current()
        self.log = self.h.role().root / ('run-' + self.record['operation_id'] + '.log')

    def preserved(self):
        return {str(p): p.read_bytes() for p in (
            self.h.root / 'WATCHDOG.json', self.h.root / 'ACTIVE.json',
            self.fixture.s.state, self.record_path, self.h.role().root / 'current.json')}

    def call(self):
        with self.lease.held():
            return self.h.preserve_running()

    def no_process_actions(self):
        stack = ExitStack()
        for target, name in ((self.h, 'control'), (self.h.role(), 'stop'),
                             (self.h.role(), 'start'), (self.h, 'write'),
                             (self.h.role(), 'write')):
            stack.enter_context(patch.object(target, name,
                side_effect=AssertionError('preservation must not mutate or signal')))
        return stack

    def test_exhausted_budget_ready_process_and_durable_bytes_stay_identical(self):
        before = self.preserved()
        with self.no_process_actions():
            first = self.call()
            second = self.call()
        self.assertEqual(first, second)
        self.assertEqual(first['state'], 'READY_PRESERVED')
        self.assertEqual(first['process'], self.record['process'])
        self.assertEqual(first['watchdog_state_sha256'], community_host.sha(self.fixture.s.state.read_bytes()))
        self.assertNotEqual(first['watchdog_state_sha256'], community_host.sha(community_host.encoded(self.fixture.s.load())))
        self.assertEqual(self.preserved(), before)
        self.assertEqual(community_lifecycle.process(self.record['process']['pid']), self.record['process'])

    def test_foreign_listener_is_rejected_before_http_without_signalling(self):
        before = self.preserved()
        with socket.socket() as foreign:
            foreign.bind(('127.0.0.1', 0)); foreign.listen()
            foreign_inode = str(os.fstat(foreign.fileno()).st_ino)
            # Model the port table reporting an actual foreign listener inode.
            # The owned Node and real /proc/<pid>/fd validation stay intact.
            with self.no_process_actions(), \
                 patch.object(community_lifecycle, 'listeners', return_value={foreign_inode}), \
                 patch('http.client.HTTPConnection', side_effect=AssertionError('foreign HTTP must not be queried')):
                with self.assertRaisesRegex(ValueError, 'listener_not_owned'):
                    self.call()
        self.assertEqual(self.preserved(), before)
        self.assertIsNotNone(community_lifecycle.process(self.record['process']['pid']))

    def test_missing_and_oversized_logs_refuse_without_draining(self):
        before = self.preserved()
        retained = self.log.with_suffix('.retained')
        self.log.rename(retained)
        try:
            with self.no_process_actions(), self.assertRaises((FileNotFoundError, ValueError)):
                self.call()
        finally:
            retained.rename(self.log)
        with self.log.open('ab') as stream:
            stream.truncate(self.h.plan['service']['log_limit_bytes'])
        with self.no_process_actions(), self.assertRaisesRegex(ValueError, 'log_limit'):
            self.call()
        self.assertEqual(self.preserved(), before)
        self.assertIsNotNone(community_lifecycle.process(self.record['process']['pid']))

    def test_wrong_pid_identity_refuses_without_adopting_a_process(self):
        changed = copy.deepcopy(self.record)
        changed['process']['ticks'] = '0'
        with self.lease.held():
            self.h.role().write(self.record_path, changed)
        before = self.preserved()
        with self.no_process_actions(), self.assertRaisesRegex(ValueError, 'process_drift'):
            self.call()
        self.assertEqual(self.preserved(), before)
        self.assertIsNotNone(community_lifecycle.process(self.record['process']['pid']))

    def test_health_failure_preserves_process_and_budget(self):
        before = self.preserved()
        with self.no_process_actions(), \
             patch('http.client.HTTPConnection', side_effect=OSError('isolated health unavailable')):
            with self.assertRaises(OSError):
                self.call()
        self.assertEqual(self.preserved(), before)
        self.assertIsNotNone(community_lifecycle.process(self.record['process']['pid']))

    def test_core_publish_and_restore_both_reuse_original_process_checkpoint(self):
        observed = self.call()
        root = self.fixture.f.f.base / 'core-maintenance'
        root.mkdir(mode=0o700)
        runtime = core_runtime.CoreRuntime.__new__(core_runtime.CoreRuntime)
        runtime.fd = 37; runtime.tx = 'a' * 32
        runtime.storage = R25Storage([str(root)], os.getuid(), os.getgid())
        runtime.transaction_folder = Mock(return_value=root)
        runtime.read = runtime.storage.read_private_record
        runtime.verify_current = Mock(); runtime.validate_recovery = Mock()
        engine = Mock()
        engine.community_action.return_value = observed
        runtime.engine = Mock(return_value=engine)
        policy = {'upgrade': {'core_followup': {'schema': 'R25_KNOWLEDGE_CORE_PUBLICATION_V1'}}}
        with patch.object(core_runtime.guard, 'policy', return_value=policy):
            runtime.stop_owned_java(None)
            checkpoint = (root / 'community-maintenance.json').read_bytes()
            runtime.stop_for_restore(None)
            runtime.community_maintenance(engine, resuming=True)
            self.assertEqual((root / 'community-maintenance.json').read_bytes(), checkpoint)
            self.assertEqual(engine.community_action.call_args_list,
                [unittest.mock.call('preserve', 37)] * 3)
            changed = dict(observed, watchdog_state_sha256='b' * 64)
            engine.community_action.return_value = changed
            engine.watchdog_stop_locked.reset_mock(); engine.stop_role.reset_mock()
            with self.assertRaisesRegex(core_runtime.Refused, 'maintenance_drift'):
                runtime.stop_for_restore(None)
            engine.watchdog_stop_locked.assert_not_called(); engine.stop_role.assert_not_called()
        self.assertEqual(len(self.fixture.s.load()['attempts']), 3)
        self.assertEqual(self.h.role().current()[1], self.record)

    def test_state_whitespace_change_refuses_checkpoint_without_stopping_owned_process(self):
        root = self.fixture.f.f.base / 'core-maintenance-whitespace'
        root.mkdir(mode=0o700)
        runtime = core_runtime.CoreRuntime.__new__(core_runtime.CoreRuntime)
        runtime.fd = 37; runtime.tx = 'a' * 32
        runtime.storage = R25Storage([str(root)], os.getuid(), os.getgid())
        runtime.transaction_folder = Mock(return_value=root)
        runtime.read = runtime.storage.read_private_record
        runtime.validate_recovery = Mock()
        engine = Mock(); engine.community_action.side_effect = lambda mode, fd: self.call()
        runtime.engine = Mock(return_value=engine)
        policy = {'upgrade': {'core_followup': {'schema': 'R25_KNOWLEDGE_CORE_PUBLICATION_V1'}}}
        original = self.fixture.s.state.read_bytes()
        with patch.object(core_runtime.guard, 'policy', return_value=policy):
            runtime.community_maintenance(engine)
            checkpoint = (root / 'community-maintenance.json').read_bytes()
            with self.lease.held():
                self.fixture.s.state.write_bytes(original + b' \n')
            self.assertEqual(community_host.strict_json(original), self.fixture.s.load())
            with self.assertRaisesRegex(core_runtime.Refused, 'maintenance_drift'):
                runtime.stop_for_restore(None)
            engine.watchdog_stop_locked.assert_not_called(); engine.stop_role.assert_not_called()
            self.assertEqual((root / 'community-maintenance.json').read_bytes(), checkpoint)
        self.assertEqual(len(self.fixture.s.load()['attempts']), 3)
        self.assertEqual(self.h.role().current()[1], self.record)
        self.assertEqual(community_lifecycle.process(self.record['process']['pid']), self.record['process'])

    def test_cli_successor_cannot_install_register_enable_or_rebind_old_plan(self):
        args = ['--plan', str(self.fixture.f.approval), '--plan-sha256', self.h.digest,
                '--bundle-manifest-sha256', self.fixture.bundle.digest,
                '--knowledge-publication-sha256', 'b' * 64]
        for command in ('check', 'install', 'recover', 'register', 'enable', 'disable', 'bind-web', 'supervise'):
            with self.subTest(command=command), \
                 patch.object(sys, 'argv', ['community_host_cli.py', command, *args]), \
                 patch.object(cli, 'bootstrap'), patch.object(cli.os, 'geteuid', return_value=0), \
                 patch.object(community_host, 'knowledge_lease_binding',
                     side_effect=AssertionError('unapproved command must not reach lease inheritance')):
                with self.assertRaisesRegex(ValueError, 'original_installation_only'):
                    cli.main()

    def test_cli_preserve_requires_an_installed_successor_binding(self):
        args = ['--plan', str(self.fixture.f.approval), '--plan-sha256', self.h.digest,
                '--bundle-manifest-sha256', self.fixture.bundle.digest, '--lease-fd', '37']
        with patch.object(sys, 'argv', ['community_host_cli.py', 'watchdog-preserve', *args]), \
             patch.object(cli, 'bootstrap'), patch.object(cli.os, 'geteuid', return_value=0), \
             patch.object(community_host, 'production_lease', side_effect=AssertionError('missing binding')):
            with self.assertRaisesRegex(ValueError, 'preserve_requires_knowledge_binding'):
                cli.main()


if __name__ == '__main__':
    unittest.main()
