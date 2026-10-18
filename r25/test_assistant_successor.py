"""Successive Assistant V2: real file transitions, mocked OS/HTTP only.

Synthetic approvals/source bytes are isolated fixtures, not project approval.
No provider, production path, database restore or migration is executed.
"""
from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ai_runtime
import ai_release_guard
import core_business as business
import core_release_guard as guard
import core_runtime
import test_ai_runtime as ai_fixture
import test_assistant_runtime_contract as contract_fixture


def config_fixture(root):
    fixture=contract_fixture.AssistantContract('runTest');fixture.setUp()
    policy=deepcopy(fixture.policy)
    old_ai={'mode':'enabled','models':[
        {'id':'text','provider':'dashscope','model':'qwen3.8-flash','baseUrl':'https://dashscope.aliyuncs.com/compatible-mode/v1'},
        {'id':'vision','provider':'dashscope','model':'qwen3-vl-plus','baseUrl':'https://dashscope.aliyuncs.com/compatible-mode/v1'}],
        'limits':{'chatTimeoutMs':90000,'summaryTimeoutMs':60000},'ragflow':{'enabled':False}}
    ai_before=guard.encoded(old_ai);new_ai=deepcopy(old_ai);new_ai['models'][0]['thinkingMode']='disabled';ai_after=guard.encoded(new_ai)
    policy['upgrade']['configuration']['assistant']['execution.expected-config-sha256']=business.sha(ai_before)
    values=policy['upgrade']['configuration']['assistant']
    app=fixture.before+b'\n# R25 reviewed Assistant configuration\n'+b''.join(
        ('auralink.assistant.'+k+': '+json.dumps(v)+'\n').encode() for k,v in sorted(values.items()))
    config=policy['upgrade']['configuration'];config['before_sha256']=config['after_sha256']=business.sha(app)
    policy['upgrade']['assistant']['configuration_sha256']=business.sha(app)
    final=app.replace(business.sha(ai_before).encode(),business.sha(ai_after).encode())
    policy['upgrade']['successor']={'schema':'R25_ASSISTANT_SUCCESSOR_CONFIGURATION_V1',
        'ai_before':{'commit_sha':'1'*40,'source_tree_sha256':'2'*64,'record_sha256':'3'*64},
        'ai_candidate':{'repository':'bailangguihun/auralink-ai-service','commit_sha':'4'*40,
            'github_run_id':'123','github_run_attempt':'1','source_tree_sha256':'5'*64,'archive_sha256':'6'*64},
        'ai_configuration':{'before_sha256':business.sha(ai_before),'after_sha256':business.sha(ai_after)},
        'core_final_sha256':business.sha(final)}
    return fixture,policy,app,final,ai_before,ai_after


class FiniteConfiguration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        fixture,self.policy,self.before,self.after,self.ai_before,self.ai_after=config_fixture(Path(self.temp.name))
        self.addCleanup(fixture.doCleanups)

    def test_bridge_unchanged_and_only_expected_hash_changes(self):
        self.assertEqual(business.render_configuration(self.before,self.policy),self.before)
        self.assertEqual(business.render_successor_configuration(self.before,self.policy),self.after)
        self.assertEqual(business.validate_successor_ai_configuration(self.ai_before,self.ai_after,self.policy)['models'][0]['thinkingMode'],'disabled')
        self.assertNotIn('thinkingMode',json.loads(self.ai_after)['models'][1])

    def test_existing_config_duplicate_alias_nested_and_unrelated_mutation_rejected(self):
        for extra in (b'auralink.assistant.enabled: true\n',b'  assistant:\n    enabled: true\n', b'"auralink.assistant.enabled": true\n'):
            value=self.before+extra;p=deepcopy(self.policy)
            p['upgrade']['configuration']['before_sha256']=p['upgrade']['configuration']['after_sha256']=business.sha(value)
            p['upgrade']['assistant']['configuration_sha256']=business.sha(value)
            with self.subTest(extra=extra),self.assertRaises(business.Refused):business.render_configuration(value,p)
        for change in ('rag','vl','limits','string'):
            value=json.loads(self.ai_after)
            if change=='rag':value['ragflow']['enabled']=True
            if change=='vl':value['models'][1]['thinkingMode']='disabled'
            if change=='limits':value['limits']['chatTimeoutMs']=90001
            if change=='string':value['models'][0]['thinkingMode']=False
            raw=guard.encoded(value);p=deepcopy(self.policy);p['upgrade']['successor']['ai_configuration']['after_sha256']=business.sha(raw)
            with self.subTest(change=change),self.assertRaisesRegex(business.Refused,'unrelated_change'):
                business.validate_successor_ai_configuration(self.ai_before,raw,p)

    def test_no_successor_cannot_enable_equal_configuration(self):
        del self.policy['upgrade']['successor']
        with self.assertRaisesRegex(business.Refused,'configuration_baseline'):business.validate_policy(self.policy)


