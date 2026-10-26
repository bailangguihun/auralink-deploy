"""Managed successor: real files, manifests, flock, CAS and overlay history.

All approval records, JAR payloads and source identities are explicitly synthetic.
The inherited OS supervision/PostgreSQL/Java verifier boundaries are fixtures;
prepare/validation/install/overlay/recovery paths are never mocked. No network,
production path, model initialization or generation exists in these tests.
"""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
from functools import partial
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import core_business as business
import core_release_guard as guard
import core_runtime
import core_publish
import upgrade_assistant_controller as upgrade
import vmm_controller as vmm
import test_managed_vmm_core_followup as managed_fixture
import test_vmm_controller as overlay_fixture
from test_core_candidate_repository import C

HERE=Path(__file__).parent


class ManagedSuccessor(unittest.TestCase):
    def setUp(self):
        base=managed_fixture.ManagedCoreFollowup('runTest');base.setUp();self.addCleanup(base.doCleanups)
        self.base=base;self.f=base.f;self.g=base.g;self.put=self.g.put
        base.prepare();base.approve();self.assertEqual(base.install(),'INSTALLED')
        self.old=deepcopy(base.new);self.oldplan=deepcopy(base.plan)
        # Simulate the already committed managed publication with retained
        # exact bytes, a real record hash, CURRENT and COMMITTED journal.
        candidate=self.old['upgrade']['candidate'];jar=(self.f.root/'incoming'/('e'*40+'-456-1')/C.JAR_NAME).read_bytes()
        application=business.render_managed_vmm_configuration(self.g.live['application'].read_bytes(),self.old)
        self.put(self.g.live['application'],application)
        for name in ('release-jar','preview-jar'):self.put(self.g.live[name],jar)
        # The inherited older installer uses stub controller sources. Its
        # installed baseline is replaced here by source-bound formal fixture
        # bytes before exercising the actual overlay transaction below.
        lifecycle=(HERE/'fixtures/lifecycle_assistant_v2.py.snapshot').read_bytes()
        guardraw=(HERE/'core_release_guard.py').read_bytes()
        self.put(self.f.root/'operations/lifecycle_adapter.py',lifecycle,0o700)
        for path,mode in ((self.f.root/'operations/core_release_guard.py',0o700),(self.f.root/'operations/r25-publisher/core_release_guard.py',0o600)):
            self.put(path,guardraw,mode)
        self.old['controllers']['lifecycle_adapter.py']=business.sha(lifecycle)
        self.old['controllers']['core_release_guard.py']=business.sha(guardraw)
        self.old['runtime_modules']['core_release_guard.py']=business.sha(guardraw)
        oldsha=self.put(self.f.store/'POLICY.json',guard.encoded(self.old))
        self.oldplan['files']['core_release_guard.py']['after_sha256']=business.sha(guardraw)
        self.oldplan['operational']['guard']['after_sha256']=business.sha(guardraw)
        self.oldplan['operational']['lifecycle']={'before_sha256':business.sha(lifecycle),'after_sha256':business.sha(lifecycle)}
        self.oldplan['operational']['policy']['after_sha256']=oldsha
        before=self.g.live['handoff'].read_bytes()
        handoff=core_runtime.replace_env(before,{'R25_CORE_RELEASE_POLICY_SHA256':oldsha,
            'R25_CORE_RELEASE_GUARD_SHA256':business.sha(guardraw),'R25_LIFECYCLE_ADAPTER_SHA256':business.sha(lifecycle)})
        self.put(self.g.live['handoff'],handoff);self.oldplan['operational']['handoff']['after_sha256']=business.sha(handoff)
        oldid=business.sha(guard.encoded(self.oldplan));oldfolder=self.f.store/'controller-upgrades'/oldid
        self.put(oldfolder/'approval.json',guard.encoded(self.oldplan))
        self.put(oldfolder/'journal.json',guard.encoded({'schema':upgrade.MANAGED_SCHEMA,'phase':'INSTALLED','approval_sha256':oldid}))
        self.put(self.f.store/'CONTROL_UPGRADE_ACTIVE.json',guard.encoded({'schema':'R25_CORE_CONTROLLER_UPGRADE_ACTIVE_V1','approval_sha256':oldid}))
        record={**candidate,'schema':guard.BUSINESS_RECORD_SCHEMA,'policy_sha256':oldsha}
        folder=self.f.store/'releases'/(candidate['artifact_sha256']+'-'+candidate['commit_sha'])
        recordsha=self.put(folder/'record.json',guard.encoded(record))
        self.current=core_runtime.Release(candidate['commit_sha'],candidate['artifact_sha256'],recordsha)
        self.coretx='8'*32
        self.put(self.f.store/'CURRENT.json',guard.encoded({'schema':'R25_CORE_CURRENT_V1','transaction_id':self.coretx,'release':asdict(self.current)}))
        self.put(self.f.store/'ACTIVE.json',guard.encoded({'transaction_id':self.coretx}))
        self.put(self.f.store/'transactions'/self.coretx/'journal.json',guard.encoded({'transaction_id':self.coretx,'phase':'COMMITTED','candidate':asdict(self.current)}))
        # Existing fixture's AI CURRENT omits its historical committed tx field.
        ai=self.f.root/'runtime/ai-publications';cur=self.f.rt.read(ai/'CURRENT.json');cur['transaction_id']=self.f.rt.read(ai/'ACTIVE.json')['transaction_id']
        self.put(ai/'CURRENT.json',guard.encoded(cur))
        self.old['upgrade']['core_followup']['ai_current_sha256']=business.sha(guard.encoded(cur))
        priorpath=self.f.store/'core-followup-baselines'/(self.old['upgrade']['core_followup']['predecessor_policy_sha256']+'.json')
        prior=self.f.rt.read(priorpath);prior['upgrade']['core_followup']['ai_current_sha256']=business.sha(guard.encoded(cur))
        adjusted_prior=business.sha(guard.encoded(prior))
        self.put(self.f.store/'core-followup-baselines'/(adjusted_prior+'.json'),guard.encoded(prior))
        self.old['upgrade']['core_followup']['predecessor_policy_sha256']=adjusted_prior
        self.old['upgrade']['assistant']['baseline_policy_sha256']=adjusted_prior
        # Keep this context correction bound in the immutable synthetic baseline.
        oldsha=self.put(self.f.store/'POLICY.json',guard.encoded(self.old));self.oldplan['operational']['policy']['after_sha256']=oldsha
        handoff=core_runtime.replace_env(handoff,{'R25_CORE_RELEASE_POLICY_SHA256':oldsha});self.put(self.g.live['handoff'],handoff)
        self.oldplan['operational']['handoff']['after_sha256']=business.sha(handoff)
        oldid=business.sha(guard.encoded(self.oldplan));oldfolder=self.f.store/'controller-upgrades'/oldid
        self.put(oldfolder/'approval.json',guard.encoded(self.oldplan));self.put(oldfolder/'journal.json',guard.encoded({'schema':upgrade.MANAGED_SCHEMA,'phase':'INSTALLED','approval_sha256':oldid}))
        self.put(self.f.store/'CONTROL_UPGRADE_ACTIVE.json',guard.encoded({'schema':'R25_CORE_CONTROLLER_UPGRADE_ACTIVE_V1','approval_sha256':oldid}))
        record['policy_sha256']=oldsha;recordsha=self.put(folder/'record.json',guard.encoded(record));self.current=core_runtime.Release(candidate['commit_sha'],candidate['artifact_sha256'],recordsha)
        self.put(self.f.store/'CURRENT.json',guard.encoded({'schema':'R25_CORE_CURRENT_V1','transaction_id':self.coretx,'release':asdict(self.current)}))
        self.put(self.f.store/'transactions'/self.coretx/'journal.json',guard.encoded({'transaction_id':self.coretx,'phase':'COMMITTED','candidate':asdict(self.current)}))
        owner=(os.geteuid(),os.getegid());self.overlay_events=[]
        self.vrt=vmm.Runtime(self.f.root,self.f.root/'lock',private=owner,scripts=owner,supervise=self.overlay_events.append)
        self.vrt.mkdir(self.vrt.store)
        for name,path in self.vrt.preserved().items():
            if not path.exists():self.put(path,('synthetic process proof '+name+'\n').encode())
        self.sources={name:(HERE/name).read_bytes() for name in vmm.MODULES}
        self.overlay_deploy=self.package_extension('d'*40,'42')
        review=guard.encoded({'fixture_only':True,'scope':'synthetic managed overlay test, not real approval'})
        self.put(self.vrt.store/'REVIEW.json',review)
        request={'schema':'R25_VMM_CONTROLLER_REQUEST_V1','deploy':self.overlay_deploy,'review_evidence_sha256':business.sha(review)}
        self.put(self.vrt.store/'REQUEST.json',guard.encoded(request))
        result=vmm.prepare(self.vrt,business.sha(guard.encoded(request)));self.overlayid=result['plan_sha256'];self.overlayplan=json.loads(Path(result['plan_path']).read_bytes())
        self.put(self.vrt.store/'APPROVAL.json',guard.encoded(self.overlayplan))
        self.assertEqual(vmm.bind(self.vrt,self.overlayid)['outcome'],'INSTALLED')
        # Reuse only the role-document fixture constructor. The actual role
        # prepare/bind, strict document validation and history chain execute.
        helper=SimpleNamespace(root=self.f.root,rt=self.vrt,ident=self.overlayid,plan=self.overlayplan,sources=self.sources,
            put=lambda path,raw,mode=0o600:self.put(path,raw,mode))
        roleid=overlay_fixture.Controller.prepare_role_case(helper)
        self.assertEqual(vmm.bind_role(self.vrt,roleid)['outcome'],'ROLE_INSTALLED_DISABLED')
        self.put(self.f.root/'runtime/config/vmm-token',b'synthetic-not-a-production-token\n')
        self.put(self.f.root/'runtime/vmm/lifecycle.json',guard.encoded({'enabled':False,'desired':'STOPPED','phase':'DISABLED','process':None}))
        self.put(self.f.root/'runtime/vmm/gate.json',guard.encoded({'mode':'CLOSED','requests':{}}))
        self.put(self.f.root/'runtime/vmm/consumed-ledger.json',guard.encoded({'request':'historical synthetic NOT_SENT','consumed':1,'replay':False}))
        self.f.rt.events.clear();self.overlay_events.clear()
        self.original_snapshot=self.snapshot()

    def package_extension(self,commit,run):
        bundle=self.f.root/'operations/r25-vmm-candidates'/commit
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as archive:
            for name,raw in self.sources.items():archive.writestr(name,raw)
        blob=data.getvalue();manifest={'schema':vmm.MANIFEST_SCHEMA,'repository':'bailangguihun/auralink-deploy','ref':'refs/heads/release',
            'commit':commit,'run_id':run,'run_attempt':'1','archive_sha256':business.sha(blob),
            'files':[{'path':name,'bytes':len(raw),'sha256':business.sha(raw)} for name,raw in self.sources.items()]}
        self.put(bundle/'vmm-extension.zip',blob);self.put(bundle/'manifest.json',guard.encoded(manifest))
        for name,raw in self.sources.items():self.put(bundle/name,raw)
        return {**{k:manifest[k] for k in ('repository','ref','commit','run_id','run_attempt','archive_sha256')},
            'manifest_sha256':business.sha(guard.encoded(manifest)),'tool_sha256':business.sha(self.sources['vmm_controller.py'])}

    def snapshot(self):
        paths=[*self.g.live.values(),self.f.store/'POLICY.json',self.f.store/'CURRENT.json',self.f.store/'ACTIVE.json',
            self.f.store/'CONTROL_UPGRADE_ACTIVE.json',self.f.root/'runtime/ai-publications/CURRENT.json',self.f.root/'runtime/ai-publications/POLICY.json',
            self.f.root/'runtime/config/assistant-private/assistant-runtime.json',self.f.root/'runtime/config/frontend-selection.json',
            self.f.root/'runtime/config/frontend-selection.reference',self.f.root/'runtime/config/vmm-role.json',self.f.root/'runtime/config/vmm-service.json',
            self.f.root/'runtime/config/vmm-token',*list((self.f.root/'runtime/vmm').glob('*.json')),
            *list((self.vrt.store/'transactions').rglob('*')),*list((self.vrt.store/'role-bindings').rglob('*')),
            self.vrt.store/'ACTIVE.json',self.vrt.store/'ROLE_ACTIVE.json']
        return {str(path):(path.read_bytes(),path.stat().st_mode&0o777,path.stat().st_uid,path.stat().st_gid) for path in paths if path.is_file()}

    def prepare(self,wrong=None,*,publication=False,reuse_candidate=False,publication_successor=False,publication_repair=False):
        """Actual successor preparation; only fake Java bytecode is adapted."""
        spec=importlib.util.spec_from_file_location('managed_successor_fixture_package',HERE.parent/'scripts/package-r25-release.py')
        package=importlib.util.module_from_spec(spec);spec.loader.exec_module(package)
        if publication_successor or publication_repair:publication=True
        commit=('6' if publication_repair else '8' if publication_successor else '9' if publication else 'c')*40;self.bundle=self.f.root/'operations/r25-controller-candidates'/commit
        payload={name:(self.f.bundle/name).read_bytes() for name in package.FILES}
        for name in ('core_release_guard.py','upgrade_assistant_controller.py','core_acceptance.py'):
            payload[name]=(HERE/name).read_bytes()
        if publication_successor or publication_repair:
            for name in ('core_runtime.py','control_overlay.py'):payload[name]=(HERE/name).read_bytes()
        # The inherited policy-inventory stub is an explicit fixture boundary.
        # Give its new reviewed identity a different digest for copy-failure tests.
        if publication:payload['core_business.py']+=b'\n# synthetic new publication validator identity\n'
        archive=package.archive_bytes(payload);manifest=package.manifest_for(payload,archive,package.identity('bailangguihun/auralink-deploy',package.REF,commit,'777','1'))
        for name,raw in payload.items():self.put(self.bundle/name,raw)
        self.put(self.bundle/'r25-controller.zip',archive);self.put(self.bundle/'r25-controller-manifest.json',guard.encoded(manifest))
        deploy={key:manifest[key] for key in ('repository','ref','commit','run_id','run_attempt')}
        deploy.update(archive_sha256=business.sha(archive),manifest_sha256=business.sha(guard.encoded(manifest)),tool_sha256=business.sha(payload['upgrade_assistant_controller.py']))
        self.sources={name:(HERE/name).read_bytes() for name in vmm.MODULES}
        extension=self.package_extension(commit,'777')
        core={'commit':('5' if publication_repair else '7' if publication_successor else 'a' if publication else 'b')*40,'run_id':'888','run_attempt':'1'}
        incoming=self.f.root/'incoming'/(core['commit']+'-888-1');incoming.mkdir(mode=0o700,exist_ok=reuse_candidate)
        jar=incoming/C.JAR_NAME
        if not reuse_candidate:
            with zipfile.ZipFile(jar,'w') as zipped:
                zipped.writestr('BOOT-INF/classes/application-catalog-postgres-production.yml','synthetic: true\n')
                zipped.writestr('BOOT-INF/classes/com/auralink/Application.class',b'non-executable synthetic repair JAR' if publication_repair else b'non-executable synthetic publication JAR' if publication else b'non-executable synthetic successor JAR')
        jar.chmod(0o600)
        report={'status':'PASS','jar_sha256':business.sha(jar.read_bytes()),'jar_bytes':jar.stat().st_size,'profile_count':11,'profiles':{},'scope':C.SCOPE}
        self.put(incoming/C.REPORT_NAME,guard.encoded(report))
        with patch.object(C,'packaged_verifier',return_value=SimpleNamespace(verify=lambda _:report)):
            coremanifest=C.create(jar,incoming/C.REPORT_NAME,business.CURRENT_REPOSITORY,'refs/heads/release',core['commit'],'888','1')
        if wrong=='attempt':coremanifest['github_run_attempt']='2'
        if wrong=='repository':coremanifest['repository']='untrusted/foreign'
        self.put(incoming/'core-release-candidate.json',guard.encoded(coremanifest))
        if wrong=='archive':self.put(self.bundle/'r25-controller.zip',archive+b'foreign suffix')
        if wrong=='extension-run':extension['run_id']='999'
        if wrong=='extension-guard':self.put(self.f.root/'operations/r25-vmm-candidates'/commit/'core_release_guard.py',b'foreign source')
        request={'schema':'R25_MANAGED_VMM_CORE_PUBLICATION_REPAIR_REQUEST_V1' if publication_repair else 'R25_MANAGED_VMM_CORE_PUBLICATION_SUCCESSOR_REQUEST_V1' if publication_successor else 'R25_MANAGED_VMM_CORE_PUBLICATION_REQUEST_V1' if publication else 'R25_MANAGED_VMM_CORE_SUCCESSOR_REQUEST_V1','deploy':deploy,'vmm_deploy':extension,'core':core,
            'web_commit':self.old['upgrade']['web_commit'],'evidence':self.old['upgrade']['evidence'],'compatibility':self.old['upgrade']['compatibility'],
            'compatibility_evidence_sha256':('8' if reuse_candidate else 'c')*64,'review_evidence_sha256':self.oldplan['review_evidence_sha256']}
        if publication:self.put(self.f.store/'publication-reviews'/(request['review_evidence_sha256']+'.json'),(self.f.store/'CONTROL_UPGRADE_REVIEW.json').read_bytes())
        digest=business.sha(guard.encoded(request));load=guard.source_module;identity=self.f.rt.identity;validate=self.f.rt.storage.validate_target
        owner=tuple(self.oldplan['private_owner'])
        def source(path,name,raw):return C if Path(path).name=='core-release-candidate.py' else load(path,name,raw)
        def local_identity(path,**kwargs):
            if kwargs.get('expected_owner')==(0,0):kwargs['expected_owner']=owner
            return identity(path,**kwargs)
        def local_validate(path,**kwargs):
            if kwargs.get('expected_owner')==(0,0):kwargs['expected_owner']=owner
            return validate(path,**kwargs)
        # The installed full Postgres-policy loader remains the inherited
        # boundary; the new successor and overlay validators execute normally.
        with ExitStack() as stack:
            for context in (patch.object(guard,'policy',return_value=self.old),patch.object(core_publish,'INCOMING',incoming.parent),
                patch.object(guard,'source_module',side_effect=source),patch.object(self.f.rt,'identity',side_effect=local_identity),
                patch.object(self.f.rt.storage,'validate_target',side_effect=local_validate),
                patch.object(C,'packaged_verifier',return_value=SimpleNamespace(verify=lambda _:report))):stack.enter_context(context)
            result=upgrade.prepare_core_followup(self.f.rt,request,digest,self.bundle,managed_successor=not publication,publication=publication,publication_successor=publication_successor,publication_repair=publication_repair)
        self.plan=json.loads(Path(result['path']).read_bytes());self.new=self.f.rt.read(upgrade.source(self.bundle,'policy',self.plan))
        self.assertFalse(result['approved']);self.assertEqual(self.snapshot(),self.original_snapshot)
        return result

    def approve(self):
        self.ident=self.put(self.f.store/'CONTROL_UPGRADE_APPROVAL.json',guard.encoded(self.plan))

    def install(self):
        return upgrade.install(self.f.rt,self.plan,self.bundle,approval_sha256=self.ident)

    def recover(self):
        # Recovery compiles the hash-verified lifecycle before-image. Execute
        # those real bytes, then adapt only its OS supervision factory to the
        # same explicit CPU fixture used during installation. No recovery,
        # validation, journal, CAS or overlay method is replaced.
        actual_exec=exec
        self.f.rt.engine_value.load_execution_policy=lambda:None
        def isolated_exec(code,namespace,locals=None,**kwargs):
            actual_exec(code,namespace,locals,**kwargs)
            if str(getattr(code,'co_filename','')).endswith('/before/lifecycle'):
                namespace['production']=lambda fd:self.f.rt.engine_value
        with patch('builtins.exec',side_effect=isolated_exec):
            return upgrade.recover(self.f.rt,self.plan,self.bundle,approval_sha256=self.ident)

    def assert_preserved(self,*,installed=False):
        current=self.snapshot()
        allowed={str(self.f.store/'POLICY.json'),str(self.f.store/'CONTROL_UPGRADE_ACTIVE.json'),str(self.g.live['handoff'])} if installed else set()
        for path,expected in self.original_snapshot.items():
            if path not in allowed:self.assertEqual(current[path],expected,path)

    def test_successful_successor_binding_preserves_role_token_ledgers_and_applications(self):
        self.prepare();self.approve()
        self.assertEqual(self.plan['schema'],'R25_MANAGED_VMM_CORE_SUCCESSOR_BINDING_V1')
        self.assertEqual(self.new['upgrade']['core_followup']['schema'],'R25_MANAGED_VMM_CORE_SUCCESSOR_V1')
        self.assertEqual(self.install(),'INSTALLED');self.assertEqual(self.install(),'INSTALLED_VERIFIED_NOOP')
        self.assertEqual(self.f.rt.events,['stop-watchdog','start-watchdog'])
        self.assert_preserved(installed=True)
        effective=vmm.apply_policy_overlay(self.new,vmm.env_values(self.g.live['handoff'].read_bytes()),self.f.root,fixture_runtime=self.vrt)
        self.assertEqual(effective['upgrade'],self.new['upgrade'])
        self.assertTrue((self.vrt.store/'CORE_SUCCESSOR_ACTIVE.json').exists())

    def test_candidate_identity_mismatch_refuses_before_supervision(self):
        with self.assertRaisesRegex(Exception,'expected_workflow_identity_mismatch'):self.prepare('attempt')
        self.assertEqual(self.f.rt.events,[]);self.assertEqual(self.snapshot(),self.original_snapshot)

    def test_partial_policy_copy_recovers_same_transaction_and_preserves_state(self):
        self.prepare();self.approve();original=self.f.rt.storage.atomic_copy_file;failed=False
        def interrupted(source,target,digest,**kwargs):
            nonlocal failed
            if Path(target)==self.f.store/'POLICY.json' and not failed:
                failed=True;raise OSError('synthetic policy copy interruption')
            return original(source,target,digest,**kwargs)
        with patch.object(self.f.rt.storage,'atomic_copy_file',side_effect=interrupted),self.assertRaises(OSError):self.install()
        with self.assertRaisesRegex(Exception,'recovery_required'):self.install()
        self.assertEqual(self.recover(),'RESTORED');self.assert_preserved()
        self.assertFalse((self.vrt.store/'CORE_SUCCESSOR_ACTIVE.json').exists())
        vmm.apply_policy_overlay(self.old,vmm.env_values(self.g.live['handoff'].read_bytes()),self.f.root,fixture_runtime=self.vrt)

    def test_real_shared_lock_contention_preserves_inode_and_does_not_pause(self):
        self.prepare();self.approve();lock=self.f.root/'lock';inode=lock.stat().st_ino
        with lock.open('rb') as holder:
            fcntl.flock(holder,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(Exception,'shared_publication_busy'):self.install()
        self.assertEqual(lock.stat().st_ino,inode);self.assertEqual(self.f.rt.events,[]);self.assert_preserved()

    def test_mismatched_formal_bundle_layers_reject_without_pause(self):
        for wrong,reason in (('repository','expected_repository_mismatch'),('archive','file_digest'),
            ('extension-run','managed_successor_formal_run_mismatch'),('extension-guard','file_digest')):
            fixture=ManagedSuccessor('runTest');fixture.setUp()
            try:
                with self.subTest(wrong=wrong),self.assertRaisesRegex(Exception,reason):fixture.prepare(wrong)
                self.assertEqual(fixture.f.rt.events,[])
                self.assertEqual(fixture.snapshot(),fixture.original_snapshot)
            finally:fixture.doCleanups()

    def test_preserved_application_token_role_ai_web_and_ledger_drift_rejected(self):
        self.prepare();self.approve()
        paths=[self.g.live['application'],self.f.root/'runtime/config/vmm-token',
            self.f.root/'runtime/config/vmm-role.json',self.f.root/'runtime/config/vmm-service.json',
            self.f.root/'runtime/config/assistant-private/assistant-runtime.json',
            self.f.root/'runtime/ai-publications/CURRENT.json',self.f.root/'runtime/config/frontend-selection.json',
            self.f.root/'runtime/vmm/gate.json']
        for path in paths:
            raw=path.read_bytes();self.put(path,raw+b' ')
            try:
                with self.subTest(path=path.name),self.assertRaises(Exception):self.install()
                self.assertEqual(self.f.rt.events,[])
            finally:self.put(path,raw)
        self.assert_preserved()

    def test_disabled_role_and_unknown_work_remain_blocked_without_replay(self):
        self.prepare();self.approve()
        path=self.f.root/'runtime/vmm/lifecycle.json';raw=path.read_bytes()
        self.put(path,guard.encoded({'enabled':True,'desired':'RUNNING','phase':'STARTING','process':None}))
        with self.assertRaises(Exception):self.install()
        self.put(path,raw)
        gate=self.f.root/'runtime/vmm/gate.json';saved=gate.read_bytes()
        self.put(gate,guard.encoded({'mode':'CLOSED','requests':{'historical':{'state':'UNKNOWN'}}}))
        observed=gate.read_bytes()
        with self.assertRaises(Exception):self.install()
        self.assertEqual(gate.read_bytes(),observed);self.assertEqual(self.f.rt.events,[])
        self.put(gate,saved);self.assert_preserved()

    def test_lost_commit_observation_recovers_original_without_rebinding_or_dispatch(self):
        self.prepare();self.approve();write=self.f.rt.write;failed=False
        def lost_reply(path,value):
            nonlocal failed
            result=write(path,value)
            if value.get('phase')=='INSTALLED' and not failed:
                failed=True;raise TimeoutError('synthetic durable commit reply loss')
            return result
        with patch.object(self.f.rt,'write',side_effect=lost_reply),self.assertRaises(TimeoutError):self.install()
        self.assertTrue((self.vrt.store/'CORE_SUCCESSOR_ACTIVE.json').exists())
        with self.assertRaisesRegex(Exception,'recovery_required'):self.install()
        self.assertEqual(self.recover(),'RESTORED');self.assert_preserved()
        self.assertFalse((self.vrt.store/'CORE_SUCCESSOR_ACTIVE.json').exists())

    def test_not_started_recovery_is_readonly(self):
        self.prepare();self.approve()
        self.assertEqual(self.recover(),'CORE_FOLLOWUP_NOT_STARTED')
        self.assertEqual(self.f.rt.events,[]);self.assert_preserved()

    def test_actual_guard_policy_loads_bound_overlay_and_both_current_sides(self):
        self.prepare();self.approve();self.assertEqual(self.install(),'INSTALLED')
        source=guard.source_module
        def local_source(path,name,raw):
            module=source(path,name,raw)
            if Path(path).name=='vmm_controller.py':
                # Only UID/root and OS-supervision boundaries differ from
                # production; all loaded overlay code executes unchanged.
                owner=tuple(self.plan['private_owner']);module.LOCK=self.f.root/'lock'
                module.Runtime=partial(module.Runtime,private=owner,scripts=owner,supervise=self.overlay_events.append)
            return module
        currentpath=self.f.store/'CURRENT.json';journalpath=self.f.store/'transactions'/self.coretx/'journal.json'
        before_current=currentpath.read_bytes();before_journal=journalpath.read_bytes()
        with patch.object(guard,'business_module',return_value=business),patch.object(business,'validate_policy',side_effect=lambda value:value),patch.object(guard,'source_module',side_effect=local_source):
            # This is the actual entry point, including current/configuration,
            # immutable predecessor chain, and source-bound optional overlay.
            baseline=guard.policy();self.assertEqual(baseline['upgrade'],self.new['upgrade'])
            candidate=self.new['upgrade']['candidate'];record={**candidate,'schema':guard.BUSINESS_RECORD_SCHEMA,
                'policy_sha256':self.plan['operational']['policy']['after_sha256']}
            folder=self.f.store/'releases'/(candidate['artifact_sha256']+'-'+candidate['commit_sha'])
            recordsha=self.put(folder/'record.json',guard.encoded(record))
            reference={'commit_sha':candidate['commit_sha'],'artifact_sha256':candidate['artifact_sha256'],'accepted_record_sha256':recordsha}
            self.put(currentpath,guard.encoded({'schema':'R25_CORE_CURRENT_V1','transaction_id':self.coretx,'release':reference}))
            self.put(journalpath,guard.encoded({'transaction_id':self.coretx,'phase':'COMMITTED','current':asdict(self.current),'candidate':reference}))
            committed=guard.policy();self.assertEqual(committed['upgrade'],self.new['upgrade'])
            # Missing/changing a retained predecessor must still fail closed.
            previous=self.f.store/'core-followup-baselines'/(self.new['upgrade']['core_followup']['predecessor_policy_sha256']+'.json')
            original=previous.read_bytes();self.put(previous,original+b' ')
            try:
                with self.assertRaisesRegex(Exception,'file_digest'):guard.policy()
            finally:self.put(previous,original)
        self.put(currentpath,before_current);self.put(journalpath,before_journal)
        self.assert_preserved(installed=True);self.assertEqual(self.overlay_events,[])

    def test_verify_intent_marker_then_failed_supervision_recovers_same_transaction(self):
        self.prepare();self.approve();engine=self.f.rt.engine_value
        def fail_resume():
            record=self.f.rt.read(self.f.store/'controller-upgrades'/self.ident/'journal.json')
            self.assertEqual(record['phase'],'VERIFY_INTENT')
            self.assertTrue((self.vrt.store/'CORE_SUCCESSOR_ACTIVE.json').exists())
            raise OSError('synthetic watchdog resume failure after verified marker')
        with patch.object(engine,'watchdog_start_locked',side_effect=fail_resume),self.assertRaisesRegex(OSError,'resume failure'):self.install()
        self.assertEqual(self.f.rt.read(self.f.store/'controller-upgrades'/self.ident/'journal.json')['phase'],'RECOVERY_REQUIRED')
        self.assertEqual(self.recover(),'RESTORED');self.assert_preserved()
        self.assertFalse((self.vrt.store/'CORE_SUCCESSOR_ACTIVE.json').exists())
        self.assertEqual(self.f.rt.events,['stop-watchdog','stop-watchdog','start-watchdog'])

    def test_actual_managed_overlay_fixture_is_disabled_and_valid(self):
        effective=vmm.apply_policy_overlay(self.old,vmm.env_values(self.g.live['handoff'].read_bytes()),self.f.root,fixture_runtime=self.vrt)
        self.assertEqual(effective['upgrade'],self.old['upgrade'])
        self.assertFalse(json.loads((self.f.root/'runtime/config/vmm-role.json').read_bytes())['enabled'])
        self.assertEqual(self.overlay_events,[])


if __name__=='__main__':unittest.main()
