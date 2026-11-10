"""Full retained knowledge proof plus the controller-only Community successor.

All files, CAS and source manifests are real isolated artifacts. Existing Java,
database and watchdog processes use the predecessor's explicit fixture adapters.
Actual Node recovery behavior is covered in test_community_recovery.py.
"""
from pathlib import Path
import unittest
from unittest.mock import patch
import vmm_controller as v
import test_knowledge_publication as knowledge_fixture


class Upgrade(unittest.TestCase):
    def setUp(self):
        def original(k):
            deployed=k.community_package(k.f.oldplan['deploy'],original=True)
            plan=dict(schema='ARTLIVE_COMMUNITY_HOST_V1',controller_manifest_sha256=deployed['manifest_sha256'],
                      runtime={'python':{'identity':{'sha256':'3'*64}}},service={'port':19086},
                      lease={'guard':{'sha256':v.sha((k.root/'operations/core_release_guard.py').read_bytes())}})
            raw=v.encoded(plan);ident=v.sha(raw);base=k.root/'runtime/community';folder=base/'installations'/ident
            k.put(folder/'approval.json',raw);k.put(folder/'journal.json',v.encoded(dict(phase='INSTALLED')))
            k.put(base/'ACTIVE.json',v.encoded(dict(schema='ARTLIVE_COMMUNITY_HOST_V1',plan_sha256=ident)))
            k.put(base/'WATCHDOG.json',v.encoded(dict(schema='ARTLIVE_COMMUNITY_WATCHDOG_V1',plan_sha256=ident,
                  controller_manifest_sha256=deployed['manifest_sha256'],python_sha256='3'*64,
                  cli_sha256=v.sha((k.root/'operations/community-controller'/deployed['manifest_sha256']/'community_host_cli.py').read_bytes()))))
            attempts=[str(i)*32 for i in (1,2,3)]
            k.put(folder/'watchdog-state.json',v.encoded(dict(schema='ARTLIVE_COMMUNITY_WATCHDOG_V1',plan_sha256=ident,
                  desired='RUNNING',attempts=attempts,unhealthy=None,reason='START_DECISION')))
            for operation in attempts:
                k.put(folder/'process'/('run-'+operation+'.json'),v.encoded(dict(operation_id=operation,phase='EXITED',
                      process={'pid':999999999,'ticks':'1','boot_id':'previous-fixture-boot'})))
                k.put(folder/'process'/('run-'+operation+'.log'),b'{"event":"community_ready","port":19086}\n')
            k.put(folder/'process/current.json',v.encoded({'operation_id':attempts[-1]}))
        k=knowledge_fixture.Knowledge('runTest')
        with patch.object(knowledge_fixture.Knowledge,'community_original',original):k.setUp()
        self.addCleanup(k.doCleanups)
        k.prepare();k.f.install();k.commit_current(k.f.new,'c'*32,k.plan['current_release'])
        self.k=k;self.rt=k.rt;self.put=k.put
        self.community=k.root/'runtime/community/installations'/k.plan['vmm']['community']['plan_sha256']
        state=v.json_read(self.community/'watchdog-state.json',self.rt.private)
        state.update(desired='STOPPED',reason='RESTART_BUDGET_EXHAUSTED')
        self.put(self.community/'watchdog-state.json',v.encoded(state))
        self.legacy={p:p.read_bytes() for p in (k.root/'runtime/community').rglob('*') if p.is_file()}
        self.parent={p:p.read_bytes() for p in (k.root/'runtime/core-publications').rglob('*') if p.is_file()}
        commit='e3'*20;controller=k.controller_package(commit,'1001')
        k.f.sources['vmm_controller.py']+=b'\n# isolated community successor release\n'
        deployment=k.f.package_extension(commit,'1001');community=k.community_package(controller)
        self.review=v.encoded({'fixture_only':True,'scope':'community recovery successor'})
        self.request=dict(schema='R25_COMMUNITY_SUPERVISION_REQUEST_V1',deploy=deployment,controller_deploy=controller,
                          community_deploy=community,review_evidence_sha256=v.sha(self.review))
        self.events=[];self.rt.supervise=self.events.append

    def prepare(self):
        result=v.prepare_community_supervision(self.rt,self.request,self.review)
        self.ident=result['approval_sha256'];self.folder=Path(result['folder'])
        self.plan=v.json_read(self.folder/'UNSIGNED_PLAN.json',self.rt.private)
        self.put(self.folder/'approval.json',v.encoded(self.plan))

    def effective(self):
        return v.apply_policy_overlay(self.k.f.new,v.env_values((self.k.root/'runtime/handoff-control.env').read_bytes()),
                                      self.k.root,fixture_runtime=self.rt)

    def assert_retained(self):
        for path,raw in {**self.legacy,**self.parent}.items():self.assertEqual(path.read_bytes(),raw,str(path))

    def test_full_upgrade_preserves_core_proofs_and_legacy_ledger(self):
        self.prepare();self.assertEqual(self.events,[])
        result=v.install_community_supervision(self.rt,self.ident)
        self.assertEqual(result['outcome'],'COMMUNITY_SUPERVISION_INSTALLED')
        self.assertEqual(self.events,['stop','start'])
        self.assertEqual(self.effective()['controllers']['lifecycle_adapter.py'],self.plan['files']['lifecycle']['after_sha256'])
        self.assert_retained()
        state=v.json_read(self.community/'supervision-v2.json',self.rt.private)
        self.assertEqual(state['attempts'],[]);self.assertEqual(state['desired'],'RUNNING')
        # Simulate only the runtime-mutable successor record, not a real spawn.
        state['attempts']=['a'*32];self.put(self.community/'supervision-v2.json',v.encoded(state))
        self.put(self.community/'process/current.json',v.encoded({'operation_id':'a'*32}))
        self.effective()
        self.assertEqual(v.install_community_supervision(self.rt,self.ident)['outcome'],'COMMUNITY_SUPERVISION_INSTALLED_VERIFIED')
        self.assertEqual(v.json_read(self.community/'supervision-v2.json',self.rt.private)['attempts'],['a'*32])

    def test_partial_control_write_requires_explicit_forward_recovery(self):
        self.prepare()
        def fail(name):
            if name=='vmm_controller.py':raise InterruptedError('fixture lost first-copy receipt')
        with self.assertRaises(InterruptedError):v.install_community_supervision(self.rt,self.ident,fault=fail)
        self.assertEqual(self.events,['stop'])
        with self.assertRaisesRegex(Exception,'explicit_recovery_required'):
            v.install_community_supervision(self.rt,self.ident)
        self.assertEqual(v.install_community_supervision(self.rt,self.ident,recover=True)['outcome'],
                         'COMMUNITY_SUPERVISION_INSTALLED')
        self.effective();self.assert_retained()

    def test_foreign_preimage_or_review_refuses_before_supervisor_pause(self):
        self.prepare();path=self.folder/'review.json';raw=path.read_bytes()
        self.put(path,raw+b' ')
        with self.assertRaises(Exception):v.install_community_supervision(self.rt,self.ident)
        self.assertEqual(self.events,[]);self.put(path,raw)
        handoff=self.rt.targets()['handoff'][0];old=handoff.read_bytes();self.put(handoff,old+b'# foreign\n')
        with self.assertRaisesRegex(Exception,'file_digest|target_drift'):
            v.install_community_supervision(self.rt,self.ident)
        self.assertEqual(self.events,[]);self.put(handoff,old)

    def test_legacy_ledger_drift_after_commit_is_not_treated_as_refunded_budget(self):
        self.prepare();v.install_community_supervision(self.rt,self.ident)
        path=self.community/'watchdog-state.json';raw=path.read_bytes();state=v.json_bytes(raw)
        state['attempts']=state['attempts'][1:];self.put(path,v.encoded(state))
        with self.assertRaisesRegex(Exception,'exhausted_predecessor|legacy_drift'):self.effective()
        self.put(path,raw);self.effective()

    def test_durable_install_receipt_loss_never_rolls_back_or_recreates_state(self):
        self.prepare();write=self.rt.write_state
        def fail(path,value):
            write(path,value)
            if value.get('phase')=='INSTALLED':raise InterruptedError('fixture lost committed receipt')
        with patch.object(self.rt,'write_state',side_effect=fail),self.assertRaises(InterruptedError):
            v.install_community_supervision(self.rt,self.ident)
        raw=(self.community/'supervision-v2.json').read_bytes()
        self.assertEqual(v.install_community_supervision(self.rt,self.ident,recover=True)['outcome'],
                         'COMMUNITY_SUPERVISION_INSTALLED_VERIFIED')
        self.assertEqual((self.community/'supervision-v2.json').read_bytes(),raw)
        self.effective();self.assert_retained()

    def test_failure_before_control_copy_restores_original_watchdog(self):
        self.prepare();write=self.rt.write_state
        def fail(path,value):
            if value.get('phase')=='INSTALL_INTENT':raise OSError('fixture disk error before controls')
            return write(path,value)
        with patch.object(self.rt,'write_state',side_effect=fail),self.assertRaises(OSError):
            v.install_community_supervision(self.rt,self.ident)
        self.assertEqual(self.events,['stop','start'])
        self.assertFalse((self.rt.store/'COMMUNITY_SUPERVISION_ACTIVE.json').exists())
        self.assert_retained()
        v.install_community_supervision(self.rt,self.ident)
        self.effective()

    def test_replaced_original_lease_refuses_before_pause(self):
        self.prepare();self.rt.lock.rename(self.rt.lock.with_suffix('.original'))
        self.put(self.rt.lock,b'')
        with self.assertRaisesRegex(Exception,'lease_replaced'):
            v.install_community_supervision(self.rt,self.ident)
        self.assertEqual(self.events,[])


if __name__=='__main__':unittest.main()
