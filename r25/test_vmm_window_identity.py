"""Window authorization through real lifecycle, private files, flock and gate.

Only the installed role-construction boundary is injected. CPU child HTTP
readiness and /proc identity remain real; no model or business endpoint exists.
"""
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
import time
import unittest
from unittest.mock import patch

import vmm_lifecycle as life
import vmm_service
import test_vmm_lifecycle as fixture
FixtureEngine = fixture.FixtureEngine


def write_window(engine, role):
    """Synthetic private observer records for the actual consumer contract."""
    # A health response can first turn ready during Role.start/status before
    # the normal recovery tick persists READY. Exercise that real tick.
    assert role.recover()['result'] == 'READY'
    now = time.monotonic()
    wall = dt.datetime.now(dt.timezone.utc)
    startup = {'schema': 'ARTLIVE_CLOSED_START_OBSERVER_PLAN_V2',
               'acceptance_gate_schema': life.ACCEPTANCE_COMMAND_SCHEMA,
               'installed_root': str(engine.root), 'limits': life.STARTUP_LIMITS,
               'prepared_at': wall.isoformat()}
    raw = json.dumps(startup, sort_keys=True, separators=(',', ':')).encode()
    startup_sha = life.hashlib.sha256(raw).hexdigest()
    directory = engine.root / 'runtime/vmm-startup-windows' / startup_sha
    directory.mkdir(parents=True, mode=0o700)
    life.write(directory / 'STARTUP_PLAN.json', startup)
    process = role.state()['process']
    execution = {'schema': 'ARTLIVE_CLOSED_START_WINDOW_V2', 'plan_sha256': startup_sha,
                 'enable_calls': 1, 'start_calls': 1, 'generation_requests': 0,
                 'phase': 'OBSERVING', 'completed': False, 'boot_id': process['boot_id'],
                 'start_intent_monotonic': role.state()['started_monotonic'] - .01,
                 'startup_deadline_monotonic': role.state()['started_monotonic'] - .01 + 900}
    health = {'contract': 'artlive-vmm-managed-v1', 'process_alive': True,
              'model_ready': True, 'mode': 'CLOSED', 'dispatch_permitted': False,
              'attempts': 0, 'in_flight': False, 'uncertain': False}
    first = {'process': process, 'monotonic': now, 'deadline_monotonic': now + 600,
             'at': wall.isoformat(), 'deadline_at': (wall + dt.timedelta(seconds=600)).isoformat(),
             'health': health, 'gate_sha256': life.sha(role.gate.path)}
    for name, value in {
        'EXECUTION.json': execution, 'FIRST_READY.json': first,
        'SECOND_AUTHENTICATED_READY.json': {'process': process, 'health': health},
        'NATIVE_READY.json': {'status': role.status()},
        'READY_TELEMETRY.json': {'process': process},
        'READY_GATE.raw.json': role.gate.read(),
    }.items():
        life.write(directory / name, value)
    permit = {'owner_id': '7', 'source_painting_id': '33', 'image_sha256': 'b' * 64, 'duration': 3}
    command = {'schema': life.ACCEPTANCE_COMMAND_SCHEMA, 'mode': 'acceptance',
               'role_sha256': engine.v['R25_VMM_ROLE_SHA256'], 'before_gate_sha256': life.sha(role.gate.path),
               'permit': permit, 'window': {'startup_plan_sha256': startup_sha,
                                          'first_ready_sha256': life.sha(directory / 'FIRST_READY.json')}}
    config = engine.root / 'runtime/config'; config.mkdir(mode=0o700, exist_ok=True)
    life.write(config / 'vmm-gate-command.json', command)
    plan = {'schema': 'ARTLIVE_VMM_SINGLE_REQUEST_REVIEW_PLAN_V2',
            'startup_plan_sha256': startup_sha, 'role_sha256': command['role_sha256'],
            'limits': life.ACCEPTANCE_LIMITS, 'ready_at': first['at'],
            'approval_deadline_wall': first['deadline_at'],
            'approval_deadline_monotonic': first['deadline_monotonic'], 'ready_process': process}
    life.write(directory / 'SINGLE_REQUEST_ACCEPTANCE_UNSIGNED_PLAN.json', plan)
    authorize(engine, directory)
    return directory


