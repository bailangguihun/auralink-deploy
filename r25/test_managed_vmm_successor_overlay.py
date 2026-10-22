"""Real files and immutable overlay proof for one synthetic Core successor.

No production identities, approvals, model loads, generation or database IO.
Actual R25 overlay/role installation fixtures use explicit OS callbacks.
"""
from copy import deepcopy
import io
import json
from pathlib import Path
import unittest
import zipfile

import vmm_controller as v
import test_vmm_controller as fixture


class SuccessorOverlay(unittest.TestCase):
    def setUp(self):
        f=fixture.Controller('runTest');f.setUp();self.addCleanup(f.doCleanups)
        self.f=f;self.rt=f.rt;self.root=f.root
        f.managed_core_baseline();prepared=v.prepare(f.rt,f.req)
        f.ident=prepared['plan_sha256'];f.plan=json.loads(Path(prepared['plan_path']).read_bytes())
        f.put(f.rt.store/'APPROVAL.json',v.encoded(f.plan));v.bind(f.rt,f.ident)
        self.roleid=f.prepare_role_case();v.bind_role(f.rt,self.roleid)
        f.put(self.root/'runtime/config/vmm-token',b'synthetic fixture secret, not a production token\n')
        f.put(self.root/'runtime/vmm/lifecycle.json',v.encoded({'enabled':False,'desired':'STOPPED','state':'DISABLED','process':None,'attempts':1}))
        f.put(self.root/'runtime/vmm/gate.json',v.encoded({'mode':'CLOSED','requests':{}}))
        self.before={str(p):p.read_bytes() for p in v.core_successor_paths(self.rt).values()}
        self.oldpolicy=self.rt.preserved()['policy'].read_bytes();self.oldactive=self.rt.preserved()['original-controller-active'].read_bytes()
        self.sources=deepcopy(f.sources)
        self.sources['vmm_controller.py']+=b'\n# synthetic successor controller revision\n'
        self.sources['core_release_guard.py']+=b'\n# synthetic successor guard revision\n'
        self.bundle=self.root/'operations/r25-vmm-candidates'/('b'*40);self.deploy=self.package()
        self.binding=v.capture_core_successor(self.rt,self.deploy)
        newpolicy=json.loads(self.oldpolicy);newpolicy['upgrade']['core_followup']['schema']='R25_MANAGED_VMM_CORE_SUCCESSOR_V1'
        newsha=v.sha(self.sources['core_release_guard.py'])
        newpolicy['controllers']['core_release_guard.py']=newsha;newpolicy['runtime_modules']['core_release_guard.py']=newsha
        self.newpolicy=v.encoded(newpolicy);self.oldhandoff=self.rt.targets()['handoff'][0].read_bytes()
        fields=v.env_values(self.oldhandoff);self.newhandoff=self.oldhandoff
        for key,value in {'R25_CORE_RELEASE_GUARD_SHA256':newsha,'R25_VMM_CONTROLLER_SHA256':self.deploy['tool_sha256']}.items():
            self.newhandoff=self.newhandoff.replace((key+'='+fields[key]+'\n').encode(),(key+'='+value+'\n').encode())
        pair=lambda before,after:{'before_sha256':v.sha(before),'after_sha256':v.sha(after)}
        self.plan={'schema':v.CORE_SUCCESSOR_SCHEMA,'deploy':dict(self.deploy),'vmm':self.binding,
            'files':{'vmm_controller.py':pair(f.sources['vmm_controller.py'],self.sources['vmm_controller.py']),
                     'core_release_guard.py':pair(f.sources['core_release_guard.py'],self.sources['core_release_guard.py'])},
            'operational':{'guard':pair(f.sources['core_release_guard.py'],self.sources['core_release_guard.py']),
                'policy':pair(self.oldpolicy,self.newpolicy),'handoff':pair(self.oldhandoff,self.newhandoff)},
            'review_evidence_sha256':v.sha(v.encoded({'synthetic_review':True}))}
        self.ident=v.sha(v.encoded(self.plan));self.folder=self.root/'runtime/core-publications/controller-upgrades'/self.ident
        self.f.put(self.folder/'before/CONTROL_UPGRADE_ACTIVE.json',self.oldactive)
        self.f.put(self.folder/'approval.json',v.encoded(self.plan));self.f.put(self.folder/'review.json',v.encoded({'synthetic_review':True}))

    def package(self):
        archive=io.BytesIO()
        with zipfile.ZipFile(archive,'w') as z:
            for name,raw in self.sources.items():z.writestr(name,raw)
        raw=archive.getvalue();manifest={'schema':v.MANIFEST_SCHEMA,'repository':'bailangguihun/auralink-deploy','ref':'refs/heads/release',
            'commit':'b'*40,'run_id':'124','run_attempt':'1','archive_sha256':v.sha(raw),
            'files':[{'path':name,'bytes':len(data),'sha256':v.sha(data)} for name,data in self.sources.items()]}
        self.f.put(self.bundle/'vmm-extension.zip',raw);self.f.put(self.bundle/'manifest.json',v.encoded(manifest))
        for name,data in self.sources.items():self.f.put(self.bundle/name,data)
        return {k:manifest[k] for k in ('repository','ref','commit','run_id','run_attempt','archive_sha256')}|{
            'manifest_sha256':v.sha(v.encoded(manifest)),'tool_sha256':v.sha(self.sources['vmm_controller.py'])}

    def activate_fixture(self):
        for name,data in [('vmm_controller.py',self.sources['vmm_controller.py']),('publisher-guard',self.sources['core_release_guard.py']),
                          ('operation-guard',self.sources['core_release_guard.py']),('handoff',self.newhandoff)]:
            path,_,mode=self.rt.targets()[name];self.f.put(path,data,mode)
        self.f.put(self.rt.preserved()['policy'],self.newpolicy)
        self.f.put(self.rt.preserved()['original-controller-active'],(json.dumps({'schema':'R25_CORE_CONTROLLER_UPGRADE_ACTIVE_V1','approval_sha256':self.ident},sort_keys=True,indent=2)+'\n').encode())
        self.f.put(self.folder/'journal.json',v.encoded({'schema':v.CORE_SUCCESSOR_SCHEMA,'approval_sha256':self.ident,'phase':'VERIFY_INTENT'}))
        v.verify_core_successor(self.rt,self.plan,'after')
        self.f.put(self.rt.store/'CORE_SUCCESSOR_ACTIVE.json',v.encoded({'schema':v.CORE_SUCCESSOR_ACTIVE_SCHEMA,'approval_sha256':self.ident}))

    def test_before_after_runtime_proof_preserves_old_overlay_role_and_ledgers(self):
        v.verify_core_successor(self.rt,self.plan,'before')
        self.activate_fixture()
        value=v.apply_policy_overlay(json.loads(self.newpolicy),v.env_values(self.newhandoff),self.root,fixture_runtime=self.rt)
        self.assertEqual(value['controllers']['lifecycle_adapter.py'],self.f.plan['files']['lifecycle']['after_sha256'])
        self.assertEqual(value['controllers']['core_release_guard.py'],v.sha(self.sources['core_release_guard.py']))
        for path,raw in self.before.items():self.assertEqual(Path(path).read_bytes(),raw)
        # Later legitimate application transaction/proof changes must stay with
        # their original guard; runtime overlay is not an installation replay.
        self.f.put(self.rt.preserved()['frontend'],b'new accepted web selection')
        self.f.put(self.root/'runtime/vmm/lifecycle.json',v.encoded({'enabled':True,'desired':'RUNNING'}))
        v.apply_policy_overlay(json.loads(self.newpolicy),v.env_values(self.newhandoff),self.root,fixture_runtime=self.rt)
        with self.assertRaises(Exception):v.verify_core_successor(self.rt,self.plan,'after')

    def test_mixed_state_observable_but_runtime_rejects_incomplete_installation(self):
        self.f.put(self.rt.preserved()['original-controller-active'],(json.dumps({'schema':'R25_CORE_CONTROLLER_UPGRADE_ACTIVE_V1','approval_sha256':self.ident},sort_keys=True,indent=2)+'\n').encode())
        path,_,mode=self.rt.targets()['publisher-guard'];self.f.put(path,self.sources['core_release_guard.py'],mode)
        v.verify_core_successor(self.rt,self.plan,'mixed')
        with self.assertRaises(Exception):v.verify_core_successor(self.rt,self.plan,'after')
        self.f.put(path,self.f.sources['core_release_guard.py'],mode);self.f.put(self.rt.preserved()['original-controller-active'],self.oldactive)
        v.verify_core_successor(self.rt,self.plan,'before')
        v.apply_policy_overlay(json.loads(self.oldpolicy),v.env_values(self.oldhandoff),self.root,fixture_runtime=self.rt)

    def test_foreign_identity_and_state_fail_closed(self):
        for name in ('token','service','role','gate','lifecycle-state'):
            path=v.core_successor_paths(self.rt)[name];raw=path.read_bytes();self.f.put(path,raw+b' ')
            with self.subTest(name=name),self.assertRaises(Exception):v.verify_core_successor(self.rt,self.plan,'before')
            self.f.put(path,raw)
        bad=deepcopy(self.plan);bad['vmm']['deploy']['run_attempt']='2'
        with self.assertRaises(Exception):v.verify_core_successor(self.rt,bad,'before')
        self.activate_fixture();path,_,mode=self.rt.targets()['operation-guard'];self.f.put(path,b'foreign guard',mode)
        with self.assertRaises(Exception):v.apply_policy_overlay(json.loads(self.newpolicy),v.env_values(self.newhandoff),self.root,fixture_runtime=self.rt)

    def test_runtime_requires_exact_marker_approval_review_phase_and_preimage(self):
        self.activate_fixture()
        targets=[self.folder/'review.json',self.folder/'before/CONTROL_UPGRADE_ACTIVE.json',self.folder/'approval.json']
        for path in targets:
            raw=path.read_bytes();self.f.put(path,raw+b' ')
            with self.subTest(path=path.name),self.assertRaises(Exception):v.apply_policy_overlay(json.loads(self.newpolicy),v.env_values(self.newhandoff),self.root,fixture_runtime=self.rt)
            self.f.put(path,raw)
        self.f.put(self.folder/'journal.json',v.encoded({'schema':v.CORE_SUCCESSOR_SCHEMA,'approval_sha256':self.ident,'phase':'INSTALL_INTENT'}))
        with self.assertRaisesRegex(Exception,'not_installed'):v.apply_policy_overlay(json.loads(self.newpolicy),v.env_values(self.newhandoff),self.root,fixture_runtime=self.rt)


if __name__=='__main__':unittest.main()
