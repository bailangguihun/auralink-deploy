"""Successor from an already INSTALLED revision; old proofs are immutable."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import vmm_controller as v
import test_vmm_module_revision as base


class ModuleSuccessor(unittest.TestCase):
    def setUp(self):
        self.parent=base.ModuleRevision('runTest');self.parent.setUp();self.addCleanup(self.parent.doCleanups)
        p=self.parent;v.bind_module_revision(p.rt,p.ident)
        self.rt=p.rt;self.put=p.put;self.root=p.root;self.previous=p.ident
        folder=v.revision_transaction(self.rt,p.ident)
        self.old={str(f):f.read_bytes()for f in folder.rglob('*')if f.is_file()}
        for name in ['MODULE_REQUEST.json','MODULE_REVIEW.json','MODULE_APPROVAL.json']:
            path=self.rt.store/name;self.old[str(path)]=path.read_bytes()
        self.marker=(self.rt.store/'MODULE_REVISION_ACTIVE.json').read_bytes()
        # Existing consumed operation and historical outcome may advance after
        # installation. Freeze the actual latest ledger, not an install snapshot.
        life=self.root/'runtime/vmm/lifecycle.json';state=json.loads(life.read_bytes())
        state.update(gate_commands=['1'*64],open_operations=['2'*64],gate_windows={'3'*64:{'command_sha256':'1'*64}})
        self.put(life,v.encoded(state));self.ledger=life.read_bytes()
        self.before={n:path.read_bytes()for n,(path,_,_)in v.revision_targets(self.rt).items()}
        p.package_commit='d'*40;p.package_run='126'
        p.sources={n:(Path(__file__).parent/n).read_bytes()for n in v.MODULES}
        p.sources['core_release_guard.py']=p.f.sources['core_release_guard.py']
        p.sources['vmm_controller.py']+=b'\n# synthetic successor artifact identity\n'
        ext=p.package_extension();controller=p.package_controller()
        review=v.encoded({'synthetic_only':True,'production_authorization':False,'scope':'module successor'})
        request={'schema':'R25_VMM_MODULE_REVISION_REQUEST_V2','deploy':ext,'controller_deploy':controller,
                 'review_evidence_sha256':v.sha(review)}
        self.request=v.sha(v.encoded(request));inputs=self.rt.store/'module-successor-inputs'/self.request
        self.put(inputs/'request.json',v.encoded(request));self.put(inputs/'review.json',review)
        prepared=v.prepare_module_revision(self.rt,self.request,successor=True);self.ident=prepared['plan_sha256']
        self.plan=json.loads(Path(prepared['plan_path']).read_bytes())
        self.put(self.rt.store/'module-successor-approvals'/(self.ident+'.json'),v.encoded(self.plan))
        p.f.f.events.clear()

    def preserved(self):
        for path,raw in self.old.items():self.assertEqual(raw,Path(path).read_bytes(),path)
        self.assertEqual(self.ledger,(self.root/'runtime/vmm/lifecycle.json').read_bytes())
        self.assertEqual(self.parent.lock_inode,self.rt.lock.stat().st_ino)

    def test_install_from_installed_revision_preserves_ledger_old_approvals_and_runtime(self):
        self.assertEqual(self.previous,self.plan['previous_revision']['approval_sha256'])
        self.assertEqual('MODULE_PREFLIGHT_PASS',v.preflight_module_revision(self.rt,self.ident)['outcome'])
        self.assertEqual('MODULE_INSTALLED_DISABLED',v.bind_module_revision(self.rt,self.ident)['outcome'])
        self.parent.runtime();self.preserved()
        self.assertEqual('MODULE_ALREADY_INSTALLED_SUPERVISION_RESTORED',v.recover_module_revision(self.rt,self.ident)['outcome'])
        # An old committed installation must never override its successor.
        with self.assertRaisesRegex(v.Refused,'active_preimage'):v.recover_module_revision(self.rt,self.previous)
        self.preserved()

    def test_each_write_and_marker_failure_restores_parent_marker_and_proofs(self):
        for point in (*v.REVISION_NAMES,'marker','verified'):
            with self.subTest(point=point):
                case=ModuleSuccessor('runTest');case.setUp()
                try:
                    def fail(name):
                        if name==point:raise InterruptedError('synthetic partial module update')
                    with self.assertRaises(InterruptedError):v.bind_module_revision(case.rt,case.ident,fault=fail)
                    self.assertEqual('MODULE_RESTORED',v.recover_module_revision(case.rt,case.ident)['outcome'])
                    self.assertEqual(case.marker,(case.rt.store/'MODULE_REVISION_ACTIVE.json').read_bytes())
                    for n,(path,_,_)in v.revision_targets(case.rt).items():self.assertEqual(case.before[n],path.read_bytes())
                    case.parent.runtime();case.preserved()
                finally:case.doCleanups()

    def test_interrupted_recovery_reenters_with_parent_preserved(self):
        with self.assertRaises(InterruptedError):v.bind_module_revision(self.rt,self.ident,
            fault=lambda n:(_ for _ in ()).throw(InterruptedError())if n=='marker'else None)
        original=self.rt.atomic;interrupted=[False]
        def fail(path,raw,**kwargs):
            original(path,raw,**kwargs)
            if Path(path)==self.rt.targets()['role'][0]and not interrupted[0]:interrupted[0]=True;raise InterruptedError()
        with patch.object(self.rt,'atomic',side_effect=fail):
            with self.assertRaises(InterruptedError):v.recover_module_revision(self.rt,self.ident)
        self.assertEqual('MODULE_RESTORED',v.recover_module_revision(self.rt,self.ident)['outcome']);self.preserved();self.parent.runtime()

    def test_parent_proof_drift_wrong_marker_or_active_parent_rejected_before_pause(self):
        folder=v.revision_transaction(self.rt,self.previous)
        for name in ('approval.json','review.json','journal.json','after/vmm_lifecycle.py'):
            path=folder/name;raw=path.read_bytes();self.put(path,raw+b' ')
            with self.subTest(name=name),self.assertRaises(v.Refused):v.preflight_module_revision(self.rt,self.ident)
            self.put(path,raw)
        path=self.rt.store/'MODULE_REVISION_ACTIVE.json';self.put(path,v.encoded({'schema':v.MODULE_REVISION_SCHEMA,'approval_sha256':'f'*64}))
        with self.assertRaisesRegex(v.Refused,'active_preimage'):v.bind_module_revision(self.rt,self.ident)
        self.put(path,self.marker);self.assertEqual([],self.parent.f.f.events);self.preserved()

    def test_partial_recovery_rejects_consumption_drift_without_refund(self):
        with self.assertRaises(InterruptedError):v.bind_module_revision(self.rt,self.ident,
            fault=lambda n:(_ for _ in ()).throw(InterruptedError())if n=='vmm_service.py'else None)
        path=self.root/'runtime/vmm/lifecycle.json';state=json.loads(path.read_bytes());state['open_operations'].append('4'*64)
        self.put(path,v.encoded(state));before=path.read_bytes()
        with self.assertRaises(v.Refused):v.recover_module_revision(self.rt,self.ident)
        self.assertEqual(before,path.read_bytes())

    def test_committed_recovery_preserves_later_unknown_and_consumption(self):
        v.bind_module_revision(self.rt,self.ident)
        path=self.root/'runtime/vmm/lifecycle.json';state=json.loads(path.read_bytes());state['open_operations'].append('4'*64)
        self.put(path,v.encoded(state));gate=self.root/'runtime/vmm/gate.json'
        self.put(gate,v.encoded({'mode':'CLOSED','requests':{'historical':{'state':'UNKNOWN'}}}));before=(path.read_bytes(),gate.read_bytes())
        self.assertEqual('MODULE_ALREADY_INSTALLED_SUPERVISION_RESTORED',v.recover_module_revision(self.rt,self.ident)['outcome'])
        self.assertEqual(before,(path.read_bytes(),gate.read_bytes()))

    def test_installed_successor_executes_actual_open_producer_receiver_consumer(self):
        v.bind_module_revision(self.rt,self.ident)
        path=self.rt.targets()['vmm_lifecycle.py'][0];module=type(v)('installed_open_consumer');module.__file__=str(path)
        exec(compile(path.read_bytes(),str(path),'exec'),module.__dict__)
        import test_vmm_open_operation as opened
        import test_vmm_lifecycle as cpu
        case=cpu.LifecycleTest('runTest');case.setUp()
        try:
            case.start();engine=cpu.FixtureEngine(case.root)
            with patch.object(opened,'life',module):
                ident,_=opened.startup(engine,case.role)
                for i in range(2):
                    with engine.lease():result=module.prepare_open_operation(engine,case.role,ident)
                    opened.approve(engine,result)
                    with patch.object(module,'managed_role',return_value=case.role):self.assertEqual('OPEN',module.manage(engine,'open')['mode'])
                    case.store.configure('CLOSED')
            self.assertEqual(2,len(case.role.state()['open_operations']));self.assertEqual(0,case.store.snapshot()['attempts'])
        finally:case.tearDown()
        self.preserved()
