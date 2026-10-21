"""Real files/CAS/flock/recovery; OS watchdog callbacks are explicit fixtures.

No production identity, remote approval, process/model startup or database IO.
"""
import copy
import fcntl
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

import vmm_controller as v

HERE=Path(__file__).parent

class Controller(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'r25';self.root.mkdir();self.events=[]
        self.owner=(os.geteuid(),os.getegid());lock=Path(self.tmp.name)/'shared.lock';lock.write_bytes(b'');lock.chmod(0o600)
        self.rt=v.Runtime(self.root,lock,private=self.owner,scripts=self.owner,supervise=lambda action:self.events.append(action))
        (self.root/'runtime').mkdir();self.rt.mkdir(self.rt.store)
        for rel in ('operations/r25-publisher','runtime/config/assistant-private','runtime/app-pids'):(self.root/rel).mkdir(parents=True,exist_ok=True)
        self.policy={'schema':'R25_CORE_BUSINESS_POLICY_V2','upgrade':{'capability':'assistant-v1','core_followup':{}},
            'controllers':{'lifecycle_adapter.py':v.BASE_LIFECYCLE,'core_release_guard.py':v.BASE_GUARD,'r25-production-java-launch.py':'2'*64},
            'runtime_modules':{'core_release_guard.py':v.BASE_GUARD}}
        self.put(self.rt.preserved()['policy'],v.encoded(self.policy))
        self.prior={'schema':'R25_ASSISTANT_CORE_FOLLOWUP_BINDING_V1','operational':{'policy':{'after_sha256':v.sha(v.encoded(self.policy))}}}
        raw=v.encoded(self.prior);prior_id=v.sha(raw);base=self.root/'runtime/core-publications/controller-upgrades'/prior_id
        self.put(base/'approval.json',raw);self.put(base/'journal.json',v.encoded({'phase':'INSTALLED','approval_sha256':prior_id}))
        self.put(self.rt.preserved()['original-controller-active'],v.encoded({'schema':'R25_CORE_CONTROLLER_UPGRADE_ACTIVE_V1','approval_sha256':prior_id}))
        for kind in ('core','ai'):
            store=self.root/f'runtime/{kind}-publications';tx=('1' if kind=='core' else '2')*32
            self.put(store/'CURRENT.json',v.encoded({'schema':f'R25_{kind.upper()}_CURRENT_V1','transaction_id':tx,'release':{'commit':'3'*40}}))
            self.put(store/'transactions'/tx/'journal.json',v.encoded({'phase':'COMMITTED'}))
        for name,path in self.rt.preserved().items():
            if not path.exists():self.put(path,b'synthetic preserved '+name.encode())
        lifecycle=(HERE/'fixtures/lifecycle_assistant_v2.py.snapshot').read_bytes()
        self.assertEqual(v.sha(lifecycle),v.BASE_LIFECYCLE)
        self.put(self.rt.targets()['lifecycle'][0],lifecycle,0o700)
        # Remove exactly this extension's new hook to reconstruct audited base.
        guard=(HERE/'core_release_guard.py').read_text()
        start=guard.index("        if 'R25_VMM_CONTROLLER' in values:\n");end=guard.index('        return result\n',start)
        oldguard=(guard[:start]+guard[end:]).encode();self.assertEqual(v.sha(oldguard),v.BASE_GUARD)
        self.put(self.rt.targets()['operation-guard'][0],oldguard,0o700);self.put(self.rt.targets()['publisher-guard'][0],oldguard)
        handoff=(f'R25_LIFECYCLE_ADAPTER_SHA256={v.BASE_LIFECYCLE}\nR25_CORE_RELEASE_GUARD_SHA256={v.BASE_GUARD}\nUNCHANGED_SETTING=unchanged\n').encode()
        self.put(self.rt.targets()['handoff'][0],handoff)
        self.bundle=self.root/'operations/r25-vmm-candidates'/('a'*40);self.bundle.mkdir(parents=True)
        self.sources={name:(HERE/name).read_bytes() for name in v.MODULES}
        self.package()
        review=v.encoded({'fixture_only':True,'scope':'offline synthetic review not project approval'})
        self.put(self.rt.store/'REVIEW.json',review)
        request={'schema':'R25_VMM_CONTROLLER_REQUEST_V1','deploy':self.deploy,'review_evidence_sha256':v.sha(review)}
        self.put(self.rt.store/'REQUEST.json',v.encoded(request));self.req=v.sha(v.encoded(request))
        result=v.prepare(self.rt,self.req);self.ident=result['plan_sha256'];self.plan=json.loads(Path(result['plan_path']).read_bytes())
        self.put(self.rt.store/'APPROVAL.json',v.encoded(self.plan))
        self.before={name:path.read_bytes() if path.exists() else None for name,(path,_,_) in self.rt.targets().items()}
        self.preserved={name:path.read_bytes() for name,path in self.rt.preserved().items()}

    def put(self,path,raw,mode=0o600):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);path.chmod(mode)

    def package(self):
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:
            for name,raw in self.sources.items():z.writestr(name,raw)
        archive=data.getvalue();manifest={'schema':v.MANIFEST_SCHEMA,'repository':'bailangguihun/auralink-deploy','ref':'refs/heads/release','commit':'a'*40,
            'run_id':'123','run_attempt':'1','archive_sha256':v.sha(archive),
            'files':[{'path':name,'bytes':len(raw),'sha256':v.sha(raw)} for name,raw in self.sources.items()]}
        self.put(self.bundle/'vmm-extension.zip',archive);self.put(self.bundle/'manifest.json',v.encoded(manifest))
        for name,raw in self.sources.items():self.put(self.bundle/name,raw)
        self.deploy={k:manifest[k] for k in ('repository','ref','commit','run_id','run_attempt','archive_sha256')}
        self.deploy.update(manifest_sha256=v.sha(v.encoded(manifest)),tool_sha256=v.sha(self.sources['vmm_controller.py']))

    def assert_preserved(self):
        for name,path in self.rt.preserved().items():self.assertEqual(path.read_bytes(),self.preserved[name],name)

    def test_prepare_has_no_live_mutation_preflight_and_install_disabled(self):
        self.assertEqual(self.events,[]);self.assertEqual(v.preflight(self.rt,self.ident)['outcome'],'PREFLIGHT_PASS')
        self.assertEqual(v.bind(self.rt,self.ident)['outcome'],'INSTALLED');self.assertEqual(self.events,['stop','start'])
        self.assertEqual(json.loads(self.rt.targets()['role'][0].read_bytes()),{'schema':'R25_VMM_ROLE_V1','enabled':False})
        self.assert_preserved();self.assertEqual(v.bind(self.rt,self.ident)['outcome'],'ALREADY_INSTALLED');self.assertEqual(self.events,['stop','start'])

    def test_overlay_adjusts_only_control_hashes_and_preserves_raw_policy(self):
        v.bind(self.rt,self.ident)
        effective=v.apply_policy_overlay(self.policy,v.env_values(self.rt.targets()['handoff'][0].read_bytes()),self.root,fixture_runtime=self.rt)
        self.assertEqual(set(effective),set(self.policy));self.assertEqual(effective['upgrade'],self.policy['upgrade'])
        self.assertEqual(effective['controllers']['lifecycle_adapter.py'],self.plan['files']['lifecycle']['after_sha256'])
        self.assertEqual(effective['controllers']['core_release_guard.py'],self.plan['files']['operation-guard']['after_sha256'])
        self.assert_preserved()

    def test_existing_without_binding_policy_unchanged(self):
        self.assertIs(v.apply_policy_overlay(self.policy,{},self.root),self.policy)

    def test_each_copy_failure_recover_restores_only_owned_metadata(self):
        for name in self.rt.targets():
            with self.subTest(name=name):
                def fault(current):
                    if current==name:raise RuntimeError('fixture injected copy interruption')
                with self.assertRaises(RuntimeError):v.bind(self.rt,self.ident,fault=fault)
                self.assertEqual(v.journal(self.rt,self.rt.store/'transactions'/self.ident,self.ident)['phase'],'RECOVERY_REQUIRED')
                self.assertEqual(v.recover(self.rt,self.ident)['outcome'],'RESTORED')
                for key,(path,_,_) in self.rt.targets().items():self.assertEqual(path.read_bytes() if path.exists() else None,self.before[key])
                self.assert_preserved()
        self.assertEqual(v.recover(self.rt,self.ident)['outcome'],'ALREADY_RESTORED')

    def test_foreign_drift_refuses_before_pause(self):
        self.rt.targets()['handoff'][0].write_bytes(b'foreign bytes')
        with self.assertRaisesRegex(v.Refused,'target_drift'):v.bind(self.rt,self.ident)
        self.assertEqual(self.events,[])

    def test_foreign_drift_after_partial_copy_refuses_recovery(self):
        with self.assertRaises(RuntimeError):v.bind(self.rt,self.ident,fault=lambda name:(_ for _ in ()).throw(RuntimeError()) if name=='lifecycle' else None)
        self.rt.targets()['lifecycle'][0].write_bytes(b'foreign edit')
        with self.assertRaisesRegex(v.Refused,'target_drift'):v.recover(self.rt,self.ident)
        self.assertEqual(self.rt.targets()['lifecycle'][0].read_bytes(),b'foreign edit')

    def test_hash_and_approval_scope_conflicts_rejected(self):
        with self.assertRaises(v.Refused):v.bind(self.rt,'b'*64)
        wrong=copy.deepcopy(self.plan);wrong['files']['application']={'before_sha256':'1'*64,'after_sha256':'2'*64}
        with self.assertRaisesRegex(v.Refused,'plan_inventory'):v.validate_plan(self.rt,wrong)
        self.assertEqual(self.events,[])

    def test_real_shared_lock_contention_preserves_inode(self):
        inode=self.rt.lock.stat().st_ino
        with self.rt.lock.open('r+') as other:
            fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(v.Refused,'shared_publication_busy'):v.preflight(self.rt,self.ident)
        self.assertEqual(self.rt.lock.stat().st_ino,inode);self.assertEqual(self.events,[])

    def test_symlink_hardlink_and_mode_refused(self):
        path=self.rt.targets()['operation-guard'][0]
        for kind in ('symlink','hardlink','mode'):
            original=path.read_bytes();path.unlink();extra=path.with_name('extra');self.put(extra,original,0o700)
            if kind=='symlink':path.symlink_to(extra)
            elif kind=='hardlink':os.link(extra,path)
            else:self.put(path,original,0o777)
            with self.subTest(kind=kind),self.assertRaises(v.Refused):v.bind(self.rt,self.ident)
            path.unlink();extra.unlink();self.put(path,original,0o700)

    def test_formal_repository_manifest_archive_and_source_binding(self):
        d=copy.deepcopy(self.deploy);d['repository']='fixture/untrusted'
        with self.assertRaisesRegex(v.Refused,'formal_repository'):v.verify_bundle(self.rt,d)
        self.put(self.bundle/'vmm_service.py',b'not official source')
        with self.assertRaisesRegex(v.Refused,'file_digest'):v.verify_bundle(self.rt,self.deploy)
        self.assertEqual(self.events,[])

    def test_application_or_backup_incomplete_refused(self):
        path=self.root/'runtime/core-publications/transactions'/('1'*32)/'journal.json'
        self.put(path,v.encoded({'phase':'RECOVERY_REQUIRED'}))
        with self.assertRaisesRegex(v.Refused,'application_not_committed'):v.bind(self.rt,self.ident)
        self.put(path,v.encoded({'phase':'COMMITTED'}))
        store=self.root/'runtime/assistant-backups/assistant-daily-v1';self.put(store/'ACTIVE.json',v.encoded({'day':'2026-10-21'}));self.put(store/'runs/2026-10-21.json',v.encoded({'phase':'STOPPED'}))
        with self.assertRaisesRegex(v.Refused,'active_backup'):v.bind(self.rt,self.ident)
        self.assertEqual(self.events,[])

    def test_not_started_recovery_is_readonly(self):
        self.assertEqual(v.recover(self.rt,'f'*64),{'outcome':'NOT_STARTED'});self.assertEqual(self.events,[])

    def test_partial_transaction_not_rebound_and_overlay_not_usable(self):
        with self.assertRaises(RuntimeError):v.bind(self.rt,self.ident,fault=lambda name:(_ for _ in ()).throw(RuntimeError()) if name=='handoff' else None)
        with self.assertRaisesRegex(v.Refused,'recover_original'):v.bind(self.rt,self.ident)
        with self.assertRaisesRegex(v.Refused,'overlay_not_installed'):
            v.apply_policy_overlay(self.policy,v.env_values(self.rt.targets()['handoff'][0].read_bytes()),self.root,fixture_runtime=self.rt)

    def prepare_role_case(self):
        import vmm_service
        service={'schema':'R25_VMM_SERVICE_BINDING_V1','host':'127.0.0.1','port':5001,
            'legacy_commit':vmm_service.LEGACY_COMMIT,'legacy_hashes':vmm_service.LEGACY_HASHES,
            'source_root':'/root/autodl-tmp/auralink','token_file':str(self.root/'runtime/config/vmm-token'),
            'state_dir':str(self.root/'runtime/vmm'),'output_root':'/root/autodl-tmp/auralink/backend/temp_uploads',
            'checkpoint':'/root/autodl-tmp/auralink/VMM/models/final_model.pth','musicgen_path':'/root/autodl-tmp/auralink/VMM/models/musicgen-small'}
        paths=[service['musicgen_path']+'/'+x for x in ('compression_state_dict.bin','state_dict.bin','config.json','spiece.model','tokenizer.json','tokenizer_config.json')]+[service['checkpoint'],'/root/.cache/clip/ViT-B-32.pt']
        service['model_assets']=[{'path':x,'sha256':'40d365715913c9da98579312b702a82c18be219cc2a73407c4526f58eba950af' if x.endswith('ViT-B-32.pt') else '1'*64} for x in paths]
        role={'schema':'R25_VMM_ROLE_V1','enabled':False,'service_binding':str(self.root/'runtime/config/vmm-service.json'),
            'service_binding_sha256':v.sha(v.encoded(service)),'service_script_sha256':v.sha(self.sources['vmm_service.py']),
            'interpreter':'/root/autodl-tmp/auralink/micromamba/envs/auralink-ai/bin/python','interpreter_sha256':'2'*64,
            'restart_limit':1,'startup_seconds':900,'stop_seconds':30}
        self.put(self.rt.store/'ROLE.next.json',v.encoded(role));self.put(self.rt.store/'SERVICE.next.json',v.encoded(service))
        request={'schema':'R25_VMM_ROLE_BINDING_REQUEST_V1','controller_approval_sha256':self.ident,
            'review_evidence_sha256':self.plan['review_evidence_sha256'],'role_sha256':v.sha(v.encoded(role)),'service_sha256':v.sha(v.encoded(service))}
        self.put(self.rt.store/'ROLE_REQUEST.json',v.encoded(request));r=v.prepare_role(self.rt,v.sha(v.encoded(request)))
        self.put(self.rt.store/'ROLE_APPROVAL.json',Path(r['plan_path']).read_bytes())
        return r['plan_sha256']

    def test_role_configuration_binding_disabled_and_metadata_rollback(self):
        v.bind(self.rt,self.ident);roleid=self.prepare_role_case()
        self.assertEqual(v.bind_role(self.rt,roleid)['outcome'],'ROLE_INSTALLED_DISABLED')
        self.assertFalse(json.loads((self.root/'runtime/config/vmm-role.json').read_bytes())['enabled'])
        self.assertFalse((self.root/'runtime/vmm/lifecycle.json').exists())
        effective=v.apply_policy_overlay(self.policy,v.env_values(self.rt.targets()['handoff'][0].read_bytes()),self.root,fixture_runtime=self.rt)
        self.assertEqual(effective['upgrade'],self.policy['upgrade'])
        self.assertEqual(v.recover_role(self.rt,roleid)['outcome'],'ROLE_RESTORED')
        self.assertFalse((self.root/'runtime/config/vmm-service.json').exists())
        self.assertEqual(json.loads(self.rt.targets()['role'][0].read_bytes()),{'schema':'R25_VMM_ROLE_V1','enabled':False})
        self.assert_preserved()

    def test_role_each_partial_write_is_rolled_back_without_ledger_changes(self):
        v.bind(self.rt,self.ident);roleid=self.prepare_role_case()
        for name in ('role','service','handoff'):
            with self.subTest(name=name):
                def fail(key):
                    if key==name:raise RuntimeError('injected role copy failure')
                with self.assertRaises(RuntimeError):v.bind_role(self.rt,roleid,fault=fail)
                self.assertFalse((self.root/'runtime/config/vmm-service.json').exists())
                self.assertEqual(json.loads(self.rt.targets()['role'][0].read_bytes()),{'schema':'R25_VMM_ROLE_V1','enabled':False})
                self.assert_preserved()

    def test_role_update_refuses_enabled_or_uncertain_work(self):
        v.bind(self.rt,self.ident);roleid=self.prepare_role_case();v.bind_role(self.rt,roleid)
        state=self.root/'runtime/vmm/lifecycle.json';self.put(state,v.encoded({'enabled':True,'desired':'RUNNING'}))
        with self.assertRaisesRegex(v.Refused,'disabled_stopped'):v.recover_role(self.rt,roleid)
        self.put(state,v.encoded({'enabled':False,'desired':'STOPPED'}));gate=self.root/'runtime/vmm/gate.json'
        self.put(gate,v.encoded({'mode':'CLOSED','requests':{'synthetic':{'state':'UNKNOWN'}}}))
        saved=gate.read_bytes()
        with self.assertRaisesRegex(v.Refused,'uncertainty'):v.recover_role(self.rt,roleid)
        self.assertEqual(gate.read_bytes(),saved)

    def test_overlay_allows_normal_web_and_process_change_but_not_policy_drift(self):
        v.bind(self.rt,self.ident)
        self.put(self.rt.preserved()['frontend'],b'new accepted web selection')
        self.put(self.rt.preserved()['java-proof'],b'new owned java process proof')
        v.apply_policy_overlay(self.policy,v.env_values(self.rt.targets()['handoff'][0].read_bytes()),self.root,fixture_runtime=self.rt)
        self.put(self.rt.preserved()['policy'],b'foreign policy bytes')
        with self.assertRaisesRegex(v.Refused,'preserved_context_drift'):
            v.apply_policy_overlay(self.policy,v.env_values(self.rt.targets()['handoff'][0].read_bytes()),self.root,fixture_runtime=self.rt)

    def test_runtime_overlay_does_not_block_original_publication_or_backup_recovery(self):
        v.bind(self.rt,self.ident)
        # The original guard validates these transactions. This overlay must
        # not impose an installation-only terminal-state check during resume.
        core=self.root/'runtime/core-publications';tx='4'*32
        self.put(core/'ACTIVE.json',v.encoded({'transaction_id':tx}))
        self.put(core/'transactions'/tx/'journal.json',v.encoded({'phase':'START_INTENT'}))
        backup=self.root/'runtime/assistant-backups/assistant-daily-v1'
        self.put(backup/'ACTIVE.json',v.encoded({'day':'2026-10-21'}))
        self.put(backup/'runs/2026-10-21.json',v.encoded({'phase':'STOPPED'}))
        self.rt.preserved()['java-proof'].unlink()
        effective=v.apply_policy_overlay(self.policy,v.env_values(self.rt.targets()['handoff'][0].read_bytes()),self.root,fixture_runtime=self.rt)
        self.assertEqual(effective['upgrade'],self.policy['upgrade'])
        # Installation remains a strict snapshot/idle boundary.
        with self.assertRaises((v.Refused,FileNotFoundError)):
            self.rt.require_context(self.plan['preserved'])

    def test_runtime_handoff_permits_web_updates_but_rejects_control_drift(self):
        v.bind(self.rt,self.ident);path=self.rt.targets()['handoff'][0]
        good=path.read_bytes().replace(b'UNCHANGED_SETTING=unchanged',b'UNCHANGED_SETTING=accepted-new-web')
        good+=b'R25_FRONTEND_SELECTION_REFERENCE=updated-reference\n';self.put(path,good)
        v.apply_policy_overlay(self.policy,v.env_values(good),self.root,fixture_runtime=self.rt)
        for changed in (good.replace(b'R25_VMM_SERVICE_SHA256=',b'R25_VMM_SERVICE_SHA256=0'),
                        good+b'R25_VMM_UNAPPROVED_MODULE=foreign\n',
                        good.replace(b'R25_CORE_RELEASE_GUARD_SHA256=',b'R25_CORE_RELEASE_GUARD_SHA256=0')):
            with self.subTest(change=v.sha(changed)):
                self.put(path,changed)
                with self.assertRaisesRegex(v.Refused,'runtime_handoff_control_drift'):
                    v.apply_policy_overlay(self.policy,v.env_values(changed),self.root,fixture_runtime=self.rt)

    def test_role_binding_after_unrelated_handoff_change_and_live_service_drift(self):
        v.bind(self.rt,self.ident);path=self.rt.targets()['handoff'][0]
        self.put(path,path.read_bytes()+b'R25_FRONTEND_SELECTION_REFERENCE=accepted-next-web\n')
        roleid=self.prepare_role_case();v.bind_role(self.rt,roleid)
        good=path.read_bytes();v.apply_policy_overlay(self.policy,v.env_values(good),self.root,fixture_runtime=self.rt)
        service=self.root/'runtime/config/vmm-service.json';original=service.read_bytes()
        self.put(service,original+b' ')
        with self.assertRaisesRegex(v.Refused,'file_digest'):
            v.apply_policy_overlay(self.policy,v.env_values(good),self.root,fixture_runtime=self.rt)
        self.put(service,original)
        self.assertEqual(v.recover_role(self.rt,roleid)['outcome'],'ROLE_RESTORED')
        self.assertIn(b'R25_FRONTEND_SELECTION_REFERENCE=accepted-next-web',path.read_bytes())

    def test_live_watchdog_blocks_mixed_recovery_without_signal(self):
        with self.assertRaises(RuntimeError):v.bind(self.rt,self.ident,fault=lambda name:(_ for _ in ()).throw(RuntimeError()) if name=='lifecycle' else None)
        raw=Path('/proc/self/stat').read_text();ticks=raw[raw.rfind(')')+2:].split()[19]
        self.put(self.root/'runtime/app-supervisor.pid',f'pid={os.getpid()}\nstart_ticks={ticks}\n'.encode())
        with self.assertRaisesRegex(v.Refused,'watchdog_must_be_stopped'):v.recover(self.rt,self.ident)
        self.assertTrue(Path('/proc/self').exists())

    def test_stop_or_resume_failure_is_honest_and_recoverable(self):
        events=[]
        def failed_stop(mode):
            events.append(mode)
            if mode=='stop':raise v.Refused('stop acknowledgement lost')
        self.rt.supervise=failed_stop
        with self.assertRaisesRegex(v.Refused,'acknowledgement'):v.bind(self.rt,self.ident)
        self.assertEqual(events,['stop','start'])
        def failed_resume(mode):
            events.append(mode)
            if mode=='start':raise v.Refused('start failure')
        self.rt.supervise=failed_resume
        with self.assertRaisesRegex(v.Refused,'start failure'):v.bind(self.rt,self.ident)
        self.rt.supervise=lambda x:events.append(x)
        self.assertEqual(v.recover(self.rt,self.ident)['outcome'],'ALREADY_INSTALLED_SUPERVISION_RESTORED')
        self.assertEqual(events[-1],'start')

    def test_actual_shell_supervision_fixture_receives_no_held_shared_lease(self):
        import subprocess
        script=Path(self.tmp.name)/'owned-supervisor-fixture.sh';log=Path(self.tmp.name)/'supervisor.log'
        script.write_text('#!/bin/bash\nset -Eeuo pipefail\nexec 9<>"$2"\nflock -n 9\nprintf "%s\\n" "$1" >> "$3"\n')
        def run(mode):
            subprocess.run(['/bin/bash',str(script),mode,str(self.rt.lock),str(log)],check=True,timeout=2)
        self.rt.supervise=run;v.bind(self.rt,self.ident)
        self.assertEqual(log.read_text().splitlines(),['stop','start'])

    def test_explicit_controller_rollback_preserves_original_policy_and_data(self):
        v.bind(self.rt,self.ident)
        self.assertEqual(v.recover(self.rt,self.ident,rollback=True)['outcome'],'RESTORED')
        self.assert_preserved()
        for key,(path,_,_) in self.rt.targets().items():self.assertEqual(path.read_bytes() if path.exists() else None,self.before[key])

if __name__=='__main__':unittest.main()
