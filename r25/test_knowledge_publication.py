"""Real full repair/visibility/knowledge proof chain, flock, CAS and recovery.

Java, PostgreSQL and supervision are CPU fixtures. Production visibility IDs
are mapped to this synthetic host; no proof validator is replaced or bypassed.
"""
from copy import deepcopy
from dataclasses import asdict
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import unittest
from unittest.mock import patch
import uuid
import zipfile
import core_release_guard as guard
import core_runtime
import core_publish
import upgrade_assistant_controller as u
import vmm_controller as v
import test_core_business as legacy
import test_community_publication_repair as previous
from test_core_candidate_repository import C
HERE=Path(__file__).parent

class Knowledge(unittest.TestCase):
    def setUp(self):
        original=legacy.fixture_policy
        def retained(root,*a,**kw):
            value=original(root,*a,**kw)
            tables={n:{'rows':1,'md5':'b'*32} for n in ('catalog_paintings','catalog_source_records',*[f'fixture_{i}' for i in range(12)])}
            tables['catalog_batch_current_revisions']=deepcopy(v.VISIBILITY_HEADS['before'])
            raw=v.encoded({'schema':'R25_CORE_RETAINED_DATA_V1','tables':tables,'media':[]})
            path=Path(value['upgrade']['compatibility']['retention_manifest_path']);path.write_bytes(raw);path.chmod(0o600)
            value['upgrade']['compatibility']['retention_manifest_sha256']=v.sha(raw)
            return value
        with patch.object(legacy,'fixture_policy',side_effect=retained):
            parent=previous.Repair('runTest');parent.setUp();self.addCleanup(parent.doCleanups)
            parent.prepare();parent.f.install()
        self.parent=parent;self.f=f=parent.f;self.rt=f.vrt;self.root=f.f.root;self.put=f.put
        f.old=deepcopy(f.new);f.oldplan=deepcopy(f.plan);self.commit_current(f.old,'a'*32)
        self.manifest={'heads':[dict(batch_id=str(uuid.UUID(int=i+1)),revision=1,selected_at='fixture') for i in range(12)],'rows':[dict(painting_id=str(uuid.UUID(int=1000+i))) for i in range(486)]}
        self.ids={'VISIBILITY_MANIFEST':v.sha(v.encoded(self.manifest)),'VISIBILITY_POLICY':v.sha(v.encoded(f.old)),'VISIBILITY_PARENT':parent.ident,'VISIBILITY_CORE':f.old['upgrade']['candidate']['commit_sha'],'VISIBILITY_RETENTION':f.old['upgrade']['compatibility']['retention_manifest_sha256']}
        for name,value in self.ids.items():self.keep(patch.object(v,name,value))
        source=guard.source_module
        def local_source(path,name,raw):
            module=source(path,name,raw)
            if Path(path).name=='vmm_controller.py':
                for key,value in self.ids.items():setattr(module,key,value)
            return module
        self.keep(patch.object(guard,'source_module',side_effect=local_source))
        self.visibility();self.community_original();f.knowledge_community=self.community_new
        f.knowledge_compatibility=v.apply_policy_overlay(f.old,v.env_values(f.g.live['handoff'].read_bytes()),self.root,fixture_runtime=self.rt)['upgrade']['compatibility']
        f.original_snapshot=f.snapshot()
        self.preserved={p:p.read_bytes() for base in (self.rt.store,f.f.store/'controller-upgrades',self.root/'runtime/community') for p in base.rglob('*') if p.is_file()}
        self.actions=[]
        f.f.rt.engine_value.community_action=lambda mode,fd:self.actions.append(mode) or dict(state='READY_PRESERVED',process={'fixture':True},watchdog_state_sha256='1'*64,process_record_sha256='2'*64)
        f.f.rt.events.clear();f.overlay_events.clear();self.keep(patch.object(core_publish,'INCOMING',self.root/'incoming'))
    def keep(self,p):p.start();self.addCleanup(p.stop)
    def commit_current(self,policy,tx,baseline=None):
        candidate=policy['upgrade']['candidate'];store=self.f.f.store
        for name in ('release-jar','preview-jar'):self.put(self.f.g.live[name],(self.root/'incoming'/(candidate['commit_sha']+'-888-1')/C.JAR_NAME).read_bytes())
        record={**candidate,'schema':guard.BUSINESS_RECORD_SCHEMA,'policy_sha256':v.sha(v.encoded(policy))}
        digest=self.put(store/'releases'/(candidate['artifact_sha256']+'-'+candidate['commit_sha'])/'record.json',v.encoded(record))
        self.f.current=core_runtime.Release(candidate['commit_sha'],candidate['artifact_sha256'],digest);release=asdict(self.f.current)
        self.put(store/'CURRENT.json',(json.dumps(dict(schema='R25_CORE_CURRENT_V1',transaction_id=tx,release=release),sort_keys=True,indent=2)+'\n').encode())
        self.put(store/'ACTIVE.json',v.encoded(dict(schema='R25_CORE_ACTIVE_V1',transaction_id=tx)))
        self.put(store/'transactions'/tx/'journal.json',v.encoded(dict(schema='R25_CORE_TRANSACTION_V1',transaction_id=tx,snapshot_id=tx,phase='COMMITTED',candidate=release,current=baseline or policy['upgrade']['baseline_release'])))
        return release
    def controller_package(self,commit,run):
        spec=importlib.util.spec_from_file_location('knowledge_fixture_package',HERE.parent/'scripts/package-r25-release.py');package=importlib.util.module_from_spec(spec);spec.loader.exec_module(package)
        sources={n:(self.f.bundle/n).read_bytes() for n in package.FILES};raw=package.archive_bytes(sources)
        manifest=package.manifest_for(sources,raw,package.identity('bailangguihun/auralink-deploy',package.REF,commit,run,'1'));folder=self.root/'operations/r25-controller-candidates'/commit
        for n,b in sources.items():self.put(folder/n,b)
        self.put(folder/'r25-controller.zip',raw);self.put(folder/'r25-controller-manifest.json',v.encoded(manifest))
        return {k:manifest[k] for k in ('repository','ref','commit','run_id','run_attempt')}|dict(archive_sha256=v.sha(raw),manifest_sha256=v.sha(v.encoded(manifest)),tool_sha256=v.sha(sources['upgrade_assistant_controller.py']))
    def visibility(self):
        f=self.f;commit='f1'*20;deployment=self.controller_package(commit,'999');f.sources['vmm_controller.py']+=b'\n# synthetic visibility revision\n';extension=f.package_extension(commit,'999')
        review=v.encoded({'fixture_only':True,'scope':'actual retained visibility proof'});pre=json.loads(Path(f.old['upgrade']['compatibility']['retention_manifest_path']).read_bytes())['tables']
        request=dict(schema='R25_CATALOG_VISIBILITY_REQUEST_V1',deploy=extension,controller_deploy=deployment,review_sha256=v.sha(review),host={'fixture_only':True})
        result=v.prepare_catalog_visibility(self.rt,request,manifest_raw=v.encoded(self.manifest),preimages_raw=v.encoded(pre),review_raw=review)
        folder=Path(result['plan_path']).parent;self.put(folder/'approval.json',Path(result['plan_path']).read_bytes());state=['before'];events=[]
        def database(mode):
            if mode=='observe':return state[0]
            self.assertEqual(mode,'delist');self.assertEqual(state[0],'before');state[0]='after'
        def lease(mode,fd):self.assertEqual(os.fstat(fd).st_ino,self.rt.lock.stat().st_ino)
        self.assertEqual('COMMITTED',v.visibility_execute(self.rt,result['plan_sha256'],database=database,guard=lease,supervise=lambda mode,fd:events.append(mode)))
        self.assertEqual(['stop','start','status'],events)
    def community_package(self,deployment,original=False):
        sources={n:(HERE/n).read_bytes() for n in v.COMMUNITY_FILES}
        if original:
            for n in ('community_host.py','community_host_cli.py'):sources[n]+=b'\n# retained original Community fixture\n'
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as archive:
            for name,raw in sorted(sources.items()):
                info=zipfile.ZipInfo(name);info.create_system=3;info.external_attr=(stat.S_IFREG|0o600)<<16;archive.writestr(info,raw)
        raw=data.getvalue();manifest={k:deployment[k] for k in ('repository','ref','commit','run_id','run_attempt')};manifest.update(schema='ARTLIVE_COMMUNITY_CONTROLLER_BUNDLE_V1',archive_sha256=v.sha(raw),files=[dict(path=n,bytes=len(b),sha256=v.sha(b)) for n,b in sorted(sources.items())])
        mr=v.encoded(manifest);ident=v.sha(mr);folder=self.root/'operations/community-controller'/ident
        for n,b in sources.items():self.put(folder/n,b)
        self.put(folder/'manifest.json',mr);staged=self.root/'operations/r25-community-candidates'/deployment['commit'];self.put(staged/'community-controller.zip',raw);self.put(staged/'manifest.json',mr)
        return {k:manifest[k] for k in ('repository','ref','commit','run_id','run_attempt','archive_sha256')}|dict(manifest_sha256=ident)
    def community_original(self):
        deployed=self.community_package(self.f.oldplan['deploy'],original=True)
        plan=dict(schema='ARTLIVE_COMMUNITY_HOST_V1',controller_manifest_sha256=deployed['manifest_sha256'],runtime={'python':{'identity':{'sha256':'3'*64}}},lease={'guard':{'sha256':v.sha((self.root/'operations/core_release_guard.py').read_bytes())}})
        raw=v.encoded(plan);ident=v.sha(raw);base=self.root/'runtime/community';folder=base/'installations'/ident
        self.put(folder/'approval.json',raw);self.put(folder/'journal.json',v.encoded(dict(phase='INSTALLED')));self.put(base/'ACTIVE.json',v.encoded(dict(schema='ARTLIVE_COMMUNITY_HOST_V1',plan_sha256=ident)))
        self.put(base/'WATCHDOG.json',v.encoded(dict(schema='ARTLIVE_COMMUNITY_WATCHDOG_V1',plan_sha256=ident,controller_manifest_sha256=deployed['manifest_sha256'],python_sha256='3'*64,cli_sha256=v.sha((self.root/'operations/community-controller'/deployed['manifest_sha256']/'community_host_cli.py').read_bytes()))))
        self.put(folder/'watchdog-state.json',(json.dumps(dict(schema='ARTLIVE_COMMUNITY_WATCHDOG_V1',plan_sha256=ident,desired='RUNNING',attempts=[{'fixture':i} for i in range(3)]),sort_keys=True,indent=2)+'\n').encode())
    def community_new(self,deployment):return self.community_package(deployment)
    def prepare(self,**kw):
        result=self.f.prepare(knowledge=True,**kw);self.plan=self.f.plan;self.ident=result['plan_sha256'];self.put(self.f.f.store/'publication-approvals'/(self.ident+'.json'),v.encoded(self.plan));self.f.ident=self.ident;return result
    def effective(self):return v.apply_policy_overlay(self.f.new,v.env_values(self.f.g.live['handoff'].read_bytes()),self.root,fixture_runtime=self.rt)
    def assert_preserved(self):
        for p,raw in self.preserved.items():self.assertEqual(raw,p.read_bytes(),str(p))
    def test_complete_binding_and_committed_candidate_preserve_all_predecessors(self):
        self.prepare()
        current=self.f.f.store/'CURRENT.json';raw=current.read_bytes()
        self.assertNotEqual(raw,v.encoded(json.loads(raw)))
        self.assertEqual(self.plan['vmm']['core_current_sha256'],v.sha(raw))
        bound=self.plan['vmm']['community'];state=self.root/'runtime/community/installations'/bound['plan_sha256']/'watchdog-state.json'
        self.assertEqual(bound['state_sha256'],v.sha(state.read_bytes()))
        self.assertNotEqual(bound['state_sha256'],v.sha(v.encoded(json.loads(state.read_bytes()))))
        self.assertEqual('INSTALLED',self.f.install());self.effective();self.assertEqual(['preserve'],self.actions);self.assert_preserved()
        self.put(current,v.encoded(json.loads(raw)))
        with self.assertRaisesRegex(Exception,'knowledge_baseline_record'):self.effective()
        self.put(current,raw);self.effective()
        self.assertEqual('INSTALLED_VERIFIED_NOOP',self.f.install());self.assertEqual('INSTALLED_NO_RECOVERY',self.f.recover())
        self.commit_current(self.f.new,'c'*32,self.plan['current_release']);self.effective();self.assert_preserved();self.assertEqual(self.f.knowledge_compatibility,self.effective()['upgrade']['compatibility'])
    def test_partial_policy_copy_restores_exact_old_controls_without_community_restart(self):
        self.prepare();copy=self.f.f.rt.storage.atomic_copy_file
        def fail(src,dst,*a,**kw):
            result=copy(src,dst,*a,**kw)
            if Path(dst)==self.f.f.store/'POLICY.json':raise InterruptedError('synthetic lost policy receipt')
            return result
        with patch.object(self.f.f.rt.storage,'atomic_copy_file',side_effect=fail),self.assertRaises(InterruptedError):self.f.install()
        self.assertEqual('RESTORED',self.f.recover());self.assert_preserved();self.assertEqual([],self.actions);self.assertFalse((self.rt.store/'KNOWLEDGE_PUBLICATION_ACTIVE.json').exists())
        v.apply_policy_overlay(self.f.old,v.env_values(self.f.g.live['handoff'].read_bytes()),self.root,fixture_runtime=self.rt)
        with self.assertRaisesRegex(Exception,'recovery_required'):self.f.install()
    def test_verify_intent_preserve_failure_recovers_new_marker_and_keeps_community(self):
        self.prepare()
        def failed(mode,fd):
            self.actions.append(mode);raise ValueError('fixture failed readiness observation')
        self.f.f.rt.engine_value.community_action=failed
        with self.assertRaisesRegex(ValueError,'failed readiness'):self.f.install()
        self.assertTrue((self.rt.store/'KNOWLEDGE_PUBLICATION_ACTIVE.json').exists())
        self.assertEqual('RESTORED',self.f.recover());self.assert_preserved()
        self.assertFalse((self.rt.store/'KNOWLEDGE_PUBLICATION_ACTIVE.json').exists())
        self.assertEqual(['preserve'],self.actions)
        v.apply_policy_overlay(self.f.old,v.env_values(self.f.g.live['handoff'].read_bytes()),self.root,fixture_runtime=self.rt)
    def test_durable_installed_receipt_loss_cannot_rollback_binding(self):
        self.prepare();write=self.f.f.rt.write
        def fail(path,value):
            result=write(path,value)
            if value.get('phase')=='INSTALLED':raise InterruptedError('fixture lost installed receipt')
            return result
        with patch.object(self.f.f.rt,'write',side_effect=fail),self.assertRaises(InterruptedError):self.f.install()
        self.effective();self.assert_preserved()
        self.assertEqual('INSTALLED_NO_RECOVERY',self.f.recover())
        self.assertEqual('INSTALLED_VERIFIED_NOOP',self.f.install())
    def test_current_transaction_identity_and_historical_proof_drift_fail_closed(self):
        self.prepare();self.f.install();self.commit_current(self.f.new,'c'*32,self.plan['current_release']);journal=self.f.f.store/'transactions'/('c'*32)/'journal.json'
        for key,wrong in (('schema','foreign'),('transaction_id','d'*32),('snapshot_id','d'*32),('current',{}),('phase','PREPARED')):
            raw=journal.read_bytes();value=json.loads(raw);value[key]=wrong;self.put(journal,v.encoded(value))
            with self.subTest(key=key),self.assertRaisesRegex(Exception,'knowledge_current_transaction'):self.effective()
            self.put(journal,raw)
        current=self.f.f.store/'CURRENT.json';raw=current.read_bytes()
        self.put(current,raw.rstrip()[:-1]+b',"schema":"R25_CORE_CURRENT_V1"}\n')
        with self.assertRaisesRegex(Exception,'json_duplicate_key'):self.effective()
        self.put(current,raw)
        path=self.f.f.store/'controller-upgrades'/self.parent.ident/'before/policy';raw=path.read_bytes();self.put(path,raw+b' ')
        with self.assertRaises(Exception):self.effective()
        self.put(path,raw);self.effective()
    def test_migration_dependency_and_configuration_changes_rejected(self):
        self.prepare();incoming=self.root/'incoming'/(self.f.new['upgrade']['candidate']['commit_sha']+'-888-1')/C.JAR_NAME;original=incoming.read_bytes()
        for name in ('BOOT-INF/lib/fixture.jar','BOOT-INF/classes/db/migration/V1_fixture.sql','BOOT-INF/classes/application-catalog-postgres-production.yml'):
            with zipfile.ZipFile(io.BytesIO(original)) as archive:entries={n:archive.read(n) for n in archive.namelist()}
            entries[name]+=b'foreign';data=io.BytesIO()
            with zipfile.ZipFile(data,'w') as archive:
                for n,b in entries.items():archive.writestr(n,b)
            self.put(incoming,data.getvalue());policy=deepcopy(self.f.new);policy['upgrade']['candidate'].update(artifact_sha256=v.sha(data.getvalue()),artifact_bytes=len(data.getvalue()))
            with self.subTest(name=name),self.assertRaisesRegex(Exception,'schema_dependency_or_config_change'):u.verify_knowledge_candidate(self.f.f.rt,self.plan,policy,self.f.g.live['application'].read_bytes())
        self.put(incoming,original)
        with self.assertRaisesRegex(Exception,'web_search_configuration_forbidden'):u.verify_knowledge_candidate(self.f.f.rt,self.plan,self.f.new,b'auralink.assistant.web-search.enabled: true\n')
        identity=self.f.f.rt.identity
        def race(path,**kw):
            result=identity(path,**kw)
            if Path(path)==incoming:incoming.write_bytes(original+b'changed-after-identity')
            return result
        try:
            with patch.object(self.f.f.rt,'identity',side_effect=race),self.assertRaisesRegex(Exception,'file_digest'):
                u.verify_knowledge_candidate(self.f.f.rt,self.plan,self.f.new,self.f.g.live['application'].read_bytes())
        finally:self.put(incoming,original)


if __name__=='__main__':unittest.main()
