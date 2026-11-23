"""Actual retained Reference proof, formal source bundles, CAS and interruption.

Java/SQL/watchdog effects use the existing explicit isolated full-chain fixture.
Node and lost-start boundaries are exercised in test_community_code_package.
"""
from copy import deepcopy
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock,PropertyMock,patch

import vmm_controller as v
import core_release_guard as core_guard
import test_reference_publication as reference_fixture


class CodeController(unittest.TestCase):
    def setUp(self):
        self.f=reference_fixture.Reference('runTest')
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        self.f.prepare();self.assertEqual(self.f.f.install(),'INSTALLED');self.f.commit_candidate()
        self.k,self.rt,self.put=self.f.k,self.f.rt,self.f.put
        self.put(self.k.root/'release/artifacts/backend.jar',self.f.f.g.live['release-jar'].read_bytes())
        self.preserved={p:p.read_bytes()for base in (self.rt.store,self.k.root/'runtime/core-publications',
                        self.k.root/'runtime/community')for p in base.rglob('*')if p.is_file()}
        commit='e7'*20
        controller=self.k.controller_package(commit,'1007')
        self.k.f.sources['vmm_controller.py']+=b'\n# explicit code-only controller fixture revision\n'
        deployment=self.k.f.package_extension(commit,'1007');community=self.k.community_package(controller)
        self.review=v.encoded({'fixture_only':True,'scope':'schema3 code replacement'})
        self.request={'schema':'R25_COMMUNITY_CODE_CONTROLLER_REQUEST_V1','deploy':deployment,
                      'controller_deploy':controller,'community_deploy':community,
                      'community_source_provenance':{'commit':'d7'*20,'run_id':'1008','run_attempt':'1',
                                                     'archive_sha256':'a7'*32,'manifest_sha256':'b7'*32},
                      'review_evidence_sha256':v.sha(self.review)}
        self.events=[];self.rt.supervise=self.events.append

    def prepare(self):
        result=v.prepare_code_controller(self.rt,self.request,self.review)
        self.ident=result['approval_sha256'];self.folder=Path(result['folder'])
        self.plan=v.json_read(self.folder/'UNSIGNED_PLAN.json',self.rt.private)
        self.put(self.folder/'approval.json',v.encoded(self.plan))

    def effective(self):
        return v.apply_policy_overlay(self.f.f.new,v.env_values(self.rt.targets()['handoff'][0].read_bytes()),
                                      self.k.root,fixture_runtime=self.rt)

    def retained(self):
        for p,raw in self.preserved.items():self.assertEqual(p.read_bytes(),raw,str(p))

    def test_install_retains_all_reference_guards_and_changes_only_effective_lifecycle(self):
        self.prepare();before=self.effective()
        self.assertEqual(v.install_code_controller(self.rt,self.ident)['outcome'],'COMMUNITY_CODE_CONTROLLER_INSTALLED')
        self.assertEqual(self.events,['stop','start'])
        after=self.effective();before['controllers']['lifecycle_adapter.py']=self.plan['files']['lifecycle']['after_sha256']
        self.assertEqual(before,after);self.retained()
        self.assertEqual(v.install_code_controller(self.rt,self.ident)['outcome'],'COMMUNITY_CODE_CONTROLLER_INSTALLED_VERIFIED')
        lifecycle=self.rt.targets()['lifecycle'][0].read_bytes()
        self.assertIn(b"extra+=['--code-publication-sha256',code_id]",lifecycle)
        self.assertIn(b'pass_fds=(fd,),timeout=90)',lifecycle)
        # Execute the rendered selection against the full retained files. Only
        # the fixture's synthetic Python digest and the child process are mocked;
        # production interpreter checks remain unchanged.
        module=types.ModuleType('community_code_rendered_watchdog_fixture')
        module.__file__=str(self.rt.targets()['lifecycle'][0])
        exec(compile(lifecycle,module.__file__,'exec'),module.__dict__)
        engine=module.Engine(self.k.root,self.k.root/'preview',private_owner=self.rt.private,
                             script_owner=self.rt.scripts)
        original_guard=module.guard
        def fixture_guard(path,*args,**kwargs):
            if str(path)==sys.executable:return
            return original_guard(path,*args,**kwargs)
        def fixture_path(value):
            return Path(sys.executable) if str(value)=='/usr/bin/python3.10' else Path(value)
        child=types.SimpleNamespace(returncode=0,stdout=b'{"community":"CODE_ROUTED"}',stderr=b'')
        with patch.object(module,'Path',side_effect=fixture_path),patch.object(module,'guard',side_effect=fixture_guard),\
             patch.object(module.Engine,'diagnostics',new_callable=PropertyMock,return_value=Mock()),\
             patch.object(module.subprocess,'run',return_value=child) as run,self.rt.lease():
            self.assertEqual(engine.community_action('tick',self.rt.fd),'CODE_ROUTED')
            argv=run.call_args.args[0]
            self.assertEqual(argv[3],str(self.k.root/'operations/community-controller'/
                             self.plan['community_deploy']['manifest_sha256']/'community_host_cli.py'))
            self.assertEqual(argv[-2:],['--code-publication-sha256',self.ident])
            self.assertEqual(run.call_args.kwargs['pass_fds'],(self.rt.fd,))
            self.assertEqual(run.call_args.kwargs['timeout'],90)

    def interrupt_control(self,target):
        self.prepare()
        def fail(name):
            if name==target:raise InterruptedError(target)
        with self.assertRaises(InterruptedError):v.install_code_controller(self.rt,self.ident,fault=fail)
        self.assertEqual(self.events,['stop'])
        with self.assertRaisesRegex(Exception,'explicit_recovery_required'):
            v.install_code_controller(self.rt,self.ident)
        self.assertEqual(v.install_code_controller(self.rt,self.ident,recover=True)['outcome'],
                         'COMMUNITY_CODE_CONTROLLER_INSTALLED')
        self.effective();self.retained()

    def test_partial_vmm_copy_requires_explicit_forward_recovery(self):
        self.interrupt_control('vmm_controller.py')

    def test_partial_lifecycle_copy_requires_explicit_forward_recovery(self):
        self.interrupt_control('lifecycle')

    def test_partial_handoff_copy_requires_explicit_forward_recovery(self):
        self.interrupt_control('handoff')

    def test_live_target_and_historical_preimage_tampering_cannot_hide_behind_projection(self):
        self.prepare();v.install_code_controller(self.rt,self.ident)
        for path in (self.rt.targets()['lifecycle'][0],self.folder/'before/lifecycle',
                     self.rt.preserved()['core-current'],self.rt.preserved()['policy']):
            with self.subTest(path=str(path)):
                raw=path.read_bytes();path.write_bytes(raw+b' ')
                try:
                    with self.assertRaises(Exception):self.effective()
                finally:path.write_bytes(raw)
                self.effective()
        self.retained()

    def test_review_core_jar_and_lease_drift_refuse_before_pause(self):
        self.prepare()
        jar=self.k.root/'release/artifacts/backend.jar';raw=jar.read_bytes();self.put(jar,raw+b' ')
        try:
            with self.assertRaisesRegex(Exception,'runtime_jar_changed'):v.install_code_controller(self.rt,self.ident)
            self.assertEqual(self.events,[])
        finally:self.put(jar,raw)
        self.rt.lock.rename(self.rt.lock.with_suffix('.old'));self.put(self.rt.lock,b'')
        with self.assertRaisesRegex(Exception,'lease_changed'):v.install_code_controller(self.rt,self.ident)
        self.assertEqual(self.events,[])

    def test_no_arbitrary_target_can_be_added_to_historical_map(self):
        self.prepare();plan={**self.plan,'files':{**self.plan['files'],'core-current':self.plan['files']['handoff']}}
        with self.rt.lease(),self.assertRaisesRegex(Exception,'fixed_targets'):
            v.verify_code_controller(self.rt,plan,self.folder,'before')

    def install_historical_overlay(self):
        self.prepare()
        v.install_code_controller(self.rt,self.ident)
        root = self.rt.root
        store = root/'runtime/core-publications'
        current = v.json_read(store/'CURRENT.json',self.rt.private)
        previous = current['release']
        jar = root/'release/artifacts/backend.jar'
        archive = store/'releases'/(previous['artifact_sha256']+'-'+previous['commit_sha'])/'backend.jar'
        self.put(archive,jar.read_bytes())
        next_jar = b'Fixture successor Core artifact'
        next_core = {'commit_sha':'d'*40,'artifact_sha256':v.sha(next_jar),'accepted_record_sha256':'c'*64}
        # Only the independent Core runtime eligibility check is simulated.
        # Approval parsing and the archived JAR reader are the shipped guard.
        fixture_guard = Path(core_guard.__file__).read_bytes() + f'''
ROOT = Path({str(root)!r})
STORE = ROOT / 'runtime/core-publications'

def private_record(path, expected=None):
    return strict_json(read_file(path, owner={self.rt.private!r}, expected=expected))

def verify_assistant_rollout_core(value, approval, folder, *, preserve_only=False):
    must(preserve_only and private_record(STORE / 'CURRENT.json')['release'] == {next_core!r},
         'fixture_core_release_identity')
    return {next_core!r}
'''.encode()
        controller = b'# Approved successor VMM controller fixture\n'
        old_policy = self.f.f.new
        next_policy = deepcopy(old_policy)
        next_policy['upgrade']['core_followup']['schema'] = 'R25_ASSISTANT_ILLUSTRATION_CORE_PUBLICATION_V1'
        next_policy['runtime_modules']['core_release_guard.py'] = v.sha(fixture_guard)
        handoff_path = self.rt.targets()['handoff'][0]
        replacements = {'R25_CORE_RELEASE_POLICY_SHA256':v.sha(v.encoded(next_policy)),
                        'R25_VMM_CONTROLLER_SHA256':v.sha(controller)}
        next_handoff = handoff_path.read_bytes()
        old_values = v.env_values(next_handoff)
        for key,value in replacements.items():
            next_handoff = next_handoff.replace(f'{key}={old_values[key]}\n'.encode(),f'{key}={value}\n'.encode())
        files = {'core_release_guard.py':fixture_guard,'vmm_controller.py':controller}
        operational = {'policy':v.encoded(next_policy),'handoff':next_handoff}
        targets = v.publication_targets(self.rt)
        before = {name:targets[name][0].read_bytes() for name in files.keys() | operational.keys()}
        before['ILLUSTRATION_CORE_CURRENT.json'] = (store/'CURRENT.json').read_bytes()
        before['CONTROL_UPGRADE_ACTIVE.json'] = (store/'CONTROL_UPGRADE_ACTIVE.json').read_bytes()
        review = v.encoded({'fixture':'Reviewed successor VMM proof'})
        approval = {
            'schema':core_guard.ASSISTANT_ILLUSTRATION_APPROVAL_SCHEMA,
            'review_evidence_sha256':v.sha(review),
            'controller_owner':next_policy['installation']['controller_owner'],
            'private_owner':next_policy['installation']['private_owner'],
            'assistant_rollout':{'community':{'code_controller_sha256':self.ident},
                'core_current_sha256':v.sha(before['ILLUSTRATION_CORE_CURRENT.json']),
                'control_active_sha256':v.sha(before['CONTROL_UPGRADE_ACTIVE.json'])},
        }
        for field,updates in (('files',files),('operational',operational)):
            approval[field] = {name:{'before_sha256':v.sha(before[name]),'after_sha256':v.sha(raw)}
                               for name,raw in updates.items()}
        approval['files']['assistant_rollout_binding.py'] = {'before_sha256':None,'after_sha256':'a'*64}
        approval_sha = v.sha(v.encoded(approval))
        folder = store/'controller-upgrades'/approval_sha
        self.put(folder/'approval.json',v.encoded(approval))
        self.put(folder/'review.json',review)
        self.put(folder/'journal.json',v.encoded({'schema':approval['schema'],'phase':'INSTALLED',
                                                'approval_sha256':approval_sha}))
        self.put(folder/'after/handoff',next_handoff)
        for name,raw in before.items():self.put(folder/'before'/name,raw)
        for name,raw in {**files,**operational}.items():
            path,_,mode=targets[name]
            self.put(path,raw)
            path.chmod(mode)
        self.put(store/'CONTROL_UPGRADE_ACTIVE.json',v.encoded({'schema':'R25_CORE_CONTROLLER_UPGRADE_ACTIVE_V1',
                                                              'approval_sha256':approval_sha}))
        tx = 'ef'*16
        self.assertFalse((store/'transactions'/tx).exists())
        self.put(store/'CURRENT.json',v.encoded(dict(current,release=next_core,previous=previous,transaction_id=tx)))
        self.put(store/'transactions'/tx/'journal.json',v.encoded({'phase':'COMMITTED','transaction_id':tx,
                                                                  'candidate':next_core}))
        self.put(jar,next_jar)
        self.put(self.f.f.g.live['preview-jar'],next_jar)
        return next_policy, v.env_values(next_handoff), approval, folder, archive

    def test_rollout_accepts_exact_historical_code_controller_through_reference_chain(self):
        policy,handoff,approval,folder,_ = self.install_historical_overlay()
        with self.rt.lease():
            self.assertEqual(v.apply_policy_overlay(policy,handoff,self.rt.root,fixture_runtime=self.rt),policy)
            history = v.assistant_rollout_history(self.rt,approval,folder)
            for phase,runtime in (('before',False),('mixed',False),('after',False),('before',True),('mixed',True)):
                with self.subTest(phase=phase,runtime=runtime), \
                     self.assertRaisesRegex(v.Refused,'community_code_history_runtime_only'):
                    v.verify_code_controller(self.rt,self.plan,self.folder,phase,runtime=runtime,
                                             historical=history,read_archived_jar=core_guard.read_file)
            with self.assertRaisesRegex(v.Refused,'community_code_core_runtime_changed'):
                v.applied_code_publication(self.rt,self.f.f.new,handoff)

    def test_rollout_rejects_drift_in_archived_core_or_old_code_plan(self):
        policy,handoff,_,folder,archive = self.install_historical_overlay()
        code_marker = self.rt.store/'COMMUNITY_CODE_PUBLICATION_ACTIVE.json'
        cases = ((archive,b'Replaced archived JAR','file_digest'),
                 (archive.parent/'record.json',b'{}','file_digest'),
                 (folder/'before/ILLUSTRATION_CORE_CURRENT.json',b'{}','file_digest'),
                 (self.folder/'approval.json',b'{}','file_digest'),
                 (code_marker,v.encoded({'schema':v.CODE_CONTROLLER_SCHEMA,'approval_sha256':'f'*64}),
                  'assistant_rollout_code_pointer'))
        with self.rt.lease():
            self.assertEqual(v.apply_policy_overlay(policy,handoff,self.rt.root,fixture_runtime=self.rt),policy)
            for path,tampered,reason in cases:
                with self.subTest(path=str(path)):
                    raw = path.read_bytes()
                    self.put(path,tampered)
                    try:
                        with self.assertRaisesRegex(RuntimeError,reason):
                            v.apply_policy_overlay(policy,handoff,self.rt.root,fixture_runtime=self.rt)
                    finally:
                        self.put(path,raw)
            raw = archive.read_bytes()
            archive.unlink()
            try:
                with self.assertRaises(FileNotFoundError):
                    v.apply_policy_overlay(policy,handoff,self.rt.root,fixture_runtime=self.rt)
            finally:
                self.put(archive,raw)
            self.assertEqual(v.apply_policy_overlay(policy,handoff,self.rt.root,fixture_runtime=self.rt),policy)


