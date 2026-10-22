"""Finite same-config managed Core successor policy; no production/providers.

Uses the existing synthetic formal fixture, real content-addressed predecessor
files and real AI provenance reader. OS supervision and SQL retain their
explicit fixture boundaries. This is not a project approval.
"""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import core_business as business
import core_release_guard as guard
import core_acceptance
import test_managed_vmm_core_followup as managed_fixture


class ManagedSuccessorPolicy(unittest.TestCase):
    def setUp(self):
        self.m=managed_fixture.ManagedCoreFollowup('runTest');self.m.setUp();self.addCleanup(self.m.doCleanups)
        self.m.prepare();self.old=deepcopy(self.m.new)
        self.g=self.m.g;self.f=self.m.f
        gallery_sha=self.old['upgrade']['core_followup']['predecessor_policy_sha256']
        self.g.put(self.f.store/'core-followup-baselines'/(gallery_sha+'.json'),guard.encoded(self.g.old))
        self.raw=business.render_managed_vmm_configuration(self.g.live['application'].read_bytes(),self.old)
        self.oldsha=business.sha(guard.encoded(self.old))
        self.g.put(self.f.store/'core-followup-baselines'/(self.oldsha+'.json'),guard.encoded(self.old))
        self.new=deepcopy(self.old);up=self.new['upgrade'];old=self.old['upgrade']
        up['configuration']['before_sha256']=up['configuration']['after_sha256']
        record={**old['candidate'],'schema':guard.BUSINESS_RECORD_SCHEMA,'policy_sha256':self.oldsha}
        self.record_path=self.f.store/'releases'/(record['artifact_sha256']+'-'+record['commit_sha'])/'record.json'
        self.record_sha=self.g.put(self.record_path,guard.encoded(record));self.record=record
        up['baseline_release']={k:old['candidate'][k] for k in ('commit_sha','artifact_sha256')}
        up['baseline_release']['accepted_record_sha256']=self.record_sha
        up['candidate'].update(commit_sha='9'*40,artifact_sha256='8'*64,payload_fingerprint='7'*64)
        up['core_followup'].update(schema='R25_MANAGED_VMM_CORE_SUCCESSOR_V1',predecessor_policy_sha256=self.oldsha)
        up['assistant'].update(baseline_policy_sha256=self.oldsha,baseline_payload_fingerprint=old['candidate']['payload_fingerprint'],
            compatible_recovery_jar_sha256=old['candidate']['artifact_sha256'])
        up['assistant']['artifacts']['core'].update(commit=up['candidate']['commit_sha'],sha256=up['candidate']['artifact_sha256'])
        # This inherited transaction fixture deliberately contains synthetic
        # module inventories. Full policy structure is covered separately below.
        def policy(value):business.validate_core_followup(value);return value
        p=patch.object(business,'validate_policy',side_effect=policy);p.start();self.addCleanup(p.stop)

    def test_valid_unchanged_configuration_and_original_ai_provenance(self):
        business.validate_policy(self.new)
        self.assertEqual(business.render_configuration(self.raw,self.new),self.raw)
        self.assertEqual(guard.verify_core_followup_context(self.new,validator=business),self.old['upgrade']['core_followup']['ai_release'])
        self.assertEqual(self.record_path.read_bytes(),guard.encoded(self.record))

    def test_changed_configuration_ai_predecessor_or_second_successor_rejected(self):
        for kind in ('duration','ai','policy','baseline','schema'):
            value=deepcopy(self.new);old=deepcopy(self.old)
            if kind=='duration':value['upgrade']['configuration']['vmm']['acceptance-duration-seconds']=4
            if kind=='ai':value['upgrade']['core_followup']['ai_current_sha256']='6'*64
            if kind=='policy':value['upgrade']['core_followup']['predecessor_policy_sha256']='6'*64;value['upgrade']['assistant']['baseline_policy_sha256']='6'*64
            if kind=='baseline':value['upgrade']['baseline_release']['commit_sha']='6'*40
            if kind=='schema':
                old=deepcopy(self.new)
                value['upgrade']['core_followup']['predecessor_policy_sha256']=business.sha(guard.encoded(old))
                value['upgrade']['assistant']['baseline_policy_sha256']=business.sha(guard.encoded(old))
            with self.subTest(kind=kind),self.assertRaises(Exception):guard.verify_core_followup_context(value,old,business)

    def test_bound_existing_managed_configuration_rejects_alias_missing_or_duplicate(self):
        line=b'auralink.vmm-managed.acceptance-duration-seconds: 3\n'
        self.assertIn(line,self.raw)
        for kind,raw in [('missing',self.raw.replace(line,b'')),('duplicate',self.raw+line),
            ('alias',self.raw+b'auralink.vmmManaged.enabled: true\n'),('different',self.raw.replace(line,b'auralink.vmm-managed.acceptance-duration-seconds: 4\n'))]:
            value=deepcopy(self.new);digest=business.sha(raw)
            value['upgrade']['configuration'].update(before_sha256=digest,after_sha256=digest)
            value['upgrade']['assistant']['configuration_sha256']=digest
            with self.subTest(kind=kind),self.assertRaises(Exception):business.render_configuration(raw,value)

    def test_original_accepted_record_and_new_record_require_their_exact_policy(self):
        up=self.new['upgrade'];newsha=self.g.put(self.f.store/'POLICY.json',guard.encoded(self.new))
        record={**up['candidate'],'schema':guard.BUSINESS_RECORD_SCHEMA,'policy_sha256':newsha}
        path=self.f.store/'releases'/(record['artifact_sha256']+'-'+record['commit_sha'])/'record.json'
        digest=self.g.put(path,guard.encoded(record))
        with patch.object(guard,'policy',return_value=self.new),patch.object(guard,'business_module',return_value=business):
            self.assertEqual(guard.release_record(self.record['artifact_sha256'],self.record_sha,self.record['commit_sha']),self.record)
            self.assertEqual(guard.release_record(record['artifact_sha256'],digest,record['commit_sha']),record)
            wrong={**record,'policy_sha256':self.oldsha};wrongsha=self.g.put(path,guard.encoded(wrong))
            with self.assertRaisesRegex(Exception,'business_record_policy'):guard.release_record(record['artifact_sha256'],wrongsha,record['commit_sha'])
        self.assertEqual(self.record_path.read_bytes(),guard.encoded(self.record))

    def test_successor_still_binds_readonly_music_risk_query(self):
        raw=Path(core_acceptance.__file__).read_bytes()
        self.new['runtime_modules']['core_acceptance.py']=business.sha(raw)
        def read(path,**kwargs):
            self.assertEqual(kwargs['expected'],business.sha(raw));return raw
        with patch.object(business,'read_bound',side_effect=read):
            self.assertIn(core_acceptance.MANAGED_VMM_INFLIGHT_SQL,business.acceptance_queries(self.new))

    def test_successor_still_rejects_process_managed_configuration_overrides(self):
        proc=self.f.root/'4321';proc.mkdir()
        (proc/'stat').write_text('4321 (synthetic java) S '+'0 '*18+'123 0\n')
        (proc/'status').write_text('Uid: 65534 65534 65534 65534\nGid: 65534 65534 65534 65534\nGroups: 65534\nCapEff: 0000000000000000\n')
        config=str(self.g.live['application']);args=('java\0--spring.config.additional-location=file:'+config+'\0').encode()
        service={'uid':65534,'gid':65534,'groups':[65534],'isolation':'nonroot-posix'}
        with patch.object(business,'_service',return_value=service):
            (proc/'cmdline').write_bytes(args);(proc/'environ').write_bytes(b'PATH=/usr/bin\0')
            self.m.real_verify_process(self.new,4321,123,config,proc_root=self.f.root)
            for key in ('AURALINK_VMM_MANAGED_ENABLED','AURALINK_VMMMANAGED_ENABLED','AURALINK_VMMMANAGED_TOKENFILE'):
                (proc/'environ').write_bytes((key+'=unbound\0').encode())
                with self.subTest(key=key),self.assertRaisesRegex(Exception,'configuration_override'):
                    self.m.real_verify_process(self.new,4321,123,config,proc_root=self.f.root)


