"""Actual successor installer, Linux flock/fsync/CAS; process health is a fixture.

Fixture approval is synthetic local input, never a project/human approval.
No production paths, database, service restart or external requests are used.
"""
from dataclasses import asdict
import hashlib
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import assistant_runtime_overlay as overlay
import upgrade_assistant_controller as upgrade
import upgrade_core_controller as old_upgrade
import core_runtime
from publisher_transaction import Release
from r25_storage import R25Storage
import ai_release_guard as ai


def sha(raw):return hashlib.sha256(raw).hexdigest()


class Runtime(old_upgrade.ControlUpgradeRuntime):
    def __init__(self,root,store):
        self.storage=R25Storage([str(root)],os.getuid(),os.getgid());self.fd=None;self.store=store;self.events=[]
        self.engine_value=SimpleNamespace(active=lambda *_:{'pid':123,'ticks':456},frontend=lambda *_:None,
            watchdog_stop_locked=lambda:self.events.append('stop-watchdog'),watchdog_start_locked=lambda:self.events.append('start-watchdog'),watchdog_identity=lambda:{'pid':111,'ticks':222})
    def current(self):return Release(**self.read(self.store/'CURRENT.json')['release'])
    def require_terminal_application_transactions(self):self.require_lease()
    def require_no_active_work(self):self.require_lease()
    def verify_current(self,_):self.require_lease()
    def engine(self,partial_recovery=False):return self.engine_value


