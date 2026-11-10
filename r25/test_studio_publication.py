"""Studio finite binding through the actual complete retained controller chain.

Formal artifacts, proofs, CAS and recovery execute unchanged. JVM/Postgres/
watchdog effects remain the inherited explicit isolated fixture boundaries.
"""
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch
import core_release_guard as guard
import core_publish
import upgrade_assistant_controller as u
import vmm_controller as v
import test_assistant_runtime_contract as contract_fixture
import test_community_recovery_upgrade as recovery_fixture


class Studio(unittest.TestCase):
    def setUp(self):
        original=contract_fixture.AssistantContract.setUp
        def migrations(case):
            original(case)
            case.policy['upgrade']['assistant']['migrations']=[
                {'version':f'V{i:03d}','fixture_only':True} for i in range(1,17)]
        parent=recovery_fixture.Upgrade('runTest')
        with patch.object(contract_fixture.AssistantContract,'setUp',migrations):parent.setUp()
        self.addCleanup(parent.doCleanups)
        parent.prepare();v.install_community_supervision(parent.rt,parent.ident)
        self.parent=parent;self.k=parent.k;self.f=parent.k.f;self.rt=parent.rt
        self.f.old=deepcopy(self.f.new);self.f.oldplan=deepcopy(self.k.plan)
        self.f.original_snapshot=self.f.snapshot()
        self.before={p:p.read_bytes() for base in (self.rt.store,self.f.f.store/'controller-upgrades',self.k.root/'runtime/community')
                     for p in base.rglob('*') if p.is_file()}
        self.f.f.rt.events.clear();self.k.actions.clear()
        self.addCleanup(patch.stopall)
        patch.object(core_publish,'INCOMING',self.k.root/'incoming').start()
        patch.object(u,'STUDIO_SQLITE_SHA256',v.sha(b'-- isolated exact additional sqlite migration fixture')).start()

    def prepare(self):
        result=self.f.prepare(studio=True)
        self.plan=self.f.plan;self.ident=result['plan_sha256']
        self.k.put(self.f.f.store/'publication-approvals'/(self.ident+'.json'),v.encoded(self.plan))
        self.f.ident=self.ident
        return result

    def effective(self):
        return v.apply_policy_overlay(self.f.new,v.env_values(self.f.g.live['handoff'].read_bytes()),
                                      self.k.root,fixture_runtime=self.rt)

    def retained(self):
        for p,raw in self.before.items():self.assertEqual(raw,p.read_bytes(),str(p))

    def test_install_and_committed_candidate_keep_parent_proofs(self):
        self.prepare();self.assertEqual('INSTALLED',self.f.install())
        self.assertEqual(17,len(self.effective()['upgrade']['assistant']['migrations']))
        self.retained()
        self.k.commit_current(self.f.new,'e'*32,self.plan['current_release'])
        self.effective();self.retained()
        self.assertEqual('INSTALLED_VERIFIED_NOOP',self.f.install())
        self.assertEqual('INSTALLED_NO_RECOVERY',self.f.recover())

    def test_partial_policy_write_requires_recovery_and_preserves_community(self):
        self.prepare();copy=self.f.f.rt.storage.atomic_copy_file
        def failed(src,dst,*a,**kw):
            result=copy(src,dst,*a,**kw)
            if Path(dst)==self.f.f.store/'POLICY.json':raise InterruptedError('lost policy-copy reply')
            return result
        with patch.object(self.f.f.rt.storage,'atomic_copy_file',side_effect=failed),self.assertRaises(InterruptedError):
            self.f.install()
        self.assertEqual('RESTORED',self.f.recover());self.retained()
        self.assertFalse((self.rt.store/'STUDIO_PUBLICATION_ACTIVE.json').exists())
        v.apply_policy_overlay(self.f.old,v.env_values(self.f.g.live['handoff'].read_bytes()),self.k.root,fixture_runtime=self.rt)

    def test_candidate_identity_drift_and_retained_parent_tampering_refuse(self):
        self.prepare();self.f.install();self.k.commit_current(self.f.new,'e'*32,self.plan['current_release'])
        current=self.f.f.store/'CURRENT.json';raw=current.read_bytes();value=v.json_bytes(raw)
        value['release']['commit_sha']='f'*40;self.k.put(current,v.encoded(value))
        with self.assertRaises(Exception):self.effective()
        self.k.put(current,raw)
        parent=self.parent.folder/'approval.json';raw=parent.read_bytes();self.k.put(parent,raw+b' ')
        with self.assertRaises(Exception):self.effective()
        self.k.put(parent,raw);self.effective()

    def test_unrelated_schema_change_is_not_an_authorized_studio_extension(self):
        self.prepare();raw=self.f.new['upgrade']['assistant']['migrations']
        self.assertEqual(raw[-1],u.studio_migration())
        altered=deepcopy(self.f.new);altered['upgrade']['assistant']['migrations'][0]={'foreign':True}
        with self.assertRaisesRegex(Exception,'studio_migration_delta'):
            u.validate_managed_successor_bindings(self.f.f.rt,self.plan,self.f.old,altered,self.f.bundle)


if __name__=='__main__':unittest.main()
