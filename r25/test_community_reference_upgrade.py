"""Actual isolated Node/lease transitions with an explicit fixture database.

The synthetic schema-3 archive deliberately contains a tiny HTTP fixture, not
the real Community application or a formal artifact. Archive validation is not
mocked; native SQL/ACL coverage lives in rehearse-community-reference.py. These
tests exercise the lifecycle and interruption boundaries of the new successor.
"""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import pwd
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import community_host as host_module
from community_host import (ReferenceDatabase, ReferencePackageUpgrade,
                            encoded, identity, sha)
from community_bundle import REQUIRED, LEARNING_FILES, REFERENCE_FILES
import community_migration as migration
import test_community_package_upgrade as package_fixture


class DatabaseState:
    def __init__(self, trace):
        self.before = {'versions': [1], 'preserved': {'fixture': {'rows': 7, 'sha256': '7' * 64}},
                       'receipts': {'learning': [], 'reference': []}}
        self.current = deepcopy(self.before)
        self.trace = trace
        self.migrations = 0
        self.migration_attempts = 0
        self.lose_reply = False
        self.on_migrate = lambda: None


class FixtureDatabase:
    """No SQL connection; one shared durable state across fresh adapter objects."""
    def __init__(self, host, plan, state):
        assert host.root != host_module.ROOT / 'runtime/community'
        self.host, self.plan, self.state = host, plan, state

    def snapshot(self):
        self.state.trace.append('db:snapshot')
        return deepcopy(self.state.current)

    def backup(self, target):
        self.state.trace.append('db:backup')
        with Path(target).open('xb') as stream:
            stream.write(b'Disposable fake-database backup; not a PostgreSQL archive.\n')
        Path(target).chmod(0o600)
        return identity(target)

    def status(self, *, preserved=True):
        self.state.trace.append('db:status')
        return 'COMMITTED' if self.state.current['versions'] == [1, 2, 3] else 'NOT_APPLIED'

    def migrate(self, learning, reference):
        migration.reference_transition_sql(learning, reference, self.plan['operation_id'], self.plan['versions'])
        self.state.migration_attempts += 1
        self.state.trace.append('db:migrate_attempt')
        self.state.on_migrate()
        self.state.trace.append('db:migrate')
        self.state.migrations += 1
        assert self.state.migrations == 1, 'A migration result must never be replayed'
        self.state.current['versions'] = [1, 2, 3]
        self.state.current['receipts'] = {
            'learning': [{'operation_id': self.plan['operation_id'],
                          'migration_sha256': migration.LEARNING_SHA, 'schema_version': 2}],
            'reference': [{'operation_id': self.plan['operation_id'],
                           'migration_sha256': migration.REFERENCE_SHA, 'schema_version': 3}],
        }
        if self.state.lose_reply:
            raise InterruptedError('Explicit fixture: commit succeeded but its response was lost')


