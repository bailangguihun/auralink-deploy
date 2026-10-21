"""Finite Gallery -> managed Core binding with synthetic formal artifacts.

Actual prepare/approval-copy/flock/CAS/guard/config rendering/recovery is used.
OS supervision and PostgreSQL boundaries use the existing explicit fixture.
No real model import, GPU, network generation or production access.
"""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import core_business as business
import core_release_guard as guard
import core_runtime
import core_acceptance
import upgrade_assistant_controller as upgrade
import test_core_followup_binding as gallery_fixture


class ManagedCoreFollowup(unittest.TestCase):
    def setUp(self):
        g=gallery_fixture.FollowupFixture('runTest');g.setUp();self.addCleanup(g.doCleanups)
        self.g=g;self.f=g.f;f=self.f
        self.counts={k:0 for k in ('music_queued','music_running','music_current_send_started','music_dispatch_history_send_started','music_dispatch_unfinished','music_execution_attempts_unfinished','music_creation_explicit_uncertain','music_step_explicit_uncertain','music_dispatch_resolution_uncertain','music_execution_resolution_uncertain')}
        self.counts.update(music_creations_by_status={'FAILED':1},music_steps_by_status={'FAILED':1},music_steps_by_dispatch_state={'NOT_SENT':1})
        # PostgreSQL is the explicit external boundary here; query spelling,
        # fail-closed result handling and all filesystem actions remain real.
        def query(policy,sql,fd):
            self.assertEqual(sql,core_acceptance.MANAGED_VMM_INFLIGHT_SQL);self.assertIsInstance(fd,int)
            return json.dumps(self.counts)
        q=patch.object(business,'readonly_query',side_effect=query);q.start();self.addCleanup(q.stop)
        proc=patch.object(business,'verify_process',return_value={'explicit_os_fixture':True});proc.start();self.addCleanup(proc.stop)
        self.real_verify_process=proc.temp_original
        # Commit the existing Gallery binding, then represent its nonempty
        # application publication with actual retained fixture JAR bytes.
        jar=b'accepted Gallery Core fixture payload'
        candidate=g.new['upgrade']['candidate'];candidate['artifact_sha256']=business.sha(jar)
        candidate['artifact_bytes']=len(jar);candidate['payload_fingerprint']='b'*64
        g.new['upgrade']['assistant']['artifacts']['core']['sha256']=candidate['artifact_sha256']
        g.plan['operational']['policy']['after_sha256']=g.put(f.bundle/'POLICY.next.json',guard.encoded(g.new))
        after=core_runtime.replace_env(g.live['handoff'].read_bytes(),{'R25_CORE_RELEASE_POLICY_SHA256':g.plan['operational']['policy']['after_sha256']})
        g.plan['operational']['handoff']['after_sha256']=g.put(f.bundle/'HANDOFF.next.env',after);g.reapprove()
        self.assertEqual(f.install(),'INSTALLED');self.gallery_approval=f.approval
        record={**candidate,'schema':guard.BUSINESS_RECORD_SCHEMA,'policy_sha256':g.plan['operational']['policy']['after_sha256']}
        folder=f.store/'releases'/(candidate['artifact_sha256']+'-'+candidate['commit_sha'])
        recordsha=g.put(folder/'record.json',guard.encoded(record))
        f.release=core_runtime.Release(candidate['commit_sha'],candidate['artifact_sha256'],recordsha)
        g.put(f.store/'CURRENT.json',guard.encoded({'schema':'R25_CORE_CURRENT_V1','release':asdict(f.release)}))
        for name in ('release-jar','preview-jar'):g.put(g.live[name],jar)
        g.old=deepcopy(g.new)
        # All inherited source/approval fixtures are explicitly synthetic.
        f.rt.events.clear()
        self.before={str(p):p.read_bytes() for p in [g.live['application'],f.store/'POLICY.json',folder/'record.json',
            f.store/'controller-upgrades'/self.gallery_approval/'approval.json',f.store/'controller-upgrades'/self.gallery_approval/'journal.json',
            f.root/'runtime/ai-publications/CURRENT.json',f.root/'runtime/config/assistant-private/assistant-runtime.json']}

    def prepare(self,wrong=None):
        result=self.g.preparation(wrong=wrong,managed=True)
        self.plan=json.loads(Path(result['path']).read_text())
        self.new=self.f.rt.read(self.f.bundle/'POLICY.next.json')
        return result

    def approve(self):
        self.ident=self.g.put(self.f.store/'CONTROL_UPGRADE_APPROVAL.json',guard.encoded(self.plan))
        return self.ident

    def install(self):
        return upgrade.install(self.f.rt,self.plan,self.f.bundle,approval_sha256=self.ident)

    def test_prepare_bind_preserves_records_and_only_publisher_changes_application(self):
        r=self.prepare();self.assertFalse(r['approved']);self.assertEqual(self.plan['schema'],upgrade.MANAGED_SCHEMA)
        self.approve();self.assertEqual(self.install(),'INSTALLED');self.assertEqual(self.install(),'INSTALLED_VERIFIED_NOOP')
        for path,raw in self.before.items():
            if path!=str(self.f.store/'POLICY.json'):self.assertEqual(Path(path).read_bytes(),raw)
        after=business.render_managed_vmm_configuration(self.g.live['application'].read_bytes(),self.new)
        self.assertTrue(after.startswith(self.g.live['application'].read_bytes()))
        self.assertIn(b'auralink.vmm-managed.enabled: true',after)
        self.assertEqual(business.sha(after),self.new['upgrade']['configuration']['after_sha256'])
        self.assertEqual(self.f.rt.events,['stop-watchdog','start-watchdog'])
        self.assertEqual(guard.verify_core_followup_context(self.new,validator=business),self.plan['current_ai_release'])

    def test_before_after_and_restoration_config_phases_are_exact(self):
        self.prepare();self.approve();self.install();before=self.g.live['application'].read_bytes()
        after=business.render_managed_vmm_configuration(before,self.new)
        baseline=asdict(self.f.release);candidate={k:self.new['upgrade']['candidate'][k] for k in ('commit_sha','artifact_sha256')};candidate['accepted_record_sha256']='1'*64
        tx='2'*32;self.g.put(self.f.store/'ACTIVE.json',guard.encoded({'transaction_id':tx}))
        with patch.object(guard,'business_module',return_value=business):
            for phase,ref,raw in [('STOPPED',baseline,before),('START_INTENT',baseline,after),('COMMITTED',candidate,after),('RESTORE_INTENT',candidate,before),('RESTORED',baseline,before)]:
                self.g.put(self.f.store/'CURRENT.json',guard.encoded({'schema':'R25_CORE_CURRENT_V1','release':ref}))
                self.g.put(self.f.store/'transactions'/tx/'journal.json',guard.encoded({'transaction_id':tx,'phase':phase,'current':baseline,'candidate':candidate}))
                self.g.put(self.g.live['application'],raw)
                with self.subTest(phase=phase):guard.validate_configuration_context(self.new)
            self.g.put(self.g.live['application'],after)
            with self.assertRaisesRegex(Exception,'configuration_state'):guard.validate_configuration_context(self.new)

    def test_partial_binding_recovery_preserves_application_and_previous_policy(self):
        self.prepare();self.approve();copy=self.f.rt.storage.atomic_copy_file;failed=False
        def failure(source,target,digest,**kwargs):
            nonlocal failed
            if Path(target)==self.f.store/'POLICY.json' and not failed:failed=True;raise OSError('fixture interrupted bind')
            return copy(source,target,digest,**kwargs)
        with patch.object(self.f.rt.storage,'atomic_copy_file',side_effect=failure),self.assertRaises(OSError):self.install()
        with self.assertRaisesRegex(Exception,'recovery_required'):self.install()
        self.assertEqual(upgrade.recover(self.f.rt,self.plan,self.f.bundle,approval_sha256=self.ident),'RESTORED')
        for path,raw in self.before.items():self.assertEqual(Path(path).read_bytes(),raw)

    def test_unstarted_and_foreign_drift_recovery_fail_closed(self):
        self.prepare();self.approve()
        self.assertEqual(upgrade.recover(self.f.rt,self.plan,self.f.bundle,approval_sha256=self.ident),'CORE_FOLLOWUP_NOT_STARTED')
        self.g.put(self.g.live['application'],self.g.live['application'].read_bytes()+b'# foreign\n')
        with self.assertRaises(Exception):self.install()
        self.assertEqual(self.f.rt.events,[])

    def test_overlay_present_requires_separate_explicit_rollback(self):
        self.prepare();self.approve()
        self.g.put(self.g.live['handoff'],self.g.live['handoff'].read_bytes()+b'R25_VMM_CONTROLLER=/fixed/installed/controller\n')
        with self.assertRaisesRegex(Exception,'overlay_requires_explicit_rollback'):self.install()
        self.assertEqual(self.f.rt.events,[])

    def test_finite_predecessor_ai_provenance_and_alias_fail_closed(self):
        self.prepare()
        bad=deepcopy(self.new);bad['upgrade']['core_followup']['ai_configuration_sha256']='e'*64
        with self.assertRaises(Exception):guard.verify_core_followup_context(bad,self.g.old,business)
        for field,val in [('enabled',False),('token-file','/tmp/arbitrary'),('acceptance-owner-id',True),('acceptance-source-painting-id',0),('acceptance-duration-seconds',31)]:
            v=deepcopy(self.new['upgrade']['configuration']['vmm']);v[field]=val
            with self.subTest(field=field),self.assertRaises(Exception):business.validate_managed_vmm_configuration(v)
        raw=self.g.live['application'].read_bytes()+b'auralink:\n  vmm-managed:\n    enabled: true\n'
        bad=deepcopy(self.new);bad['upgrade']['configuration']['before_sha256']=business.sha(raw)
        with self.assertRaisesRegex(Exception,'existing_alias'):business.render_managed_vmm_configuration(raw,bad)
        bad=deepcopy(self.g.old);bad['upgrade']['core_followup']['schema']='R25_MANAGED_VMM_CORE_FOLLOWUP_V1'
        with self.assertRaises(Exception):guard.verify_core_followup_context(self.new,bad,business)

    def test_changed_ai_catalog_backup_policy_or_web_artifact_rejected(self):
        self.prepare()
        for kind in ('ai','media','database','web'):
            bad=deepcopy(self.new)
            if kind=='ai':bad['upgrade']['assistant']['artifacts']['ai']['commit']='3'*40
            if kind=='media':bad['upgrade']['assistant']['attachment_root']='/different'
            if kind=='database':bad['installation']['postgres']['database']='different'
            if kind=='web':bad['upgrade']['assistant']['artifacts']['web']['sha256']='0'*64
            with self.subTest(kind=kind),self.assertRaises(Exception):upgrade.validate_managed_vmm_bindings(self.f.rt,self.plan,self.g.old,bad,self.f.bundle)

    def test_actual_process_guard_rejects_relaxed_managed_overrides(self):
        self.prepare();proc=self.f.root/'4321';proc.mkdir()
        (proc/'stat').write_text('4321 (synthetic java) S '+'0 '*18+'123 0\n')
        (proc/'status').write_text('Uid: 65534 65534 65534 65534\nGid: 65534 65534 65534 65534\nGroups: 65534\nCapEff: 0000000000000000\n')
        config=str(self.g.live['application']);args=('java\0--spring.config.additional-location=file:'+config+'\0').encode()
        service={'uid':65534,'gid':65534,'groups':[65534],'isolation':'nonroot-posix'}
        with patch.object(business,'validate_policy',side_effect=lambda x:x),patch.object(business,'_service',return_value=service):
            (proc/'cmdline').write_bytes(args);(proc/'environ').write_bytes(b'PATH=/usr/bin\0')
            self.real_verify_process(self.new,4321,123,config,proc_root=self.f.root)
            for key in ('AURALINK_VMM_MANAGED_ENABLED','AURALINK_VMMMANAGED_ENABLED','AURALINK_VMMMANAGED_TOKENFILE','AURALINK_VMM_ACCEPTANCE_OWNER_ID'):
                (proc/'environ').write_bytes((key+'=unbound\0').encode())
                with self.subTest(key=key),self.assertRaisesRegex(Exception,'configuration_override'):self.real_verify_process(self.new,4321,123,config,proc_root=self.f.root)
            (proc/'environ').write_bytes(b'PATH=/usr/bin\0')
            for flag in ('--auralink.vmm-managed.enabled=false','--AURALINK.VMMMANAGED.ENABLED=false','-Dauralink.vmmManaged.enabled=false'):
                (proc/'cmdline').write_bytes(args+(flag+'\0').encode())
                with self.subTest(flag=flag),self.assertRaisesRegex(Exception,'configuration_override'):self.real_verify_process(self.new,4321,123,config,proc_root=self.f.root)

    def test_any_historical_unknown_or_unfinished_blocks_before_pause(self):
        self.prepare();self.approve()
        for key in ('music_current_send_started','music_dispatch_history_send_started','music_dispatch_unfinished','music_creation_explicit_uncertain'):
            self.counts[key]=1
            with self.subTest(key=key),self.assertRaisesRegex(Exception,'work_requires_readonly_reconciliation'):self.install()
            self.assertEqual(self.f.rt.events,[])
            self.counts[key]=0

    def test_candidate_formal_identity_rejected_before_pause(self):
        with self.assertRaises(Exception):self.prepare('attempt')
        self.assertEqual(self.f.rt.events,[])