class AIConfigurationTransaction(unittest.TestCase):
    def setUp(self):
        f=ai_fixture.AdapterTests('runTest');f.setUp();self.f=f;self.addCleanup(f.tearDown)
        fixture,self.policy,app,final,ai_before,ai_after=config_fixture(f.root);self.addCleanup(fixture.doCleanups)
        self.raw={'app':app,'final':final,'ai_before':ai_before,'ai_after':ai_after}
        policy=f.files.read_private_record(f.store/'POLICY.json');oldsha=f.files.file_identity(f.store/'POLICY.json')['sha256']
        policy.update(schema='R25_AI_SOURCE_POLICY_V2',repository='bailangguihun/auralink-ai-service',
            source_paths=list(ai_release_guard.ASSISTANT_PATHS),legacy_policy_sha256=oldsha,
            source_owner=[1000,1000],review_evidence_sha256='a'*64)
        newsha=f.files.atomic_write_json(f.store/'POLICY.json',policy,replace=True,expected_existing_sha256=oldsha)['sha256']
        (f.root/'runtime/handoff-control.env').write_text('R25_AI_RELEASE_POLICY_SHA256='+newsha+'\n')
        # Both sides genuinely have the same finite 27-file inventory.
        source={**f.source,**{name:b'# installed Assistant fixture\n' for name in set(ai_release_guard.ASSISTANT_PATHS)-set(f.source)}}
        self.before=self.accepted(source,'7'*40,newsha,policy)
        newer=dict(source);newer['critic-service/app/assistant/providers.py']+=b'# corrected parameter source\n'
        self.after=self.accepted(newer,'8'*40,newsha,policy)
        for name,raw in source.items():
            path=f.root/'release'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        f.files.atomic_write_json(f.store/'CURRENT.json',{'schema':'R25_AI_CURRENT_V1','release':self.before},replace=True,
            expected_existing_sha256=f.files.file_identity(f.store/'CURRENT.json')['sha256'])
        transition=self.policy['upgrade']['successor'];transition['ai_before']=self.before
        record=f.rt.verify_candidate(self.after) if False else f.files.read_private_record(ai_release_guard.release_folder(self.after)/'record.json')
        transition['ai_candidate']={k:record[k] for k in transition['ai_candidate']}
        core=self.policy['upgrade']['candidate'];f.rt.core.release=core_runtime.Release(core['commit_sha'],core['artifact_sha256'],'b'*64)
        f.files.atomic_write_json(f.core_store/'POLICY.json',self.policy)
        f.files.atomic_write_json(f.core_store/'CURRENT.json',{'schema':'R25_CORE_CURRENT_V1','release':asdict(f.rt.core.release)})
        f.rt.core.identity=f.files.file_identity
        f.rt.core.acceptance=lambda *a,**kw:{'gallery_total':40,'assistant':'fixture-readonly'}
        for name,raw in (('application.yml',app),('assistant-private/assistant-runtime.json',ai_before)):
            path=f.root/'runtime/config'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);path.chmod(0o600)
        folder=f.store/'successor-configurations';folder.mkdir();(folder/(business.sha(ai_after)+'.json')).write_bytes(ai_after)
        p=patch.object(guard,'policy',side_effect=self.bound_policy);p.start();self.addCleanup(p.stop)
        p=patch.object(guard,'business_module',return_value=business);p.start();self.addCleanup(p.stop)
        p=patch.object(guard,'ROOT',f.root);p.start();self.addCleanup(p.stop)
        p=patch.object(guard,'read_file',side_effect=f.read_file);p.start();self.addCleanup(p.stop)
        p=patch.object(guard,'private_record',side_effect=f.private_record);p.start();self.addCleanup(p.stop)
        self.next_count=0

    def bound_policy(self):
        guard.validate_configuration_context(self.policy)
        return self.policy

    def accepted(self,source,commit,policysha,policy):
        f=self.f;files=[{'path':k,'sha256':business.sha(v),'bytes':len(v)} for k,v in sorted(source.items())]
        ref={'commit_sha':commit,'source_tree_sha256':business.sha(ai_release_guard.canonical(files)),'record_sha256':'0'*64}
        folder=ai_release_guard.release_folder(ref);(folder/'source').mkdir(parents=True)
        for name,raw in source.items():
            p=folder/'source'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
        record={'schema':'R25_AI_ACCEPTED_SOURCE_V2','repository':'bailangguihun/auralink-ai-service','commit_sha':commit,
            'github_run_id':'123','github_run_attempt':'1','archive_sha256':'6'*64,
            'source_tree_sha256':ref['source_tree_sha256'],'files':files,'dependency_sha256':policy['dependency_sha256'],'policy_sha256':policysha}
        ref['record_sha256']=f.files.atomic_write_json(folder/'record.json',record)['sha256'];return ref

    def assert_restored(self,tx):
        f=self.f;self.assertEqual(f.rt.current(),self.before)
        self.assertEqual((f.root/'runtime/config/application.yml').read_bytes(),self.raw['app'])
        self.assertEqual((f.root/'runtime/config/assistant-private/assistant-runtime.json').read_bytes(),self.raw['ai_before'])
        self.assertEqual(f.rt.read(f.rt.transaction(tx)/'journal.json')['phase'],'RESTORED')
        self.assertEqual(guard.successor_configuration_phase(self.policy),'bridge')
        self.assertNotIn('stop:guide',f.events)

    def test_publish_exact_pair_and_restore_same_original_transaction(self):
        f=self.f;result=ai_runtime.publish(f.rt,self.after)
        self.assertEqual(result['outcome'],'AI_SOURCE_PUBLISHED',result)
        self.assertEqual((f.root/'runtime/config/application.yml').read_bytes(),self.raw['final'])
        self.assertEqual(guard.successor_configuration_phase(self.policy),'final')
        self.assertEqual(business.config_for_release(self.policy,self.policy['upgrade']['candidate']),business.sha(self.raw['final']))
        with f.rt.core.lease():ai_runtime.recover_locked(f.rt,result['transaction_id'])
        self.assert_restored(result['transaction_id'])

    def test_failure_after_ai_config_copy_restores_all_bytes_and_journal(self):
        f=self.f;original=f.files.atomic_copy_file;fired=False
        def copy(source,target,digest,**kw):
            nonlocal fired
            if Path(target)==f.root/'runtime/config/application.yml' and not fired:
                fired=True;raise OSError('injected core configuration write failure')
            return original(source,target,digest,**kw)
        with patch.object(f.files,'atomic_copy_file',side_effect=copy):result=ai_runtime.publish(f.rt,self.after)
        self.assertEqual(result['outcome'],'AI_PUBLICATION_FAILED',result);self.assertTrue(result['restored'],result)
        self.assert_restored(result['transaction_id'])

    def test_startup_failure_and_uncertain_pointer_commit_restore_without_replay(self):
        for point in ('start','commit'):
            with self.subTest(point=point):
                # One isolated transaction per test branch, no provider path exists.
                original=self.f.rt.start_changed if point=='start' else self.f.rt.commit;fired=False
                def fail(*a,**kw):
                    nonlocal fired
                    value=original(*a,**kw)
                    if not fired:fired=True;raise TimeoutError('uncertain boundary')
                    return value
                name='start_changed' if point=='start' else 'commit'
                with patch.object(self.f.rt,name,side_effect=fail):result=ai_runtime.publish(self.f.rt,self.after)
                self.assertTrue(result['restored'],result);self.assert_restored(result['transaction_id'])

    def test_foreign_config_and_wrong_formal_attempt_refused_before_quiesce(self):
        f=self.f;transition=self.policy['upgrade']['successor'];transition['ai_candidate']['github_run_attempt']='2'
        # Preserve pinned policy hash when fixture deliberately changes reviewed input.
        with self.assertRaisesRegex(Exception,'selected_candidate'):ai_runtime.publish(f.rt,self.after)
        self.assertNotIn('stop:java',f.events)
        transition['ai_candidate']['github_run_attempt']='1'
        (f.root/'runtime/config/application.yml').write_bytes(self.raw['app']+b'# foreign\n')
        with self.assertRaisesRegex(Exception,'configuration_state'):ai_runtime.publish(f.rt,self.after)
        self.assertNotIn('stop:java',f.events)

    def test_forged_or_stale_journal_cannot_authorize_final_configuration(self):
        f=self.f;result=ai_runtime.publish(f.rt,self.after);self.assertEqual(result['outcome'],'AI_SOURCE_PUBLISHED',result)
        path=f.rt.transaction(result['transaction_id'])/'journal.json';journal=f.rt.read(path)
        journal['snapshot_sha256']='0'*64;f.rt.write(path,journal)
        with self.assertRaises(Exception):guard.successor_configuration_phase(self.policy)




    def test_core_rollback_refuses_before_mutation_until_ai_restored(self):
        f=self.f;result=ai_runtime.publish(f.rt,self.after)
        self.assertEqual(result['outcome'],'AI_SOURCE_PUBLISHED')
        runtime=core_runtime.CoreRuntime.__new__(core_runtime.CoreRuntime)
        runtime.require_lease=lambda:None
        # Actual method must refuse before touching journal/files or processes.
        runtime.tx=None
        with patch.object(guard,'release_record',return_value={'schema':guard.BUSINESS_RECORD_SCHEMA}):
            with self.assertRaisesRegex(Exception,'successor_restore_ai_before_core'):
                runtime.verify_rollback_source(f.rt.core.release)
        self.assertEqual(f.rt.current(),self.after)

    def test_mixed_phase_and_wrong_current_cannot_authorize_start(self):
        f=self.f;result=ai_runtime.publish(f.rt,self.after)
        self.assertEqual(result['outcome'],'AI_SOURCE_PUBLISHED')
        with f.rt.core.lease():f.rt.record(result['transaction_id'],'INSTALLED')
        self.assertEqual(guard.successor_configuration_phase(self.policy),'transition')
        with self.assertRaisesRegex(Exception,'configuration_not_startable'):
            business.config_for_release(self.policy,self.policy['upgrade']['candidate'])
        with f.rt.core.lease():f.rt.record(result['transaction_id'],'COMMITTED')
        path=f.store/'CURRENT.json';value=f.rt.read(path);value['release']=self.before;f.rt.write(path,value)
        with self.assertRaisesRegex(Exception,'prior_journal'):
            guard.successor_configuration_phase(self.policy)