class Successor(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.store=self.root/'runtime/core-publications';self.bundle=self.root/'operations/r25-controller-candidates'/('d'*40)
        self.rt=Runtime(self.root,self.store)
        for directory in ('runtime','runtime/core-publications','runtime/ai-publications','operations','operations/r25-publisher','operations/r25-controller-candidates','operations/r25-controller-candidates/'+('d'*40),'release','release/guide_service','release/critic-service','release/critic-service/app'):
            self.rt.storage.mkdir_private(self.root/directory)
        self.rt.storage.atomic_write_bytes(self.root/'lock',b'lock')
        handoff=self.root/'runtime/handoff-control.env'
        for module,name,value in ((upgrade.guard,'ROOT',self.root),(upgrade.guard,'STORE',self.store),(old_upgrade,'LOCK',self.root/'lock'),(upgrade,'LIVE_FILES',{'handoff':handoff})):
            p=patch.object(module,name,value);p.start();self.addCleanup(p.stop)
        self.release=Release('a'*40,'b'*64,'c'*64)
        self.rt.storage.atomic_write_json(self.store/'CURRENT.json',{'release':asdict(self.release)})
        owner=[os.getuid(),os.getgid()]
        self.plan={'schema':upgrade.SCHEMA,'from_policy_schema':'R25_CORE_BUSINESS_POLICY_V2','next_owner':'c5ea',
            'current_release':asdict(self.release),'controller_owner':owner,'private_owner':owner,'review_evidence_sha256':'e'*64,
            'deploy':{'repository':'bailangguihun/auralink-deploy','ref':'refs/heads/release','commit':'d'*40,'run_id':'42','run_attempt':'1'},'files':{},'operational':{}}
        for name in upgrade.FILES:
            after=(b'def validate_policy(value): return value\n' if name=='core_business.py' else ('reviewed new fixture '+name).encode());before=None if name.startswith('assistant_') or name=='upgrade_assistant_controller.py' else ('installed V2 fixture '+name).encode()
            if before is not None:self.rt.storage.atomic_write_bytes(self.root/'operations/r25-publisher'/name,before)
            self.rt.storage.atomic_write_bytes(self.bundle/name,after)
            self.plan['files'][name]={'before_sha256':sha(before) if before else None,'after_sha256':sha(after)}
        wrapper=(Path(__file__).parent/'fixtures/r25-private-critic-env-policy-v2.py.snapshot').read_bytes()
        lifecycle=("BOUND='"+overlay.BASE_SHA256+"'\n"+'''class Engine:
    def __init__(self): self.v={};self.kind=None
    def active(self,*args): return {'pid':123,'ticks':456}
    def watchdog_stop_locked(self): pass
    def load_execution_policy(self): self.execution_policy='configured-provider'
    def watchdog_start_locked(self): pass
    def watchdog_identity(self): return {'pid':111,'ticks':222}
    def frontend(self,*args): pass
def production(fd): return Engine()
''').encode()
        ops={'guard':(b'old guard', (self.bundle/'core_release_guard.py').read_bytes()),
            'critic-wrapper':(wrapper,overlay.render(wrapper)),
            'lifecycle':(lifecycle,lifecycle.replace(overlay.BASE_SHA256.encode(),sha(overlay.render(wrapper)).encode())),
            'ai-guard':(b'old AI guard',(self.bundle/'ai_release_guard.py').read_bytes())}
        for name,(before,after) in ops.items():
            target=upgrade.targets(self.plan)[name][0];self.rt.storage.atomic_write_bytes(target,before,mode=0o700)
            path=upgrade.source(self.bundle,name)
            if not path.exists():self.rt.storage.atomic_write_bytes(path,after)
            self.plan['operational'][name]={'before_sha256':sha(before),'after_sha256':sha(after)}
        for name in ai.SOURCE_PATHS:
            target=self.root/'release'/name
            self.rt.storage.atomic_write_bytes(target,b'# recorded source\n')
        old_ai={'schema':'R25_AI_SOURCE_POLICY_V1','source_paths':list(ai.SOURCE_PATHS),'dependency_sha256':{'fixture':'f'*64}}
        meta=self.rt.storage.atomic_write_json(self.root/'runtime/ai-publications/POLICY.json',old_ai)
        new_ai={**old_ai,'schema':'R25_AI_SOURCE_POLICY_V2','repository':'bailangguihun/auralink-ai-service','source_paths':list(ai.ASSISTANT_PATHS),
            'legacy_policy_sha256':meta['sha256'],'source_owner':owner,'review_evidence_sha256':'e'*64}
        newmeta=self.rt.storage.atomic_write_json(self.bundle/'AI_POLICY.next.json',new_ai)
        self.plan['operational']['ai-policy']={'before_sha256':meta['sha256'],'after_sha256':newmeta['sha256']}
        old_policy={'schema':'R25_CORE_BUSINESS_POLICY_V2','installation':{'controller_owner':owner,'private_owner':owner},
            'controllers':{'r25-production-java-launch.py':'f'*64},'runtime_modules':{},
            'upgrade':{'assets':{'fixture':'retained-no-map'},'configuration':{'knowledge':{'map-enabled':False}}}}
        oldmeta=self.rt.storage.atomic_write_json(self.store/'POLICY.json',old_policy)
        new_policy={**old_policy,'controllers':{**old_policy['controllers'],'core_release_guard.py':self.plan['files']['core_release_guard.py']['after_sha256'],'lifecycle_adapter.py':self.plan['operational']['lifecycle']['after_sha256']},
            'runtime_modules':{name:entry['after_sha256'] for name,entry in self.plan['files'].items()},
            'upgrade':{**old_policy['upgrade'],'baseline_release':asdict(self.release),'capability':'assistant-v1'}}
        newmeta=self.rt.storage.atomic_write_json(self.bundle/'POLICY.next.json',new_policy)
        self.plan['operational']['policy']={'before_sha256':oldmeta['sha256'],'after_sha256':newmeta['sha256']}
        mapping={'R25_CORE_RELEASE_POLICY_SHA256':'policy','R25_CORE_RELEASE_GUARD_SHA256':'guard','R25_LIFECYCLE_ADAPTER_SHA256':'lifecycle','R25_R24_CRITIC_ENV_WRAPPER_SHA256':'critic-wrapper','R25_AI_RELEASE_POLICY_SHA256':'ai-policy','R25_AI_RELEASE_GUARD_SHA256':'ai-guard'}
        before=''.join(k+'='+self.plan['operational'][v]['before_sha256']+'\n' for k,v in mapping.items()).encode()
        after=core_runtime.replace_env(before,{k:self.plan['operational'][v]['after_sha256'] for k,v in mapping.items()})
        self.rt.storage.atomic_write_bytes(handoff,before);self.rt.storage.atomic_write_bytes(self.bundle/'HANDOFF.next.env',after)
        self.plan['operational']['handoff']={'before_sha256':sha(before),'after_sha256':sha(after)}
        self.approval=self.rt.storage.atomic_write_json(self.store/'CONTROL_UPGRADE_APPROVAL.json',self.plan)['sha256']

    def install(self):return upgrade.install(self.rt,self.plan,self.bundle,approval_sha256=self.approval)

    def test_success_repeat_no_java_or_next_restart_and_fixed_after_hashes(self):
        self.assertEqual(self.install(),'INSTALLED');self.assertEqual(self.install(),'INSTALLED_VERIFIED_NOOP')
        self.assertEqual(self.rt.events,['stop-watchdog','start-watchdog'])
        for name,item in upgrade.entries(self.plan).items():
            self.assertEqual(sha(upgrade.targets(self.plan)[name][0].read_bytes()),item['after_sha256'])

    def test_partial_controller_copy_requires_explicit_recovery(self):
        original=self.rt.storage.atomic_copy_file;failed=False
        def copy(source,target,digest,**kw):
            nonlocal failed
            if Path(target)==self.root/'operations/r25-publisher/core_runtime.py' and not failed:
                failed=True;raise OSError('injected copy failure')
            return original(source,target,digest,**kw)
        with patch.object(self.rt.storage,'atomic_copy_file',copy),self.assertRaises(OSError):self.install()
        with self.assertRaisesRegex(Exception,'recovery_required'):self.install()
        self.assertEqual(upgrade.recover(self.rt,self.plan,self.bundle,approval_sha256=self.approval),'RESTORED')
        for name,item in upgrade.entries(self.plan).items():
            target=upgrade.targets(self.plan)[name][0]
            if item['before_sha256'] is None:self.assertFalse(target.exists())
            else:self.assertEqual(sha(target.read_bytes()),item['before_sha256'])

    def test_foreign_source_refused_before_watchdog_stop(self):
        (self.bundle/'core_runtime.py').write_bytes(b'foreign')
        with self.assertRaises(Exception):self.install()
        self.assertEqual(self.rt.events,[])

if __name__=='__main__':unittest.main()