class FormalRuntimeBoundary(unittest.TestCase):
    def formal_runtime(self,alter=None):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bundle=root/'formal-vmm';bundle.mkdir(mode=0o700)
            for name in v.MODULES:
                path=bundle/name;path.write_bytes((Path(__file__).parent/name).read_bytes());path.chmod(0o600)
            def put(relative,raw):
                path=root/relative;path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                path.write_bytes(raw);path.chmod(0o600)
            jar=b'Explicit isolated JAR identity fixture, not a Java application.'
            release={'artifact_sha256':v.sha(jar)};tx='a'*32
            put('release/artifacts/backend.jar',jar)
            put('runtime/core-publications/CURRENT.json',v.encoded({'transaction_id':tx,'release':release}))
            put('runtime/core-publications/transactions/'+tx+'/journal.json',
                v.encoded({'transaction_id':tx,'candidate':release,'phase':'COMMITTED'}))
            if alter=='fifo':
                path=root/'release/artifacts/backend.jar';path.unlink();os.mkfifo(path,0o600)
            elif alter=='directory':(root/'release/artifacts').chmod(0o777)
            elif alter=='symlink':
                path=root/'release/artifacts';path.rename(path.with_name('retained-artifacts'))
                path.symlink_to(path.with_name('retained-artifacts'),target_is_directory=True)
            elif alter=='hardlink':os.link(root/'release/artifacts/backend.jar',root/'linked.jar')
            code="""import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import vmm_controller as v
rt=v.Runtime(Path(sys.argv[2]),Path(sys.argv[2])/'unused-lock',private=(int(sys.argv[3]),int(sys.argv[4])),scripts=(int(sys.argv[5]),int(sys.argv[4])))
print(json.dumps(v.code_core_runtime(rt)))
"""
            script_uid=os.geteuid()+1 if alter=='owner_policy' else os.geteuid()
            result=subprocess.run([sys.executable,'-I','-B','-c',code,str(bundle),str(root),str(os.geteuid()),str(os.getegid()),str(script_uid)],
                                  env={},capture_output=True,text=True,timeout=10)
            return result,v.sha(jar)

    def test_four_file_formal_bundle_can_verify_the_core_runtime_in_a_fresh_interpreter(self):
        result,digest=self.formal_runtime()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['jar_sha256'],digest)

    def test_fifo_is_refused_without_blocking_on_open(self):
        result,_=self.formal_runtime('fifo')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('runtime_jar_identity',result.stderr)

    def test_writable_jar_parent_is_refused(self):
        result,_=self.formal_runtime('directory')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('runtime_jar_directory_identity',result.stderr)

    def test_jar_parent_must_match_the_retained_script_owner(self):
        result,_=self.formal_runtime('owner_policy')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('runtime_jar_directory_identity',result.stderr)

    def test_symlink_parent_and_hardlinked_jar_are_refused(self):
        for kind in ('symlink','hardlink'):
            with self.subTest(kind=kind):
                result,_=self.formal_runtime(kind)
                self.assertNotEqual(result.returncode,0)


if __name__=='__main__':unittest.main()
