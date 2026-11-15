"""Actual retained Reference proof, formal source bundles, CAS and interruption.

Java/SQL/watchdog effects use the existing explicit isolated full-chain fixture.
Node and lost-start boundaries are exercised in test_community_code_package.
"""
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock,PropertyMock,patch

import vmm_controller as v
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


if __name__=='__main__':unittest.main()