class ReferenceUpgrade(unittest.TestCase):
    def setUp(self):
        self.first = package_fixture.Package('runTest')
        self.addCleanup(self.first.doCleanups)
        self.first.setUp()
        self.assertEqual(self.first.install(), 'COMMITTED')
        self.host, self.previous = self.first.h, self.first.service
        self.lease = self.first.f.f.lease
        with self.lease.held():
            self.old, self.old_supervisor = self.previous.active()
            self.old_state = deepcopy(self.old_supervisor.load())
            self.old_process = deepcopy(self.old.role().current()[1])
        self.trace = []
        self.database_state = DatabaseState(self.trace)
        self.learning = b'BEGIN;\nCREATE TABLE community.learning_fixture(id integer);\nINSERT INTO community.schema_version VALUES(2);\nCOMMIT;\n'
        self.reference = b'BEGIN;\nCREATE TABLE community.reference_fixture(id integer);\nINSERT INTO community.schema_version VALUES(3);\nCOMMIT;\n'
        for name, raw in [('LEARNING_SHA', self.learning), ('REFERENCE_SHA', self.reference)]:
            item = patch.object(migration, name, sha(raw))
            item.start()
            self.addCleanup(item.stop)
        self.context = {
            'reference_sha256': 'e' * 64, 'core_commit': 'd' * 40,
            'candidate': {'commit_sha': 'd' * 40, 'artifact_sha256': 'e' * 64},
            'predecessor': {'plan_sha256': sha(encoded(self.first.plan)),
                            'marker_sha256': identity(self.previous.marker)['sha256'],
                            'journal_sha256': identity(self.first.folder / 'journal.json')['sha256']},
            'previous_context': self.previous.context,
        }
        self.service = ReferencePackageUpgrade(
            self.host, self.first.f.supervisor(), self.context,
            database_factory=lambda host, plan: FixtureDatabase(host, plan, self.database_state))
        self.addCleanup(self.clean_reference_children)
        self.make_archive()
        self.dbplan = {'schema': 'ARTLIVE_COMMUNITY_REFERENCE_DATABASE_V1', 'versions': [1],
                       'operation_id': '9' * 32, 'postgres': {'explicit_fixture': True},
                       'snapshot': deepcopy(self.database_state.before)}
        self.database_plan = self.host.folder / 'reference-database-fixture.json'
        self.host.storage.atomic_write_bytes(self.database_plan, encoded(self.dbplan))
        with self.lease.held():
            self.plan, unsigned = self.service.prepare(
                self.archive, self.manifest, self.provenance, 'f' * 64, self.database_plan)
            self.folder = unsigned.parent
            self.host.storage.atomic_write_bytes(self.folder / 'approval.json', encoded(self.plan))
        self.historical = {
            path: path.read_bytes() for path in [
                self.previous.marker, self.first.folder / 'approval.json',
                self.first.folder / 'journal.json', self.host.folder / 'approval.json',
                self.host.folder / 'watchdog-state.json', self.host.folder / 'supervision-v2.json']}
        self.old_source = {path: path.read_bytes() for path in (self.old.release / 'release').rglob('*')
                           if path.is_file()}

    def make_archive(self):
        source = self.old.release / 'release'
        old_manifest = json.loads((source / 'manifest.json').read_bytes())
        files = {item['path']: (source / item['path']).read_bytes() for item in old_manifest['files']}
        for name in REQUIRED | LEARNING_FILES | REFERENCE_FILES:
            files.setdefault(name, b'// Explicit state-machine fixture, not the Community application.\n')
        files['db/002_learning_community.sql'] = self.learning
        files['db/003_reference_community.sql'] = self.reference
        listen = b"server.listen(Number(process.env.COMMUNITY_PORT),'127.0.0.1');"
        self.assertEqual(files['src/main.mjs'].count(listen), 1)
        files['src/main.mjs'] = (b"import fs from 'node:fs';\n" + files['src/main.mjs'].replace(
            listen, b"const start=()=>{" + listen + b"};\n"
            b"if(fs.existsSync(process.env.COMMUNITY_MEDIA_ROOT+'/.fixture-delay-start'))"
            b"setTimeout(start,2000);else start();"))
        files['src/main.mjs'] += b'\n// Same immutable bridge/final fixture source.\n'
        target = json.loads(subprocess.check_output([
            self.host.binding()['node'], '-p',
            "JSON.stringify({os:process.platform,arch:process.arch,libc:process.report.getReport().header.glibcVersionRuntime?'glibc':'musl'})"], env={}))
        value = dict(old_manifest, commit='d' * 40, run_id='3', learning_schema_version=2,
                     reference_schema_version=3, runtime_platform={**target, 'sharp': '0.35.5'},
                     files=[{'path': name, 'bytes': len(raw), 'sha256': sha(raw)}
                            for name, raw in sorted(files.items())])
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            for name, raw in sorted(files.items()):
                item = zipfile.ZipInfo(name)
                item.create_system = 3
                item.external_attr = 0o100600 << 16
                archive.writestr(item, raw)
        raw = output.getvalue()
        value['archive_sha256'] = sha(raw)
        self.archive = self.host.folder / 'reference-fixture.zip'
        self.manifest = self.host.folder / 'reference-fixture-manifest.json'
        self.host.storage.atomic_write_bytes(self.archive, raw)
        self.host.storage.atomic_write_bytes(self.manifest, encoded(value))
        self.provenance = {key: value[key] for key in ('commit', 'run_id', 'run_attempt')}

    def clean_reference_children(self):
        hosts = [*self.service.candidates.values(), *self.service.previous.candidates.values()]
        for host in hosts:
            if host._role:
                with self.lease.held():
                    host.role().stop(0)
                for child in host.role().children.values():
                    if child.poll() is None:
                        child.terminate()
                    child.wait(timeout=5)

    def install(self, **options):
        with self.lease.held():
            return self.service.install(self.plan, self.archive, self.manifest, **options)

    def record(self):
        return self.host.read(self.folder / 'journal.json')

    def assert_preserved(self):
        for path, raw in {**self.historical, **self.old_source}.items():
            self.assertEqual(path.read_bytes(), raw, str(path))
        self.first.preserved()
        with self.lease.held():
            current = self.old_supervisor.load()
        for key in ['attempts', 'budget_start', 'resumptions']:
            self.assertEqual(current[key], self.old_state[key], key)

    def assert_no_lease_fd(self, host):
        process = host.role().current()[1]['process']
        wanted = os.fstat(self.lease.fd)
        for path in Path('/proc', str(process['pid']), 'fd').iterdir():
            try:
                observed = path.stat()
            except FileNotFoundError:
                continue
            self.assertNotEqual((wanted.st_dev, wanted.st_ino),
                                (observed.st_dev, observed.st_ino))

    def fail_owned_final_after_start(self, phase):
        if phase == 'FINAL_STARTED':
            final = self.service.candidate(self.plan, 'final')
            child = next(iter(final.role().children.values()))
            child.terminate()
            child.wait(timeout=5)
            raise InterruptedError('owned final fixture failed after recorded start')

    def test_compatible_bridge_is_ready_before_migration_and_final_has_independent_history(self):
        def observe(phase):
            self.trace.append('phase:' + phase)
            if phase == 'BRIDGE_READY':
                self.assert_no_lease_fd(self.service.candidate(self.plan, 'bridge'))
        def migration_boundary():
            bridge = self.service.candidate(self.plan, 'bridge')
            self.assertEqual(bridge.role().current()[1]['phase'], 'STOPPED')
            self.assertEqual(self.old.role().current()[1]['phase'], 'STOPPED')
        self.database_state.on_migrate = migration_boundary
        self.assertEqual(self.install(fault=observe), 'COMMITTED')
        with self.lease.held():
            final, supervisor = self.service.active()
            bridge = self.service.candidate(self.plan, 'bridge')
            self.assertEqual(final.control('observe'), 'READY')
            self.assert_no_lease_fd(final)
            self.assertEqual(final.source_release, bridge.source_release)
            self.assertNotEqual(final.role().root, bridge.role().root)
            self.assertEqual(len(supervisor.load()['attempts']), 1)
        self.assertLess(self.trace.index('phase:BRIDGE_READY'), self.trace.index('db:migrate'))
        self.assertLess(self.trace.index('db:backup'), self.trace.index('db:migrate'))
        self.assertEqual(self.database_state.migrations, 1)
        settings = json.loads(Path(self.plan['settings']['path']).read_bytes())
        old = json.loads(Path(self.host.binding()['config_path']).read_bytes())
        self.assertEqual({key: value for key, value in settings.items() if key != 'COMMUNITY_MEDIA_ROOT'}, old)
        media = Path(settings['COMMUNITY_MEDIA_ROOT'])
        self.assertEqual(media, Path(self.plan['settings']['path']).parent / 'media')
        self.assertEqual((media / 'reference').stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.install(), 'COMMITTED_VERIFIED_NOOP')
        self.assert_preserved()

    def test_lost_commit_response_only_checks_database_receipt_then_finishes(self):
        self.database_state.lose_reply = True
        with self.assertRaises(InterruptedError):
            self.install()
        self.assertEqual(self.record()['phase'], 'MIGRATION_COMMIT_INTENT')
        self.assertEqual(self.database_state.current['versions'], [1, 2, 3])
        self.database_state.lose_reply = False
        self.assertEqual(self.install(recover=True), 'COMMITTED')
        self.assertEqual(self.database_state.migrations, 1)
        self.assertGreaterEqual(self.trace.count('db:status'), 1)
        self.assert_preserved()

    def test_uncommitted_sql_failure_restores_then_allows_a_new_exact_plan(self):
        def abort():
            raise RuntimeError('Explicit fixture SQL transaction rolled back')
        self.database_state.on_migrate = abort
        with self.assertRaisesRegex(RuntimeError, 'transaction rolled back'):
            self.install()
        self.assertEqual(self.record()['phase'], 'MIGRATION_COMMIT_INTENT')
        self.assertEqual(self.database_state.current, self.database_state.before)
        original = self.host.storage.atomic_write_json
        def lose_restored_activation(path, value, *args, **options):
            result = original(path, value, *args, **options)
            if Path(path) == self.service.marker and value.get('channel') == 'previous':
                raise InterruptedError('previous activation committed; response discarded')
            return result
        with patch.object(self.host.storage, 'atomic_write_json', side_effect=lose_restored_activation):
            with self.assertRaises(InterruptedError):
                self.install(recover=True)
        self.assertEqual(self.record()['phase'], 'RESTORE_PREVIOUS_INTENT')
        with self.lease.held():
            ready_history = deepcopy(self.service.active()[1].registered())
        self.assertEqual(self.install(recover=True), 'RESTORED')
        self.assertEqual(self.database_state.migration_attempts, 1)
        self.assertEqual(self.database_state.migrations, 0)
        with self.lease.held():
            restored, supervisor = self.service.active()
            self.assertEqual(restored.release, self.old.release)
            self.assertEqual(restored.role().root, self.old.role().root)
            self.assertEqual(restored.control('observe'), 'READY')
            self.assert_no_lease_fd(restored)
            restored_history = deepcopy(supervisor.registered())
            self.assertEqual(restored_history, ready_history)
            self.assertEqual(restored_history['attempts'][:len(self.old_state['attempts'])],
                             self.old_state['attempts'])
            self.assertEqual(len(restored_history['attempts']), len(self.old_state['attempts']) + 1)
        self.assertEqual(self.install(recover=True), 'RESTORED_VERIFIED_NOOP')
        with self.lease.held():
            self.assertEqual(self.service.active()[1].registered(), restored_history)
        failed_history = {path: path.read_bytes() for path in
                          [self.folder / 'journal.json', self.folder / 'approval.json']}
        replacement = deepcopy(self.dbplan)
        replacement['operation_id'] = '8' * 32
        next_database_plan = self.host.folder / 'reference-database-second-fixture.json'
        self.host.storage.atomic_write_bytes(next_database_plan, encoded(replacement))
        self.database_state.on_migrate = lambda: None
        with self.lease.held():
            next_plan, unsigned = self.service.prepare(
                self.archive, self.manifest, self.provenance, 'f' * 64, next_database_plan)
            self.assertNotEqual(sha(encoded(next_plan)), sha(encoded(self.plan)))
            self.assertNotEqual(next_plan['settings']['path'], self.plan['settings']['path'])
            self.host.storage.atomic_write_bytes(unsigned.parent / 'approval.json', encoded(next_plan))
            self.assertEqual(self.service.install(next_plan, self.archive, self.manifest), 'COMMITTED')
            final, _ = self.service.active()
            self.assertEqual(final, self.service.candidate(next_plan, 'final'))
        self.assertEqual(self.database_state.migration_attempts, 2)
        self.assertEqual(self.database_state.migrations, 1)
        self.assertEqual(self.database_state.current['receipts']['reference'][0]['operation_id'], '8' * 32)
        for path, raw in {**self.historical, **self.old_source, **failed_history}.items():
            self.assertEqual(path.read_bytes(), raw, str(path))
        self.first.preserved()

    def test_unknown_bridge_start_is_not_replayed_or_replaced_with_original(self):
        def fault(phase):
            if phase == 'BRIDGE_START_INTENT':
                raise InterruptedError('before bridge spawn; original receipt unknown')
        with self.assertRaises(InterruptedError):
            self.install(fault=fault)
        with self.assertRaisesRegex(ValueError, 'unknown_bridge_start'):
            self.install(recover=True)
        self.assertEqual(self.record()['phase'], 'BRIDGE_START_INTENT')
        self.assertEqual(self.database_state.migrations, 0)
        with self.lease.held():
            self.assertIsNone(self.service.candidate(self.plan, 'bridge').role().current()[1])
            self.assertEqual(self.old.role().current()[1]['phase'], 'STOPPED')
        self.assert_preserved()

    def test_unknown_final_start_keeps_forward_schema_and_does_not_replay(self):
        def fault(phase):
            if phase == 'FINAL_START_INTENT':
                raise InterruptedError('before final spawn; original receipt unknown')
        with self.assertRaises(InterruptedError):
            self.install(fault=fault)
        with self.assertRaisesRegex(ValueError, 'unknown_final_start'):
            self.install(recover=True)
        self.assertEqual(self.record()['phase'], 'FINAL_START_INTENT')
        self.assertEqual(self.database_state.migrations, 1)
        with self.lease.held():
            self.assertIsNone(self.service.candidate(self.plan, 'final').role().current()[1])
            self.assertEqual(self.old.role().current()[1]['phase'], 'STOPPED')
        self.assertEqual(self.database_state.current['versions'], [1, 2, 3])
        self.assert_preserved()

    def test_final_failure_recovers_compatible_bridge_and_recovery_is_repeatable(self):
        with self.assertRaises(InterruptedError):
            self.install(fault=self.fail_owned_final_after_start)
        self.assertEqual(self.install(recover=True), 'COMPATIBLE_BRIDGE_READY_FORWARD_SCHEMA_RETAINED')
        with self.lease.held():
            active, supervisor = self.service.active()
            self.assertEqual(active, self.service.candidate(self.plan, 'bridge'))
            self.assertEqual(active.control('observe'), 'READY')
            self.assertEqual(self.old.role().current()[1]['phase'], 'STOPPED')
            state = deepcopy(supervisor.registered())
            current = active.role().current()[1]
            token = self.record()['resume_bridge_operation_id']
            self.assertNotEqual(current['operation_id'], token)
            self.assertEqual(state['attempts'][-1], current['operation_id'])
            self.assertIn({'operation_id': token, 'budget_start': state['budget_start']},
                          state['resumptions'])
        self.assertEqual(self.database_state.current['versions'], [1, 2, 3])
        self.assertEqual(self.database_state.migrations, 1)
        self.assertEqual(self.install(recover=True), 'COMPATIBLE_BRIDGE_READY_FORWARD_SCHEMA_RETAINED')
        with self.lease.held():
            recovered = self.service.active()[1].registered()
            for key in ['attempts', 'budget_start', 'resumptions']:
                self.assertEqual(recovered[key], state[key], key)
        self.assert_preserved()

    def test_forward_recovery_rejects_unmatched_ledger_before_signals(self):
        with self.assertRaises(InterruptedError):
            self.install(fault=self.fail_owned_final_after_start)
        self.assertEqual(self.install(recover=True), 'COMPATIBLE_BRIDGE_READY_FORWARD_SCHEMA_RETAINED')
        with self.lease.held():
            bridge, supervisor = self.service.active()
        raw = supervisor.state.read_bytes()
        original = json.loads(raw)
        changes = {}
        value = deepcopy(original); value['resumptions'] = []
        changes['missing-resumption'] = value
        value = deepcopy(original); value['resumptions'][-1]['budget_start'] = 0
        changes['wrong-resumption-budget'] = value
        value = deepcopy(original); value['budget_start'] = 0
        changes['wrong-current-budget'] = value
        value = deepcopy(original); value['attempts'][-1] = '0' * 32
        value['stable'] = value['unhealthy'] = None
        changes['unknown-latest-attempt'] = value
        for name, value in changes.items():
            with self.subTest(drift=name):
                try:
                    supervisor.state.write_bytes(encoded(value))
                    with patch('community_lifecycle.signal.pidfd_send_signal',
                               side_effect=AssertionError('signal before ledger validation')):
                        with self.assertRaises((ValueError, OSError)):
                            self.install(recover=True)
                    self.assertEqual(self.record()['phase'], 'FORWARD_READY')
                    self.assertEqual(self.database_state.migrations, 1)
                finally:
                    supervisor.state.write_bytes(raw)
        with self.lease.held():
            self.assertEqual(bridge.control('observe'), 'READY')
        self.assert_preserved()

    def test_recorded_resumed_bridge_starting_is_waited_without_signal_or_second_start(self):
        with self.assertRaises(InterruptedError):
            self.install(fault=self.fail_owned_final_after_start)
        bridge = self.service.candidate(self.plan, 'bridge')
        settings = json.loads(Path(self.plan['settings']['path']).read_bytes())
        delay = Path(settings['COMMUNITY_MEDIA_ROOT']) / '.fixture-delay-start'
        self.host.storage.atomic_write_bytes(delay, b'Explicit fixture: delay only the next Node listen.\n')
        original = bridge.role().start
        def lose_spawn_reply(operation):
            original(operation)
            raise InterruptedError('owned resumed Node started; response discarded')
        with patch.object(bridge.role(), 'start', side_effect=lose_spawn_reply):
            with self.assertRaises(InterruptedError):
                self.install(recover=True)
        with self.lease.held():
            self.assertEqual(bridge.control('observe'), 'STARTING')
            process = deepcopy(bridge.role().current()[1]['process'])
            supervisor = self.service.supervisor_for(self.plan, self.record(), 'bridge')
            history = deepcopy(supervisor.registered())
        with patch.object(bridge.role(), 'start', side_effect=AssertionError('second resumed start')):
            with patch('community_lifecycle.signal.pidfd_send_signal',
                       side_effect=AssertionError('signal to an already-owned starting Node')):
                self.assertEqual(self.install(recover=True),
                                 'COMPATIBLE_BRIDGE_READY_FORWARD_SCHEMA_RETAINED')
        with self.lease.held():
            self.assertEqual(bridge.control('observe'), 'READY')
            self.assertEqual(bridge.role().current()[1]['process'], process)
            self.assert_no_lease_fd(bridge)
            recovered = supervisor.registered()
            for key in ['attempts', 'budget_start', 'resumptions']:
                self.assertEqual(recovered[key], history[key], key)
        self.assertEqual(self.database_state.migrations, 1)
        self.assert_preserved()

    def test_final_activation_response_loss_commits_without_second_start(self):
        original = self.host.storage.atomic_write_json
        def fault(path, value, *args, **options):
            result = original(path, value, *args, **options)
            if Path(path) == self.service.marker and value.get('channel') == 'final':
                raise InterruptedError('final activation committed; response discarded')
            return result
        with patch.object(self.host.storage, 'atomic_write_json', side_effect=fault):
            with self.assertRaises(InterruptedError):
                self.install()
        self.assertEqual(self.record()['phase'], 'FINAL_ACTIVATE_INTENT')
        with self.lease.held():
            final, supervisor = self.service.active()
            attempts = deepcopy(supervisor.load()['attempts'])
        self.assertEqual(self.install(recover=True), 'COMMITTED_RECONCILED')
        with self.lease.held():
            self.assertEqual(self.service.active()[1].load()['attempts'], attempts)
        self.assertEqual(self.database_state.migrations, 1)
        self.assert_preserved()

    def test_predecessor_archive_configuration_and_database_drift_refuse_before_signals(self):
        process_path, _ = self.old.role().current()
        paths = [self.previous.marker, self.first.folder / 'approval.json',
                 self.first.folder / 'journal.json', self.archive,
                 Path(self.plan['settings']['path']),
                 Path(self.host.binding()['config_path']), process_path]
        for path in paths:
            raw = path.read_bytes()
            if path == self.previous.marker:
                changed = encoded({'schema': host_module.PACKAGE_SCHEMA, 'plan_sha256': '0' * 64})
            elif path == self.first.folder / 'journal.json':
                value = json.loads(raw); value['phase'] = 'RESTORED'; changed = encoded(value)
            elif path == process_path:
                value = json.loads(raw); value['process']['ticks'] = '0'; changed = encoded(value)
            else:
                changed = raw + b'\n'
            with self.subTest(path=path.name):
                try:
                    path.write_bytes(changed)
                    with patch('community_lifecycle.signal.pidfd_send_signal', side_effect=AssertionError('signal before validation')):
                        with self.assertRaises((ValueError, OSError)):
                            self.install()
                    self.assertFalse((self.folder / 'journal.json').exists())
                finally:
                    path.write_bytes(raw)
        self.database_state.current['preserved']['fixture']['rows'] += 1
        with patch('community_lifecycle.signal.pidfd_send_signal', side_effect=AssertionError('signal before DB snapshot validation')):
            with self.assertRaisesRegex(ValueError, 'database_preimage_drift'):
                self.install()
        self.database_state.current = deepcopy(self.database_state.before)
        self.assertFalse((self.folder / 'journal.json').exists())
        with self.lease.held():
            self.assertEqual(self.old.control('observe'), 'READY')
        self.assert_preserved()


