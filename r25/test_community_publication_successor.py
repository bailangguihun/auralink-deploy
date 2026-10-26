"""Finite successor through actual retained VMM/control proofs; CPU only."""
from copy import deepcopy
from dataclasses import asdict
import fcntl
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import core_runtime
import core_release_guard as guard
import upgrade_assistant_controller as u
import vmm_controller as v
import test_managed_vmm_publication as previous
import test_managed_vmm_successor_binding as fixture


class Successor(unittest.TestCase):
    def setUp(self):
        read=Path.read_bytes;here=Path(__file__).parent
        def prior_bytes(path):
            raw=read(path)
            return raw+b'\n# synthetic retained V1 publication source identity\n' if path.parent==here and path.name in {'core_release_guard.py','vmm_controller.py','upgrade_assistant_controller.py'} else raw
        with patch.object(Path,'read_bytes',side_effect=None,autospec=True) as source:
            source.side_effect=prior_bytes
            parent=previous.Publication('runTest');parent.setUp();self.addCleanup(parent.doCleanups)
            parent.prepare();parent.f.install()
        self.parent=parent;self.f=parent.f;f=self.f;self.rt=f.vrt;self.root=f.f.root;self.put=f.put
        lock_patch=patch.object(guard,'LOCK',self.rt.lock);lock_patch.start();self.addCleanup(lock_patch.stop)
        self.parent_id=parent.ident
        f.old=deepcopy(f.new);f.oldplan=deepcopy(f.plan)
        candidate=f.old['upgrade']['candidate'];incoming=self.root/'incoming'/(candidate['commit_sha']+'-888-1')
        for name in ('release-jar','preview-jar'):self.put(f.g.live[name],(incoming/fixture.C.JAR_NAME).read_bytes())
        record={**candidate,'schema':guard.BUSINESS_RECORD_SCHEMA,'policy_sha256':v.sha(v.encoded(f.old))}
        path=f.f.store/'releases'/(candidate['artifact_sha256']+'-'+candidate['commit_sha'])/'record.json'
        digest=self.put(path,v.encoded(record));f.current=core_runtime.Release(candidate['commit_sha'],candidate['artifact_sha256'],digest)
        self.put(f.f.store/'CURRENT.json',v.encoded({'schema':'R25_CORE_CURRENT_V1','transaction_id':f.coretx,'release':asdict(f.current)}))
        self.put(f.f.store/'transactions'/f.coretx/'journal.json',v.encoded({'transaction_id':f.coretx,'phase':'COMMITTED','candidate':asdict(f.current)}))
        f.original_snapshot=f.snapshot();self.original=f.snapshot()
        self.proofs={str(p):p.read_bytes() for base in (self.rt.store, f.f.store/'controller-upgrades') for p in base.rglob('*') if p.is_file()}
        self.inode=self.rt.lock.stat().st_ino
        f.f.rt.events.clear();f.overlay_events.clear()

    def prepare(self, **kw):
        result=self.f.prepare(publication_successor=True, **kw);self.plan=self.f.plan;self.ident=result['plan_sha256']
        self.put(self.f.f.store/'publication-approvals'/(self.ident+'.json'),v.encoded(self.plan));self.f.ident=self.ident
        return result

    def runtime(self):
        return v.apply_policy_overlay(self.f.new,v.env_values(self.f.g.live['handoff'].read_bytes()),self.root,fixture_runtime=self.rt)

    def preserved(self):
        for path,raw in self.proofs.items():self.assertEqual(raw,Path(path).read_bytes(),path)
        for name in ('lifecycle.json','gate.json','consumed-ledger.json'):
            p=self.root/'runtime/vmm'/name;self.assertEqual(self.original[str(p)][0],p.read_bytes())
        self.assertEqual(self.inode,self.rt.lock.stat().st_ino)

    def test_supported_successor_and_exact_history(self):
        self.prepare();self.assertEqual(u.PUBLICATION_SUCCESSOR_SCHEMA,self.plan['schema'])
        self.assertEqual('INSTALLED',self.f.install());self.runtime();self.preserved()
        self.assertEqual('INSTALLED_VERIFIED_NOOP',self.f.install())
        self.assertEqual('INSTALLED_NO_RECOVERY',self.f.recover());self.preserved()
        for name in ('vmm_lifecycle.py','vmm_service.py'):
            self.assertEqual(*self.plan['files'][name].values())
        self.assertEqual(['stop-watchdog','start-watchdog'],self.f.f.rt.events)

    def test_actual_guard_entry_and_later_core_commit_preserve_parent_proofs(self):
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
            candidate=self.f.new['upgrade']['candidate']
            record={**candidate,'schema':guard.BUSINESS_RECORD_SCHEMA,'policy_sha256':self.plan['operational']['policy']['after_sha256']}
            path=self.f.f.store/'releases'/(candidate['artifact_sha256']+'-'+candidate['commit_sha'])/'record.json'
            digest=self.put(path,v.encoded(record));current=dict(commit_sha=candidate['commit_sha'],artifact_sha256=candidate['artifact_sha256'],accepted_record_sha256=digest)
            self.put(self.f.f.store/'CURRENT.json',v.encoded(dict(schema='R25_CORE_CURRENT_V1',transaction_id=self.f.coretx,release=current)))
            self.put(self.f.f.store/'transactions'/self.f.coretx/'journal.json',v.encoded(dict(transaction_id=self.f.coretx,phase='COMMITTED',candidate=current)))
            self.assertEqual(self.f.new['upgrade'],guard.policy()['upgrade'])
            path=self.f.f.store/'controller-upgrades'/self.parent_id/'before/policy';raw=path.read_bytes()
            self.put(path,raw+b' ')
            with self.assertRaises(Exception):guard.policy()
            self.put(path,raw)
        self.runtime();self.preserved()

    def test_every_changed_control_copy_and_successor_marker_recovers(self):
        # One isolated transaction per fault. No production action or approval.
        for point in ('core_runtime.py','core_release_guard.py','vmm_controller.py','policy','guard','handoff','marker'):
            with self.subTest(point=point):
                case=Successor('runTest');case.setUp()
                try:
                    case.prepare();storage=case.f.f.rt.storage;copy=storage.atomic_copy_file;write=storage.atomic_write_bytes
                    target=u.targets(case.plan)[point][0] if point!='marker' else case.rt.store/'CORE_PUBLICATION_SUCCESSOR_ACTIVE.json'
                    def failcopy(src,dst,*a,**kw):
                        result=copy(src,dst,*a,**kw)
                        if Path(dst)==target:raise InterruptedError('fixture copy loss')
                        return result
                    def failwrite(dst,*a,**kw):
                        result=write(dst,*a,**kw)
                        if point=='marker' and Path(dst)==target:raise InterruptedError('fixture marker loss')
                        return result
                    with patch.object(storage,'atomic_copy_file',side_effect=failcopy),patch.object(storage,'atomic_write_bytes',side_effect=failwrite):
                        with self.assertRaises(InterruptedError):case.f.install()
                    self.assertEqual('RESTORED',case.f.recover());case.preserved()
                finally:case.doCleanups()

    def test_wrong_predecessor_and_original_lock_busy(self):
        path=self.rt.store/'CORE_PUBLICATION_ACTIVE.json';raw=path.read_bytes()
        self.put(path,v.encoded({'schema':v.CORE_PUBLICATION_ACTIVE_SCHEMA,'approval_sha256':'f'*64}))
        with self.assertRaises(Exception):self.prepare()
        self.put(path,raw);self.prepare(reuse_candidate=True)
        with self.rt.lock.open('rb') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(Exception,'busy'):self.f.install()
        self.assertEqual([],self.f.f.rt.events);self.preserved()

    def test_drift_and_historical_proof_changes_rejected_before_pause(self):
        self.prepare()
        paths=[self.root/'operations/lifecycle_adapter.py',self.f.f.store/'POLICY.json',
               self.rt.store/'CORE_PUBLICATION_ACTIVE.json',self.f.f.store/'controller-upgrades'/self.parent_id/'before/policy',
               self.root/'runtime/vmm/gate.json',self.root/'runtime/config/vmm-token']
        for path in paths:
            raw=path.read_bytes();mode=path.stat().st_mode&0o777
            with self.subTest(path=path):
                self.put(path,raw+b' ',mode)
                with self.assertRaises(Exception):self.f.install()
                self.put(path,raw,mode)
        self.assertEqual([],self.f.f.rt.events);self.preserved()

    def test_partial_install_and_interrupted_recovery(self):
        self.prepare();storage=self.f.f.rt.storage;copy=storage.atomic_copy_file
        def fail(src,dst,*a,**kw):
            result=copy(src,dst,*a,**kw)
            if Path(dst)==self.root/'operations/lifecycle_adapter.py':raise InterruptedError('fixture lost copy receipt')
            return result
        with patch.object(storage,'atomic_copy_file',side_effect=fail):
            with self.assertRaises(InterruptedError):self.f.install()
        with self.assertRaisesRegex(Exception,'recovery_required'):self.f.install()
        with patch.object(storage,'atomic_copy_file',side_effect=fail):
            with self.assertRaises(InterruptedError):self.f.recover()
        self.assertEqual('RESTORED',self.f.recover());self.preserved()
        self.assertEqual('RESTORED_NO_RECOVERY',self.f.recover())
        self.assertFalse((self.rt.store/'CORE_PUBLICATION_SUCCESSOR_ACTIVE.json').exists())

    def test_lost_commit_receipt_preserves_later_consumption_and_unknown(self):
        self.prepare();write=self.f.f.rt.write
        def fail(path,value):
            result=write(path,value)
            if value.get('phase')=='INSTALLED':raise InterruptedError('fixture lost commit receipt')
            return result
        with patch.object(self.f.f.rt,'write',side_effect=fail):
            with self.assertRaises(InterruptedError):self.f.install()
        self.runtime();self.preserved()
        path=self.root/'runtime/vmm/gate.json';raw=v.encoded({'mode':'CLOSED','requests':{'forward':{'state':'UNKNOWN'}}})
        self.put(path,raw)
        self.assertEqual('INSTALLED_NO_RECOVERY',self.f.recover());self.assertEqual(raw,path.read_bytes())
        self.assertEqual('INSTALLED_VERIFIED_NOOP',self.f.install());self.assertEqual(raw,path.read_bytes())

    def test_pointer_cas_loss_and_foreign_pointer_recovery_refusal(self):
        self.prepare();storage=self.f.f.rt.storage;write=storage.atomic_write_bytes
        path=self.f.f.store/'CONTROL_UPGRADE_ACTIVE.json'
        def fail(dst,*a,**kw):
            result=write(dst,*a,**kw)
            if Path(dst)==path:raise InterruptedError('fixture pointer receipt loss')
            return result
        with patch.object(storage,'atomic_write_bytes',side_effect=fail):
            with self.assertRaises(InterruptedError):self.f.install()
        raw=path.read_bytes();self.put(path,v.encoded({'schema':'R25_CORE_CONTROLLER_UPGRADE_ACTIVE_V1','approval_sha256':'f'*64}))
        with self.assertRaises(Exception):self.f.recover()
        self.put(path,raw);self.assertEqual('RESTORED',self.f.recover());self.preserved()


class CompletePolicy(unittest.TestCase):
    def test_full_policy_inventory_accepts_only_finite_schema(self):
        import test_managed_vmm_successor_policy as fixture_policy
        import core_business as business
        case=fixture_policy.ManagedSuccessorFullPolicy('runTest');case.setUp();self.addCleanup(case.doCleanups)
        value=deepcopy(case.policy);value['upgrade']['core_followup']['schema']='R25_MANAGED_VMM_CORE_PUBLICATION_SUCCESSOR_V1'
        business.validate_policy(value)
        self.assertEqual(case.raw,business.render_configuration(case.raw,value))
        value['upgrade']['core_followup']['schema']='R25_MANAGED_VMM_CORE_PUBLICATION_SUCCESSOR_V2'
        with self.assertRaises(Exception):business.validate_policy(value)


if __name__=='__main__':unittest.main()