class ManagedSuccessorFullPolicy(unittest.TestCase):
    def setUp(self):
        import test_assistant_runtime_contract as contracts
        f=contracts.AssistantContract('runTest');f.setUp();self.addCleanup(f.doCleanups)
        self.policy=deepcopy(f.policy);up=self.policy['upgrade']
        vmm={'enabled':True,'token-file':'/root/autodl-tmp/artlive-r25-native-pg16/handoff-production-20260923T062101Z/runtime/config/vmm-token',
             'acceptance-owner-id':11,'acceptance-source-painting-id':22,'acceptance-duration-seconds':3}
        raw=business.render_configuration(f.before,f.policy)+b'\n# R25 accepted managed gate\n'+b''.join(
            ('auralink.vmm-managed.'+k+': '+json.dumps(v)+'\n').encode() for k,v in sorted(vmm.items()))
        self.raw=raw;digest=business.sha(raw)
        up['configuration'].update(before_sha256=digest,after_sha256=digest,vmm=vmm)
        up['assistant'].update(configuration_sha256=digest,baseline_policy_sha256='1'*64)
        up['core_followup']={'schema':'R25_MANAGED_VMM_CORE_SUCCESSOR_V1','acceptance':'managed-vmm-closed-v1',
            'predecessor_policy_sha256':'1'*64,'ai_release':{'commit_sha':'2'*40,'source_tree_sha256':'3'*64,'record_sha256':'4'*64},
            'ai_transaction_id':'5'*32,**{k:'1'*64 for k in ('ai_policy_sha256','ai_active_sha256','ai_current_sha256',
            'ai_journal_sha256','ai_snapshot_sha256','ai_configuration_sha256')}}

    def test_full_policy_and_same_config_render_accept_managed_successor(self):
        business.validate_policy(self.policy)
        self.assertEqual(business.render_configuration(self.raw,self.policy),self.raw)
        bad=deepcopy(self.policy);del bad['upgrade']['configuration']['vmm']
        with self.assertRaisesRegex(Exception,'business_configuration'):business.validate_policy(bad)
        bad=deepcopy(self.policy);bad['upgrade']['configuration']['after_sha256']='2'*64
        with self.assertRaisesRegex(Exception,'configuration_binding'):business.validate_policy(bad)


if __name__=='__main__':unittest.main()
