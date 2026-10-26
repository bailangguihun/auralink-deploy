"""Actual finite proof/lease/CAS transactions; Java/DB are explicit CPU fixtures."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import fcntl
from pathlib import Path
import unittest
from unittest.mock import patch
import core_release_guard as guard
import upgrade_assistant_controller as u
import vmm_controller as v
import test_community_publication_successor as previous


class Repair(unittest.TestCase):
    def setUp(self):
        read=Path.read_bytes;here=Path(__file__).parent
        def prior_bytes(path):
            raw=read(path)
            return raw+b'\n# retained successor fixture identity\n' if path.parent==here and path.name in {'core_release_guard.py','vmm_controller.py','upgrade_assistant_controller.py'} else raw
        with patch.object(Path,'read_bytes',new=prior_bytes):
            parent=previous.Successor('runTest');parent.setUp();self.addCleanup(parent.doCleanups)
            parent.prepare();parent.f.install()
        self.parent=parent;self.f=f=parent.f;self.rt=parent.rt;self.root=parent.root;self.put=parent.put
        self.parent_id=parent.ident;self.inode=self.rt.lock.stat().st_ino
        f.old=deepcopy(f.new);f.oldplan=deepcopy(f.plan)
        self.tx='4b88699f583cd859506e6b175479a3b9';store=f.f.store;self.txroot=store/'transactions'/self.tx
        candidate=f.old['upgrade']['candidate'];old=asdict(f.current)
        accepted={**candidate,'schema':guard.BUSINESS_RECORD_SCHEMA,'policy_sha256':v.sha(v.encoded(f.old))}
        record_sha=self.put(store/'releases'/(candidate['artifact_sha256']+'-'+candidate['commit_sha'])/'record.json',v.encoded(accepted))
        failed=dict(commit_sha=candidate['commit_sha'],artifact_sha256=candidate['artifact_sha256'],accepted_record_sha256=record_sha)
        self.snapshot={'schema':'R25_CORE_SNAPSHOT_V1','transaction_id':self.tx,'current':old,'candidate':failed,
            'before_current':f.f.rt.read(store/'CURRENT.json')}
        snapsha=self.put(self.txroot/'snapshot.json',v.encoded(self.snapshot))
        self.put(self.txroot/'journal.json',v.encoded(dict(schema='R25_CORE_TRANSACTION_V1',transaction_id=self.tx,
            snapshot_id=self.tx,snapshot_sha256=snapsha,current=old,candidate=failed,phase='RESTORED')))
        self.put(store/'ACTIVE.json',v.encoded(dict(schema='R25_CORE_ACTIVE_V1',transaction_id=self.tx)))
        backup=self.put(self.txroot/'assistant-backup/manifest.json',v.encoded({'fixture':'verified backup metadata only'}))
        self.put(self.txroot/'assistant-journal.json',v.encoded(dict(schema='R25_ASSISTANT_SCHEMA_TRANSACTION_V1',phase='SCHEMA_READY',
            history=[dict(phase='BACKUP_VERIFIED',manifest_sha256=backup)])))
        self.put(self.txroot/'assistant-recovery.json',v.encoded(dict(outcome='FORWARD_SCHEMA_PRESERVED',database_restore=False,automatic_replay=False)))
        self.put(self.txroot/'http-acceptance.json',v.encoded({'restored':True}))
        f.original_snapshot=f.snapshot();self.original=f.snapshot()
        self.proofs={str(p):p.read_bytes() for base in (self.rt.store,store/'controller-upgrades',self.txroot) for p in base.rglob('*') if p.is_file()}
        f.f.rt.events.clear();f.overlay_events.clear()

    def prepare(self,**kw):
        result=self.f.prepare(publication_repair=True,**kw);self.plan=self.f.plan;self.ident=result['plan_sha256']
        self.put(self.f.f.store/'publication-approvals'/(self.ident+'.json'),v.encoded(self.plan));self.f.ident=self.ident
        return result

    def runtime(self):return self.parent.runtime()
    def preserved(self):
        for path,raw in self.proofs.items():self.assertEqual(raw,Path(path).read_bytes(),path)
        for name in ('lifecycle.json','gate.json','consumed-ledger.json'):
            p=self.root/'runtime/vmm'/name;self.assertEqual(self.original[str(p)][0],p.read_bytes())
        self.assertEqual(self.inode,self.rt.lock.stat().st_ino)

    def test_supported_restored_successor_preserves_all_proofs_and_scope(self):
        self.prepare();self.assertEqual(u.PUBLICATION_REPAIR_SCHEMA,self.plan['schema'])
        self.assertEqual('INSTALLED',self.f.install());self.runtime();self.preserved()
        self.assertEqual('INSTALLED_VERIFIED_NOOP',self.f.install());self.assertEqual('INSTALLED_NO_RECOVERY',self.f.recover())
        for name in ('vmm_lifecycle.py','vmm_service.py','core_runtime.py','lifecycle','role'):
            self.assertEqual(*u.entries(self.plan)[name].values())
        self.assertEqual(['stop-watchdog','start-watchdog'],self.f.f.rt.events)

    def test_real_current_record_guard_during_install_and_receipt_reconciliation(self):
        # Exercise the original accepted-record verifier at the actual install
        # boundary. The older CPU process fixture checked only policy/file SHA.
        # No accepted record, policy identity or guard predicate is fabricated
        # to make this boundary pass; all come from the preceding transactions.
        self.prepare()
        with self.real_current_record_guard() as seen:
            self.assertEqual('INSTALLED',self.f.install())
            self.assertEqual('INSTALLED_VERIFIED_NOOP',self.f.install())
            self.assertEqual('INSTALLED_NO_RECOVERY',self.f.recover())
        self.assertGreaterEqual(len(seen),3)
        self.assertEqual({self.f.old['upgrade']['assistant']['baseline_policy_sha256']},set(seen))
        self.assertNotEqual(self.f.new['upgrade']['assistant']['baseline_policy_sha256'],
            self.f.new['upgrade']['core_followup']['predecessor_policy_sha256'])
        self.runtime();self.preserved()

    @contextmanager
    def real_current_record_guard(self):
        runtime=self.f.f.rt;verify=runtime.verify_current
        seen=[]
        def verify_current(current):
            verify(current)
            policy=runtime.read(self.f.f.store/'POLICY.json')
            with patch.object(guard,'policy',return_value=policy):
                record=guard.release_record(current.artifact_sha256,
                    current.accepted_record_sha256,current.commit_sha)
            seen.append(record['policy_sha256'])
        with patch.object(runtime,'verify_current',side_effect=verify_current):
            yield seen

    def test_baseline_drift_refused_by_preflight_and_retained_context(self):
        import core_business as business
        self.prepare()
        for key in ('baseline_policy_sha256','baseline_payload_fingerprint'):
            for wrong in ('f'*64,self.plan['operational']['policy']['before_sha256']):
                bad=deepcopy(self.f.new);bad['upgrade']['assistant'][key]=wrong
                with self.subTest(key=key,wrong=wrong):
                    with self.assertRaisesRegex(Exception,'managed_successor_accepted_baseline_binding'):
                        u.validate_managed_successor_bindings(self.f.f.rt,self.plan,self.f.old,bad,self.f.bundle)
                    with self.assertRaisesRegex(Exception,'publication_repair_accepted_baseline_binding'):
                        guard.verify_core_followup_context(bad,self.f.old,business)
        self.assertEqual([],self.f.f.rt.events);self.preserved()

    def test_real_record_drift_cannot_be_hidden_by_matching_proposed_policy(self):
        self.prepare();current=self.f.current
        path=self.f.f.store/'releases'/(current.artifact_sha256+'-'+current.commit_sha)/'record.json'
        raw=path.read_bytes()
        try:
            self.put(path,raw+b' ')
            with self.real_current_record_guard(),self.assertRaises(Exception):self.f.install()
            self.assertEqual([],self.f.f.rt.events)
        finally:self.put(path,raw)
        with patch.object(guard,'policy',return_value=self.f.new):
            with self.assertRaisesRegex(Exception,'assistant_verified_v2_rollback_record'):
                guard.release_record(current.artifact_sha256,None,current.commit_sha)
        self.preserved()

    def test_fresh_plan_after_restored_binding_preserves_consumed_failure(self):
        self.prepare();first_plan=deepcopy(self.plan);first_id=self.ident
        storage=self.f.f.rt.storage;copy=storage.atomic_copy_file
        def fail(src,dst,*args,**kwargs):
            value=copy(src,dst,*args,**kwargs)
            if Path(dst)==self.f.f.store/'POLICY.json':raise InterruptedError('fixture policy receipt loss')
            return value
        with patch.object(storage,'atomic_copy_file',side_effect=fail):
            with self.assertRaises(InterruptedError):self.f.install()
        with self.real_current_record_guard():self.assertEqual('RESTORED',self.f.recover())
        folder=self.f.f.store/'controller-upgrades'/first_id
        consumed={p:p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        self.prepare(reuse_candidate=True);self.assertNotEqual(first_id,self.ident)
        with self.real_current_record_guard():self.assertEqual('INSTALLED',self.f.install())
        for path,raw in consumed.items():self.assertEqual(raw,path.read_bytes())
        with self.assertRaisesRegex(Exception,'recovery_required'):
            u.install(self.f.f.rt,first_plan,self.f.bundle,approval_sha256=first_id)
        self.runtime();self.preserved()

    def test_wrong_restored_phase_current_candidate_and_recovery_refuse(self):
        paths=[(self.txroot/'journal.json','phase','COMMITTED'),(self.txroot/'journal.json','transaction_id','0'*32),
               (self.txroot/'assistant-recovery.json','database_restore',True),
               (self.txroot/'assistant-recovery.json','automatic_replay',True),
               (self.f.f.store/'ACTIVE.json','transaction_id',self.f.coretx)]
        for path,key,value in paths:
            with self.subTest(key=key):
                raw=path.read_bytes();row=self.f.f.rt.read(path);row[key]=value;self.put(path,v.encoded(row))
                with self.assertRaises(Exception):v.restored_core_evidence(self.rt,self.f.old)
                self.put(path,raw)
        self.assertEqual([],self.f.f.rt.events);self.preserved()

    def test_actual_guard_and_later_core_commit_keep_failed_transaction_immutable(self):
        from functools import partial
        import core_business as business
        self.prepare();self.f.install();source=guard.source_module
        def local_source(path,name,raw):
            module=source(path,name,raw)
            if Path(path).name=='vmm_controller.py':
                owner=tuple(self.plan['private_owner']);module.LOCK=self.rt.lock
                module.Runtime=partial(module.Runtime,private=owner,scripts=owner,supervise=self.f.overlay_events.append)
            return module
        with patch.object(guard,'business_module',return_value=business),patch.object(business,'validate_policy',side_effect=lambda value:value),patch.object(guard,'source_module',side_effect=local_source):
            self.assertEqual(self.f.new['upgrade'],guard.policy()['upgrade'])
            candidate=self.f.new['upgrade']['candidate'];tx='c'*32;store=self.f.f.store
            record={**candidate,'schema':guard.BUSINESS_RECORD_SCHEMA,'policy_sha256':self.plan['operational']['policy']['after_sha256']}
            digest=self.put(store/'releases'/(candidate['artifact_sha256']+'-'+candidate['commit_sha'])/'record.json',v.encoded(record))
            current=dict(commit_sha=candidate['commit_sha'],artifact_sha256=candidate['artifact_sha256'],accepted_record_sha256=digest)
            self.put(store/'CURRENT.json',v.encoded(dict(schema='R25_CORE_CURRENT_V1',transaction_id=tx,release=current)))
            self.put(store/'ACTIVE.json',v.encoded(dict(schema='R25_CORE_ACTIVE_V1',transaction_id=tx)))
            self.put(store/'transactions'/tx/'journal.json',v.encoded(dict(transaction_id=tx,phase='COMMITTED',candidate=current)))
            self.assertEqual(self.f.new['upgrade'],guard.policy()['upgrade'])
            old=(self.txroot/'journal.json').read_bytes();self.put(self.txroot/'journal.json',old+b' ')
            with self.assertRaisesRegex(Exception,'restored_proof_drift'):guard.policy()
            self.put(self.txroot/'journal.json',old)
        self.runtime();self.preserved()

    def test_repeat_repair_and_old_candidate_are_not_supported(self):
        self.prepare()
        new=deepcopy(self.f.new);new['upgrade']['candidate']=deepcopy(self.f.old['upgrade']['candidate'])
        with self.assertRaisesRegex(Exception,'requires_new_candidate'):
            guard.verify_core_followup_context(new,self.f.old,__import__('core_business'))
        self.f.install()
        from upgrade_core_controller import upgrade_lease
        with upgrade_lease(self.f.f.rt):
            self.rt.fd=self.f.f.rt.fd
            with self.assertRaisesRegex(Exception,'already_bound'):v.capture_publication_repair(self.rt,self.plan['vmm']['deploy'])
        self.preserved()

    def test_wrong_predecessor_and_lease_contention(self):
        path=self.rt.store/'CORE_PUBLICATION_SUCCESSOR_ACTIVE.json';raw=path.read_bytes()
        self.put(path,v.encoded(dict(schema=v.PUBLICATION_SUCCESSOR_MARKER,approval_sha256='f'*64)))
        with self.assertRaises(Exception):self.prepare()
        self.put(path,raw);self.prepare(reuse_candidate=True)
        with self.rt.lock.open('rb') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(Exception,'busy'):self.f.install()
        self.assertEqual([],self.f.f.rt.events);self.preserved()

    def test_drift_rejected_before_watchdog_pause(self):
        self.prepare()
        paths=[self.txroot/'journal.json',self.txroot/'assistant-recovery.json',self.f.f.store/'CURRENT.json',
               self.f.f.store/'controller-upgrades'/self.parent_id/'before/policy',
               self.root/'operations/lifecycle_adapter.py',self.root/'runtime/config/vmm-token']
        for path in paths:
            raw=path.read_bytes();mode=path.stat().st_mode&0o777
            with self.subTest(path=path):
                self.put(path,raw+b' ',mode)
                with self.assertRaises(Exception):self.f.install()
                self.put(path,raw,mode)
        self.assertEqual([],self.f.f.rt.events);self.preserved()

    def test_partial_install_and_interrupted_recovery_consumes_new_approval(self):
        self.prepare();storage=self.f.f.rt.storage;copy=storage.atomic_copy_file
        def fail(src,dst,*a,**kw):
            result=copy(src,dst,*a,**kw)
            if Path(dst)==self.f.f.store/'POLICY.json':raise InterruptedError('fixture copy receipt lost')
            return result
        with patch.object(storage,'atomic_copy_file',side_effect=fail):
            with self.assertRaises(InterruptedError):self.f.install()
        with self.assertRaisesRegex(Exception,'recovery_required'):self.f.install()
        with patch.object(storage,'atomic_copy_file',side_effect=fail):
            with self.assertRaises(InterruptedError):self.f.recover()
        self.assertEqual('RESTORED',self.f.recover());self.preserved()
        self.assertEqual('RESTORED_NO_RECOVERY',self.f.recover())
        with self.assertRaisesRegex(Exception,'recovery_required'):self.f.install()
        self.assertFalse((self.rt.store/'CORE_PUBLICATION_REPAIR_ACTIVE.json').exists())

    def test_lost_installed_receipt_preserves_forward_unknown(self):
        self.prepare();write=self.f.f.rt.write
        def fail(path,value):
            result=write(path,value)
            if value.get('phase')=='INSTALLED':raise InterruptedError('fixture lost installed receipt')
            return result
        with patch.object(self.f.f.rt,'write',side_effect=fail):
            with self.assertRaises(InterruptedError):self.f.install()
        self.runtime();self.preserved()
        path=self.root/'runtime/vmm/gate.json';raw=v.encoded({'mode':'CLOSED','requests':{'new':{'state':'UNKNOWN'}}})
        self.put(path,raw)
        self.assertEqual('INSTALLED_NO_RECOVERY',self.f.recover());self.assertEqual(raw,path.read_bytes())
        self.assertEqual('INSTALLED_VERIFIED_NOOP',self.f.install());self.assertEqual(raw,path.read_bytes())

    def test_control_pointer_receipt_loss_and_foreign_cas(self):
        self.prepare();storage=self.f.f.rt.storage;write=storage.atomic_write_bytes;path=self.f.f.store/'CONTROL_UPGRADE_ACTIVE.json'
        def fail(dst,*a,**kw):
            result=write(dst,*a,**kw)
            if Path(dst)==path:raise InterruptedError('fixture CAS receipt loss')
            return result
        with patch.object(storage,'atomic_write_bytes',side_effect=fail):
            with self.assertRaises(InterruptedError):self.f.install()
        raw=path.read_bytes();self.put(path,v.encoded(dict(schema='R25_CORE_CONTROLLER_UPGRADE_ACTIVE_V1',approval_sha256='f'*64)))
        with self.assertRaises(Exception):self.f.recover()
        self.put(path,raw);self.assertEqual('RESTORED',self.f.recover());self.preserved()

    def test_marker_receipt_loss_recovers_only_this_unfinished_binding(self):
        self.prepare();storage=self.f.f.rt.storage;write=storage.atomic_write_bytes
        marker=self.rt.store/'CORE_PUBLICATION_REPAIR_ACTIVE.json'
        def fail(dst,*a,**kw):
            result=write(dst,*a,**kw)
            if Path(dst)==marker:raise InterruptedError('fixture lost marker receipt')
            return result
        with patch.object(storage,'atomic_write_bytes',side_effect=fail):
            with self.assertRaises(InterruptedError):self.f.install()
        self.assertTrue(marker.exists())
        self.assertEqual('RESTORED',self.f.recover());self.assertFalse(marker.exists());self.preserved()


class CompletePolicy(unittest.TestCase):
    def test_finite_policy_schema_and_unchanged_configuration(self):
        import test_managed_vmm_successor_policy as fixture
        import core_business as business
        case=fixture.ManagedSuccessorFullPolicy('runTest');case.setUp();self.addCleanup(case.doCleanups)
        value=deepcopy(case.policy);value['upgrade']['core_followup']['schema']='R25_MANAGED_VMM_CORE_PUBLICATION_REPAIR_V1'
        business.validate_policy(value)
        self.assertEqual(case.raw,business.render_configuration(case.raw,value))
        value['upgrade']['core_followup']['schema']='R25_MANAGED_VMM_CORE_PUBLICATION_REPAIR_V2'
        with self.assertRaises(Exception):business.validate_policy(value)


if __name__=='__main__':unittest.main()
