"""Actual isolated Node swap, preserved histories, failure and lost-reply paths."""
from copy import deepcopy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile
from community_host import PackageUpgrade, encoded, identity, sha
import test_community_recovery as fixture
import community_host as module
import test_community_host as host_fixture


class Package(unittest.TestCase):
    def setUp(self):
        f=fixture.Recovery('runTest');self.addCleanup(f.doCleanups);f.setUp();self.f=f
        f.tick();f.stable();self.h=f.h
        self.service=PackageUpgrade(self.h,f.supervisor(),dict(core_commit='b'*40,studio_approval_sha256='c'*64))
        self.addCleanup(self.clean_candidates)
        base=self.h.folder/'candidate';base.mkdir(mode=0o700)
        source=self.h.folder/'release';m=json.loads((source/'manifest.json').read_bytes());m.update(commit='b'*40,run_id='2')
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as archive:
            for item in m['files']:
                raw=(source/item['path']).read_bytes()
                if item['path']=='src/main.mjs':raw+=b'\n// reviewed replacement fixture\n'
                item.update(bytes=len(raw),sha256=sha(raw))
                info=zipfile.ZipInfo(item['path']);info.create_system=3;info.external_attr=0o100600<<16
                archive.writestr(info,raw)
        raw=out.getvalue();m['archive_sha256']=sha(raw)
        self.archive=base/'community.zip';self.archive.write_bytes(raw);self.archive.chmod(0o600)
        self.manifest=base/'manifest.json';self.manifest.write_bytes(encoded(m));self.manifest.chmod(0o600)
        self.provenance={k:m[k] for k in ('commit','run_id','run_attempt')}
        with self.f.f.lease.held():
            self.plan,path=self.service.prepare(self.archive,self.manifest,self.provenance,'d'*64)
            self.folder=path.parent;self.h.storage.atomic_write_bytes(self.folder/'approval.json',encoded(self.plan))
        self.originals={p:p.read_bytes() for p in self.h.folder.rglob('*') if p.is_file() and
                        ('release' in p.parts or p.name in ('approval.json','watchdog-state.json'))}

    def clean_candidates(self):
        for h in self.service.candidates.values():
            if h._role:
                with self.f.f.lease.held():h.role().stop(0)
                for child in h.role().children.values():
                    if child.poll() is None:child.terminate()
                    child.wait(timeout=5)

    def install(self,**kw):
        with self.f.f.lease.held():return self.service.install(self.plan,self.archive,self.manifest,**kw)

    def preserved(self):
        for p,raw in self.originals.items():self.assertEqual(raw,p.read_bytes(),str(p))
        self.f.assert_legacy_preserved()

    def test_successful_swap_keeps_legacy_data_and_new_v2_supervision(self):
        old=self.h.role().current()[1]['process']['pid']
        self.assertEqual(self.install(),'COMMITTED')
        with self.f.f.lease.held():
            fresh=PackageUpgrade(self.h,self.f.supervisor(),self.service.context)
            h,s=fresh.active();result=h.preserve_running(s)
            self.assertNotEqual(old,result['process']['pid'])
            self.assertEqual(result['process']['cwd'],str(h.release/'release'))
            self.assertEqual(s.tick(),'READY')
            self.assertEqual(len(s.load()['attempts']),1)
        self.assertEqual(self.install(),'COMMITTED_VERIFIED_NOOP');self.preserved()

    def test_owned_candidate_failure_restores_original_with_new_audit_record(self):
        def fail(phase):
            if phase=='STARTED':raise InterruptedError('fixture process start reply lost')
        with self.assertRaises(InterruptedError):self.install(fault=fail)
        self.assertEqual(self.install(recover=True),'RESTORED')
        with self.f.f.lease.held():self.h.preserve_running(self.f.supervisor())
        self.assertFalse(self.service.marker.exists());self.preserved()
        self.assertEqual(len(self.f.supervisor().load()['resumptions']),1)

    def test_activation_receipt_loss_reconciles_without_new_spawn(self):
        original=self.h.storage.atomic_write_json
        def fail(path,*a,**kw):
            result=original(path,*a,**kw)
            if Path(path)==self.service.marker:raise InterruptedError('fixture lost activation reply')
            return result
        with patch.object(self.h.storage,'atomic_write_json',side_effect=fail),self.assertRaises(InterruptedError):self.install()
        record=self.h.read(self.folder/'journal.json');operation=record['operation_id']
        self.assertEqual(self.install(recover=True),'COMMITTED_RECONCILED')
        with self.f.f.lease.held():h,s=self.service.active();self.assertEqual(s.load()['attempts'],[operation])
        self.preserved()

    def test_missing_spawn_receipt_never_replays_or_starts_old_process(self):
        def fail(phase):
            if phase=='START_INTENT':raise InterruptedError('unknown start boundary')
        with self.assertRaises(InterruptedError):self.install(fault=fail)
        with self.assertRaisesRegex(Exception,'unknown_start_receipt'):self.install(recover=True)
        self.assertFalse(self.service.marker.exists())
        self.assertEqual(self.h.read(self.folder/'journal.json')['phase'],'START_INTENT');self.preserved()

    def test_activation_reply_loss_then_recorded_exit_uses_normal_v2_recovery(self):
        original=self.h.storage.atomic_write_json
        def fail(path,*a,**kw):
            result=original(path,*a,**kw)
            if Path(path)==self.service.marker:raise InterruptedError('lost activation reply')
            return result
        with patch.object(self.h.storage,'atomic_write_json',side_effect=fail),self.assertRaises(InterruptedError):self.install()
        candidate=self.service.candidate(self.plan);role=candidate.role()
        pid=role.current()[1]['process']['pid'];child=role.children[pid]
        child.terminate();child.wait(timeout=5)
        self.assertEqual(self.install(recover=True),'COMMITTED_RECONCILED_RESTART_PENDING')
        with self.f.f.lease.held():
            h,s=self.service.active()
            self.assertEqual(s.tick(),'STARTING')
            self.assertEqual(len(s.load()['attempts']),2)
        self.preserved()

    def test_foreign_archive_or_preimage_is_rejected_before_drain(self):
        self.archive.write_bytes(self.archive.read_bytes()+b'foreign')
        with self.assertRaisesRegex(Exception,'identity_drift'):self.install()
        with self.f.f.lease.held():self.h.preserve_running(self.f.supervisor())
        self.assertFalse((self.folder/'journal.json').exists());self.preserved()

    def test_interruption_before_drain_stops_restart_decisions_then_restores(self):
        def fail(phase):
            if phase=='DRAIN_INTENT':raise InterruptedError('before old supervision drain')
        with self.assertRaises(InterruptedError):self.install(fault=fail)
        with self.f.f.lease.held(),self.assertRaisesRegex(Exception,'unfinished_predecessor'):
            self.service.prepare(self.archive,self.manifest,self.provenance,'e'*64)
        self.assertEqual(self.install(recover=True),'RESTORED')
        with self.f.f.lease.held():self.h.preserve_running(self.f.supervisor())
        self.preserved()

    def test_new_approved_attempt_retains_failed_package_process_and_budget(self):
        def fail(phase):
            if phase=='STARTED':raise InterruptedError('isolated first package failure')
        with self.assertRaises(InterruptedError):self.install(fault=fail)
        self.assertEqual(self.install(recover=True),'RESTORED')
        previous=self.service.candidate(self.plan)
        retained={p:p.read_bytes() for p in previous.release.rglob('*') if p.is_file()}
        with self.f.f.lease.held():
            plan,path=self.service.prepare(self.archive,self.manifest,self.provenance,'e'*64)
            self.h.storage.atomic_write_bytes(path.parent/'approval.json',encoded(plan))
            self.assertNotEqual(previous.release,self.service.candidate(plan).release)
            self.assertEqual(self.service.install(plan,self.archive,self.manifest),'COMMITTED')
        for p,raw in retained.items():self.assertEqual(raw,p.read_bytes(),str(p))
        self.preserved()