class InstalledV2Upgrade(unittest.TestCase):
    """Actual installer/CAS/fsync/flock, synthetic signed-input/source boundary.

    Outer business/config validators are exercised above; the dynamic complete
    PG-contract validator remains the existing installer fixture. No fixture is
    presented as an approved project artifact or production rollback exercise.
    """
    def setUp(self):
        import test_assistant_controller_upgrade as installer_fixture
        import upgrade_assistant_controller as upgrade
        import importlib.util
        fixture=installer_fixture.Successor('runTest');fixture.setUp();self.f=fixture;self.addCleanup(fixture.doCleanups)
        self.upgrade=upgrade;f=fixture
        contract,self.policy,app,final,ai_before,ai_after=config_fixture(f.root);self.addCleanup(contract.doCleanups)
        self.plan=f.plan;self.plan['schema']=upgrade.SUCCESSOR_SCHEMA
        owner=[os.getuid(),os.getgid()]
        live={'handoff':f.root/'runtime/handoff-control.env','application':f.root/'runtime/config/application.yml',
              'manifest':f.root/'manifest.json','release-jar':f.root/'release/backend.jar','preview-jar':f.root/'preview.jar'}
        patcher=patch.object(upgrade,'LIVE_FILES',live);patcher.start();self.addCleanup(patcher.stop)
        for path in (f.root/'runtime/config',f.root/'runtime/config/assistant-private'):f.rt.storage.mkdir_private(path)
        for name,raw in (('application',app),('manifest',b'{}\n'),('release-jar',b'current jar fixture'),('preview-jar',b'current jar fixture')):
            f.rt.storage.atomic_write_bytes(live[name],raw)
        f.release=core_runtime.Release('a'*40,business.sha(b'current jar fixture'),'c'*64)
        f.rt.write(f.store/'CURRENT.json',{'release':asdict(f.release)})
        self.plan['current_release']=asdict(f.release)
        self.plan['preserved']={k:f.rt.identity(live[k])['sha256'] for k in ('application','manifest','release-jar','preview-jar')}
        self.plan['services']={k:{'pid':123,'ticks':456} for k in ('java','guide','critic')}
        self.plan['current_ai_release']=self.policy['upgrade']['successor']['ai_before']
        f.rt.storage.atomic_write_json(f.root/'runtime/ai-publications/CURRENT.json',{'schema':'R25_AI_CURRENT_V1','release':self.plan['current_ai_release']})
        for name,entry in self.plan['files'].items():
            if entry['before_sha256'] is None:
                raw=('already installed old V2 '+name).encode()
                f.rt.storage.atomic_write_bytes(f.root/'operations/r25-publisher'/name,raw)
                entry['before_sha256']=business.sha(raw)
        for name in ('critic-wrapper','lifecycle'):
            target,owner,mode=upgrade.targets(self.plan)[name];after=upgrade.source(f.bundle,name).read_bytes()
            f.rt.storage.atomic_write_bytes(target,after,mode=mode,replace=True,expected_existing_sha256=self.plan['operational'][name]['before_sha256'])
            self.plan['operational'][name]['before_sha256']=self.plan['operational'][name]['after_sha256']
        for name in set(ai_release_guard.ASSISTANT_PATHS)-set(ai_release_guard.SOURCE_PATHS):
            p=f.root/'release'/name;f.rt.storage.mkdir_private(p.parent);f.rt.storage.atomic_write_bytes(p,b'# old Assistant source\n')
        old_ai={'schema':'R25_AI_SOURCE_POLICY_V2','repository':'bailangguihun/auralink-ai-service',
            'source_paths':list(ai_release_guard.ASSISTANT_PATHS),'dependency_sha256':{'fixture':'f'*64},
            'legacy_policy_sha256':'a'*64,'source_owner':[os.getuid(),os.getgid()],'review_evidence_sha256':'b'*64}
        a=f.rt.write(f.root/'runtime/ai-publications/POLICY.json',old_ai)
        f.rt.write(f.bundle/'AI_POLICY.next.json',old_ai)
        self.plan['operational']['ai-policy']={'before_sha256':a['sha256'],'after_sha256':a['sha256']}
        f.rt.storage.atomic_write_bytes(f.root/'runtime/config/assistant-private/assistant-runtime.json',ai_before)
        f.rt.storage.atomic_write_bytes(f.bundle/'ASSISTANT_CONFIG.next.json',ai_after)
        old=deepcopy(self.policy);old['upgrade'].pop('successor')
        old['upgrade']['candidate'].update(commit_sha=f.release.commit_sha,artifact_sha256=f.release.artifact_sha256)
        old['upgrade']['configuration']['before_sha256']='f'*64
        old['runtime_modules']={k:v['before_sha256'] for k,v in self.plan['files'].items()}
        old['controllers']={'core_release_guard.py':self.plan['files']['core_release_guard.py']['before_sha256'],
            'lifecycle_adapter.py':self.plan['operational']['lifecycle']['before_sha256'],'r25-production-java-launch.py':'f'*64}
        for role,repo in (('core','core-api'),('ai','ai-service'),('web','web'),('deploy','deploy')):
            old['upgrade']['assistant']['artifacts'][role]={'repository':'bailangguihun/auralink-'+repo,'commit':'a'*40,'sha256':'f'*64}
        old['upgrade']['assistant']['compatibility_evidence_sha256']='1'*64;old['upgrade']['assistant']['review_evidence_sha256']='2'*64
        oldmeta=f.rt.write(f.store/'POLICY.json',old)
        # Package exact synthetic review-bound payload, not an invented project run.
        spec=importlib.util.spec_from_file_location('successor_fixture_package',Path(__file__).parent.parent/'scripts/package-r25-release.py')
        pack=importlib.util.module_from_spec(spec);spec.loader.exec_module(pack)
        payload={name:(f.bundle/name).read_bytes() if (f.bundle/name).exists() else (Path(__file__).parent/name).read_bytes() for name in pack.FILES}
        blob=pack.archive_bytes(payload);manifest=pack.manifest_for(payload,blob,
            pack.identity('bailangguihun/auralink-deploy',pack.REF,'d'*40,'42','1'))
        for name,raw in payload.items():
            if not (f.bundle/name).exists():f.rt.storage.atomic_write_bytes(f.bundle/name,raw)
        f.rt.storage.atomic_write_bytes(f.bundle/'r25-controller.zip',blob)
        fm=f.rt.storage.atomic_write_json(f.bundle/'r25-controller-manifest.json',manifest)
        self.plan['deploy'].update(archive_sha256=business.sha(blob),manifest_sha256=fm['sha256'],tool_sha256=business.sha(payload['upgrade_assistant_controller.py']))
        new=deepcopy(old);new['runtime_modules']={k:v['after_sha256'] for k,v in self.plan['files'].items()}
        new['controllers']['core_release_guard.py']=self.plan['files']['core_release_guard.py']['after_sha256']
        new['upgrade']['candidate']=deepcopy(self.policy['upgrade']['candidate'])
        new['upgrade']['baseline_release']=asdict(f.release)
        new['upgrade']['configuration']=deepcopy(self.policy['upgrade']['configuration'])
        new['upgrade']['successor']=deepcopy(self.policy['upgrade']['successor'])
        assistant=new['upgrade']['assistant'];assistant.update(
            baseline_policy_sha256=oldmeta['sha256'],baseline_payload_fingerprint=old['upgrade']['candidate']['payload_fingerprint'],
            compatible_recovery_jar_sha256=f.release.artifact_sha256,review_evidence_sha256=self.plan['review_evidence_sha256'])
        assistant['artifacts']['core']={'repository':new['repository'],'commit':new['upgrade']['candidate']['commit_sha'],'sha256':new['upgrade']['candidate']['artifact_sha256']}
        ai=new['upgrade']['successor']['ai_candidate']
        assistant['artifacts']['ai']={'repository':ai['repository'],'commit':ai['commit_sha'],'sha256':ai['archive_sha256']}
        assistant['artifacts']['deploy']={'repository':self.plan['deploy']['repository'],'commit':'d'*40,'sha256':self.plan['deploy']['archive_sha256']}
        newmeta=f.rt.write(f.bundle/'POLICY.next.json',new)
        self.plan['operational']['policy']={'before_sha256':oldmeta['sha256'],'after_sha256':newmeta['sha256']}
        mapping={'R25_CORE_RELEASE_POLICY_SHA256':'policy','R25_CORE_RELEASE_GUARD_SHA256':'guard','R25_LIFECYCLE_ADAPTER_SHA256':'lifecycle',
            'R25_R24_CRITIC_ENV_WRAPPER_SHA256':'critic-wrapper','R25_AI_RELEASE_GUARD_SHA256':'ai-guard','R25_AI_RELEASE_POLICY_SHA256':'ai-policy'}
        before=''.join(k+'='+self.plan['operational'][v]['before_sha256']+'\n' for k,v in mapping.items()).encode()
        after=core_runtime.replace_env(before,{k:self.plan['operational'][v]['after_sha256'] for k,v in mapping.items()})
        for path,raw in ((live['handoff'],before),(f.bundle/'HANDOFF.next.env',after)):
            f.rt.storage.atomic_write_bytes(path,raw,replace=True,expected_existing_sha256=f.rt.identity(path)['sha256'])
        self.plan['operational']['handoff']={'before_sha256':business.sha(before),'after_sha256':business.sha(after)}
        review=f.rt.storage.atomic_write_json(f.store/'CONTROL_UPGRADE_REVIEW.json',{'fixture':'synthetic owner delegation; not project approval'})
        self.plan['review_evidence_sha256']=review['sha256']
        # Review digest is also part of the exact candidate policy bytes.
        new['upgrade']['assistant']['review_evidence_sha256']=review['sha256']
        self.plan['operational']['policy']['after_sha256']=f.rt.write(f.bundle/'POLICY.next.json',new)['sha256']
        after=core_runtime.replace_env(before,{k:self.plan['operational'][v]['after_sha256'] for k,v in mapping.items()})
        f.rt.storage.atomic_write_bytes(f.bundle/'HANDOFF.next.env',after,replace=True,expected_existing_sha256=f.rt.identity(f.bundle/'HANDOFF.next.env')['sha256'])
        self.plan['operational']['handoff']['after_sha256']=business.sha(after)
        f.approval=f.rt.write(f.store/'CONTROL_UPGRADE_APPROVAL.json',self.plan)['sha256']

    def test_actual_successor_installs_without_reapplying_overlay_or_starting_services(self):
        self.assertEqual(self.f.install(),'INSTALLED')
        self.assertEqual(self.f.install(),'INSTALLED_VERIFIED_NOOP')
        self.assertEqual(self.f.rt.events,['stop-watchdog','start-watchdog'])

    def test_installed_new_guard_uses_fresh_engine_for_watchdog(self):
        from types import SimpleNamespace
        f=self.f;old=f.rt.engine_value
        fresh=SimpleNamespace(**vars(old))
        def stale():raise AssertionError('old guard cache must never be forked')
        old.watchdog_start_locked=stale
        with patch.object(f.rt,'engine',side_effect=[old,fresh]) as factory:
            self.assertEqual(f.install(),'INSTALLED')
        self.assertEqual(factory.call_count,2)

    def test_missing_review_and_process_drift_refuse_before_mutation(self):
        f=self.f;path=f.store/'CONTROL_UPGRADE_REVIEW.json';raw=path.read_bytes();path.unlink()
        with self.assertRaisesRegex(Exception,'FileNotFoundError'):f.install()
        path.write_bytes(raw);path.chmod(0o600)
        f.rt.engine_value.active=lambda *_:{'pid':999,'ticks':456}
        with self.assertRaisesRegex(Exception,'process_context_drift'):f.install()
        self.assertEqual(f.rt.events,[])

    def test_partial_successor_controller_copy_recovers_original_same_plan(self):
        f=self.f;original=f.rt.storage.atomic_copy_file;fired=False
        def copy(source,target,digest,**kw):
            nonlocal fired
            if Path(target)==f.root/'operations/r25-publisher/ai_runtime.py' and not fired:
                fired=True;raise OSError('injected successor controller write')
            return original(source,target,digest,**kw)
        with patch.object(f.rt.storage,'atomic_copy_file',side_effect=copy),self.assertRaises(OSError):f.install()
        with self.assertRaisesRegex(Exception,'recovery_required'):f.install()
        self.assertEqual(self.upgrade.recover(f.rt,self.plan,f.bundle,approval_sha256=f.approval),'RESTORED')
        for name,item in self.upgrade.entries(self.plan).items():
            target,owner,mode=self.upgrade.targets(self.plan)[name]
            self.assertEqual(f.rt.identity(target,expected_owner=owner,expected_mode=mode)['sha256'],item['before_sha256'])


if __name__=='__main__':unittest.main()
