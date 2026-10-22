"""Actual module prepare/bind/CAS/overlay/recovery, with synthetic OS approvals.

No production paths, secrets, model imports, initialization or generation.
The watchdog is an explicit callback; files, locks and consumers are real.
"""
import copy
import fcntl
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

import vmm_controller as v
import test_managed_vmm_successor_overlay as fixture

HERE=Path(__file__).parent


class ModuleRevision(unittest.TestCase):
    def setUp(self):
        self.f=fixture.SuccessorOverlay('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        f=self.f;f.activate_fixture();self.rt=f.rt;self.root=f.root;self.put=f.f.put
        self.put(f.folder/'journal.json',v.encoded({'schema':v.CORE_SUCCESSOR_SCHEMA,
            'approval_sha256':f.ident,'phase':'INSTALLED','service_identities':{'synthetic':True}}))
        self.original={str(p):p.read_bytes() for p in v.revision_preserved(self.rt).values()}
        self.before={n:p.read_bytes() for n,(p,_,_) in v.revision_targets(self.rt).items()}
        self.lock_inode=self.rt.lock.stat().st_ino
        # Different, same-run packages. Synthetic preimage code identities are
        # retained by the old proof; new bytes are the actual candidate modules.
        self.sources={n:(HERE/n).read_bytes() for n in v.MODULES}
        self.sources['core_release_guard.py']=f.sources['core_release_guard.py']
        for name in ('vmm_lifecycle.py','vmm_service.py'):self.sources[name]+=b'\n# synthetic release packaging boundary\n'
        self.extension=self.package_extension()
        self.controller=self.package_controller()
        review=v.encoded({'synthetic_only':True,'not_production_authorization':True})
        self.put(self.rt.store/'MODULE_REVIEW.json',review)
        request={'schema':'R25_VMM_MODULE_REVISION_REQUEST_V1','deploy':self.extension,
                 'controller_deploy':self.controller,'review_evidence_sha256':v.sha(review)}
        self.put(self.rt.store/'MODULE_REQUEST.json',v.encoded(request));self.request=v.sha(v.encoded(request))
        result=v.prepare_module_revision(self.rt,self.request)
        self.ident=result['plan_sha256'];self.plan=json.loads(Path(result['plan_path']).read_bytes())
        self.put(self.rt.store/'MODULE_APPROVAL.json',v.encoded(self.plan))
        f.f.events.clear()

    def package_extension(self):
        folder=self.root/'operations/r25-vmm-candidates'/('c'*40);data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:
            for name,raw in self.sources.items():z.writestr(name,raw)
        raw=data.getvalue();manifest={'schema':v.MANIFEST_SCHEMA,'repository':'bailangguihun/auralink-deploy',
            'ref':'refs/heads/release','commit':'c'*40,'run_id':'125','run_attempt':'1','archive_sha256':v.sha(raw),
            'files':[{'path':n,'bytes':len(b),'sha256':v.sha(b)} for n,b in self.sources.items()]}
        self.put(folder/'vmm-extension.zip',raw);self.put(folder/'manifest.json',v.encoded(manifest))
        for n,b in self.sources.items():self.put(folder/n,b)
        return {k:manifest[k] for k in ('repository','ref','commit','run_id','run_attempt','archive_sha256')}|{
            'manifest_sha256':v.sha(v.encoded(manifest)),'tool_sha256':v.sha(self.sources['vmm_controller.py'])}

    def package_controller(self):
        folder=self.root/'operations/r25-controller-candidates'/('c'*40)
        payload={name:(HERE/name).read_bytes() for name in v.CONTROLLER_FILES}
        payload['core_release_guard.py']=self.sources['core_release_guard.py'];data=io.BytesIO()
        with zipfile.ZipFile(data,'w',compression=zipfile.ZIP_STORED) as z:
            for name,body in payload.items():
                info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.create_system=3
                info.external_attr=(0o100600)<<16;z.writestr(info,body)
        raw=data.getvalue();manifest={'schema':1,'repository':'bailangguihun/auralink-deploy','ref':'refs/heads/release',
            'commit':'c'*40,'run_id':'125','run_attempt':'1','status':'NOT_DEPLOYED','installation_performed':False,
            'archive':{'name':'r25-controller.zip','bytes':len(raw),'sha256':v.sha(raw)},
            'files':[{'path':n,'bytes':len(b),'sha256':v.sha(b)} for n,b in payload.items()]}
        self.put(folder/'r25-controller.zip',raw);self.put(folder/'r25-controller-manifest.json',v.encoded(manifest))
        for n,b in payload.items():self.put(folder/n,b)
        return {k:manifest[k] for k in ('repository','ref','commit','run_id','run_attempt')}|{
            'archive_sha256':v.sha(raw),'manifest_sha256':v.sha(v.encoded(manifest)),
            'tool_sha256':v.sha(payload['upgrade_assistant_controller.py'])}

    def assert_preserved(self):
        for path,raw in self.original.items():self.assertEqual(Path(path).read_bytes(),raw,path)
        self.assertEqual(self.rt.lock.stat().st_ino,self.lock_inode)

    def runtime(self):
        return v.apply_policy_overlay(json.loads(self.f.newpolicy),v.env_values(self.rt.targets()['handoff'][0].read_bytes()),
                                      self.root,fixture_runtime=self.rt)

    def test_actual_installation_preserves_original_proofs_and_ledger(self):
        self.assertEqual(v.preflight_module_revision(self.rt,self.ident)['outcome'],'MODULE_PREFLIGHT_PASS')
        self.assertEqual(self.f.f.events,[])
        self.assertEqual(v.bind_module_revision(self.rt,self.ident)['outcome'],'MODULE_INSTALLED_DISABLED')
        self.assertEqual(self.f.f.events,['stop','start']);self.assert_preserved()
        effective=self.runtime();self.assertEqual(effective['upgrade'],json.loads(self.f.newpolicy)['upgrade'])
        role=json.loads(self.rt.targets()['role'][0].read_bytes());old=json.loads(self.before['role'])
        self.assertEqual(role,dict(old,service_script_sha256=v.sha(self.sources['vmm_service.py'])))
        self.assertFalse(role['enabled'])
        self.assertEqual(v.recover_module_revision(self.rt,self.ident)['outcome'],'MODULE_ALREADY_INSTALLED_SUPERVISION_RESTORED')
        self.assert_preserved()

    def test_identity_drift_before_pause_and_mixed_bundles_rejected(self):
        for name in ('vmm_lifecycle.py','role','handoff'):
            p,_,mode=self.rt.targets()[name];raw=p.read_bytes();self.put(p,raw+b' ',mode)
            with self.subTest(name=name),self.assertRaises(v.Refused):v.bind_module_revision(self.rt,self.ident)
            self.put(p,raw,mode)
        bad=copy.deepcopy(self.controller);bad['run_attempt']='2'
        with self.assertRaisesRegex(v.Refused,'same_formal_run'):v.revision_bundles(self.rt,self.extension,bad)
        self.assertEqual(self.f.f.events,[]);self.assert_preserved()

    def test_partial_failure_at_every_write_restores_same_transaction(self):
        # Independent synthetic installations, not retries of a live approval.
        for target in (*v.REVISION_NAMES,'marker','verified'):
            with self.subTest(target=target):
                case=ModuleRevision('runTest');case.setUp()
                try:
                    def fail(name):
                        if name==target:raise InterruptedError('synthetic interrupted copy')
                    with self.assertRaises(InterruptedError):v.bind_module_revision(case.rt,case.ident,fault=fail)
                    with self.assertRaisesRegex(v.Refused,'recover_original'):v.bind_module_revision(case.rt,case.ident)
                    self.assertEqual(v.recover_module_revision(case.rt,case.ident)['outcome'],'MODULE_RESTORED')
                    for name,(p,_,_) in v.revision_targets(case.rt).items():self.assertEqual(p.read_bytes(),case.before[name])
                    case.assert_preserved();case.runtime()
                    self.assertEqual(v.recover_module_revision(case.rt,case.ident)['outcome'],'MODULE_ALREADY_RESTORED')
                finally:case.doCleanups()

    def test_recovery_refuses_drift_and_never_rewrites_consumption(self):
        with self.assertRaises(InterruptedError):v.bind_module_revision(self.rt,self.ident,
            fault=lambda n:(_ for _ in ()).throw(InterruptedError()) if n=='vmm_service.py' else None)
        path=self.root/'runtime/vmm/lifecycle.json';state=json.loads(path.read_bytes())
        state['gate_commands']=['a'*64];state['gate_windows']={'b'*64:{'command_sha256':'a'*64}}
        self.put(path,v.encoded(state));saved=path.read_bytes()
        with self.assertRaises(v.Refused):v.recover_module_revision(self.rt,self.ident)
        self.assertEqual(path.read_bytes(),saved)

    def test_committed_recovery_does_not_refund_later_consumption(self):
        v.bind_module_revision(self.rt,self.ident)
        p=self.root/'runtime/vmm/lifecycle.json';state=json.loads(p.read_bytes())
        state.update(gate_commands=['e'*64],gate_windows={'f'*64:{'command_sha256':'e'*64}})
        self.put(p,v.encoded(state));gate=self.root/'runtime/vmm/gate.json'
        self.put(gate,v.encoded({'mode':'CLOSED','requests':{'spent':{'state':'UNKNOWN'}}}))
        before=(p.read_bytes(),gate.read_bytes())
        self.assertEqual(v.recover_module_revision(self.rt,self.ident)['outcome'],'MODULE_ALREADY_INSTALLED_SUPERVISION_RESTORED')
        self.assertEqual((p.read_bytes(),gate.read_bytes()),before)
        with self.assertRaisesRegex(v.Refused,'already_bound'):v.prepare_module_revision(self.rt,self.request)

    def test_interrupted_recovery_reenters_without_touching_ledger(self):
        with self.assertRaises(InterruptedError):v.bind_module_revision(self.rt,self.ident,
            fault=lambda n:(_ for _ in ()).throw(InterruptedError()) if n=='marker' else None)
        original=self.rt.atomic;triggered=False
        def interrupt(path,raw,**kwargs):
            nonlocal triggered
            original(path,raw,**kwargs)
            if Path(path)==self.rt.targets()['role'][0] and not triggered:
                triggered=True;raise InterruptedError('synthetic recovery interruption')
        with patch.object(self.rt,'atomic',side_effect=interrupt):
            with self.assertRaises(InterruptedError):v.recover_module_revision(self.rt,self.ident)
        self.assertTrue(triggered);self.assert_preserved()
        self.assertEqual(v.recover_module_revision(self.rt,self.ident)['outcome'],'MODULE_RESTORED')
        self.assert_preserved();self.runtime()

    def test_formal_package_extracted_or_guard_drift_rejected(self):
        folder=self.root/'operations/r25-controller-candidates'/('c'*40)
        p=folder/'core_runtime.py';raw=p.read_bytes();self.put(p,raw+b' ')
        with self.assertRaisesRegex(v.Refused,'file_digest'):v.bind_module_revision(self.rt,self.ident)
        self.put(p,raw)
        p=self.rt.targets()['operation-guard'][0];raw=p.read_bytes();self.put(p,raw+b' ',0o700)
        with self.assertRaises(v.Refused):v.bind_module_revision(self.rt,self.ident)
        self.put(p,raw,0o700);self.assertEqual(self.f.f.events,[])

    def test_lock_busy_keeps_inode_and_has_no_pause(self):
        with self.rt.lock.open('r+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(v.Refused,'shared_publication_busy'):v.preflight_module_revision(self.rt,self.ident)
        self.assertEqual(self.f.f.events,[]);self.assert_preserved()

    def test_unknown_enabled_or_inflight_reject_before_pause(self):
        gate=self.root/'runtime/vmm/gate.json';raw=gate.read_bytes()
        for state in ('DISPATCHED','UNKNOWN'):
            self.put(gate,v.encoded({'mode':'CLOSED','requests':{'existing':{'state':state}}}))
            with self.subTest(state=state),self.assertRaises(v.Refused):v.bind_module_revision(self.rt,self.ident)
        self.put(gate,raw);p=self.root/'runtime/vmm/lifecycle.json';state=json.loads(p.read_bytes());state['enabled']=True
        self.put(p,v.encoded(state))
        with self.assertRaises(v.Refused):v.bind_module_revision(self.rt,self.ident)
        self.assertEqual(self.f.f.events,[])

    def test_runtime_drift_and_missing_revision_proof_fail_closed(self):
        v.bind_module_revision(self.rt,self.ident)
        for path in (self.rt.targets()['vmm_service.py'][0],self.rt.targets()['role'][0],
                     v.revision_transaction(self.rt,self.ident)/'review.json'):
            raw=path.read_bytes();self.put(path,raw+b' ')
            with self.subTest(path=path.name),self.assertRaises(v.Refused):self.runtime()
            self.put(path,raw)
        marker=self.rt.store/'MODULE_REVISION_ACTIVE.json';marker.unlink()
        with self.assertRaises(v.Refused):self.runtime()

    def test_original_application_backup_and_watchdog_guards(self):
        current=json.loads(self.rt.preserved()['core-current'].read_bytes())['transaction_id']
        p=self.root/'runtime/core-publications/transactions'/current/'journal.json';raw=p.read_bytes()
        self.put(p,v.encoded({'phase':'BACKUP_INTENT'}))
        with self.assertRaisesRegex(v.Refused,'application_active'):v.bind_module_revision(self.rt,self.ident)
        self.put(p,raw)
        with patch.object(self.rt,'require_watchdog_stopped',side_effect=v.Refused('live watchdog')):
            with self.assertRaisesRegex(v.Refused,'live watchdog'):v.bind_module_revision(self.rt,self.ident)
        self.assertEqual(self.f.f.events,['stop','start']);self.assert_preserved()

    def test_exact_new_module_loaded_and_original_deadline_guard_usable(self):
        v.bind_module_revision(self.rt,self.ident)
        path=self.rt.targets()['vmm_lifecycle.py'][0]
        module=type(v)('synthetic_installed_vmm_lifecycle');module.__file__=str(path)
        exec(compile(path.read_bytes(),str(path),'exec'),module.__dict__)
        self.assertEqual(module.ACCEPTANCE_COMMAND_SCHEMA,'R25_VMM_GATE_COMMAND_V2')
        with self.assertRaisesRegex(module.Refused,'observation_expired'):
            module.check_window_deadline({'ready_monotonic':0,'deadline_monotonic':1,'ready_wall':0,'deadline_wall':1})
        self.assert_preserved()

    def test_not_started_and_malformed_plan_do_not_pause(self):
        self.assertEqual(v.recover_module_revision(self.rt,'f'*64),{'outcome':'MODULE_NOT_STARTED'})
        bad=copy.deepcopy(self.plan);bad['files']['gate']={'before_sha256':'a'*64,'after_sha256':'b'*64}
        with self.assertRaisesRegex(v.Refused,'inventory'):v.revision_validate(self.rt,bad)
        self.assertEqual(self.f.f.events,[])

    def test_exact_plan_bytes_and_original_adapter_preserved_before_recovery(self):
        path=self.rt.store/'MODULE_APPROVAL.json';raw=path.read_bytes();self.put(path,raw+b' ')
        with self.assertRaisesRegex(v.Refused,'exact_prepared_plan_bytes'):
            v.preflight_module_revision(self.rt,v.sha(raw+b' '))
        self.put(path,raw)
        with self.assertRaises(InterruptedError):v.bind_module_revision(self.rt,self.ident,
            fault=lambda n:(_ for _ in ()).throw(InterruptedError()) if n=='vmm_service.py' else None)
        adapter,_,mode=self.rt.targets()['lifecycle'];self.put(adapter,adapter.read_bytes()+b' ',mode)
        before={str(p):p.read_bytes() for p,_,_ in v.revision_targets(self.rt).values()}
        with self.assertRaises(v.Refused):v.recover_module_revision(self.rt,self.ident)
        self.assertEqual(before,{str(p):p.read_bytes() for p,_,_ in v.revision_targets(self.rt).values()})


if __name__=='__main__':unittest.main()