def authorize(engine, directory):
    """Synthetic review signs actual fixture bytes, never production paths."""
    path = engine.root / 'runtime/config/vmm-gate-command.json'
    engine.vmm_gate_sha256 = life.sha(path)
    plan_path = directory / 'SINGLE_REQUEST_ACCEPTANCE_UNSIGNED_PLAN.json'
    plan = life.read(plan_path)
    plan.update(gate_command_sha256=engine.vmm_gate_sha256,
                ready_evidence_sha256={name: life.sha(directory / name) for name in (
                    'EXECUTION.json', 'FIRST_READY.json', 'SECOND_AUTHENTICATED_READY.json',
                    'NATIVE_READY.json', 'READY_TELEMETRY.json', 'READY_GATE.raw.json')})
    life.write(plan_path, plan)
    life.write(directory / 'ACCEPTANCE_APPROVAL.json', {
        'schema': 'ARTLIVE_USER_MUSIC_ACCEPTANCE_APPROVAL_V1', 'plan_sha256': life.sha(plan_path),
        'scope': 'ONE_WORKFLOW_CREATE_ONE_CREATION_ONE_UPSTREAM_MUSIC_THREE_SECONDS', 'authorized': True})


class WindowIdentityTest(unittest.TestCase):
    setUp = fixture.LifecycleTest.setUp
    tearDown = fixture.LifecycleTest.tearDown
    start = fixture.LifecycleTest.start
    ready = fixture.LifecycleTest.ready

    def prepare(self):
        self.start()
        self.engine = FixtureEngine(self.root)
        return write_window(self.engine, self.role)

    def consume(self):
        with patch.object(life, 'managed_role', return_value=self.role):
            return life.manage(self.engine, 'acceptance')

    def mutate(self, directory, name, callback, resign=True):
        path = directory / name
        value = life.read(path); callback(value); life.write(path, value)
        if resign:
            authorize(self.engine, directory)

    def test_independent_windows_same_permit_and_raw_gate_are_distinct(self):
        first = self.prepare(); digest = self.engine.vmm_gate_sha256
        self.assertEqual('ACCEPTANCE', self.consume()['mode'])
        self.store.configure('CLOSED'); self.role.stop(); self.start()
        second = write_window(self.engine, self.role)
        self.assertNotEqual(first.name, second.name)
        self.assertNotEqual(digest, self.engine.vmm_gate_sha256)
        self.assertEqual('ACCEPTANCE', self.consume()['mode'])
        self.assertEqual({first.name, second.name}, set(self.role.state()['gate_windows']))
        self.assertEqual(0, self.store.snapshot()['attempts'])

    def test_old_consumed_command_stays_rejected_and_unchanged(self):
        self.start(); self.engine = FixtureEngine(self.root)
        old = self.engine.command(self.store, 'acceptance', permit={
            'owner_id': '7', 'source_painting_id': '33', 'image_sha256': 'b' * 64, 'duration': 3})
        value = self.role.state(); value['gate_commands'] = [old]; life.write(self.role.record, value)
        before = self.role.record.read_bytes()
        with self.assertRaisesRegex(life.Refused, 'command_consumed'): self.consume()
        self.assertEqual(before, self.role.record.read_bytes())

    def test_unconsumed_v1_and_ignored_salt_are_not_new_authorization(self):
        self.start(); self.engine = FixtureEngine(self.root)
        for extra in ({}, {'window_nonce': 'not-an-authorization'}):
            self.engine.command(self.store, 'acceptance', permit={}, **extra)
            with self.assertRaisesRegex(life.Refused, 'window_required'): self.consume()
        self.assertNotIn('gate_commands', self.role.state())

    def test_consumed_window_cannot_rearm_with_new_command_bytes(self):
        directory = self.prepare(); self.consume(); self.store.configure('CLOSED')
        # A different permit/hash under the same immutable startup is not a
        # fresh window, even if a new approval file were mistakenly staged.
        command_path = self.root / 'runtime/config/vmm-gate-command.json'
        command = life.read(command_path); command['permit']['source_painting_id'] = '34'
        life.write(command_path, command); authorize(self.engine, directory)
        with self.assertRaisesRegex(life.Refused, 'window_consumed'): self.consume()
        self.assertEqual('CLOSED', self.store.snapshot()['mode'])
        self.assertEqual(1, len(self.role.state()['gate_commands']))

    def test_wrong_window_or_ready_evidence_rejected(self):
        directory = self.prepare()
        path = self.root / 'runtime/config/vmm-gate-command.json'
        command = life.read(path); command['window']['first_ready_sha256'] = 'd' * 64
        life.write(path, command); authorize(self.engine, directory)
        with self.assertRaisesRegex(life.Refused, 'ready_inventory'): self.consume()
        self.assertNotIn('gate_windows', self.role.state())

    def test_expiry_preserves_original_wall_and_monotonic_deadlines(self):
        directory = self.prepare()
        first = life.read(directory / 'FIRST_READY.json')
        with patch.object(life.time, 'monotonic', return_value=first['deadline_monotonic']):
            with self.assertRaisesRegex(life.Refused, 'monotonic_window'): self.consume()
        expires = dt.datetime.fromisoformat(first['deadline_at']).timestamp()
        with patch.object(life.time, 'time', return_value=expires):
            with self.assertRaisesRegex(life.Refused, 'wall_window'): self.consume()
        self.assertNotIn('gate_windows', self.role.state())

    def test_status_crossing_expiry_does_not_consume_or_open(self):
        directory = self.prepare(); first = life.read(directory / 'FIRST_READY.json')
        observed = [False]
        original_status, original_clock = self.role.status, time.monotonic
        def status():
            result = original_status(); observed[0] = True; return result
        def clock():
            return first['deadline_monotonic'] if observed[0] else original_clock()
        with patch.object(self.role, 'status', side_effect=status), patch.object(life.time, 'monotonic', side_effect=clock):
            with self.assertRaisesRegex(life.Refused, 'observation_expired'): self.consume()
        self.assertNotIn('gate_windows', self.role.state())
        self.assertEqual('CLOSED', self.store.snapshot()['mode'])

    def test_durable_write_crossing_expiry_stays_consumed_and_closed(self):
        directory = self.prepare(); first = life.read(directory / 'FIRST_READY.json')
        persisted = [False]
        original_write, original_clock = life.write, time.monotonic
        def write(path, value):
            original_write(path, value)
            if Path(path) == self.role.record:
                persisted[0] = True
        def clock():
            return first['deadline_monotonic'] if persisted[0] else original_clock()
        with patch.object(life, 'write', side_effect=write), patch.object(life.time, 'monotonic', side_effect=clock):
            with self.assertRaisesRegex(life.Refused, 'observation_expired'): self.consume()
        self.assertIn(directory.name, self.role.state()['gate_windows'])
        self.assertEqual('CLOSED', self.store.snapshot()['mode'])
        with self.assertRaisesRegex(life.Refused, 'command_consumed'): self.consume()

    def test_v1_reconcile_preserves_unknown_request_and_consumption(self):
        self.start(); self.engine = FixtureEngine(self.root)
        self.store.configure('OPEN'); self.store.reserve('uncertain-existing', {'synthetic': True})
        self.store.finish('uncertain-existing', 'UNKNOWN')
        self.engine.command(self.store, 'reconcile', request_id='uncertain-existing', reason='Observed synthetic uncertainty')
        with patch.object(life, 'managed_role', return_value=self.role):
            life.manage(self.engine, 'reconcile')
        record = self.store.read()['requests']['uncertain-existing']
        self.assertEqual('UNKNOWN', record['state']); self.assertTrue(record['acknowledged'])
        self.assertEqual(1, self.store.snapshot()['attempts'])
        with self.assertRaises(vmm_service.Refused): self.store.reserve('uncertain-existing', {'synthetic': True})

    def test_v1_open_rejects_ignored_fields(self):
        self.start(); self.engine = FixtureEngine(self.root)
        self.engine.command(self.store, 'open', random_salt='forbidden')
        with patch.object(life, 'managed_role', return_value=self.role):
            with self.assertRaisesRegex(life.Refused, 'operation_required'): life.manage(self.engine, 'open')
        self.assertNotIn('gate_commands', self.role.state())

    def test_real_gate_lock_delay_rejects_expired_admission(self):
        permit = {'owner_id': '7', 'source_painting_id': '33', 'image_sha256': 'b' * 64, 'duration': 3}
        holder = subprocess.Popen([sys.executable, '-B', '-c',
            "import fcntl,sys,time; f=open(sys.argv[1],'rb'); fcntl.flock(f,fcntl.LOCK_EX); "
            "print('LOCK_HELD',flush=True); time.sleep(.2)", str(self.store.lock_path)],
            stdout=subprocess.PIPE, text=True)
        self.processes.append(holder)
        try:
            self.assertEqual('LOCK_HELD', holder.stdout.readline().strip())
            before = self.store.path.read_bytes(); start = time.monotonic()
            with self.assertRaisesRegex(vmm_service.Refused, 'ACCEPTANCE_WINDOW_EXPIRED'):
                self.store.configure('ACCEPTANCE', permit, expected_sha256=life.sha(self.store.path),
                    acceptance_deadline={'monotonic': start + .05, 'wall': time.time() + 600})
            self.assertGreater(time.monotonic() - start, .1)
            self.assertEqual(before, self.store.path.read_bytes())
            self.assertEqual('CLOSED', self.store.snapshot()['mode'])
        finally:
            holder.wait(2); holder.stdout.close()

    def test_internal_gate_deadline_rejects_invalid_values_and_wall_expiry(self):
        permit = {'owner_id': '7', 'source_painting_id': '33', 'image_sha256': 'b' * 64, 'duration': 3}
        for value in ({}, {'monotonic': True, 'wall': time.time() + 10},
                      {'monotonic': float('inf'), 'wall': time.time() + 10},
                      {'monotonic': time.monotonic() + 10, 'wall': time.time() + 10, 'salt': 1}):
            with self.subTest(value=value), self.assertRaisesRegex(vmm_service.Refused, 'DEADLINE_INVALID'):
                self.store.configure('ACCEPTANCE', permit, acceptance_deadline=value)
        with self.assertRaisesRegex(vmm_service.Refused, 'WINDOW_EXPIRED'):
            self.store.configure('ACCEPTANCE', permit,
                acceptance_deadline={'monotonic': time.monotonic() + 600, 'wall': time.time() - 1})
        self.assertEqual('CLOSED', self.store.snapshot()['mode'])

    def test_changed_or_unapproved_plan_rejected(self):
        directory = self.prepare()
        self.mutate(directory, 'SINGLE_REQUEST_ACCEPTANCE_UNSIGNED_PLAN.json',
                    lambda v: v['limits'].update(core_creation_posts=2), resign=False)
        with self.assertRaisesRegex(life.Refused, 'human_approval'): self.consume()
        authorize(self.engine, directory)
        with self.assertRaisesRegex(life.Refused, 'plan_binding'): self.consume()

    def test_window_directory_must_be_private_not_symlink(self):
        directory = self.prepare(); directory.chmod(0o755)
        with self.assertRaisesRegex(life.Refused, 'private_identity'): self.consume()
        directory.chmod(0o700)
        target = directory.with_name(directory.name + '-actual'); directory.rename(target)
        directory.symlink_to(target)
        with self.assertRaisesRegex(life.Refused, 'symlink'): self.consume()

    def test_completion_or_wrong_process_is_not_current_ready(self):
        directory = self.prepare()
        self.mutate(directory, 'EXECUTION.json', lambda v: v.update(completed=True))
        with self.assertRaisesRegex(life.Refused, 'execution_state'): self.consume()
        self.mutate(directory, 'EXECUTION.json', lambda v: v.update(completed=False, boot_id='wrong-boot'))
        with self.assertRaisesRegex(life.Refused, 'process_identity'): self.consume()

    def test_consumption_precedes_configure_and_survives_interruption(self):
        directory = self.prepare(); digest = self.engine.vmm_gate_sha256
        with patch.object(self.store, 'configure', side_effect=RuntimeError('interrupted-before-configure')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'): self.consume()
        self.assertEqual([digest], self.role.state()['gate_commands'])
        self.assertEqual(digest, self.role.state()['gate_windows'][directory.name]['command_sha256'])
        self.assertEqual('CLOSED', self.store.snapshot()['mode'])
        with self.assertRaisesRegex(life.Refused, 'command_consumed'): self.consume()

    def test_unknown_is_not_refunded_replayed_or_hidden(self):
        self.prepare(); self.consume()
        identity = {'owner_id': '7', 'source_painting_id': '33', 'image_sha256': 'b' * 64, 'duration': 3}
        self.store.reserve('synthetic-uncertain-request', identity)
        self.store.on_start()
        before = self.store.path.read_bytes()
        self.assertTrue(self.store.snapshot()['uncertain'])
        with self.assertRaises(vmm_service.Refused): self.store.reserve('synthetic-uncertain-request', identity)
        with self.assertRaises(vmm_service.Refused): self.store.configure('ACCEPTANCE', identity)
        self.assertEqual(before, self.store.path.read_bytes())
        self.assertEqual(1, self.store.snapshot()['attempts'])
        self.assertEqual('UNKNOWN', self.store.read()['requests']['synthetic-uncertain-request']['state'])

    def test_unknown_gate_command_field_is_rejected(self):
        directory = self.prepare()
        path = self.root / 'runtime/config/vmm-gate-command.json'
        value = life.read(path); value['ignored_nonce'] = 'forbidden'
        life.write(path, value); authorize(self.engine, directory)
        with self.assertRaisesRegex(life.Refused, 'window_required'): self.consume()


if __name__ == '__main__':
    unittest.main()
