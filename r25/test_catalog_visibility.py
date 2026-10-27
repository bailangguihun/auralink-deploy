"""Finite visibility SQL/transaction boundary; no production or provider I/O.

Private captured-predecessor rehearsal is separate: it exercises the actual
installed proof bytes and guard. These tests inject only the host I/O boundary
and never substitute approval/retention validation in that rehearsal.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import vmm_controller as v


class Visibility(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.owner=(os.getuid(),os.getgid())
        self.lock=self.root/'lock';self.lock.write_bytes(b'');self.lock.chmod(0o600)
        self.rt=v.Runtime(self.root,self.lock,private=self.owner,scripts=self.owner)
        self.ident='a'*64;self.folder=v.visibility_folder(self.rt,self.ident)
        self.folder.mkdir(parents=True)
        self.before={'vmm_controller.py':b'prior controller','handoff':b'old handoff'}
        self.after={'vmm_controller.py':b'new controller','handoff':b'new handoff'}
        self.plan={'schema':v.VISIBILITY_SCHEMA,'files':{k:dict(before_sha256=v.sha(self.before[k]),after_sha256=v.sha(self.after[k])) for k in self.before}}
        for n,vals in [('before',self.before),('after',self.after)]:
            (self.folder/n).mkdir()
            for k,b in vals.items():self.put(self.folder/n/k,b)
        self.put(self.folder/'approval.json',v.encoded(self.plan))
        for name,(p,_,_) in v.visibility_paths(self.rt).items():self.put(p,self.before[name])
        self.state='before';self.events=[];self.sql=[];self.stopped=False
        original_read=v.json_read
        def json_read(path,owner=(0,0),expected=None):
            # The host transaction driver fixture is not an approval test.
            return original_read(path,owner,None if Path(path)==self.folder/'approval.json' else expected)
        self.patch(patch.object(v,'json_read',side_effect=json_read))
        self.patch(patch.object(v,'visibility_proof',side_effect=self.proof))
        self.patch(patch.object(v,'revision_stopped',side_effect=lambda rt:self.events.append('VMM_STOPPED')))
        self.patch(patch.object(self.rt,'require_watchdog_stopped',side_effect=lambda:None if self.stopped else (_ for _ in ()).throw(v.Refused('watchdog_must_be_stopped_before_mixed_recovery'))))

    def patch(self,p):p.start();self.addCleanup(p.stop)
    def put(self,p,b):p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b);p.chmod(0o600)
    def proof(self,rt,plan,folder,phase='after'):
        for n,(p,_,_) in v.visibility_paths(rt).items():
            self.assertIn(p.read_bytes(),[self.before[n],self.after[n]] if phase=='mixed' else [(self.before if phase=='before' else self.after)[n]])
    def db(self,command):
        if command=='observe':return self.state
        self.sql.append(command)
        if command=='delist':self.assertEqual(self.state,'before');self.state='after'
        elif command=='restore':self.assertEqual(self.state,'after');self.state='before'
        else:raise AssertionError(command)
    def guard(self,mode,fd):
        self.assertEqual(os.fstat(fd).st_ino,self.lock.stat().st_ino)
        self.events.append('guard:'+mode)
        if mode=='eligible' and (self.folder/'journal.json').exists():
            phase=json.loads((self.folder/'journal.json').read_bytes())['phase']
            if phase in ('DELIST_VERIFY_INTENT','COMMITTED'):self.assertEqual(self.state,'after')
            else:self.assertEqual(self.state,'before')
    def supervise(self,mode,fd):
        self.events.append('watchdog:'+mode)
        if mode=='stop':self.stopped=True
        if mode=='start':self.guard('eligible',fd);self.stopped=False
        if mode=='status':self.assertFalse(self.stopped)
    def run_tx(self,**kw):return v.visibility_execute(self.rt,self.ident,database=self.db,guard=self.guard,supervise=self.supervise,**kw)
    def fail(self,label):
        def fault(at):
            if at==label:raise InterruptedError(label)
        return fault

    def test_success_and_lost_terminal_receipt_do_not_repeat_sql(self):
        self.assertEqual('COMMITTED',self.run_tx());self.assertEqual(['delist'],self.sql)
        self.assertEqual('COMMITTED_VERIFIED_NOOP',self.run_tx());self.assertEqual(['delist'],self.sql)
        self.assertLess(self.events.index('guard:eligible'),self.events.index('watchdog:start'))

    def test_partial_control_install_resumes_only_exact_transaction(self):
        with self.assertRaises(InterruptedError):self.run_tx(fault=self.fail('after:vmm_controller.py'))
        self.assertEqual([],self.sql);self.assertTrue(self.stopped)
        self.assertEqual('COMMITTED',self.run_tx(recover=True));self.assertEqual(['delist'],self.sql)

    def test_lost_sql_commit_receipt_is_read_back_not_replayed(self):
        with self.assertRaises(InterruptedError):self.run_tx(fault=self.fail('after:sql'))
        self.assertEqual('after',self.state)
        self.assertEqual('COMMITTED',self.run_tx(recover=True));self.assertEqual(['delist'],self.sql)

    def test_uncommitted_uncertain_intent_is_retired_not_replayed(self):
        with self.assertRaises(InterruptedError):self.run_tx(fault=self.fail('before:sql'))
        self.assertEqual('RESTORED',self.run_tx(recover=True));self.assertEqual([],self.sql)
        for n,(p,_,_) in v.visibility_paths(self.rt).items():self.assertEqual(self.before[n],p.read_bytes())
        self.assertEqual('RESTORED_VERIFIED_NOOP',self.run_tx(recover=True))

    def test_exact_restoration_keeps_receipts_and_forward_records(self):
        self.run_tx();history=self.root/'forward';self.put(history,b'new user result; UNKNOWN kept')
        self.assertEqual('RESTORED',self.run_tx(restore=True));self.assertEqual(['delist','restore'],self.sql)
        self.assertEqual(b'new user result; UNKNOWN kept',history.read_bytes())
        for n,(p,_,_) in v.visibility_paths(self.rt).items():self.assertEqual(self.before[n],p.read_bytes())
        self.assertTrue((self.rt.store/'CATALOG_VISIBILITY_ACTIVE.json').exists())

    def test_interrupted_restoration_and_foreign_marker(self):
        self.run_tx()
        with self.assertRaises(InterruptedError):self.run_tx(restore=True,fault=self.fail('after:restore-sql'))
        self.assertEqual('RESTORED',self.run_tx(recover=True));self.assertEqual(['delist','restore'],self.sql)
        path=self.rt.store/'CATALOG_VISIBILITY_ACTIVE.json';self.put(path,v.encoded({'schema':v.VISIBILITY_MARKER,'approval_sha256':'b'*64}))
        with self.assertRaises(Exception):self.run_tx(recover=True,restore=True)

    def test_lost_watchdog_receipt_uses_owned_status_without_second_start(self):
        with self.assertRaises(InterruptedError):self.run_tx(fault=self.fail('after:watchdog'))
        starts=self.events.count('watchdog:start');stops=self.events.count('watchdog:stop')
        self.assertEqual('COMMITTED',self.run_tx(recover=True))
        self.assertEqual(starts,self.events.count('watchdog:start'))
        self.assertEqual(stops,self.events.count('watchdog:stop'))
        self.assertEqual(['delist'],self.sql)

    def test_mixed_recovery_does_not_call_original_policy_dependent_stop(self):
        with self.assertRaises(InterruptedError):self.run_tx(fault=self.fail('after:vmm_controller.py'))
        original=self.supervise
        def supervise(mode,fd):
            self.assertNotEqual(mode,'stop','mixed controls must use already-stopped proof')
            original(mode,fd)
        self.supervise=supervise
        self.assertEqual('COMMITTED',self.run_tx(recover=True))

    def test_uncertain_watchdog_start_intent_never_forks_again(self):
        with self.assertRaises(InterruptedError):self.run_tx(fault=self.fail('after:watchdog-intent'))
        self.assertEqual(0,self.events.count('watchdog:start'))
        with self.assertRaises(AssertionError):self.run_tx(recover=True)
        self.assertEqual(0,self.events.count('watchdog:start'))
        self.assertEqual(['delist'],self.sql)
        self.assertTrue((self.folder/'COMMITTED-watchdog-start-intent.json').exists())

    def test_original_lease_contention_refuses_before_actions(self):
        import fcntl
        with self.lock.open('rb') as f:
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(v.Refused,'shared_publication_busy'):self.run_tx()
        self.assertEqual([],self.sql);self.assertEqual([],self.events)

    def test_unknown_observation_cannot_mutate_or_resume(self):
        self.state='partial'
        with self.assertRaisesRegex(v.Refused,'unknown_database_state'):self.run_tx()
        self.assertEqual([],self.sql);self.assertNotIn('watchdog:stop',self.events)


class Derivation(unittest.TestCase):
    def test_only_exact_manifest_admitted(self):
        with self.assertRaisesRegex(v.Refused,'exact_original_manifest'):v.visibility_manifest(b'{}')
    def test_only_one_retained_table_can_change(self):
        old={'schema':'R25_CORE_RETAINED_DATA_V1','tables':{'catalog_batch_current_revisions':deepcopy(v.VISIBILITY_HEADS['before']),'other':{'rows':42,'md5':'d'*32}},'media':[{'path':'not changed'}]}
        before=deepcopy(old);after=v.visibility_retention(old)
        self.assertEqual(before,old);self.assertEqual(after['media'],old['media'])
        self.assertEqual(after['tables']['other'],old['tables']['other'])
        self.assertEqual(v.VISIBILITY_HEADS['after'],after['tables']['catalog_batch_current_revisions'])
        old['tables']['catalog_batch_current_revisions']['rows']=12
        with self.assertRaisesRegex(v.Refused,'retained_before_image'):v.visibility_retention(old)
    def test_handoff_changes_only_one_exact_digest(self):
        before=b'KEEP=unchanged\nR25_VMM_CONTROLLER_SHA256='+b'a'*64+b'\n'
        after=v.visibility_handoff(before,'b'*64)
        self.assertEqual(before.replace(b'a'*64,b'b'*64),after)
        with self.assertRaises(v.Refused):v.visibility_handoff(before+before,'b'*64)

if __name__=='__main__':unittest.main()