class ManagedClosedAcceptance(unittest.TestCase):
    def test_inspection_validates_anonymous_shape_before_printable_result(self):
        with self.assertRaisesRegex(Exception,'inventory_contract'):
            core_acceptance.verify_managed_vmm_inventory({'unexpected_private_field':'not reportable'},require_clear=False)

    def test_one_read_only_capability_get_requires_actual_closed_music(self):
        calls=[]
        value={'featureEnabled':True,'operations':[{'code':'PAINTING_TO_MUSIC','inputModality':'PAINTING','outputModality':'AUDIO','executionAvailable':False,'availabilityReason':'CREATION_PROVIDER_NOT_READY','providers':[{'executionAvailable':False}]}]}
        def get(path):calls.append(path);return 200,'application/json',json.dumps(value).encode()
        self.assertFalse(core_acceptance.verify_managed_vmm_closed_http(get_authenticated=get)['dispatch_permitted'])
        self.assertEqual(calls,['/api/v1/workflow/node-types'])
        value['operations'][0]['executionAvailable']=True
        with self.assertRaisesRegex(Exception,'not_closed'):core_acceptance.verify_managed_vmm_closed_http(get_authenticated=get)


class ActualAcceptanceAndSQLBoundary(unittest.TestCase):
    def test_actual_runtime_acceptance_applies_closed_gate_only_to_candidate(self):
        from types import SimpleNamespace
        baseline=core_runtime.Release('a'*40,'b'*64,'c'*64)
        candidate=core_runtime.Release('d'*40,'e'*64,'f'*64)
        policy={'runtime_modules':{'assistant_acceptance.py':'1'*64},'upgrade':{'capability':'assistant-v1','candidate':{'artifact_sha256':candidate.artifact_sha256},'core_followup':{'acceptance':'managed-vmm-closed-v1'}}}
        rt=SimpleNamespace(query=lambda _:None,read=lambda _: {'bearer':'synthetic-private-session-not-a-real-token'})
        with patch.object(core_runtime,'verify_http',return_value={}),patch.object(guard,'release_record',return_value={'schema':guard.BUSINESS_RECORD_SCHEMA}),patch.object(guard,'policy',return_value=policy),patch.object(core_runtime,'verify_knowledge_http',return_value={}),patch.object(core_runtime,'authenticated_request',return_value=lambda _:None),patch.object(guard,'read_file',return_value=b'fixture'),patch.object(guard,'source_module',return_value=SimpleNamespace(verify_assistant_http=lambda *a,**kw:{})),patch.object(core_runtime,'verify_gallery_followup_http',return_value={}),patch.object(core_runtime,'verify_managed_vmm_closed_http',return_value={'dispatch_permitted':False}) as managed:
            core_runtime.CoreRuntime.acceptance(rt,baseline);managed.assert_not_called()
            core_runtime.CoreRuntime.acceptance(rt,candidate);self.assertEqual(managed.call_count,1)
            managed.side_effect=core_runtime.Refused('managed_vmm_dispatch_not_closed')
            with self.assertRaisesRegex(Exception,'not_closed'):core_runtime.CoreRuntime.acceptance(rt,candidate)

    def test_new_sql_is_an_exact_source_bound_literal_not_arbitrary_query(self):
        raw=Path(core_acceptance.__file__).read_bytes()
        policy={'runtime_modules':{'core_acceptance.py':business.sha(raw)},'installation':{'private_owner':[0,0],'postgres':{}},'upgrade':{'capability':'assistant-v1','core_followup':{'schema':'R25_MANAGED_VMM_CORE_FOLLOWUP_V1'}}}
        def read(path,**kwargs):self.assertEqual(kwargs['expected'],business.sha(raw));return raw
        with patch.object(business,'read_bound',side_effect=read):
            self.assertIn(core_acceptance.MANAGED_VMM_INFLIGHT_SQL,business.acceptance_queries(policy))
            legacy=deepcopy(policy);legacy['upgrade']['core_followup']['schema']='R25_ASSISTANT_CORE_FOLLOWUP_V1'
            self.assertNotIn(core_acceptance.MANAGED_VMM_INFLIGHT_SQL,business.acceptance_queries(legacy))
            with patch.object(business,'validate_policy',side_effect=lambda x:x),patch.object(business.subprocess,'run') as command:
                for sql in ('SELECT * FROM creations;',core_acceptance.MANAGED_VMM_INFLIGHT_SQL+'DELETE FROM creations;'):
                    with self.assertRaisesRegex(Exception,'readonly_sql_contract'):business.readonly_query(policy,sql,7)
                command.assert_not_called()

if __name__=='__main__':unittest.main()
