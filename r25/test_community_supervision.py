"""Actual Node + retained guard + persistent watchdog entry in isolated layout."""
from copy import deepcopy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from community_host import Host, InstalledLease, SUCCESSOR_SCHEMA, encoded, identity
from community_supervision import Supervisor
import test_community_host as fixture
import test_community_host_cli as bundle_fixture


class Persistent(unittest.TestCase):
    def setUp(self):
        f=fixture.HostTests('runTest');f.setUp();self.addCleanup(f.doCleanups);self.f=f
        b=bundle_fixture.ControllerPackage('runTest');b.setUp();self.addCleanup(b.doCleanups);self.bundle=b
        f.ap.write_bytes(encoded(dict(schema=SUCCESSOR_SCHEMA)))
        f.plan['parent'].update(schema=SUCCESSOR_SCHEMA,approval_identity=identity(f.ap))
        f.plan['service']['restart_budget']=2
        f.plan['controller_manifest_sha256']=b.digest
        f.host=Host(f.f.base/'host',f.plan,f.lease);f.approval.write_bytes(encoded(f.plan))
        f.install();self.h=f.host;self.s=Supervisor(self.h)
        with f.lease.held():self.assertEqual(self.s.register(b.root),'REGISTERED_NOT_STARTED')

    def tick(self,drain=False):
        # New wrapper each time models separate original-watchdog CLI calls.
        with self.f.lease.held():return Supervisor(self.h).tick(drain=drain)


    def wait(self,expected):
        deadline=time.monotonic()+6
        while time.monotonic()<deadline:
            value=self.tick()
            if value==expected:return
            time.sleep(.03)
        self.fail('wanted '+expected+' got '+value)

    def test_separate_cli_processes_inherit_real_lock_and_survive_terminal_exit(self):
        # Only host paths/UID and guard.production's installed-policy factory
        # are fixture boundaries. Actual released CLI dispatcher, inventory,
        # retained verify_lease, state machine and Node process execute.
        script=self.f.f.base/'watchdog-fixture.py'
        script.write_text("""import json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import core_release_guard as g
from community_host_cli import verify_controller,supervision_command
from community_host import Host,InstalledLease
plan=json.loads(Path(sys.argv[2]).read_bytes());bundle=Path(sys.argv[3]);fd=int(sys.argv[5])
verify_controller(bundle,plan['controller_manifest_sha256'],(os.getuid(),os.getgid()))
raw=g.read_file
g.read_file=lambda p,**kw:raw(p,owner=(os.getuid(),os.getgid()),**kw)
guard=g.BusinessGuard();guard.lock=Path(sys.argv[6]);st=guard.lock.stat()
lease=InstalledLease(guard,guard.lock,dict(device=st.st_dev,inode=st.st_ino),fd)
host=Host(Path(sys.argv[7]),plan,lease)
print(supervision_command(host,sys.argv[4],bundle))
""")
        def call(command):
            with self.f.lease.held():
                fd=self.f.lease.fd
                result=subprocess.run([sys.executable,'-B',str(script),str(Path(__file__).parent),
                    str(self.f.approval),str(self.bundle.root),command,str(fd),str(self.f.lease.lock),str(self.h.root)],
                    pass_fds=(fd,),capture_output=True,text=True,timeout=15)
                self.assertEqual(0,result.returncode,result.stderr)
                self.f.lease.require()
                return result.stdout.strip()
        try:
            self.assertEqual(call('watchdog-tick'),'STARTING')
            deadline=time.monotonic()+6
            while time.monotonic()<deadline:
                if call('watchdog-tick')=='READY':break
                time.sleep(.05)
            else:self.fail('detached child did not reach READY')
            current=self.h.role().current()[1]
            self.assertEqual(call('watchdog-tick'),'READY')
            self.assertEqual(current,self.h.role().current()[1])
            self.assertEqual(1,len(self.s.load()['attempts']))
            self.assertIn(call('watchdog-drain'),('STOPPING','STOPPED'))
            deadline=time.monotonic()+6
            while time.monotonic()<deadline:
                if call('watchdog-tick')=='STOPPED':break
                time.sleep(.05)
            else:self.fail('detached child did not drain')
        finally:
            with self.f.lease.held():self.h.role().stop(5)

    def test_health_deadline_is_not_reset_by_ticks_and_only_graceful_drain(self):
        self.tick();self.wait('READY')
        state=self.s.load();state['unhealthy']=dict(boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),since=time.monotonic()-61)
        with self.f.lease.held():self.h.write(self.s.state,state)
        original=self.h.control
        with patch.object(self.h,'control',side_effect=lambda action,*a: 'STARTING' if action=='observe' else original(action,*a)):
            self.assertIn(self.tick(),('STOPPING','STOPPED'))
        self.assertEqual(self.s.load()['unhealthy'],state['unhealthy'])
        self.assertEqual(self.s.load()['reason'],'HEALTH_TIMEOUT')
        self.assertEqual(1,len(self.s.load()['attempts']))

    def test_terminal_exit_new_tick_original_guard_and_drain(self):
        before=self.f.preserved.read_bytes();ino=self.f.lease.lock.stat().st_ino
        self.assertEqual(self.tick(),'STARTING');self.wait('READY')
        current=self.h.role().current()[1]
        # Reopening the registered entry never replays an initial start.
        with self.f.lease.held():self.assertEqual(self.s.register(self.bundle.root),'REGISTERED_VERIFIED_NOOP')
        self.wait('READY');self.assertEqual(current['operation_id'],self.h.role().current()[1]['operation_id'])
        self.assertEqual(1,len(self.s.load()['attempts']))
        self.tick(drain=True);self.wait('STOPPED')
        self.assertEqual(before,self.f.preserved.read_bytes());self.assertEqual(ino,self.f.lease.lock.stat().st_ino)

    def test_inherited_descriptor_real_verifier_no_nested_lock_or_unlock(self):
        with self.f.lease.held():
            inherited=InstalledLease(self.f.lease.guard,self.f.lease.lock,self.f.lease.expected,self.f.lease.fd)
            with inherited.held():inherited.require()
            self.f.lease.require();os.fstat(self.f.lease.fd)
            inherited.expected=dict(inherited.expected,inode=inherited.expected['inode']+1)
            with self.assertRaisesRegex(ValueError,'lock_replaced'):
                with inherited.held():pass

    def test_crash_budget_persists_across_runner_recreation(self):
        for count in range(1,4):
            self.assertEqual(self.tick(),'STARTING');self.wait('READY')
            record=self.h.role().current()[1]
            os.kill(record['process']['pid'],signal.SIGTERM)
            self.h.role().children[record['process']['pid']].wait(timeout=5)
            self.assertEqual(count,len(self.s.load()['attempts']))
        self.assertEqual(self.tick(),'RESTART_BUDGET_EXHAUSTED')
        self.assertEqual(self.tick(),'EXITED')
        with self.f.lease.held():
            with self.assertRaisesRegex(ValueError,'budget_exhausted'):self.s.enable()
        self.assertEqual(3,len(list(self.h.role().root.glob('run-*.json'))))

    def test_lost_start_receipt_and_unknown_do_not_retry_or_signal(self):
        write=self.h.role().write
        def fail(path,value):
            result=write(path,value)
            if value.get('phase')=='STARTING':raise InterruptedError('lost process receipt')
            return result
        with patch.object(self.h.role(),'write',side_effect=fail):
            with self.assertRaises(InterruptedError):self.tick()
        self.wait('READY');self.assertEqual(1,len(self.s.load()['attempts']))
        path,record=self.h.role().current();record['process']=None
        with self.f.lease.held():self.h.role().write(path,record)
        with patch('community_lifecycle.signal.pidfd_send_signal') as send:
            self.assertEqual(self.tick(),'UNKNOWN');self.assertEqual(self.tick(),'UNKNOWN')
            with self.assertRaisesRegex(ValueError,'unknown_process'):self.tick(drain=True)
            send.assert_not_called()
        self.assertEqual(1,len(self.s.load()['attempts']))

    def test_interrupted_start_decision_requires_investigation_no_replay(self):
        with patch.object(self.h,'control',side_effect=lambda action,*a: 'NOT_STARTED' if action=='observe' else (_ for _ in ()).throw(InterruptedError('before start'))):
            with self.assertRaises(InterruptedError):self.tick()
        self.assertEqual(self.tick(),'UNKNOWN_START_RECEIPT')
        self.assertEqual(self.tick(),'UNKNOWN_START_RECEIPT');self.assertEqual(1,len(self.s.load()['attempts']))
        self.assertIsNone(self.h.role().current()[1])

    def test_reboot_known_process_reconciles_only_without_listener(self):
        self.tick();self.wait('READY');role=self.h.role();path,r=role.current()
        child=role.children[r['process']['pid']];child.terminate();child.wait(timeout=5)
        old=deepcopy(r);r['process']['boot_id']='previous-boot-fixture'
        with self.f.lease.held():role.write(path,r)
        self.assertEqual(self.tick(),'STARTING');self.wait('READY')
        retired=role.read(path)
        self.assertEqual(retired['exit_reason'],'RECORDED_PREVIOUS_BOOT')
        self.assertEqual(retired['process']['pid'],old['process']['pid'])
        self.assertEqual(2,len(self.s.load()['attempts']))

    def test_reboot_foreign_listener_refuses_without_adoption(self):
        self.tick();self.wait('READY');role=self.h.role();path,r=role.current();r['process']['boot_id']='previous-boot-fixture'
        with self.f.lease.held():role.write(path,r)
        with self.assertRaisesRegex(ValueError,'foreign_listener_after_reboot'):self.tick()
        self.assertEqual(1,len(self.s.load()['attempts']))

    def test_log_limit_and_explicit_resume_never_reset_budget(self):
        self.tick();self.wait('READY');record=self.h.role().current()[1]
        log=self.h.role().root/('run-'+record['operation_id']+'.log')
        with log.open('ab') as out:out.write(b'x'*(8*1024*1024))
        self.assertEqual(self.tick(),'LOG_LIMIT_DRAINING');self.wait('STOPPED')
        with self.f.lease.held():self.assertEqual(self.s.enable(),'ENABLED_NOT_STARTED')
        self.assertEqual(self.tick(),'STARTING');self.wait('READY')
        self.assertEqual(2,len(self.s.load()['attempts']));self.assertGreaterEqual(log.stat().st_size,8*1024*1024)


