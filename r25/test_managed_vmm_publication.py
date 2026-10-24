"""Finite publication after real successor and V1->V2 module transactions.
Files, manifests, locks, binder and recovery are real. Only the inherited
Java/PG/OS process boundary is synthetic; no model or production IO.
"""
from copy import deepcopy
from dataclasses import asdict
import fcntl
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import core_business as business
import core_release_guard as guard
import core_runtime
import upgrade_assistant_controller as u
import vmm_controller as v
import test_managed_vmm_successor_binding as fixture
import test_vmm_module_revision as module_fixture
HERE=Path(__file__).parent

class Publication(unittest.TestCase):
    def setUp(self):
        original_read=Path.read_bytes
        changed={'core_release_guard.py','core_business.py','upgrade_assistant_controller.py'}
        def predecessor_bytes(path):
            raw=original_read(path)
            return raw+b'\n# synthetic prior reviewed controller identity\n' if path.parent==HERE and path.name in changed else raw
        patcher=patch.object(Path,'read_bytes',predecessor_bytes);patcher.start()
        self.addCleanup(patcher.stop)
        f=fixture.ManagedSuccessor('runTest');f.setUp();self.addCleanup(f.doCleanups)
        self.f=f;f.prepare();f.approve();f.install()
        f.old=deepcopy(f.new);f.oldplan=deepcopy(f.plan)
        candidate=f.old['upgrade']['candidate'];incoming=f.f.root/'incoming'/(candidate['commit_sha']+'-888-1')
        for name in ('release-jar','preview-jar'):f.put(f.g.live[name],(incoming/fixture.C.JAR_NAME).read_bytes())
        record={**candidate,'schema':guard.BUSINESS_RECORD_SCHEMA,'policy_sha256':business.sha(guard.encoded(f.old))}
        release=f.f.store/'releases'/(candidate['artifact_sha256']+'-'+candidate['commit_sha'])
        recordsha=f.put(release/'record.json',guard.encoded(record))
        f.current=core_runtime.Release(candidate['commit_sha'],candidate['artifact_sha256'],recordsha)
        f.put(f.f.store/'CURRENT.json',guard.encoded({'schema':'R25_CORE_CURRENT_V1','transaction_id':f.coretx,'release':asdict(f.current)}))
        f.put(f.f.store/'transactions'/f.coretx/'journal.json',guard.encoded({'transaction_id':f.coretx,'phase':'COMMITTED','candidate':asdict(f.current)}))
        self.rt=f.vrt;self.root=f.f.root;self.put=f.put
        p=SimpleNamespace(root=self.root,put=self.put,sources={n:(HERE/n).read_bytes() for n in v.MODULES})
        for index in (1,2):
            p.package_commit=str(index+3)*40;p.package_run=str(900+index)
            p.sources['vmm_controller.py']=(HERE/'vmm_controller.py').read_bytes()+f'\n# synthetic predecessor {index}\n'.encode()
            p.sources['vmm_service.py']=(HERE/'vmm_service.py').read_bytes()+f'\n# synthetic predecessor service {index}\n'.encode()
            ext=module_fixture.ModuleRevision.package_extension(p);controller=module_fixture.ModuleRevision.package_controller(p)
            review=v.encoded({'synthetic_only':True,'scope':'predecessor module fixture'})
            request={'schema':'R25_VMM_MODULE_REVISION_REQUEST_V2' if index==2 else 'R25_VMM_MODULE_REVISION_REQUEST_V1',
                'deploy':ext,'controller_deploy':controller,'review_evidence_sha256':v.sha(review)}
            requestid=v.sha(v.encoded(request))
            inputs=self.rt.store/'module-successor-inputs'/requestid if index==2 else self.rt.store
            self.put(inputs/('request.json' if index==2 else 'MODULE_REQUEST.json'),v.encoded(request))
            self.put(inputs/('review.json' if index==2 else 'MODULE_REVIEW.json'),review)
            prepared=v.prepare_module_revision(self.rt,requestid,successor=index==2)
            ident=prepared['plan_sha256'];plan=Path(prepared['plan_path']).read_bytes()
            self.put(self.rt.store/'module-successor-approvals'/(ident+'.json') if index==2 else self.rt.store/'MODULE_APPROVAL.json',plan)
            v.bind_module_revision(self.rt,ident)
        patcher.stop()
        self.parent=ident
        life=self.root/'runtime/vmm/lifecycle.json';state=json.loads(life.read_bytes())
        state.update(gate_commands=['1'*64],open_operations=['2'*64],gate_windows={'3'*64:{'command_sha256':'1'*64}})
        self.put(life,v.encoded(state))
        f.original_snapshot=f.snapshot();self.original=f.snapshot();self.lock_inode=self.rt.lock.stat().st_ino
        self.proofs={str(p):p.read_bytes() for base in (self.rt.store/'module-revisions',self.rt.store/'transactions',self.rt.store/'role-bindings') for p in base.rglob('*') if p.is_file()}
        f.f.rt.events.clear();f.overlay_events.clear()

    def prepare(self):
        result=self.f.prepare(publication=True);self.plan=self.f.plan;self.ident=result['plan_sha256']
        self.put(self.f.f.store/'publication-approvals'/(self.ident+'.json'),v.encoded(self.plan));self.f.ident=self.ident
        return result

    def runtime(self):
        return v.apply_policy_overlay(self.f.new,v.env_values(self.f.g.live['handoff'].read_bytes()),self.root,fixture_runtime=self.rt)

    def preserved(self):
        for path,raw in self.proofs.items():self.assertEqual(raw,Path(path).read_bytes(),path)
        for name in ('lifecycle.json','gate.json','consumed-ledger.json'):
            p=self.root/'runtime/vmm'/name;self.assertEqual(self.original[str(p)][0],p.read_bytes())
        self.assertEqual(self.lock_inode,self.rt.lock.stat().st_ino)

    def test_successful_finite_binding_actual_history_consumer_and_idempotent_receipt(self):
        self.prepare();self.assertEqual(u.PUBLICATION_SCHEMA,self.plan['schema'])
        self.assertEqual('INSTALLED',self.f.install());self.runtime();self.preserved()
        self.assertEqual('INSTALLED_VERIFIED_NOOP',self.f.install())
        self.assertEqual('INSTALLED_NO_RECOVERY',self.f.recover());self.preserved()
        self.assertEqual(['stop-watchdog','start-watchdog'],self.f.f.rt.events)
        with self.assertRaisesRegex(v.Refused,'publication_requires_new_reviewed_transition'):
            v.prepare_module_revision(self.rt,'f'*64,successor=True)

    def test_interrupted_copy_recovers_without_ledger_or_parent_restore(self):
        self.prepare();original=self.f.f.rt.storage.atomic_copy_file
        def fail(src,target,*args,**kwargs):
            result=original(src,target,*args,**kwargs)
            if Path(target)==self.root/'runtime/config/vmm-role.json':raise InterruptedError('synthetic after role CAS')
            return result
        with patch.object(self.f.f.rt.storage,'atomic_copy_file',side_effect=fail):
            with self.assertRaises(InterruptedError):self.f.install()
        with self.assertRaisesRegex(Exception,'recovery_required'):self.f.install()
        self.assertEqual('RESTORED',self.f.recover());self.preserved()
        with self.assertRaisesRegex(Exception,'recovery_required'):self.f.install()
        self.assertFalse((self.rt.store/'CORE_PUBLICATION_ACTIVE.json').exists())
        v.apply_policy_overlay(self.f.old,v.env_values(self.f.g.live['handoff'].read_bytes()),self.root,fixture_runtime=self.rt)

    def test_unsupported_predecessors_and_repeat_publication_are_rejected(self):
        marker=self.rt.store/'MODULE_REVISION_ACTIVE.json';raw=marker.read_bytes()
        changed=json.loads(raw);changed['schema']=v.MODULE_REVISION_SCHEMA;self.put(marker,v.encoded(changed))
        with self.assertRaisesRegex(v.Refused,'requires_module_v2'):v.capture_core_publication(self.rt,{})
        self.put(marker,raw)
        path=self.rt.preserved()['policy'];raw=path.read_bytes();value=json.loads(raw)
        value['upgrade']['core_followup']['schema']='R25_MANAGED_VMM_CORE_FOLLOWUP_V1';self.put(path,v.encoded(value))
        with self.assertRaisesRegex(v.Refused,'requires_core_successor'):v.capture_core_publication(self.rt,{})
        self.put(path,raw);self.prepare();self.f.install()
        with self.assertRaisesRegex(v.Refused,'already_bound'):v.capture_core_publication(self.rt,{})
        self.preserved()

    def test_current_identity_parent_metadata_and_policy_drift_refuse_before_pause(self):
        self.prepare()
        paths=[self.rt.preserved()['policy'],self.rt.preserved()['original-controller-active'],
            self.rt.store/'module-revisions'/self.parent/'review.json',
            self.rt.store/'module-revisions'/self.parent/'after/vmm_service.py',
            self.root/'runtime/config/vmm-role.json',self.root/'runtime/vmm/gate.json',
            self.root/'runtime/app-pids/java.pid',self.root/'runtime/config/vmm-token',
            self.f.bundle/'core_business.py']
        for path in paths:
            raw=path.read_bytes()
            with self.subTest(path=path.relative_to(self.root)):
                self.put(path,raw+b' ')
                with self.assertRaises(Exception):self.f.install()
                self.put(path,raw)
        path=self.root/'operations/r25-publisher/vmm_service.py';path.chmod(0o700)
        with self.assertRaises(Exception):self.f.install()
        path.chmod(0o600);self.assertEqual([],self.f.f.rt.events);self.preserved()

    def test_exact_shared_lock_busy_has_no_supervision_action(self):
        self.prepare()
        with self.rt.lock.open('rb') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(Exception,'busy'):self.f.install()
        self.assertEqual([],self.f.f.rt.events);self.preserved()

    def test_unknown_or_enabled_state_rejects_before_prepare(self):
        path=self.root/'runtime/vmm/gate.json';raw=path.read_bytes()
        self.put(path,v.encoded({'mode':'CLOSED','requests':{'spent':{'state':'UNKNOWN'}}}))
        with self.assertRaises(Exception):v.capture_core_publication(self.rt,{})
        self.put(path,raw)
        path=self.root/'runtime/vmm/lifecycle.json';raw=path.read_bytes();state=json.loads(raw);state['enabled']=True
        self.put(path,v.encoded(state))
        with self.assertRaises(Exception):v.capture_core_publication(self.rt,{})
        self.put(path,raw);self.assertEqual([],self.f.f.rt.events)

    def test_every_changed_copy_and_marker_interruption_recovers_original_control_only(self):
        # Independent CPU fixtures for separate fault points, never retry a live approval.
        for point in ('core_business.py','upgrade_assistant_controller.py','core_release_guard.py','vmm_controller.py','vmm_service.py','policy','handoff','guard','role','marker','verify'):
            with self.subTest(point=point):
                case=Publication('runTest');case.setUp()
                try:
                    case.prepare();storage=case.f.f.rt.storage;copy=storage.atomic_copy_file;write=storage.atomic_write_bytes;jwrite=case.f.f.rt.write
                    target=u.targets(case.plan)[point][0] if point not in {'marker','verify'} else case.rt.store/'CORE_PUBLICATION_ACTIVE.json'
                    def failcopy(src,dst,*args,**kwargs):
                        result=copy(src,dst,*args,**kwargs)
                        if Path(dst)==target:raise InterruptedError('synthetic copy completion loss')
                        return result
                    def failwrite(dst,data,*args,**kwargs):
                        result=write(dst,data,*args,**kwargs)
                        if point=='marker' and Path(dst)==target:raise InterruptedError('synthetic marker completion loss')
                        if point=='verify' and Path(dst).name=='journal.json' and json.loads(data).get('phase')=='VERIFY_INTENT':
                            raise InterruptedError('synthetic verify completion loss')
                        return result
                    def failjournal(path,value):
                        result=jwrite(path,value)
                        if point=='verify' and value.get('phase')=='VERIFY_INTENT':raise InterruptedError('synthetic durable verify interruption')
                        return result
                    with patch.object(storage,'atomic_copy_file',side_effect=failcopy),patch.object(storage,'atomic_write_bytes',side_effect=failwrite),patch.object(case.f.f.rt,'write',side_effect=failjournal):
                        with self.assertRaises(InterruptedError):case.f.install()
                    self.assertEqual('RESTORED',case.f.recover());case.preserved()
                    self.assertEqual('RESTORED_NO_RECOVERY',case.f.recover())
                    for name,(path,_,_) in u.targets(case.plan).items():
                        self.assertEqual(case.plan['files'].get(name,case.plan['operational'].get(name))['before_sha256'],v.sha(path.read_bytes()))
                finally:case.doCleanups()

    def interrupt_at_role(self):
        original=self.f.f.rt.storage.atomic_copy_file
        def fail(src,dst,*args,**kwargs):
            result=original(src,dst,*args,**kwargs)
            if Path(dst)==self.root/'runtime/config/vmm-role.json':raise InterruptedError('synthetic interruption')
            return result
        with patch.object(self.f.f.rt.storage,'atomic_copy_file',side_effect=fail):
            with self.assertRaises(InterruptedError):self.f.install()

    def test_interrupted_recovery_resumes_same_transaction(self):
        self.prepare();self.interrupt_at_role();original=self.f.f.rt.storage.atomic_copy_file
        def fail(src,dst,*args,**kwargs):
            result=original(src,dst,*args,**kwargs)
            if Path(dst)==self.root/'runtime/config/vmm-role.json':raise InterruptedError('synthetic restore interruption')
            return result
        with patch.object(self.f.f.rt.storage,'atomic_copy_file',side_effect=fail):
            with self.assertRaises(InterruptedError):self.f.recover()
        self.assertEqual('RESTORED',self.f.recover());self.preserved()

    def test_partial_recovery_refuses_consumption_or_foreign_control_drift(self):
        self.prepare();self.interrupt_at_role()
        for path in (self.root/'runtime/vmm/lifecycle.json',self.root/'operations/r25-publisher/vmm_service.py'):
            raw=path.read_bytes();self.put(path,raw+b' ')
            with self.subTest(path=path),self.assertRaises(Exception):self.f.recover()
            self.assertEqual(raw+b' ',path.read_bytes());self.put(path,raw)
        self.assertEqual('RESTORED',self.f.recover());self.preserved()

    def test_committed_receipt_loss_preserves_later_consumption_and_unknown(self):
        self.prepare();self.f.install()
        life=self.root/'runtime/vmm/lifecycle.json';state=json.loads(life.read_bytes());state['open_operations'].append('4'*64)
        self.put(life,v.encoded(state));gate=self.root/'runtime/vmm/gate.json'
        self.put(gate,v.encoded({'mode':'CLOSED','requests':{'later':{'state':'UNKNOWN'}}}))
        expected=(life.read_bytes(),gate.read_bytes())
        self.assertEqual('INSTALLED_NO_RECOVERY',self.f.recover());self.assertEqual('INSTALLED_VERIFIED_NOOP',self.f.install())
        self.assertEqual(expected,(life.read_bytes(),gate.read_bytes()))
        # The consumed parent cannot downgrade a committed publication either.
        with self.assertRaises(Exception):v.recover_module_revision(self.rt,self.parent)
        self.assertEqual(expected,(life.read_bytes(),gate.read_bytes()))

    def test_policy_rejects_configuration_ai_baseline_and_recursive_upgrade(self):
        self.prepare()
        for kind in ('duration','ai','baseline','recursive'):
            value=deepcopy(self.f.new);old=deepcopy(self.f.old)
            if kind=='duration':value['upgrade']['configuration']['vmm']['acceptance-duration-seconds']=4
            if kind=='ai':value['upgrade']['core_followup']['ai_current_sha256']='6'*64
            if kind=='baseline':value['upgrade']['baseline_release']['commit_sha']='6'*40
            if kind=='recursive':old=deepcopy(value)
            with self.subTest(kind=kind),self.assertRaises(Exception):guard.verify_core_followup_context(value,old,business)
        business.validate_existing_managed_vmm_configuration(self.f.g.live['application'].read_bytes(),self.f.new)

    def test_missing_approval_and_wrong_formal_bundle_cannot_install(self):
        self.prepare();approval=self.f.f.store/'publication-approvals'/(self.ident+'.json')
        raw=approval.read_bytes();self.put(approval,raw+b' ')
        with self.assertRaises(Exception):self.f.install()
        self.put(approval,raw)
        path=self.root/'operations/r25-vmm-candidates'/self.plan['deploy']['commit']/'vmm-extension.zip'
        raw=path.read_bytes();self.put(path,raw+b'foreign')
        with self.assertRaises(Exception):self.f.install()
        self.put(path,raw);self.assertEqual([],self.f.f.rt.events);self.preserved()

    def test_runtime_rejects_changed_archive_current_guard_or_new_marker(self):
        self.prepare();self.f.install();self.runtime()
        paths=[self.rt.store/'CORE_PUBLICATION_ACTIVE.json',self.rt.store/'MODULE_REVISION_ACTIVE.json',
            self.root/'operations/core_release_guard.py',
            self.f.f.store/'controller-upgrades'/self.ident/'before/policy',
            self.rt.store/'module-revisions'/self.parent/'approval.json']
        for path in paths:
            raw=path.read_bytes();mode=path.stat().st_mode&0o777
            self.put(path,raw+b' ',mode)
            with self.subTest(path=path),self.assertRaises(Exception):self.runtime()
            self.put(path,raw,mode)
        self.runtime();self.preserved()

    def test_compatible_core_publication_and_later_web_binding_do_not_rewrite_parent(self):
        self.prepare();self.f.install()
        candidate=self.f.new['upgrade']['candidate'];incoming=self.root/'incoming'/(candidate['commit_sha']+'-888-1')
        for name in ('release-jar','preview-jar'):self.put(self.f.g.live[name],(incoming/fixture.C.JAR_NAME).read_bytes())
        record={**candidate,'schema':guard.BUSINESS_RECORD_SCHEMA,'policy_sha256':v.sha(v.encoded(self.f.new))}
        rp=self.f.f.store/'releases'/(candidate['artifact_sha256']+'-'+candidate['commit_sha'])/'record.json'
        recsha=self.put(rp,v.encoded(record));current=core_runtime.Release(candidate['commit_sha'],candidate['artifact_sha256'],recsha)
        self.put(self.f.f.store/'CURRENT.json',v.encoded({'schema':'R25_CORE_CURRENT_V1','transaction_id':self.f.coretx,'release':asdict(current)}))
        self.put(self.f.f.store/'transactions'/self.f.coretx/'journal.json',v.encoded({'transaction_id':self.f.coretx,'phase':'COMMITTED','candidate':asdict(current)}))
        self.runtime();self.assertEqual('INSTALLED_NO_RECOVERY',self.f.recover())
        # Parent proof tolerates a separately verified later Web selection only
        # in unrelated handoff keys; its VMM and Core controls remain exact.
        path=self.f.g.live['handoff'];raw=path.read_bytes();self.put(path,raw+b'R25_WEB_EXAMPLE=separately-reviewed-fixture\n')
        self.runtime();self.assertEqual('INSTALLED_NO_RECOVERY',self.f.recover())

    def test_commit_fsync_then_exception_reconciles_without_downgrade(self):
        self.prepare();original=self.f.f.rt.write
        def fail(path,value):
            result=original(path,value)
            if value.get('phase')=='INSTALLED':raise InterruptedError('synthetic lost commit response')
            return result
        with patch.object(self.f.f.rt,'write',side_effect=fail):
            with self.assertRaises(InterruptedError):self.f.install()
        folder=self.f.f.store/'controller-upgrades'/self.ident
        self.assertEqual('INSTALLED',json.loads((folder/'journal.json').read_bytes())['phase'])
        self.assertEqual('INSTALLED_NO_RECOVERY',self.f.recover());self.runtime();self.preserved()

    def test_pointer_cas_interruption_can_recover_without_model_or_ledger_writes(self):
        self.prepare();storage=self.f.f.rt.storage;original=storage.atomic_write_bytes
        def fail(path,*args,**kwargs):
            result=original(path,*args,**kwargs)
            if Path(path)==self.f.f.store/'CONTROL_UPGRADE_ACTIVE.json':raise InterruptedError('synthetic pointer reply loss')
            return result
        with patch.object(storage,'atomic_write_bytes',side_effect=fail):
            with self.assertRaises(InterruptedError):self.f.install()
        self.assertEqual('RESTORED',self.f.recover());self.preserved()


    def test_noncanonical_approval_bytes_refused_before_any_pause(self):
        self.prepare();raw=v.encoded(self.plan)+b' ';ident=v.sha(raw)
        self.put(self.f.f.store/'publication-approvals'/(ident+'.json'),raw)
        with self.assertRaisesRegex(Exception,'publication_exact_plan_bytes'):
            u.install(self.f.f.rt,self.plan,self.f.bundle,approval_sha256=ident)
        self.assertEqual([],self.f.f.rt.events);self.preserved()

    def test_no_journal_recovery_has_no_maintenance_action(self):
        self.prepare();self.assertEqual('CORE_FOLLOWUP_NOT_STARTED',self.f.recover())
        self.assertEqual([],self.f.f.rt.events);self.preserved()

    def test_supervision_resume_failure_uses_original_recovery_and_keeps_parent(self):
        self.prepare();engine=self.f.f.rt.engine_value
        with patch.object(engine,'watchdog_start_locked',side_effect=OSError('synthetic resume failure')):
            with self.assertRaisesRegex(OSError,'resume failure'):self.f.install()
        self.assertEqual('RESTORED',self.f.recover());self.preserved()
        self.assertEqual(['stop-watchdog','stop-watchdog','start-watchdog'],self.f.f.rt.events)

    def test_actual_guard_policy_entry_before_and_after_compatible_core_publication(self):
        from functools import partial
        self.prepare();self.f.install();source=guard.source_module
        def local_source(path,name,raw):
            module=source(path,name,raw)
            if Path(path).name=='vmm_controller.py':
                owner=tuple(self.plan['private_owner']);module.LOCK=self.rt.lock
                module.Runtime=partial(module.Runtime,private=owner,scripts=owner,supervise=self.f.overlay_events.append)
            return module
        # The inherited synthetic inventory stays an explicit boundary; the
        # full real policy structure is separately validated below. The actual
        # guard configuration, source loader and transitive proof paths run.
        with patch.object(guard,'business_module',return_value=business),patch.object(business,'validate_policy',side_effect=lambda value:value),patch.object(guard,'source_module',side_effect=local_source):
            self.assertEqual(self.f.new['upgrade'],guard.policy()['upgrade'])
            candidate=self.f.new['upgrade']['candidate']
            record={**candidate,'schema':guard.BUSINESS_RECORD_SCHEMA,'policy_sha256':self.plan['operational']['policy']['after_sha256']}
            folder=self.f.f.store/'releases'/(candidate['artifact_sha256']+'-'+candidate['commit_sha'])
            digest=self.put(folder/'record.json',v.encoded(record))
            current={'commit_sha':candidate['commit_sha'],'artifact_sha256':candidate['artifact_sha256'],'accepted_record_sha256':digest}
            self.put(self.f.f.store/'CURRENT.json',v.encoded({'schema':'R25_CORE_CURRENT_V1','transaction_id':self.f.coretx,'release':current}))
            self.put(self.f.f.store/'transactions'/self.f.coretx/'journal.json',v.encoded({'transaction_id':self.f.coretx,'phase':'COMMITTED','current':asdict(self.f.current),'candidate':current}))
            self.assertEqual(self.f.new['upgrade'],guard.policy()['upgrade'])
            previous=self.f.f.store/'core-followup-baselines'/(self.f.new['upgrade']['core_followup']['predecessor_policy_sha256']+'.json')
            raw=previous.read_bytes();self.put(previous,raw+b' ')
            with self.assertRaisesRegex(Exception,'file_digest'):guard.policy()
            self.put(previous,raw)
        self.preserved()


    def test_restored_transaction_allows_fresh_preparation_without_overwriting_old_material(self):
        self.prepare();first=self.ident;first_plan=deepcopy(self.plan)
        material=u.source(self.f.bundle,'policy',first_plan).parent
        preserved={str(p):p.read_bytes() for p in material.iterdir()}
        self.interrupt_at_role();self.assertEqual('RESTORED',self.f.recover())
        result=self.f.prepare(publication=True,reuse_candidate=True)
        self.assertNotEqual(first,result['plan_sha256'])
        self.assertNotEqual(first_plan['publication_request_sha256'],self.f.plan['publication_request_sha256'])
        for path,raw in preserved.items():self.assertEqual(raw,Path(path).read_bytes())
        self.assertEqual('RESTORED',self.f.f.rt.read(self.f.f.store/'controller-upgrades'/first/'journal.json')['phase'])
        self.preserved()


class PublicationFullPolicy(unittest.TestCase):
    def test_actual_complete_policy_validates_without_inventory_mocks(self):
        import test_managed_vmm_successor_policy as source
        case=source.ManagedSuccessorFullPolicy('runTest');case.setUp();self.addCleanup(case.doCleanups)
        value=deepcopy(case.policy);value['upgrade']['core_followup']['schema']='R25_MANAGED_VMM_CORE_PUBLICATION_V1'
        business.validate_policy(value)
        self.assertEqual(case.raw,business.render_configuration(case.raw,value))

if __name__=='__main__':unittest.main()

