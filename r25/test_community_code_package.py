"""Real isolated Node/flock and exact packages; explicit in-memory DB fixture.

The retained Reference fixture first migrates its fake DB once. These tests
exercise the subsequent code-only transaction without any SQL migration.
"""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import unittest
from unittest.mock import patch
import zipfile

from community_host import CodePackageUpgrade,encoded,sha
import test_community_reference_upgrade as reference_fixture


class CodePackage(unittest.TestCase):
    def setUp(self):
        self.f=reference_fixture.ReferenceUpgrade('runTest')
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        self.assertEqual(self.f.install(),'COMMITTED')
        self.h=self.f.host;self.lease=self.f.lease
        with self.lease.held():
            self.old,self.supervisor=self.f.service.active()
            self.old.control('stop')
            deadline=time.monotonic()+5
            while self.old.control('observe') not in ('STOPPED','EXITED'):
                self.assertLess(time.monotonic(),deadline);time.sleep(.01)
            state=self.supervisor.load();state['attempts'].append('8'*32)
            state.update(stable=None,unhealthy=None,reason='START_DECISION')
            self.h.write(self.supervisor.state,state)
        files={}
        with zipfile.ZipFile(self.f.archive) as archive:
            for name in archive.namelist():files[name]=archive.read(name)
        files['src/main.mjs']+=b'\n// Explicit code-only successor fixture.\n'
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as archive:
            for name,raw in sorted(files.items()):
                info=zipfile.ZipInfo(name);info.create_system=3;info.external_attr=0o100600<<16
                archive.writestr(info,raw)
        raw=data.getvalue();value=json.loads(self.f.manifest.read_bytes())
        value.update(commit='6'*40,run_id='4',archive_sha256=sha(raw),
                     files=[{'path':name,'bytes':len(body),'sha256':sha(body)}for name,body in sorted(files.items())])
        self.archive=self.h.folder/'code-fixture.zip';self.manifest=self.h.folder/'code-fixture-manifest.json'
        self.h.storage.atomic_write_bytes(self.archive,raw)
        self.h.storage.atomic_write_bytes(self.manifest,encoded(value))
        source={k:value[k]for k in ('commit','run_id','run_attempt','archive_sha256')}
        source['manifest_sha256']=sha(encoded(value))
        self.code=CodePackageUpgrade(self.f.service,{'code_controller_sha256':'7'*64,
                    'core_runtime':{'fixture_only':'old Java unchanged'},'community_source_provenance':source})
        self.addCleanup(self.cleanup)
        with self.lease.held():
            self.plan,unsigned=self.code.prepare(self.archive,self.manifest,'5'*64)
            self.folder=unsigned.parent
            self.h.storage.atomic_write_bytes(self.folder/'approval.json',encoded(self.plan))
        self.preserved={path:path.read_bytes()for path in self.old.release.rglob('*')if path.is_file()}
        self.preserved[self.f.service.marker]=self.f.service.marker.read_bytes()
        self.db_before=deepcopy(self.f.database_state.current)

    def cleanup(self):
        for host in self.code.candidates.values():
            if host._role:
                with self.lease.held():host.role().stop(0)
                for child in host.role().children.values():
                    if child.poll() is None:child.terminate()
                    child.wait(timeout=5)

    def install(self,**kwargs):
        with self.lease.held():return self.code.install(self.plan,self.archive,self.manifest,**kwargs)

    def retained(self):
        for path,raw in self.preserved.items():self.assertEqual(path.read_bytes(),raw,str(path))
        self.assertEqual(self.db_before,self.f.database_state.current)
        self.assertEqual(1,self.f.database_state.migrations)

    def test_code_only_commit_retires_without_replaying_and_keeps_remaining_budget(self):
        old=deepcopy(self.plan['before']['state'])
        self.assertEqual(self.install(),'COMMITTED')
        with self.lease.held():
            candidate,supervisor=self.code.active();state=supervisor.load()
            self.assertEqual(candidate.control('observe'),'READY')
            self.assertEqual(state['attempts'][:-1],old['attempts'])
            self.assertEqual(state['budget_start'],old['budget_start'])
            self.assertNotEqual(state['attempts'][-1],'8'*32)
            self.assertEqual(len(candidate.role().children),1)
        disposition=self.h.read(self.folder/'decision-retirement.json')
        self.assertEqual(disposition['decision'],'8'*32)
        self.assertFalse((self.old.role().root/('run-'+'8'*32+'.json')).exists())
        self.assertEqual(self.install(),'COMMITTED_VERIFIED_NOOP')
        self.retained()

    def interrupt_before_spawn(self,target):
        def fail(phase):
            if phase==target:raise InterruptedError(target)
        with self.assertRaises(InterruptedError):self.install(fault=fail)
        self.assertEqual(self.install(recover=True),'COMMITTED')
        self.assertEqual(len(self.code.candidate(self.plan).role().children),1)
        self.retained()

    def test_prepared_receipt_can_resume_without_replaying_old_decision(self):
        self.interrupt_before_spawn('PREPARED')

    def test_materialized_receipt_can_resume_without_replaying_old_decision(self):
        self.interrupt_before_spawn('MATERIALIZED')

    def test_retired_receipt_can_resume_without_replaying_old_decision(self):
        self.interrupt_before_spawn('RETIRED')

    def test_intent_without_process_receipt_is_never_replayed(self):
        def fail(phase):
            if phase=='START_INTENT':raise InterruptedError('before spawn')
        with self.assertRaises(InterruptedError):self.install(fault=fail)
        with self.assertRaisesRegex(Exception,'unknown_start_no_replay'):self.install(recover=True)
        self.assertEqual(self.code.candidate(self.plan).role().children,{})
        self.retained()

    def test_lost_spawn_reply_recovers_owned_process_without_second_spawn(self):
        candidate=self.code.candidate(self.plan);start=candidate.role().start
        def lost(operation):
            start(operation);raise InterruptedError('lost owned start reply')
        with patch.object(candidate.role(),'start',side_effect=lost),self.assertRaises(InterruptedError):self.install()
        self.assertEqual(len(candidate.role().children),1)
        self.assertEqual(self.install(recover=True),'COMMITTED')
        self.assertEqual(len(candidate.role().children),1);self.retained()

    def test_durable_activation_receipt_loss_finishes_same_operation(self):
        def fail(phase):
            if phase=='ACTIVATE_INTENT':raise InterruptedError('activation')
        with self.assertRaises(InterruptedError):self.install(fault=fail)
        self.assertEqual(self.install(recover=True),'COMMITTED')
        self.assertEqual(len(self.code.candidate(self.plan).role().children),1);self.retained()

    def test_foreign_listener_and_related_non_listening_node_refuse(self):
        listener=socket.socket();listener.bind(('127.0.0.1',self.old.binding()['port']));listener.listen()
        try:
            with self.assertRaisesRegex(Exception,'listener_alive'):self.install()
        finally:listener.close()
        child=subprocess.Popen([self.old.binding()['node'],'-e','setInterval(()=>{},1000)'],
                               cwd=str(self.old.source_release),env={},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            time.sleep(.05)
            with self.assertRaisesRegex(Exception,'surviving_process'):self.install()
        finally:child.terminate();child.wait(timeout=5)
        self.assertFalse((self.folder/'journal.json').exists());self.retained()

    def test_source_or_frozen_predecessor_drift_refuses(self):
        path=self.supervisor.state;raw=path.read_bytes();self.h.write(path,{**self.h.read(path),'reason':'FOREIGN'})
        try:
            with self.assertRaises(Exception):self.install()
        finally:self.h.write(path,json.loads(raw))
        raw=self.archive.read_bytes();self.archive.write_bytes(raw+b'foreign')
        try:
            with self.assertRaises(Exception):self.install()
        finally:self.archive.write_bytes(raw)
        self.assertEqual(self.install(),'COMMITTED');self.retained()

    def test_retired_recovery_rechecks_survivors_before_new_intent(self):
        def fail(phase):
            if phase=='RETIRED':raise InterruptedError('retired')
        with self.assertRaises(InterruptedError):self.install(fault=fail)
        child=subprocess.Popen([self.old.binding()['node'],'-e','setInterval(()=>{},1000)'],
                               cwd=str(self.old.source_release),env={},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            time.sleep(.05)
            with self.assertRaisesRegex(Exception,'surviving_process'):self.install(recover=True)
            self.assertEqual(self.code.candidate(self.plan).role().children,{})
        finally:child.terminate();child.wait(timeout=5)
        self.assertEqual(self.install(recover=True),'COMMITTED');self.retained()

    def test_retirement_receipt_is_required_during_recovery_and_active_observation(self):
        def fail(phase):
            if phase=='RETIRED':raise InterruptedError('retired')
        with self.assertRaises(InterruptedError):self.install(fault=fail)
        path=self.folder/'decision-retirement.json';raw=path.read_bytes();path.unlink()
        with self.assertRaises(Exception):self.install(recover=True)
        self.h.storage.atomic_write_bytes(path,raw)
        self.assertEqual(self.install(recover=True),'COMMITTED')
        path.write_bytes(raw+b' ')
        with self.lease.held(),self.assertRaises(Exception):self.code.active()
        path.write_bytes(raw)
        with self.lease.held():self.code.active()
        self.retained()

    def test_original_final_supervisor_parent_ledger_is_still_verified(self):
        path=self.supervisor.legacy_state;raw=path.read_bytes()
        self.h.write(path,{**self.h.read(path),'reason':'FOREIGN'})
        try:
            with self.assertRaisesRegex(Exception,'legacy_state_drift'):self.install()
            self.assertFalse((self.folder/'journal.json').exists())
        finally:self.h.write(path,json.loads(raw))
        self.assertEqual(self.install(),'COMMITTED');self.retained()


if __name__=='__main__':unittest.main()