class RepairParent(unittest.TestCase):
    def test_repair_parent_uses_same_watchdog_budget_and_graceful_drain(self):
        from community_host import REPAIR_SCHEMA
        self.bundle=bundle_fixture.ControllerPackage('runTest');self.bundle.setUp();self.addCleanup(self.bundle.doCleanups)
        f=fixture.HostTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
        f.ap.write_bytes(encoded(dict(schema=REPAIR_SCHEMA)))
        f.plan['parent'].update(schema=REPAIR_SCHEMA,approval_identity=identity(f.ap))
        f.plan['service']['restart_budget']=2;f.plan['controller_manifest_sha256']=self.bundle.digest
        f.host=Host(f.f.base/'host',f.plan,f.lease);f.approval.write_bytes(encoded(f.plan));f.install()
        supervisor=Supervisor(f.host)
        try:
            with f.lease.held():
                self.assertEqual(supervisor.register(self.bundle.root),'REGISTERED_NOT_STARTED')
                self.assertEqual(supervisor.tick(),'STARTING')
            deadline=time.monotonic()+6
            while time.monotonic()<deadline:
                with f.lease.held():value=supervisor.tick()
                if value=='READY':break
                time.sleep(.03)
            self.assertEqual(value,'READY');self.assertEqual(len(supervisor.load()['attempts']),1)
            with f.lease.held():
                self.assertIn(supervisor.tick(drain=True),('STOPPING','STOPPED'))
            self.assertEqual(len(supervisor.load()['attempts']),1)
        finally:
            with f.lease.held():f.host.role().stop(5)


if __name__=='__main__':unittest.main()