class DatabaseIdentity(unittest.TestCase):
    """Default adapter validation; the bound executables are inert local fixtures."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.host = SimpleNamespace(root=self.root / 'host', folder=self.root / 'host',
                                    lease=SimpleNamespace(require=lambda: None))
        self.bin = self.root / 'bin'; self.bin.mkdir(mode=0o700)
        tools = {}
        for name in ['psql', 'pg_dump', 'pg_restore']:
            path = self.bin / name
            path.write_bytes(b'#!/bin/sh\nexit 0\n'); path.chmod(0o700)
            tools[name] = identity(path)
        self.passfile = self.root / 'passfile'
        self.passfile.write_bytes(b'Disposable inert credential-file fixture.\n')
        self.passfile.chmod(0o600)
        account = pwd.getpwuid(os.geteuid())
        principal = {'account': account.pw_name, 'uid': account.pw_uid, 'gid': account.pw_gid,
                     'groups': sorted(set(os.getgrouplist(account.pw_name, account.pw_gid)))}
        self.plan = {'schema': 'ARTLIVE_COMMUNITY_REFERENCE_DATABASE_V1', 'versions': [1],
                     'operation_id': 'a' * 32, 'snapshot': {}, 'postgres': {
                         'binary_dir': str(self.bin), 'binaries': tools, 'host': str(self.root),
                         'library_dir': '', 'library_identity': {},
                         'port': 54321, 'user': 'isolated_fixture',
                         'passfile': {'path': str(self.passfile), 'identity': identity(self.passfile)},
                         'credential': principal}}

    def test_account_uid_and_group_drift_are_rejected_without_running_sql(self):
        for key, value in [('uid', os.getuid() + 1), ('groups', [])]:
            plan = deepcopy(self.plan); plan['postgres']['credential'][key] = value
            with self.subTest(key=key), patch('community_host.subprocess.run') as invoke:
                with self.assertRaisesRegex(ValueError, 'database_account'):
                    ReferenceDatabase(self.host, plan)
                invoke.assert_not_called()

    def test_private_passfile_permissions_are_required(self):
        self.passfile.chmod(0o644)
        self.plan['postgres']['passfile']['identity'] = identity(self.passfile)
        with patch('community_host.subprocess.run') as invoke:
            with self.assertRaisesRegex(ValueError, 'passfile_identity'):
                ReferenceDatabase(self.host, self.plan)
            invoke.assert_not_called()

    def test_bound_passfile_is_rechecked_before_every_command(self):
        database = ReferenceDatabase(self.host, self.plan)
        self.passfile.write_bytes(b'Changed inert fixture.\n')
        with patch('community_host.subprocess.run') as invoke:
            with self.assertRaisesRegex(ValueError, 'identity_drift'):
                database.query('SELECT 1;')
            invoke.assert_not_called()

    def test_bound_library_bytes_and_alias_are_rechecked_before_sql(self):
        library = self.root / 'lib'; library.mkdir(mode=0o700)
        target = library / 'libpq.so.5.16'
        target.write_bytes(b'Inert dependency identity fixture.\n')
        second = library / 'libpq-fixture.so'
        second.write_bytes(b'A different already-bound inert dependency.\n')
        alias = library / 'libpq.so.5'; alias.symlink_to(target.name)
        self.plan['postgres'].update(library_dir=str(library),
                                     library_identity={
                                         entry.name: {'target': entry.resolve(strict=True).name,
                                                      'identity': identity(entry.resolve(strict=True))}
                                         for entry in library.iterdir()})
        database = ReferenceDatabase(self.host, self.plan)
        self.assertEqual(database.env['LD_LIBRARY_PATH'], str(library))
        original = target.read_bytes()
        target.write_bytes(b'Changed inert dependency.\n')
        with patch('community_host.subprocess.run') as invoke:
            with self.assertRaisesRegex(ValueError, 'library_drift'):
                database.query('SELECT 1;')
            invoke.assert_not_called()
        target.write_bytes(original)
        alias.unlink(); alias.symlink_to(second.name)
        with patch('community_host.subprocess.run') as invoke:
            with self.assertRaisesRegex(ValueError, 'library_drift'):
                database.query('SELECT 1;')
            invoke.assert_not_called()
        outside = self.root / 'outside-library'
        outside.write_bytes(original)
        alias.unlink(); alias.symlink_to(outside)
        with patch('community_host.subprocess.run') as invoke:
            with self.assertRaisesRegex(ValueError, 'library_target'):
                database.query('SELECT 1;')
            invoke.assert_not_called()

    def test_nonloopback_target_and_production_factory_injection_are_refused(self):
        plan = deepcopy(self.plan); plan['postgres']['host'] = 'database.invalid'
        with self.assertRaisesRegex(ValueError, 'pg_loopback'):
            ReferenceDatabase(self.host, plan)
        production_shaped = SimpleNamespace(root=host_module.ROOT / 'runtime/community', folder=self.root)
        context = {'reference_sha256': 'a' * 64, 'core_commit': 'b' * 40,
                   'candidate': {}, 'predecessor': {}, 'previous_context': {}}
        with self.assertRaisesRegex(ValueError, 'fixture_database_forbidden'):
            ReferencePackageUpgrade(production_shaped, None, context,
                                    database_factory=lambda *_: None)


if __name__ == '__main__':
    unittest.main()
