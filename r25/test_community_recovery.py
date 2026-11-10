"""Real isolated Node processes exercise the new policy without production access."""
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from community_host import identity
from community_supervision import RecoverySupervisor, Supervisor, recovery_initial_state
import test_community_supervision as legacy_fixture


class Recovery(unittest.TestCase):
    def setUp(self):
        legacy_fixture.Persistent.setUp(self)
        for _ in range(3):
            with self.f.lease.held(): self.assertEqual(Supervisor(self.h).tick(), 'STARTING')
            self.wait_ready(legacy=True)
            self.stop_child()
        with self.f.lease.held():
            self.assertEqual(Supervisor(self.h).tick(), 'RESTART_BUDGET_EXHAUSTED')
        self.legacy = (self.h.folder/'watchdog-state.json').read_bytes()
        self.history = {p.name:p.read_bytes() for p in (self.h.folder/'process').glob('run-*.json')}
        self.approval = 'a'*64
        self.legacy_sha = identity(self.h.folder/'watchdog-state.json')['sha256']
        with self.f.lease.held():
            self.h.write(self.h.folder/'supervision-v2.json',
                         recovery_initial_state(self.h,self.approval,self.legacy_sha))
        self.addCleanup(self.stop_child)

    def supervisor(self):
        return RecoverySupervisor(self.h,self.approval,self.legacy_sha)

    def tick(self):
        with self.f.lease.held():return self.supervisor().tick()

    def wait_ready(self, legacy=False):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            with self.f.lease.held():
                result=(Supervisor(self.h) if legacy else self.supervisor()).tick()
            if result=='READY':return
            time.sleep(.03)
        self.fail('Node did not become ready: '+result)

    def stop_child(self):
        role=self.h.role()
        with self.f.lease.held():
            _,record=role.current()
            role.stop(0)
        child=role.children.get((record.get('process') or {}).get('pid')) if record else None
        if child is not None:child.wait(timeout=5)
        with self.f.lease.held():role.observe()

    def stable(self):
        self.wait_ready()
        state=self.supervisor().load()
        with patch('community_supervision.time.monotonic',return_value=state['stable']['since']+61):
            self.assertEqual(self.tick(),'READY')

    def assert_legacy_preserved(self):
        self.assertEqual((self.h.folder/'watchdog-state.json').read_bytes(),self.legacy)
        for name,raw in self.history.items():
            self.assertEqual((self.h.folder/'process'/name).read_bytes(),raw)

    def test_successful_runs_can_restart_more_than_three_times_with_complete_history(self):
        for count in range(1,6):
            self.assertEqual(self.tick(),'STARTING')
            self.stable()
            state=self.supervisor().load()
            self.assertEqual(len(state['attempts']),count)
            self.assertEqual(state['budget_start'],count)
            self.stop_child()
        self.assertEqual(len(list((self.h.folder/'process').glob('run-*.json'))),8)
        self.assert_legacy_preserved()

    def test_short_ready_responses_do_not_refund_consecutive_attempts(self):
        for count in range(1,4):
            self.assertEqual(self.tick(),'STARTING');self.wait_ready()
            self.assertEqual(self.supervisor().load()['budget_start'],0)
            self.stop_child()
        self.assertEqual(self.tick(),'CONSECUTIVE_START_LIMIT')
        self.assertEqual(self.tick(),'STOPPED')
        self.assertEqual(len(self.supervisor().load()['attempts']),3)
        self.assert_legacy_preserved()

    def test_old_boot_process_reconciles_and_healthy_budget_survives_cli_recreation(self):
        self.tick();self.stable();self.stop_child()
        role=self.h.role()
        with self.f.lease.held():
            path,record=role.current()
            record['phase']='READY';record['process']['boot_id']='previous-fixture-boot'
            role.write(path,record)
        self.assertEqual(self.tick(),'STARTING');self.wait_ready()
        retained=self.h.read(path)
        self.assertEqual(retained['phase'],'EXITED')
        self.assertEqual(retained['exit_reason'],'RECORDED_PREVIOUS_BOOT')
        self.assertEqual(len(self.supervisor().load()['attempts']),2)
        self.assert_legacy_preserved()

    def test_resume_is_explicit_and_idempotent_without_erasing_history(self):
        for _ in range(3):
            self.tick();self.wait_ready();self.stop_child()
        self.tick()
        with self.f.lease.held():
            self.assertEqual(self.supervisor().enable('b'*32),'ENABLED_NOT_STARTED')
            raw=(self.h.folder/'supervision-v2.json').read_bytes()
            self.assertEqual(self.supervisor().enable('b'*32),'RESUME_ALREADY_RECORDED')
            self.assertEqual((self.h.folder/'supervision-v2.json').read_bytes(),raw)
        self.assertEqual(self.tick(),'STARTING');self.wait_ready()
        self.assertEqual(len(self.supervisor().load()['attempts']),4)
        self.assert_legacy_preserved()

    def test_lost_start_receipt_stays_unknown_and_never_replays(self):
        original=self.h.control
        def fail(action,*args):
            if action=='start':raise InterruptedError('fixture lost spawn receipt')
            return original(action,*args)
        with patch.object(self.h,'control',side_effect=fail):
            with self.assertRaises(InterruptedError):self.tick()
        before=(self.h.folder/'supervision-v2.json').read_bytes()
        self.assertEqual(self.tick(),'UNKNOWN_START_RECEIPT')
        self.assertEqual((self.h.folder/'supervision-v2.json').read_bytes(),before)
        self.assert_legacy_preserved()

    def test_legacy_or_upgrade_identity_drift_refuses_before_start(self):
        with self.f.lease.held(),self.assertRaisesRegex(ValueError,'recovery_state'):
            RecoverySupervisor(self.h,'c'*64,self.legacy_sha).tick()
        legacy=self.h.folder/'watchdog-state.json'
        legacy.write_bytes(self.legacy+b'\n')
        try:
            with self.assertRaisesRegex(ValueError,'legacy_ledger_changed'):self.tick()
        finally:legacy.write_bytes(self.legacy)
        self.assertEqual(len(list((self.h.folder/'process').glob('run-*.json'))),3)

    def test_drain_and_resume_keep_current_process_ownership(self):
        self.tick();self.wait_ready()
        with self.f.lease.held():
            result=self.supervisor().tick(drain=True)
        self.assertIn(result,('STOPPING','STOPPED'))
        self.stop_child()
        self.assertEqual(self.tick(),'STOPPED')
        self.assertEqual(self.supervisor().load()['reason'],'OPERATOR_OR_MAINTENANCE_DRAIN')
        self.assert_legacy_preserved()


if __name__=='__main__':unittest.main()
