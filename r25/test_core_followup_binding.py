"""Core-only binding: real files/flock/CAS and actual guard/transition logic.

Formal provenance/approvals are explicitly synthetic. Installer OS health and
the full PG policy loader use the existing fixture; standalone guard tests run
the actual source without a publisher import path. No production or providers.
"""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import zipfile

import core_business as business
import core_release_guard as guard
import core_runtime
import upgrade_assistant_controller as upgrade
import test_assistant_successor as successor
import ai_release_guard


class FollowupFixture(unittest.TestCase):
    def setUp(self):
        original=successor.InstalledV2Upgrade('runTest');original.setUp()
        self.addCleanup(original.doCleanups);self.f=original.f;f=self.f;self.plan=f.plan
        self.live=upgrade.LIVE_FILES;owner=tuple(self.plan['private_owner'])
        def directory(path):
            if not path.parent.exists():directory(path.parent)
            f.rt.storage.mkdir_private(path)
        def write(path,raw,mode=0o600):
            if not path.parent.exists():directory(path.parent)
            return f.rt.storage.atomic_write_bytes(path,raw,mode=mode,replace=path.exists(),
                existing_mode=mode,expected_existing_sha256=f.rt.identity(path,expected_mode=mode)['sha256'] if path.exists() else None)['sha256']
        self.put=write
        # First successor is already installed and both applications committed.
        for name in upgrade.entries(self.plan):
            path,_,mode=upgrade.targets(self.plan)[name]
            write(path,upgrade.source(f.bundle,name).read_bytes(),mode)
        old=f.rt.read(f.bundle/'POLICY.next.json')
        old['repository']=old['upgrade']['candidate']['repository']=business.CURRENT_REPOSITORY
        old['runtime_modules']['core-release-candidate.py']=business.sha((f.bundle/'core-release-candidate.py').read_bytes())
        old['runtime_modules']['core_acceptance.py']=business.sha(b'# installed acceptance fixture\n')
        old['upgrade']['candidate'].update(commit_sha=f.release.commit_sha,artifact_sha256=f.release.artifact_sha256)
        old['upgrade']['baseline_release']={'commit_sha':'0'*40,'artifact_sha256':'0'*64,'accepted_record_sha256':'0'*64}
        final=original.policy['upgrade']['successor']['core_final_sha256']
        app=self.live['application'].read_bytes().replace(old['upgrade']['successor']['ai_configuration']['before_sha256'].encode(),
             old['upgrade']['successor']['ai_configuration']['after_sha256'].encode())
        self.assertEqual(business.sha(app),final);write(self.live['application'],app)
        write(f.root/'runtime/config/assistant-private/assistant-runtime.json',(f.bundle/'ASSISTANT_CONFIG.next.json').read_bytes())
        ai_store=f.root/'runtime/ai-publications'
        ai_policy=f.rt.read(ai_store/'POLICY.json');policy_sha=f.rt.identity(ai_store/'POLICY.json')['sha256']
        files=[{'path':name,'bytes':len((f.root/'release'/name).read_bytes()),'sha256':business.sha((f.root/'release'/name).read_bytes())}
               for name in ai_release_guard.ASSISTANT_PATHS]
        tree=business.sha(guard.encoded(files).rstrip(b'\n'))
        old['upgrade']['successor']['ai_candidate']['source_tree_sha256']=tree
        formal=old['upgrade']['successor']['ai_candidate']
        record={**formal,'schema':'R25_AI_ACCEPTED_SOURCE_V2','policy_sha256':policy_sha,'files':files}
        release_dir=ai_store/'releases'/(tree+'-'+formal['commit_sha']);directory(release_dir)
        record_sha=write(release_dir/'record.json',guard.encoded(record))
        ref={'commit_sha':formal['commit_sha'],'source_tree_sha256':tree,'record_sha256':record_sha}
        old['upgrade']['assistant']['artifacts']['ai']={'repository':formal['repository'],'commit':formal['commit_sha'],'sha256':formal['archive_sha256']}
        oldsha=write(f.store/'POLICY.json',guard.encoded(old));self.old=old
        tx='f'*32;txdir=ai_store/'transactions'/tx;directory(txdir)
        snapshot={'schema':'R25_AI_SNAPSHOT_V1','transaction_id':tx,'core':asdict(f.release),
            'current':old['upgrade']['successor']['ai_before'],'candidate':ref,'configuration_transition':{'policy_sha256':oldsha}}
        snapsha=write(txdir/'snapshot.json',guard.encoded(snapshot))
        journal={'schema':'R25_AI_TRANSACTION_V1','transaction_id':tx,'phase':'COMMITTED',
            'current':snapshot['current'],'candidate':ref,'snapshot_sha256':snapsha}
        write(txdir/'journal.json',guard.encoded(journal))
        write(ai_store/'CURRENT.json',guard.encoded({'schema':'R25_AI_CURRENT_V1','release':ref}))
        write(ai_store/'ACTIVE.json',guard.encoded({'schema':'R25_AI_ACTIVE_V1','transaction_id':tx}))
        priorplan=deepcopy(self.plan);priorplan['operational']['policy']['after_sha256']=oldsha
        prior=business.sha(guard.encoded(priorplan));priorfolder=f.store/'controller-upgrades'/prior;directory(priorfolder)
        write(priorfolder/'approval.json',guard.encoded(priorplan))
        priorjournal=write(priorfolder/'journal.json',guard.encoded({'schema':upgrade.SCHEMA,'phase':'INSTALLED','approval_sha256':prior}))
        write(f.store/'CONTROL_UPGRADE_ACTIVE.json',guard.encoded({'schema':'R25_CORE_CONTROLLER_UPGRADE_ACTIVE_V1','approval_sha256':prior}))
        self.plan.update(schema=upgrade.FOLLOWUP_SCHEMA,current_ai_release=ref,
            installed_control={'approval_sha256':prior,'journal_sha256':priorjournal},frontend={
                'selection_sha256':write(f.root/'runtime/config/frontend-selection.json',b'{"fixture":"accepted frontend"}\n'),
                'reference_sha256':write(f.root/'runtime/config/frontend-selection.reference',b'fixture selection reference\n')})
        self.plan['preserved']['application']=final
        # The finite followup includes the existing acceptance module explicitly.
        acceptance=f.root/'operations/r25-publisher/core_acceptance.py';write(acceptance,b'# installed acceptance fixture\n')
        self.plan['files']['core_acceptance.py']={'before_sha256':business.sha(acceptance.read_bytes()),
            'after_sha256':business.sha((f.bundle/'core_acceptance.py').read_bytes())}
        for name in upgrade.FILES:
            self.plan['files'][name]['before_sha256']=self.plan['files'][name]['after_sha256']
        for name in ('guard','ai-guard','critic-wrapper','lifecycle','ai-policy'):
            self.plan['operational'][name]['before_sha256']=self.plan['operational'][name]['after_sha256']
        # Real source validators below operate independently of the inherited
        # synthetic outer installed full-PG-policy loader.
        new=deepcopy(old);up=new['upgrade'];transition=up.pop('successor')
        up['candidate']=deepcopy(original.policy['upgrade']['candidate']);up['baseline_release']=asdict(f.release)
        up['candidate']['repository']=old['repository']
        up['configuration'].update(before_sha256=final,after_sha256=final)
        up['configuration']['assistant']['execution.expected-config-sha256']=transition['ai_configuration']['after_sha256']
        up['core_followup']={'schema':'R25_ASSISTANT_CORE_FOLLOWUP_V1','acceptance':'gallery-daily-v1',
            'predecessor_policy_sha256':oldsha,'ai_release':ref,'ai_transaction_id':tx,
            **{'ai_'+key+'_sha256':f.rt.identity(path)['sha256'] for key,path in (
                ('policy',ai_store/'POLICY.json'),('current',ai_store/'CURRENT.json'),('active',ai_store/'ACTIVE.json'),
                ('journal',txdir/'journal.json'),('snapshot',txdir/'snapshot.json'),
                ('configuration',f.root/'runtime/config/assistant-private/assistant-runtime.json'))}}
        a=up['assistant'];a.update(configuration_sha256=final,baseline_policy_sha256=oldsha,
            baseline_payload_fingerprint=old['upgrade']['candidate']['payload_fingerprint'],compatible_recovery_jar_sha256=f.release.artifact_sha256)
        a['artifacts']['core']={'repository':up['candidate']['repository'],'commit':up['candidate']['commit_sha'],'sha256':up['candidate']['artifact_sha256']}
        new['runtime_modules']['core_acceptance.py']=self.plan['files']['core_acceptance.py']['after_sha256']
        self.plan['operational']['policy']={'before_sha256':oldsha,'after_sha256':write(f.bundle/'POLICY.next.json',guard.encoded(new))}
        before=self.live['handoff'].read_bytes()
        # Fixture handoff must now name the actually committed prior policy.
        before=core_runtime.replace_env(before,{'R25_CORE_RELEASE_POLICY_SHA256':oldsha})
        write(self.live['handoff'],before)
        after=core_runtime.replace_env(before,{'R25_CORE_RELEASE_POLICY_SHA256':self.plan['operational']['policy']['after_sha256']})
        self.plan['operational']['handoff']={'before_sha256':business.sha(before),'after_sha256':write(f.bundle/'HANDOFF.next.env',after)}
        self.new=new;self.reapprove()
        # Real descriptor reader, only the default root uid is adapted for local
        # test ownership; explicit mode/owner/digest checks are preserved.
        read=guard.read_file
        def local_read(path,**kwargs):return read(path,**{'owner':owner,**kwargs})
        patcher=patch.object(guard,'read_file',side_effect=local_read);patcher.start();self.addCleanup(patcher.stop)

    def reapprove(self):self.f.approval=self.put(self.f.store/'CONTROL_UPGRADE_APPROVAL.json',guard.encoded(self.plan))

    def test_bind_and_noop_keep_application_ai_and_original_journals(self):
        f=self.f;before={str(p):p.read_bytes() for p in [*self.live.values(),f.root/'runtime/ai-publications/CURRENT.json',
            f.root/'runtime/config/assistant-private/assistant-runtime.json']}
        self.assertEqual(f.install(),'INSTALLED')
        self.assertEqual(f.install(),'INSTALLED_VERIFIED_NOOP')
        self.assertEqual(f.rt.events,['stop-watchdog','start-watchdog'])
        for path,raw in before.items():
            if path!=str(self.live['handoff']):self.assertEqual(Path(path).read_bytes(),raw)
        self.assertEqual(guard.verify_core_followup_context(self.new,validator=business),self.plan['current_ai_release'])

    def test_unstarted_recover_does_not_stop_watchdog(self):
        f=self.f
        self.assertEqual(upgrade.recover(f.rt,self.plan,f.bundle,approval_sha256=f.approval),'CORE_FOLLOWUP_NOT_STARTED')
        self.assertEqual(f.rt.events,[])

    def test_partial_copy_requires_original_recovery_and_restores_all_controls(self):
        f=self.f;copy=f.rt.storage.atomic_copy_file;failed=False
        def fail(source,target,digest,**kw):
            nonlocal failed
            if Path(target)==f.store/'POLICY.json' and not failed:failed=True;raise OSError('synthetic binding boundary')
            return copy(source,target,digest,**kw)
        with patch.object(f.rt.storage,'atomic_copy_file',side_effect=fail),self.assertRaises(OSError):f.install()
        with self.assertRaisesRegex(Exception,'recovery_required'):f.install()
        self.assertEqual(upgrade.recover(f.rt,self.plan,f.bundle,approval_sha256=f.approval),'RESTORED')
        for name,entry in upgrade.entries(self.plan).items():
            path,owner,mode=upgrade.targets(self.plan)[name]
            self.assertEqual(f.rt.identity(path,expected_owner=owner,expected_mode=mode)['sha256'],entry['before_sha256'])

    def test_uncertain_installed_marker_requires_recovery_not_rebind(self):
        f=self.f;write=f.rt.write;failed=False
        def uncertain(path,value):
            nonlocal failed
            result=write(path,value)
            if value.get('phase')=='INSTALLED' and not failed:failed=True;raise TimeoutError('synthetic durable reply loss')
            return result
        with patch.object(f.rt,'write',side_effect=uncertain),self.assertRaises(TimeoutError):f.install()
        with self.assertRaisesRegex(Exception,'recovery_required'):f.install()
        self.assertEqual(upgrade.recover(f.rt,self.plan,f.bundle,approval_sha256=f.approval),'RESTORED')

    def test_ai_journal_snapshot_policy_source_and_config_drift_reject_before_stop(self):
        f=self.f;b=self.new['upgrade']['core_followup'];store=f.root/'runtime/ai-publications'
        paths=[store/'CURRENT.json',store/'ACTIVE.json',store/'POLICY.json',store/'transactions'/b['ai_transaction_id']/'journal.json',
            store/'transactions'/b['ai_transaction_id']/'snapshot.json',f.root/'release/critic-service/app/main.py',
            f.root/'runtime/config/assistant-private/assistant-runtime.json']
        for path in paths:
            raw=path.read_bytes();self.put(path,raw+b'\n')
            with self.subTest(path=path.name),self.assertRaises(Exception):f.install()
            self.assertEqual(f.rt.events,[]);self.put(path,raw)

    def test_rebound_noncommitted_ai_is_rejected(self):
        f=self.f;binding=self.new['upgrade']['core_followup'];path=f.root/'runtime/ai-publications/transactions'/binding['ai_transaction_id']/'journal.json'
        journal=f.rt.read(path);journal['phase']='START_INTENT'
        binding['ai_journal_sha256']=self.put(path,guard.encoded(journal))
        with self.assertRaisesRegex(Exception,'not_committed'):guard.verify_core_followup_context(self.new,self.old,business)

    def test_foreign_service_context_missing_review_backup_and_lease_busy_refused(self):
        import fcntl
        f=self.f;path=f.store/'CONTROL_UPGRADE_REVIEW.json';raw=path.read_bytes();path.unlink()
        with self.assertRaises(Exception):f.install()
        self.put(path,raw)
        active=f.root/'runtime/assistant-backups/assistant-daily-v1/ACTIVE.json'
        self.put(active,guard.encoded({'day':'2026-10-19'}))
        run=active.parent/'runs/2026-10-19.json';self.put(run,guard.encoded({'schema':'R25_ASSISTANT_SCHEDULED_BACKUP_V1','day':'2026-10-19','phase':'STOPPED'}))
        with self.assertRaisesRegex(Exception,'backup_recovery_required'):f.install()
        self.put(run,guard.encoded({'schema':'R25_ASSISTANT_SCHEDULED_BACKUP_V1','day':'2026-10-19','phase':'COMMITTED'}))
        with (f.root/'lock').open('rb') as lease:
            fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(Exception,'shared_publication_busy'):f.install()
        f.rt.engine_value.active=lambda *_:{'pid':999,'ticks':456}
        with self.assertRaisesRegex(Exception,'process_context_drift'):f.install()
        self.assertEqual(f.rt.events,[])

    def test_unrelated_policy_changes_and_legacy_mode_impersonation_rejected(self):
        for field in ('storage','ai','schema','configuration','web-artifact'):
            bad=deepcopy(self.new)
            if field=='storage':bad['upgrade']['assistant']['attachment_root']='/unrelated'
            if field=='ai':bad['upgrade']['assistant']['artifacts']['ai']['commit']='9'*40
            if field=='schema':bad['installation']['postgres']['database']='other'
            if field=='configuration':bad['upgrade']['configuration']['assistant']['storage.quota-bytes']=1
            if field=='web-artifact':bad['upgrade']['assistant']['artifacts']['web']['sha256']='0'*64
            with self.subTest(field=field),self.assertRaises(Exception):upgrade.validate_core_followup_bindings(self.f.rt,self.plan,self.old,bad,self.f.bundle)
        bad=deepcopy(self.plan);bad['schema']=upgrade.SUCCESSOR_SCHEMA
        with self.assertRaisesRegex(Exception,'plan_fields'):upgrade.validate(bad)

    def test_current_candidate_and_recovery_context_all_require_unchanged_g1(self):
        f=self.f;self.assertEqual(f.install(),'INSTALLED')
        baseline=asdict(f.release);candidate={k:self.new['upgrade']['candidate'][k] for k in ('commit_sha','artifact_sha256')}
        candidate['accepted_record_sha256']='b'*64;tx='2'*32
        def current(ref):self.put(f.store/'CURRENT.json',guard.encoded({'schema':'R25_CORE_CURRENT_V1','release':ref}))
        with patch.object(guard,'business_module',return_value=business):
            current(baseline);guard.validate_configuration_context(self.new)
            self.put(f.store/'ACTIVE.json',guard.encoded({'transaction_id':tx}))
            path=f.store/'transactions'/tx/'journal.json'
            for phase,ref in (('START_INTENT',baseline),('COMMITTED',candidate),('RESTORE_INTENT',candidate),('RESTORED',baseline)):
                self.put(path,guard.encoded({'transaction_id':tx,'phase':phase,'current':baseline,'candidate':candidate}))
                current(ref)
                with self.subTest(phase=phase):guard.validate_configuration_context(self.new)
            raw=self.live['application'].read_bytes();self.put(self.live['application'],raw+b'# drift\n')
            with self.assertRaisesRegex(Exception,'configuration_state'):guard.validate_configuration_context(self.new)
            self.put(self.live['application'],raw)

    def test_historical_baseline_record_keeps_prior_policy_and_new_record_requires_new_policy(self):
        f=self.f;self.assertEqual(f.install(),'INSTALLED')
        baseline=asdict(f.release);up=self.new['upgrade'];old_record={'schema':guard.BUSINESS_RECORD_SCHEMA,
            'repository':business.CURRENT_REPOSITORY,'commit_sha':baseline['commit_sha'],'artifact_sha256':baseline['artifact_sha256'],
            'artifact_bytes':123,'policy_sha256':up['assistant']['baseline_policy_sha256'],
            'payload_fingerprint':up['assistant']['baseline_payload_fingerprint']}
        old_path=f.store/'releases'/(baseline['artifact_sha256']+'-'+baseline['commit_sha'])/'record.json'
        old_sha=self.put(old_path,guard.encoded(old_record));self.new['upgrade']['baseline_release']['accepted_record_sha256']=old_sha
        new_record={**up['candidate'],'schema':guard.BUSINESS_RECORD_SCHEMA,'policy_sha256':f.rt.identity(f.store/'POLICY.json')['sha256']}
        new_path=f.store/'releases'/(new_record['artifact_sha256']+'-'+new_record['commit_sha'])/'record.json'
        new_sha=self.put(new_path,guard.encoded(new_record))
        with patch.object(guard,'policy',return_value=self.new),patch.object(guard,'business_module',return_value=business),patch.object(business,'validate_policy',side_effect=lambda x:x):
            self.assertEqual(guard.release_record(baseline['artifact_sha256'],old_sha,baseline['commit_sha']),old_record)
            self.assertEqual(guard.release_record(new_record['artifact_sha256'],new_sha,new_record['commit_sha']),new_record)
            wrong={**new_record,'policy_sha256':old_record['policy_sha256']};wrong_sha=self.put(new_path,guard.encoded(wrong))
            with self.assertRaisesRegex(Exception,'business_record_policy'):guard.release_record(new_record['artifact_sha256'],wrong_sha,new_record['commit_sha'])
        self.assertEqual(old_path.read_bytes(),guard.encoded(old_record))

    def test_frontend_and_installed_control_identity_drift_are_rejected(self):
        f=self.f
        for path in (f.root/'runtime/config/frontend-selection.json',f.root/'runtime/config/frontend-selection.reference'):
            raw=path.read_bytes();self.put(path,raw+b' ')
            with self.subTest(path=path.name),self.assertRaises(Exception):f.install()
            self.put(path,raw)
        path=f.store/'CONTROL_UPGRADE_ACTIVE.json';self.put(path,guard.encoded({'schema':'R25_CORE_CONTROLLER_UPGRADE_ACTIVE_V1','approval_sha256':'e'*64}))
        with self.assertRaises(Exception):f.install()
        self.assertEqual(f.rt.events,[])

    def test_guard_standalone_source_loading_without_sibling_imports(self):
        f=self.f;self.assertEqual(f.install(),'INSTALLED')
        # Use actual source-bound business/guard bytes, never the install fixture
        # loader. Other outer policy tests cover the full migration contract.
        business_path=f.root/'operations/r25-publisher/core_business.py'
        raw=Path(business.__file__).read_bytes();self.put(business_path,raw)
        policy=deepcopy(self.new);policy['runtime_modules']['core_business.py']=business.sha(raw)
        path=f.root/'standalone-policy.json';self.put(path,guard.encoded(policy))
        script='''import importlib.util,json,os,pathlib,sys
root=pathlib.Path(sys.argv[1]);source=pathlib.Path(sys.argv[2])
spec=importlib.util.spec_from_file_location('standalone_guard',source)
guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
guard.ROOT=root;guard.STORE=root/'runtime/core-publications'
original=guard.read_file
def read(path,**kwargs):return original(path,**{'owner':(os.getuid(),os.getgid()),**kwargs})
guard.read_file=read
sys.path=[p for p in sys.path if p and 'r25' not in p]
assert 'core_business' not in sys.modules and 'ai_release_guard' not in sys.modules
policy=json.loads((root/'standalone-policy.json').read_text())
guard.verify_core_followup_context(policy)
print('STANDALONE_CORE_FOLLOWUP_GUARD_OK')
'''
        result=subprocess.run([sys.executable,'-I','-B','-c',script,str(f.root),str(Path(guard.__file__).absolute())],
            cwd=f.root,env={'PATH':os.environ['PATH']},text=True,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr);self.assertIn('STANDALONE_CORE_FOLLOWUP_GUARD_OK',result.stdout)

    def preparation(self,*,wrong=None):
        """Actual prepare path; only Java-bytecode/OS-root boundary is simulated."""
        from contextlib import ExitStack
        from types import SimpleNamespace
        import core_publish
        from test_core_candidate_repository import C
        from r25_storage import StorageRefused
        f=self.f;core={'commit':'e'*40,'run_id':'456','run_attempt':'1'}
        incoming=f.root/'incoming'/(core['commit']+'-456-1');incoming.mkdir(parents=True,mode=0o700)
        incoming.parent.chmod(0o700)
        jar=incoming/C.JAR_NAME
        with zipfile.ZipFile(jar,'w') as archive:
            archive.writestr('BOOT-INF/classes/application-catalog-postgres-production.yml','synthetic: true\n')
            archive.writestr('BOOT-INF/classes/com/auralink/Application.class',b'not executable; packaged-verifier fixture only')
        jar.chmod(0o600)
        actual={'status':'PASS','jar_sha256':business.sha(jar.read_bytes()),'jar_bytes':jar.stat().st_size,
                'profile_count':11,'profiles':{},'scope':C.SCOPE}
        self.put(incoming/C.REPORT_NAME,json.dumps(actual).encode())
        with patch.object(C,'packaged_verifier',return_value=SimpleNamespace(verify=lambda _:actual)):
            manifest=C.create(jar,incoming/C.REPORT_NAME,business.CURRENT_REPOSITORY,'refs/heads/release',core['commit'],'456','1')
        if wrong=='repository':manifest['repository']='foreign/repository'
        if wrong=='attempt':manifest['github_run_attempt']='2'
        if wrong=='run':manifest['github_run_id']='999'
        if wrong=='ref':manifest['ref']='refs/heads/main'
        self.put(incoming/'core-release-candidate.json',json.dumps(manifest).encode())
        if wrong=='bytes':jar.write_bytes(jar.read_bytes()+b'changed bytes')
        if wrong=='mode':jar.chmod(0o644)
        request={'schema':'R25_ASSISTANT_CORE_FOLLOWUP_REQUEST_V1','deploy':self.plan['deploy'],'core':core,
            'web_commit':'9'*40,'evidence':self.new['upgrade']['evidence'],'compatibility':self.new['upgrade']['compatibility'],
            'compatibility_evidence_sha256':'c'*64,'review_evidence_sha256':self.plan['review_evidence_sha256']}
        request_sha=business.sha(guard.encoded(request))
        # Existing setup's hand-built output is removed only inside this disposable
        # test fixture. The real prepare operation refuses conflicting sidecars.
        for name in ('POLICY.next.json','HANDOFF.next.env'):(f.bundle/name).unlink()
        source=guard.source_module;identity=f.rt.identity;validate=f.rt.storage.validate_target
        owner=tuple(self.plan['private_owner'])
        def load(path,name,raw):return C if Path(path).name=='core-release-candidate.py' else source(path,name,raw)
        def local_identity(path,**kw):
            if Path(path).parent==incoming and wrong=='owner':raise StorageRefused('file_owner')
            if kw.get('expected_owner')==(0,0):kw['expected_owner']=owner
            return identity(path,**kw)
        def local_validate(path,**kw):
            if kw.get('expected_owner')==(0,0):kw['expected_owner']=owner
            return validate(path,**kw)
        approval_before=(f.store/'CONTROL_UPGRADE_APPROVAL.json').read_bytes()
        with ExitStack() as stack:
            for context in (patch.object(guard,'policy',return_value=self.old),patch.object(core_publish,'INCOMING',incoming.parent),
                patch.object(guard,'source_module',side_effect=load),patch.object(f.rt,'identity',side_effect=local_identity),
                patch.object(f.rt.storage,'validate_target',side_effect=local_validate),
                patch.object(C,'packaged_verifier',return_value=SimpleNamespace(verify=lambda _:{**actual,
                    'jar_sha256':business.sha(jar.read_bytes()),'jar_bytes':jar.stat().st_size}))):stack.enter_context(context)
            try:
                result=upgrade.prepare_core_followup(f.rt,request,request_sha,f.bundle)
                self.assertEqual(upgrade.prepare_core_followup(f.rt,request,request_sha,f.bundle),result)
                return result
            finally:
                self.assertEqual((f.store/'CONTROL_UPGRADE_APPROVAL.json').read_bytes(),approval_before)
                self.assertEqual(f.rt.events,[])
                self.assertEqual(f.rt.read(f.store/'POLICY.json'),self.old)

    def test_prepare_materializes_unsigned_plan_without_approval_or_live_mutations(self):
        result=self.preparation()
        self.assertEqual(result['outcome'],'CORE_FOLLOWUP_UNSIGNED_PLAN_PREPARED');self.assertIs(result['approved'],False)
        raw=Path(result['path']).read_bytes();self.assertEqual(business.sha(raw),result['plan_sha256'])
        plan=json.loads(raw);self.assertEqual(plan['schema'],upgrade.FOLLOWUP_SCHEMA)
        self.assertEqual(plan['preserved'],self.plan['preserved'])
        # Explicit synthetic test approval, never produced by prepare itself.
        digest=self.put(self.f.store/'CONTROL_UPGRADE_APPROVAL.json',raw)
        self.assertEqual(upgrade.install(self.f.rt,plan,self.f.bundle,approval_sha256=digest),'INSTALLED')
        self.assertEqual(asdict(self.f.rt.current()),plan['current_release'])
        self.assertEqual(business.sha(self.live['application'].read_bytes()),plan['preserved']['application'])

    def test_prepare_refuses_wrong_formal_repository_run_attempt_and_incoming_metadata(self):
        # New complete fixture per case keeps failed preparations independent.
        for wrong in ('repository','run','attempt','ref','bytes','owner','mode'):
            fixture=FollowupFixture('runTest');fixture.setUp()
            try:
                with self.subTest(wrong=wrong),self.assertRaises(Exception):fixture.preparation(wrong=wrong)
                self.assertFalse((fixture.f.store/'core-followup-plans').exists())
            finally:fixture.doCleanups()


if __name__=='__main__':unittest.main()