class CoreContext(unittest.TestCase):
    def setUp(self):
        f=host_fixture.HostTests('runTest');self.addCleanup(f.doCleanups);f.setUp();self.f=f
        self.root=f.f.base/'production-context';self.base=self.root/'runtime/core-publications'
        self.root.mkdir(mode=0o700)
        p=patch.object(module,'ROOT',self.root);p.start();self.addCleanup(p.stop)
        self.candidate=dict(commit_sha='b'*40,artifact_sha256='c'*64)
        self.current=dict(transaction_id='d'*32,release=self.candidate)
        self.policy=dict(upgrade=dict(candidate=self.candidate))
        self.approval=dict(schema='R25_STUDIO_CORE_PUBLICATION_BINDING_V1',
                           operational=dict(policy=dict(after_sha256=sha(encoded(self.policy)))))
        self.ident=sha(encoded(self.approval));self.folder=self.base/'controller-upgrades'/self.ident
        self.put(self.folder/'approval.json',self.approval)
        self.put(self.folder/'journal.json',dict(schema=self.approval['schema'],approval_sha256=self.ident,phase='INSTALLED'))
        self.put(self.base/'POLICY.json',self.policy);self.put(self.base/'CURRENT.json',self.current)
        self.receipt=self.base/'transactions'/('d'*32)/'journal.json'
        self.record=dict(transaction_id='d'*32,phase='COMMITTED',candidate=self.candidate)
        self.put(self.receipt,self.record)

    def put(self,path,value):
        path.parent.mkdir(parents=True,mode=0o700,exist_ok=True);path.write_bytes(encoded(value));path.chmod(0o600)

    def context(self):
        with self.f.lease.held():return module.package_context(self.f.host,self.ident)

    def test_actual_current_and_committed_receipt_required(self):
        result=self.context();self.assertEqual(result['core_commit'],'b'*40)
        self.put(self.receipt,dict(self.record,phase='RESTORED'))
        with self.assertRaisesRegex(Exception,'core_not_committed'):self.context()
        self.put(self.receipt,self.record)
        self.put(self.base/'CURRENT.json',dict(self.current,release=dict(self.candidate,commit_sha='e'*40)))
        with self.assertRaisesRegex(Exception,'new_core_required'):self.context()

    def test_drifted_policy_refuses_without_package_or_process_change(self):
        self.put(self.base/'POLICY.json',dict(self.policy,foreign=True))
        with self.assertRaisesRegex(Exception,'context_drift'):self.context()
        self.assertFalse(self.f.host.root.exists())


if __name__=='__main__':unittest.main()
