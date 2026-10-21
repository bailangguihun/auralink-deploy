"""Installed Knowledge V2 -> Assistant-capable V2 controller successor.

No bootstrap downgrade and no independent production launcher. This helper is
called through the reviewed controller installation procedure under the same
R25/c5ea lock. Approval is an existing private owner/delegated-review record;
tests do not create project approval. Fixed targets exclude DB/data/credentials.
"""
from __future__ import annotations
from dataclasses import asdict
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import zipfile

# Formal source bundle must not execute stale sibling bytecode.
if __name__ == "__main__":
    folder=Path(__file__).absolute().parent
    if any(folder.glob("*.pyc")) or any(folder.glob("*.pyo")) or (folder/"__pycache__").exists():
        raise SystemExit("ASSISTANT_CONTROLLER_REFUSED: bundle_bytecode")
    sys.dont_write_bytecode=True

import assistant_runtime_overlay as overlay
import core_business
import core_release_guard as guard
from core_runtime import LIVE_FILES, env_values, replace_env
from upgrade_core_controller import ControlUpgradeRuntime, upgrade_lease
from publisher_transaction import Refused

SCHEMA='R25_ASSISTANT_CONTROLLER_UPGRADE_V1'
SUCCESSOR_SCHEMA='R25_ASSISTANT_CONTROLLER_SUCCESSOR_V1'
FOLLOWUP_SCHEMA='R25_ASSISTANT_CORE_FOLLOWUP_BINDING_V1'
MANAGED_SCHEMA='R25_MANAGED_VMM_CORE_FOLLOWUP_BINDING_V1'
FOLLOWUP_SCHEMAS=(FOLLOWUP_SCHEMA,MANAGED_SCHEMA)
FOLLOWUP_CHANGED_FILES=frozenset(('core_business.py','core_release_guard.py','upgrade_assistant_controller.py','core_runtime.py','core_acceptance.py'))
FILES=('assistant_release.py','assistant_runtime.py','assistant_runtime_overlay.py','assistant_acceptance.py','assistant_backup.py','upgrade_assistant_controller.py',
       'core_business.py','core_runtime.py','core_release_guard.py','core_publish.py','publisher_transaction.py','r25_storage.py',
       'ai_release_candidate.py','ai_release_guard.py','ai_runtime.py','ai_publish.py')
FOLLOWUP_FILES=FILES+('core_acceptance.py',)
OPERATIONAL=('guard','critic-wrapper','lifecycle','ai-guard','ai-policy','policy','handoff')
PHASES={'PREPARED','INSTALL_INTENT','INSTALLED','RESTORE_INTENT','RESTORED','RECOVERY_REQUIRED'}

# This is a controller-only repair for the observed failed AI restoration, not
# an alternate application launcher or a general permission to edit policy.
REPAIR_SCHEMA='R25_ASSISTANT_LAUNCHER_IMPORT_REPAIR_V1'
REPAIR_TX='a540f3f722f13acaaebceb0c785af083'
REPAIR_FILES=('assistant_release.py','policy','handoff')
REPAIR_PHASES={'PREPARED','INSTALL_INTENT','INSTALLED','RESTORE_INTENT','RESTORED','RECOVERY_REQUIRED'}
REPAIR_OLD=b'from publisher_transaction import Refused\n'
REPAIR_NEW=b'''\nclass Refused(RuntimeError):
    """Standalone refusal for source-bound launcher validation.

    The Java launcher loads this helper by verified bytes without the private
    publisher directory on sys.path. Do not resolve a sibling transaction
    module or trust an ambient module cache merely to report a refusal.
    """

    pass
'''
REPAIR_BUNDLE_FILES=frozenset(FILES)|{'ai-baseline.json','control_overlay.py','core_acceptance.py',
    'core-release-candidate.py','core_asset_sandbox.py','upgrade_core_controller.py',
    'install_ai_controller.py','install_core_controller.py','verify-r25-artifact.py'}


def must(ok,why):
    if not ok: raise Refused(why)


def sha(raw): return hashlib.sha256(raw).hexdigest()


def validate(plan):
    successor = type(plan) is dict and plan.get('schema') == SUCCESSOR_SCHEMA
    followup = type(plan) is dict and plan.get('schema') in FOLLOWUP_SCHEMAS
    bound = successor or followup
    fields={'schema','from_policy_schema','deploy','current_release','next_owner','controller_owner','private_owner','files','operational','review_evidence_sha256'}
    must(type(plan)is dict and set(plan)==fields | ({'current_ai_release','preserved','services'} if bound else set()) |
         ({'installed_control','frontend'} if followup else set()),'assistant_upgrade_plan_fields')
    must(plan['schema'] in (SCHEMA,SUCCESSOR_SCHEMA,*FOLLOWUP_SCHEMAS) and plan['from_policy_schema']=='R25_CORE_BUSINESS_POLICY_V2','assistant_upgrade_requires_installed_v2')
    must(set(plan['files'])==set(FOLLOWUP_FILES if followup else FILES) and set(plan['operational'])==set(OPERATIONAL),'assistant_upgrade_exact_inventory')
    must(plan['next_owner']=='c5ea','assistant_next_owner')
    identity=plan['deploy']
    must(set(identity)==({'repository','ref','commit','run_id','run_attempt'} | ({'archive_sha256','manifest_sha256','tool_sha256'} if bound else set())) and identity['repository']=='bailangguihun/auralink-deploy' and identity['ref']=='refs/heads/release','assistant_upgrade_formal_repository')
    must(re.fullmatch('[a-f0-9]{40}',identity['commit']) and all(re.fullmatch('[1-9][0-9]{0,19}',identity[x]) for x in ('run_id','run_attempt')),'assistant_upgrade_formal_identity')
    must(re.fullmatch('[a-f0-9]{64}',plan['review_evidence_sha256']),'assistant_upgrade_review_evidence')
    for name,entry in {**plan['files'],**plan['operational']}.items():
        must(set(entry)=={'before_sha256','after_sha256'},'assistant_upgrade_file_fields')
        for value in entry.values():
            must(value is None or (type(value)is str and re.fullmatch('[a-f0-9]{64}',value)),'assistant_upgrade_hash')
        must(entry['after_sha256']is not None,'assistant_upgrade_after_required')
        if name in OPERATIONAL:must(entry['before_sha256']is not None,'assistant_upgrade_installed_controls_required')
    for field in ('controller_owner','private_owner'):
        must(type(plan[field])is list and len(plan[field])==2 and all(type(x)is int and x>=0 for x in plan[field]),'assistant_upgrade_owner')
    if bound:
        for key in ('archive_sha256','manifest_sha256','tool_sha256'):guard.digest_value(identity[key])
        import ai_release_guard as ai_guard
        ai_guard.reference(plan['current_ai_release'])
        must(set(plan['preserved']) == {'release-jar','preview-jar','manifest','application'}, 'successor_preserved_inventory')
        for value in plan['preserved'].values():guard.digest_value(value)
        must(plan['preserved']['release-jar'] == plan['preserved']['preview-jar'] == plan['current_release']['artifact_sha256'], 'successor_preserved_jar')
        must(set(plan['services']) == {'java','guide','critic'}, 'successor_service_inventory')
        for identity in plan['services'].values():
            must(type(identity) is dict and set(identity)=={'pid','ticks'} and type(identity['pid']) is int and identity['pid']>1 and str(identity['ticks']).isdigit(), 'successor_service_identity')
        must(all(x['before_sha256'] is not None for x in entries(plan).values()), 'successor_requires_installed_files')
        for name in ('critic-wrapper','lifecycle','ai-policy'):
            must(plan['operational'][name]['before_sha256']==plan['operational'][name]['after_sha256'], 'successor_existing_overlay_preserved')
    if followup:
        must(type(plan['installed_control']) is dict and set(plan['installed_control'])=={'approval_sha256','journal_sha256'}, 'core_followup_installed_control')
        for item in plan['installed_control'].values():guard.digest_value(item)
        must(type(plan['frontend']) is dict and set(plan['frontend'])=={'selection_sha256','reference_sha256'}, 'core_followup_frontend_binding')
        for item in plan['frontend'].values():guard.digest_value(item)
        for name in set(FOLLOWUP_FILES)-FOLLOWUP_CHANGED_FILES:
            must(plan['files'][name]['before_sha256']==plan['files'][name]['after_sha256'], 'core_followup_unrelated_controller_change')
        must(plan['operational']['ai-guard']['before_sha256']==plan['operational']['ai-guard']['after_sha256'], 'core_followup_ai_guard_change')
    return plan


def targets(plan):
    root=guard.ROOT
    result={name:(root/'operations/r25-publisher'/name,tuple(plan['private_owner']),0o600) for name in (FOLLOWUP_FILES if plan['schema'] in FOLLOWUP_SCHEMAS else FILES)}
    result.update(policy=(guard.STORE/'POLICY.json',tuple(plan['private_owner']),0o600),
        handoff=(LIVE_FILES['handoff'],tuple(plan['private_owner']),0o600),
        guard=(root/'operations/core_release_guard.py',tuple(plan['controller_owner']),0o700),
        **{'critic-wrapper':(root/'operations/r25-private-critic-env-policy-v2.py',tuple(plan['controller_owner']),0o700),
           'lifecycle':(root/'operations/lifecycle_adapter.py',tuple(plan['controller_owner']),0o700),
           'ai-policy':(root/'runtime/ai-publications/POLICY.json',tuple(plan['private_owner']),0o600),
           'ai-guard':(root/'operations/ai_release_guard.py',tuple(plan['controller_owner']),0o700)})
    return result


def entries(plan):return {**plan['files'],**plan['operational']}


def source(bundle,name):
    return bundle/({'policy':'POLICY.next.json','handoff':'HANDOFF.next.env','guard':'core_release_guard.py','critic-wrapper':'r25-private-critic-env-policy-v2.py','lifecycle':'lifecycle_adapter.py','ai-policy':'AI_POLICY.next.json','ai-guard':'ai_release_guard.py'}.get(name,name))


def verify(rt,plan,bundle,*,mixed=False,after=False):
    validate(plan);rt.require_lease();rt.require_terminal_application_transactions()
    if plan['schema']==MANAGED_SCHEMA:
        must('R25_VMM_CONTROLLER' not in env_values(guard.read_file(LIVE_FILES['handoff'],owner=tuple(plan['private_owner']))), 'managed_vmm_overlay_requires_explicit_rollback')
    must(asdict(rt.current())==plan['current_release'],'assistant_upgrade_current_drift')
    if plan['schema'] in (SUCCESSOR_SCHEMA,*FOLLOWUP_SCHEMAS):
        current_ai = rt.read(guard.ROOT/'runtime/ai-publications/CURRENT.json')
        must(current_ai.get('release') == plan['current_ai_release'], 'successor_ai_current_drift')
        for name,digest in plan['preserved'].items():rt.identity(LIVE_FILES[name],expected_sha256=digest)
        verify_launcher_repair_bundle(rt,plan,bundle)
    if plan['schema'] in FOLLOWUP_SCHEMAS:
        # A stopped backup can retain a nonterminal journal even without a
        # held flock. The controller binder must not become its recovery path.
        import assistant_backup
        assistant_backup.require_terminal(rt)
        installed=plan['installed_control']
        prior=rt.read(guard.STORE/'controller-upgrades'/installed['approval_sha256']/'journal.json',installed['journal_sha256'])
        must(prior.get('phase')=='INSTALLED' and prior.get('approval_sha256')==installed['approval_sha256'], 'core_followup_prior_control_incomplete')
        prior_plan=rt.read(guard.STORE/'controller-upgrades'/installed['approval_sha256']/'approval.json',installed['approval_sha256'])
        must(prior_plan.get('schema')==(FOLLOWUP_SCHEMA if plan['schema']==MANAGED_SCHEMA else SUCCESSOR_SCHEMA) and
             prior_plan.get('operational',{}).get('policy',{}).get('after_sha256')==plan['operational']['policy']['before_sha256'],
             'core_followup_prior_control_policy')
        active=rt.read(guard.STORE/'CONTROL_UPGRADE_ACTIVE.json')
        active_id=guard.digest_value(active.get('approval_sha256'))
        must(active.get('schema')=='R25_CORE_CONTROLLER_UPGRADE_ACTIVE_V1','core_followup_active_control_schema')
        if active_id!=installed['approval_sha256']:
            must(rt.read(guard.STORE/'controller-upgrades'/active_id/'approval.json',active_id)==plan,'core_followup_active_control_drift')
        for name,suffix in (('selection_sha256','json'),('reference_sha256','reference')):
            rt.identity(guard.ROOT/'runtime/config'/('frontend-selection.'+suffix),expected_sha256=plan['frontend'][name])
        new=rt.read(bundle/'POLICY.next.json',plan['operational']['policy']['after_sha256'])
        prior_path=guard.STORE/'core-followup-baselines'/(plan['operational']['policy']['before_sha256']+'.json')
        old=rt.read(prior_path if prior_path.exists() else guard.STORE/'POLICY.json',plan['operational']['policy']['before_sha256'])
        guard.verify_core_followup_context(new,old,core_business)
    must(bundle==guard.ROOT/'operations/r25-controller-candidates'/plan['deploy']['commit'],'assistant_upgrade_fixed_bundle')
    for name,entry in entries(plan).items():
        rt.identity(source(bundle,name),expected_sha256=entry['after_sha256'])
        target,owner,mode=targets(plan)[name]
        if not target.exists():
            must(entry['before_sha256']is None and not after and not target.is_symlink(),'assistant_upgrade_missing_old_file');continue
        observed=rt.identity(target,expected_owner=owner,expected_mode=mode)['sha256']
        allowed={entry['after_sha256']} if after else ({entry['before_sha256'],entry['after_sha256']} if mixed else {entry['before_sha256']})
        must(observed in allowed,'assistant_upgrade_foreign_drift')


def rendered_bindings(rt,plan,bundle):
    old=rt.read(guard.STORE/'POLICY.json',plan['operational']['policy']['before_sha256'])
    new=rt.read(bundle/'POLICY.next.json',plan['operational']['policy']['after_sha256'])
    must(old['schema']=='R25_CORE_BUSINESS_POLICY_V2' and new['schema']==old['schema'] and new['upgrade']['capability']=='assistant-v1','assistant_upgrade_policy_transition')
    must(new['upgrade']['baseline_release']==plan['current_release'] and old['installation']==new['installation'],'assistant_upgrade_identity_preserved')
    must(old['upgrade']['assets']==new['upgrade']['assets'] and old['upgrade']['configuration']['knowledge']==new['upgrade']['configuration']['knowledge'],'assistant_upgrade_knowledge_preserved')
    must(new['installation']['controller_owner']==plan['controller_owner'] and new['installation']['private_owner']==plan['private_owner'],'assistant_upgrade_owner_binding')
    for name in new['runtime_modules']:
        expected=plan['files'][name]['after_sha256'] if name in plan['files'] else old['runtime_modules'][name]
        must(new['runtime_modules'][name]==expected,'assistant_upgrade_runtime_bindings')
    must(new['controllers']['core_release_guard.py']==plan['files']['core_release_guard.py']['after_sha256'] and
         new['controllers']['lifecycle_adapter.py']==plan['operational']['lifecycle']['after_sha256'] and
         new['controllers']['r25-production-java-launch.py']==old['controllers']['r25-production-java-launch.py'],'assistant_upgrade_controller_bindings')
    followup = plan['schema'] in FOLLOWUP_SCHEMAS
    successor = plan['schema'] in (SUCCESSOR_SCHEMA,*FOLLOWUP_SCHEMAS)
    if successor:
        (validate_core_followup_bindings if followup else validate_successor_bindings)(rt,plan,old,new,bundle)
    wrapper=guard.read_file(targets(plan)['critic-wrapper'][0],owner=tuple(plan['controller_owner']),mode=0o700,
                             expected=plan['operational']['critic-wrapper']['before_sha256'])
    after_wrapper=wrapper if successor else overlay.render(wrapper)
    must(sha(after_wrapper)==plan['operational']['critic-wrapper']['after_sha256'],'assistant_upgrade_wrapper_render')
    lifecycle=guard.read_file(targets(plan)['lifecycle'][0],owner=tuple(plan['controller_owner']),mode=0o700,
                             expected=plan['operational']['lifecycle']['before_sha256'])
    if not successor:must(lifecycle.count(overlay.BASE_SHA256.encode())==1,'assistant_upgrade_lifecycle_wrapper_anchor')
    after_lifecycle=lifecycle if successor else lifecycle.replace(overlay.BASE_SHA256.encode(),sha(after_wrapper).encode())
    must(sha(after_lifecycle)==plan['operational']['lifecycle']['after_sha256'],'assistant_upgrade_lifecycle_render')
    import ai_release_guard as ai_guard
    old_ai=rt.read(guard.ROOT/'runtime/ai-publications/POLICY.json',plan['operational']['ai-policy']['before_sha256'])
    new_ai=rt.read(bundle/'AI_POLICY.next.json',plan['operational']['ai-policy']['after_sha256'])
    if successor:
        must(old_ai == new_ai and old_ai.get('schema')=='R25_AI_SOURCE_POLICY_V2' and old_ai.get('source_paths')==list(ai_guard.ASSISTANT_PATHS), 'successor_installed_ai_v2')
    else:
        must(old_ai.get('schema')=='R25_AI_SOURCE_POLICY_V1' and old_ai.get('source_paths')==list(ai_guard.SOURCE_PATHS),'assistant_ai_installed_v1_required')
    must(new_ai.get('schema')=='R25_AI_SOURCE_POLICY_V2' and new_ai.get('repository')=='bailangguihun/auralink-ai-service' and new_ai.get('source_paths')==list(ai_guard.ASSISTANT_PATHS),'assistant_ai_successor_inventory')
    must((successor or new_ai.get('legacy_policy_sha256')==plan['operational']['ai-policy']['before_sha256']) and new_ai.get('dependency_sha256')==old_ai.get('dependency_sha256') and (successor or new_ai.get('review_evidence_sha256')==plan['review_evidence_sha256']),'assistant_ai_policy_binding')
    owner=new_ai.get('source_owner');must(type(owner)is list and len(owner)==2 and all(type(x)is int and x>=0 for x in owner),'assistant_ai_source_owner')
    for name in (ai_guard.ASSISTANT_PATHS if successor else ai_guard.SOURCE_PATHS):
        rt.identity(guard.ROOT/'release'/name,expected_owner=tuple(owner),expected_mode=0o600)
    before=guard.read_file(LIVE_FILES['handoff'],owner=tuple(plan['private_owner']),expected=plan['operational']['handoff']['before_sha256'])
    after=replace_env(before,{'R25_CORE_RELEASE_POLICY_SHA256':plan['operational']['policy']['after_sha256'],
        'R25_CORE_RELEASE_GUARD_SHA256':plan['files']['core_release_guard.py']['after_sha256'],
        'R25_LIFECYCLE_ADAPTER_SHA256':plan['operational']['lifecycle']['after_sha256'],
        'R25_R24_CRITIC_ENV_WRAPPER_SHA256':plan['operational']['critic-wrapper']['after_sha256'],
        'R25_AI_RELEASE_GUARD_SHA256':plan['operational']['ai-guard']['after_sha256'],
        'R25_AI_RELEASE_POLICY_SHA256':plan['operational']['ai-policy']['after_sha256']})
    must(sha(after)==plan['operational']['handoff']['after_sha256'],'assistant_upgrade_handoff_render')
    # Execute only review-bound validator bytes from this formal controller
    # bundle; do not import an unverified candidate module or cached .pyc.
    validator_path=bundle/'core_business.py'
    validator_raw=guard.read_file(validator_path,owner=tuple(plan['private_owner']),expected=plan['files']['core_business.py']['after_sha256'])
    validator=guard.source_module(validator_path,'assistant_upgrade_policy_validator',validator_raw)
    validator.validate_policy(new)
    return new



def validate_successor_bindings(rt,plan,old,new,bundle):
    """Keep installed identities and behavior except exact reviewed release bindings."""
    must(old.get('upgrade',{}).get('capability') == 'assistant-v1' and 'successor' not in old['upgrade'],
         'successor_installed_assistant_v2_required')
    must(set(new) == set(old) and all(new[k]==old[k] for k in old if k not in {'controllers','runtime_modules','upgrade'}),
         'successor_policy_unrelated_change')
    before,after = old['upgrade'],new['upgrade']
    mutable = {'candidate','baseline_release','web_commit','configuration','assistant','compatibility','evidence','successor'}
    must(set(after)==set(before)|{'successor'} and all(after[k]==before[k] for k in before if k not in mutable),
         'successor_upgrade_unrelated_change')
    must(after['configuration'] == {**before['configuration'],
        'before_sha256':before['configuration']['after_sha256'], 'after_sha256':before['configuration']['after_sha256']},
         'successor_bridge_config_preserved')
    must(after['configuration']['after_sha256'] == plan['preserved']['application'], 'successor_actual_bridge_config')
    a,b = before['assistant'],after['assistant']
    changes={'artifacts','configuration_sha256','baseline_policy_sha256','baseline_payload_fingerprint',
             'compatible_recovery_jar_sha256','compatibility_evidence_sha256','review_evidence_sha256'}
    must(set(a)==set(b) and all(a[k]==b[k] for k in a if k not in changes), 'successor_assistant_policy_preserved')
    must(b['baseline_policy_sha256']==plan['operational']['policy']['before_sha256'] and
         b['baseline_payload_fingerprint']==before['candidate']['payload_fingerprint'] and
         b['compatible_recovery_jar_sha256']==plan['current_release']['artifact_sha256'] and
         b['review_evidence_sha256']==plan['review_evidence_sha256'], 'successor_accepted_baseline_binding')
    must(b['artifacts']['web']==a['artifacts']['web'], 'successor_preserve_accepted_web_artifact')
    transition = core_business.validate_successor(new)
    must(transition['ai_before']==plan['current_ai_release'], 'successor_ai_baseline_binding')
    must(b['artifacts']['ai']['commit']==transition['ai_candidate']['commit_sha'] and
         b['artifacts']['ai']['sha256']==transition['ai_candidate']['archive_sha256'] and
         b['artifacts']['deploy']=={'repository':plan['deploy']['repository'],'commit':plan['deploy']['commit'],
                                   'sha256':plan['deploy']['archive_sha256']}, 'successor_artifact_bindings')
    raw=guard.read_file(LIVE_FILES['application'],owner=tuple(plan['private_owner']),expected=plan['preserved']['application'])
    core_business.render_successor_configuration(raw,new)
    ai_before=guard.read_file(guard.ROOT/'runtime/config/assistant-private/assistant-runtime.json',
                             owner=tuple(plan['private_owner']),expected=transition['ai_configuration']['before_sha256'])
    ai_after=guard.read_file(bundle/'ASSISTANT_CONFIG.next.json',owner=tuple(plan['private_owner']),expected=transition['ai_configuration']['after_sha256'])
    core_business.validate_successor_ai_configuration(ai_before,ai_after,new)


def validate_core_followup_bindings(rt,plan,old,new,bundle):
    """A new functional Core artifact, with unchanged accepted AI/G1 and data."""
    if plan['schema']==MANAGED_SCHEMA:return validate_managed_vmm_bindings(rt,plan,old,new,bundle)
    must(old.get('upgrade',{}).get('capability')=='assistant-v1' and 'successor' in old['upgrade'] and
         'core_followup' not in old['upgrade'], 'core_followup_requires_completed_successor')
    must(set(new)==set(old) and all(new[k]==old[k] for k in old if k not in {'controllers','runtime_modules','upgrade'}),
         'core_followup_policy_unrelated_change')
    before,after=old['upgrade'],new['upgrade'];transition=core_business.validate_successor(old)
    mutable={'candidate','baseline_release','web_commit','configuration','assistant','compatibility','evidence','successor'}
    must(set(after)==(set(before)-{'successor'})|{'core_followup'} and
         all(after[k]==before[k] for k in before if k not in mutable), 'core_followup_upgrade_unrelated_change')
    expected=copy.deepcopy(before['configuration'])
    expected.update(before_sha256=transition['core_final_sha256'],after_sha256=transition['core_final_sha256'])
    expected['assistant']['execution.expected-config-sha256']=transition['ai_configuration']['after_sha256']
    must(after['configuration']==expected and expected['after_sha256']==plan['preserved']['application'],
         'core_followup_current_configuration_unchanged')
    a,b=before['assistant'],after['assistant']
    mutable={'artifacts','configuration_sha256','baseline_policy_sha256','baseline_payload_fingerprint',
             'compatible_recovery_jar_sha256','compatibility_evidence_sha256','review_evidence_sha256'}
    must(set(a)==set(b) and all(a[k]==b[k] for k in a if k not in mutable), 'core_followup_assistant_policy_change')
    must(set(b['artifacts'])==set(a['artifacts']) and all(b['artifacts'][k]==a['artifacts'][k] for k in a['artifacts'] if k not in {'core','deploy'}),
         'core_followup_other_application_artifact_change')
    must(b['artifacts']['core']=={'repository':after['candidate']['repository'],'commit':after['candidate']['commit_sha'],
                                 'sha256':after['candidate']['artifact_sha256']} and
         b['artifacts']['deploy']=={'repository':plan['deploy']['repository'],'commit':plan['deploy']['commit'],
                                   'sha256':plan['deploy']['archive_sha256']}, 'core_followup_formal_artifact_binding')
    must(b['baseline_policy_sha256']==plan['operational']['policy']['before_sha256'] and
         b['baseline_payload_fingerprint']==before['candidate']['payload_fingerprint'] and
         b['compatible_recovery_jar_sha256']==plan['current_release']['artifact_sha256'] and
         b['configuration_sha256']==plan['preserved']['application'] and
         b['review_evidence_sha256']==plan['review_evidence_sha256'], 'core_followup_accepted_baseline_binding')
    binding=core_business.validate_core_followup(new)
    must(binding['ai_release']==plan['current_ai_release'] and
         binding['ai_policy_sha256']==plan['operational']['ai-policy']['before_sha256'], 'core_followup_current_ai_binding')
    guard.verify_core_followup_context(new,old,core_business)
    raw=guard.read_file(LIVE_FILES['application'],owner=tuple(plan['private_owner']),expected=plan['preserved']['application'])
    core_business.validate_existing_assistant_configuration(raw,new)



def validate_managed_vmm_bindings(rt,plan,old,new,bundle):
    previous=core_business.validate_core_followup(old)
    must(previous['schema']=='R25_ASSISTANT_CORE_FOLLOWUP_V1','managed_vmm_requires_gallery_followup')
    must(set(new)==set(old) and all(new[k]==old[k] for k in old if k not in {'controllers','runtime_modules','upgrade'}),'managed_vmm_policy_unrelated_change')
    before,after=old['upgrade'],new['upgrade']
    mutable={'candidate','baseline_release','web_commit','configuration','assistant','compatibility','evidence','core_followup'}
    must(set(after)==set(before) and all(after[k]==before[k] for k in before if k not in mutable),'managed_vmm_upgrade_unrelated_change')
    a,b=before['configuration'],after['configuration']
    must(set(b)==set(a)|{'vmm'} and all(b[k]==a[k] for k in a if k not in {'before_sha256','after_sha256'}) and
         b['before_sha256']==a['after_sha256']==plan['preserved']['application'],'managed_vmm_configuration_baseline')
    raw=guard.read_file(LIVE_FILES['application'],owner=tuple(plan['private_owner']),expected=plan['preserved']['application'])
    core_business.render_managed_vmm_configuration(raw,new)
    a,b=before['assistant'],after['assistant']
    mutable={'artifacts','configuration_sha256','baseline_policy_sha256','baseline_payload_fingerprint',
             'compatible_recovery_jar_sha256','compatibility_evidence_sha256','review_evidence_sha256'}
    must(set(a)==set(b) and all(a[k]==b[k] for k in a if k not in mutable),'managed_vmm_assistant_unrelated_change')
    must(set(b['artifacts'])==set(a['artifacts']) and all(b['artifacts'][k]==a['artifacts'][k] for k in a['artifacts'] if k not in {'core','deploy'}),'managed_vmm_other_application_artifact_change')
    must(b['artifacts']['core']=={'repository':after['candidate']['repository'],'commit':after['candidate']['commit_sha'],'sha256':after['candidate']['artifact_sha256']} and
         b['artifacts']['deploy']=={'repository':plan['deploy']['repository'],'commit':plan['deploy']['commit'],'sha256':plan['deploy']['archive_sha256']},'managed_vmm_formal_artifact_binding')
    must(b['baseline_policy_sha256']==plan['operational']['policy']['before_sha256'] and
         b['baseline_payload_fingerprint']==before['candidate']['payload_fingerprint'] and
         b['compatible_recovery_jar_sha256']==plan['current_release']['artifact_sha256'] and
         b['configuration_sha256']==after['configuration']['after_sha256'] and
         b['review_evidence_sha256']==plan['review_evidence_sha256'],'managed_vmm_accepted_baseline_binding')
    binding=core_business.validate_core_followup(new)
    must(binding['schema']=='R25_MANAGED_VMM_CORE_FOLLOWUP_V1' and binding['ai_release']==plan['current_ai_release'] and
         binding['ai_policy_sha256']==plan['operational']['ai-policy']['before_sha256'],'managed_vmm_current_ai_binding')
    guard.verify_core_followup_context(new,old,core_business)
    process=plan['services']['java']
    core_business.verify_process(new,process['pid'],int(process['ticks']),LIVE_FILES['application'],require_isolated=False)
    managed_vmm_inventory(rt,new)


def managed_vmm_inventory(rt,policy,*,require_clear=True):
    # Only this exact SQL literal from the source-bound official bundle can run;
    # use the established native-PG account/credential/sealed-psql read adapter.
    import core_acceptance
    rt.require_lease()
    raw=core_business.readonly_query(policy,core_acceptance.MANAGED_VMM_INFLIGHT_SQL,rt.fd)
    value=core_business.strict_json(raw)
    try:core_acceptance.verify_managed_vmm_inventory(value,require_clear=require_clear)
    except Refused as error:
        if str(error)=='managed_vmm_work_requires_readonly_reconciliation':error.managed_vmm_counts=value
        raise
    return value

def prepare_core_followup(rt,request,request_sha256,bundle,*,managed=False):
    """Materialize an unsigned, exact plan; never write an approval or policy.

    The private request supplies independently obtained formal identities and
    retained review/compatibility evidence. Runtime observations are read under
    the existing lease. Result is a private reviewable plan, not authorization.
    """
    must(type(request) is dict and set(request)=={'schema','deploy','core','web_commit','evidence',
        'compatibility','compatibility_evidence_sha256','review_evidence_sha256'}|({'managed_vmm'} if managed else set()), 'core_followup_request_fields')
    must(request['schema']==('R25_MANAGED_VMM_CORE_FOLLOWUP_REQUEST_V1' if managed else 'R25_ASSISTANT_CORE_FOLLOWUP_REQUEST_V1'),'core_followup_request_schema')
    must(type(request['core']) is dict and set(request['core'])=={'commit','run_id','run_attempt'}, 'core_followup_request_core_identity')
    deployment=request['deploy']
    must(type(deployment) is dict and set(deployment)=={'repository','ref','commit','run_id','run_attempt','archive_sha256','manifest_sha256','tool_sha256'} and
         deployment['repository']=='bailangguihun/auralink-deploy' and deployment['ref']=='refs/heads/release','core_followup_request_deploy')
    for key in ('commit','archive_sha256','manifest_sha256','tool_sha256'):guard.digest_value(deployment[key],40 if key=='commit' else 64)
    for key in ('run_id','run_attempt'):must(type(deployment[key]) is str and re.fullmatch('[1-9][0-9]{0,19}',deployment[key]),'core_followup_request_run')
    with upgrade_lease(rt):
        rt.require_terminal_application_transactions()
        import assistant_backup
        assistant_backup.require_terminal(rt)
        old=guard.policy();oldsha=rt.identity(guard.STORE/'POLICY.json')['sha256']
        if managed:
            must('R25_VMM_CONTROLLER' not in env_values(guard.read_file(LIVE_FILES['handoff'],owner=tuple(old['installation']['private_owner']))),'managed_vmm_overlay_requires_explicit_rollback')
            previous=core_business.validate_core_followup(old)
            must(previous['schema']=='R25_ASSISTANT_CORE_FOLLOWUP_V1','managed_vmm_requires_gallery_followup')
            guard.verify_core_followup_context(old,validator=core_business)
        else:
            must(old.get('upgrade',{}).get('capability')=='assistant-v1' and 'successor' in old['upgrade'] and
                 'core_followup' not in old['upgrade'], 'core_followup_requires_completed_successor')
            must(guard.successor_configuration_phase(old)=='final','core_followup_successor_incomplete')
        current=asdict(rt.current());rt.verify_current(rt.current());rt.require_no_active_work()
        prior_active=rt.read(guard.STORE/'CONTROL_UPGRADE_ACTIVE.json')
        prior_id=guard.digest_value(prior_active.get('approval_sha256'))
        prior_journal=guard.STORE/'controller-upgrades'/prior_id/'journal.json'
        must(rt.read(prior_journal).get('phase')=='INSTALLED','core_followup_prior_control_incomplete')
        review=rt.read(guard.STORE/'CONTROL_UPGRADE_REVIEW.json',request['review_evidence_sha256'])
        must(type(review) is dict,'core_followup_review_record')
        plan={'schema':MANAGED_SCHEMA if managed else FOLLOWUP_SCHEMA,'from_policy_schema':old['schema'],'deploy':request['deploy'],
            'current_release':current,'current_ai_release':rt.read(guard.ROOT/'runtime/ai-publications/CURRENT.json')['release'],
            'installed_control':{'approval_sha256':prior_id,'journal_sha256':rt.identity(prior_journal)['sha256']},
            'frontend':{'selection_sha256':rt.identity(guard.ROOT/'runtime/config/frontend-selection.json')['sha256'],
                        'reference_sha256':rt.identity(guard.ROOT/'runtime/config/frontend-selection.reference')['sha256']},
            'preserved':{k:rt.identity(LIVE_FILES[k])['sha256'] for k in ('release-jar','preview-jar','manifest','application')},
            'services':service_identities(rt.engine()),'next_owner':'c5ea','controller_owner':old['installation']['controller_owner'],
            'private_owner':old['installation']['private_owner'],'files':{},'operational':{},
            'review_evidence_sha256':request['review_evidence_sha256']}
        must(bundle==guard.ROOT/'operations/r25-controller-candidates'/request['deploy']['commit'],'core_followup_fixed_bundle')
        for name in FOLLOWUP_FILES:
            target,owner,mode=targets(plan)[name]
            plan['files'][name]={'before_sha256':rt.identity(target,expected_owner=owner,expected_mode=mode)['sha256'],
                                'after_sha256':rt.identity(bundle/name,expected_owner=tuple(plan['private_owner']),expected_mode=0o600)['sha256']}
        verify_launcher_repair_bundle(rt,plan,bundle)
        # Verify official Core package before accepting its fields as input.
        from core_publish import incoming_path
        identity=request['core'];incoming=incoming_path(identity['commit'],identity['run_id'],identity['run_attempt'])
        rt.storage.validate_target(incoming,exists=True,directory=True,expected_owner=(0,0),expected_mode=0o700)
        for name in ('auralink-backend-0.0.1-SNAPSHOT.jar','core-release-candidate.json','r25-artifact-verification.json'):
            rt.identity(incoming/name,expected_owner=(0,0),expected_mode=0o600)
        verifier_path=bundle/'core-release-candidate.py'
        verifier=guard.source_module(verifier_path,'core_followup_formal_candidate',guard.read_file(verifier_path,
            expected=old['runtime_modules']['core-release-candidate.py'],owner=tuple(plan['private_owner']),mode=0o600))
        jar=incoming/'auralink-backend-0.0.1-SNAPSHOT.jar'
        candidate=verifier.verify(incoming/'core-release-candidate.json',jar,incoming/'r25-artifact-verification.json',
            identity['commit'],identity['run_id'],identity['run_attempt'],expected_repository=old['repository'])
        next_policy=copy.deepcopy(old);up=next_policy['upgrade'];transition=up.pop('core_followup' if managed else 'successor')
        up['candidate']={'repository':candidate['repository'],'commit_sha':identity['commit'],'github_run_id':identity['run_id'],
            'github_run_attempt':identity['run_attempt'],'artifact_sha256':candidate['artifact']['sha256'],
            'artifact_bytes':candidate['artifact']['bytes'],'payload_fingerprint':guard.payload_fingerprint(jar)}
        up['baseline_release']=current;up['web_commit']=request['web_commit']
        up['evidence']=request['evidence'];up['compatibility']=request['compatibility']
        ai_store=guard.ROOT/'runtime/ai-publications';active=rt.read(ai_store/'ACTIVE.json');tx=active['transaction_id']
        guard.digest_value(tx,32)
        txroot=ai_store/'transactions'/tx
        up['core_followup']={'schema':'R25_MANAGED_VMM_CORE_FOLLOWUP_V1' if managed else 'R25_ASSISTANT_CORE_FOLLOWUP_V1','acceptance':'managed-vmm-closed-v1' if managed else 'gallery-daily-v1',
            'predecessor_policy_sha256':oldsha,'ai_release':plan['current_ai_release'],'ai_transaction_id':tx,
            **{'ai_'+key+'_sha256':rt.identity(path)['sha256'] for key,path in (
                ('policy',ai_store/'POLICY.json'),('active',ai_store/'ACTIVE.json'),('current',ai_store/'CURRENT.json'),
                ('journal',txroot/'journal.json'),('snapshot',txroot/'snapshot.json'),
                ('configuration',guard.ROOT/'runtime/config/assistant-private/assistant-runtime.json'))}}
        config=up['configuration'];config['before_sha256']=config['after_sha256']=plan['preserved']['application']
        config['assistant']['execution.expected-config-sha256']=up['core_followup']['ai_configuration_sha256']
        if managed:
            config['vmm']=request['managed_vmm']
            core_business.validate_managed_vmm_configuration(config['vmm'])
            before=guard.read_file(LIVE_FILES['application'],owner=tuple(plan['private_owner']),expected=plan['preserved']['application'])
            config['after_sha256']=sha(core_business.render_managed_vmm_configuration(before,next_policy,verify_after=False))
        a=up['assistant'];a.update(configuration_sha256=config['after_sha256'],baseline_policy_sha256=oldsha,
            baseline_payload_fingerprint=old['upgrade']['candidate']['payload_fingerprint'],
            compatible_recovery_jar_sha256=current['artifact_sha256'],compatibility_evidence_sha256=request['compatibility_evidence_sha256'],
            review_evidence_sha256=request['review_evidence_sha256'])
        a['artifacts']['core']={'repository':candidate['repository'],'commit':identity['commit'],'sha256':candidate['artifact']['sha256']}
        a['artifacts']['deploy']={k:request['deploy'][k] for k in ('repository','commit')}
        a['artifacts']['deploy']['sha256']=request['deploy']['archive_sha256']
        for name in next_policy['runtime_modules']:
            if name in plan['files']:next_policy['runtime_modules'][name]=plan['files'][name]['after_sha256']
        next_policy['controllers']['core_release_guard.py']=plan['files']['core_release_guard.py']['after_sha256']
        def materialize(path,raw):
            if path.exists():return rt.identity(path,expected_sha256=sha(raw))['sha256']
            return rt.storage.atomic_write_bytes(path,raw)['sha256']
        for name in OPERATIONAL:
            target,owner,mode=targets(plan)[name]
            before=rt.identity(target,expected_owner=owner,expected_mode=mode)['sha256']
            if name=='policy':raw=guard.encoded(next_policy)
            elif name=='handoff':continue
            elif name in ('guard','ai-guard'):raw=(bundle/('core_release_guard.py' if name=='guard' else 'ai_release_guard.py')).read_bytes()
            else:raw=guard.read_file(target,owner=owner,mode=mode,expected=before)
            plan['operational'][name]={'before_sha256':before,'after_sha256':materialize(source(bundle,name),raw)}
        before=guard.read_file(LIVE_FILES['handoff'],owner=tuple(plan['private_owner']),mode=0o600)
        after=replace_env(before,{'R25_CORE_RELEASE_POLICY_SHA256':plan['operational']['policy']['after_sha256'],
            'R25_CORE_RELEASE_GUARD_SHA256':plan['operational']['guard']['after_sha256']})
        plan['operational']['handoff']={'before_sha256':sha(before),'after_sha256':materialize(bundle/'HANDOFF.next.env',after)}
        validate(plan);verify(rt,plan,bundle);rendered_bindings(rt,plan,bundle)
        # Existing approved paths are never created/overwritten here.
        folder=guard.STORE/'core-followup-plans'/guard.digest_value(request_sha256)
        rt.storage.mkdir_private(folder.parent);rt.storage.mkdir_private(folder)
        digest=materialize(folder/'UNSIGNED_PLAN.json',guard.encoded(plan))
        return {'outcome':'CORE_FOLLOWUP_UNSIGNED_PLAN_PREPARED','plan_sha256':digest,'path':str(folder/'UNSIGNED_PLAN.json'),
                'approved':False,'application_restart':False,'database_writes':False}


def service_identities(engine):
    result={}
    for name,port in (('java',19080),('guide',19084),('critic',19085)):
        value=engine.active(name,port);must(value is not None,'assistant_upgrade_service_missing')
        result[name]={'pid':value['pid'],'ticks':value['ticks']}
    # Existing c5ea read-only status, never a second Next lifecycle owner.
    return result


def approval(rt,plan,expected):
    path=guard.STORE/'controller-upgrades'/expected
    record=path/'approval.json' if (path/'approval.json').exists() else guard.STORE/'CONTROL_UPGRADE_APPROVAL.json'
    must(rt.read(record,expected)==plan,'assistant_upgrade_private_approval_binding')
    if plan['schema'] in (SUCCESSOR_SCHEMA,*FOLLOWUP_SCHEMAS):
        review = path/'review.json' if (path/'review.json').exists() else guard.STORE/'CONTROL_UPGRADE_REVIEW.json'
        must(type(rt.read(review,plan['review_evidence_sha256'])) is dict, 'successor_private_review_binding')
    return path


def install(rt,plan,bundle,*,approval_sha256):
    with upgrade_lease(rt):
        folder=approval(rt,plan,approval_sha256);journal_path=folder/'journal.json'
        if journal_path.exists():
            old=rt.read(journal_path)
            if old['phase']=='INSTALLED':verify(rt,plan,bundle,after=True);return 'INSTALLED_VERIFIED_NOOP'
            must(old['phase']=='RESTORED','assistant_upgrade_recovery_required')
        active=guard.STORE/'CONTROL_UPGRADE_ACTIVE.json'
        if active.exists():
            pointer=rt.read(active);previous=pointer.get('approval_sha256')
            must(type(previous)is str and re.fullmatch('[a-f0-9]{64}',previous),'assistant_upgrade_active_pointer')
            prior=rt.read(guard.STORE/'controller-upgrades'/previous/'journal.json')
            must(prior.get('phase')in ('INSTALLED','RESTORED'),'assistant_previous_controller_recovery_required')
        verify(rt,plan,bundle);rendered_bindings(rt,plan,bundle)
        rt.verify_current(rt.current());rt.require_no_active_work()
        engine=rt.engine();engine.frontend('status',rt.fd);before=service_identities(engine)
        if plan['schema'] in (SUCCESSOR_SCHEMA,*FOLLOWUP_SCHEMAS):must(before == plan['services'], 'successor_process_context_drift')
        rt.storage.mkdir_private(guard.STORE/'controller-upgrades');rt.storage.mkdir_private(folder);rt.storage.mkdir_private(folder/'before')
        if not (folder/'approval.json').exists():
            rt.storage.atomic_copy_file(guard.STORE/'CONTROL_UPGRADE_APPROVAL.json',folder/'approval.json',approval_sha256)
        if plan['schema'] in (SUCCESSOR_SCHEMA,*FOLLOWUP_SCHEMAS) and not (folder/'review.json').exists():
            rt.storage.atomic_copy_file(guard.STORE/'CONTROL_UPGRADE_REVIEW.json',folder/'review.json',plan['review_evidence_sha256'])
        for name,entry in entries(plan).items():
            if entry['before_sha256']is not None:
                path,owner,mode=targets(plan)[name]
                saved=folder/'before'/name
                if plan['schema'] in FOLLOWUP_SCHEMAS and saved.exists():rt.identity(saved,expected_sha256=entry['before_sha256'])
                else:rt.storage.atomic_copy_file(path,saved,entry['before_sha256'],source_owner=owner,source_mode=mode)
        if plan['schema'] in FOLLOWUP_SCHEMAS:
            root=guard.STORE/'core-followup-baselines';rt.storage.mkdir_private(root)
            digest=plan['operational']['policy']['before_sha256'];retained=root/(digest+'.json')
            if retained.exists():rt.identity(retained,expected_sha256=digest)
            else:rt.storage.atomic_copy_file(folder/'before/policy',retained,digest)
        record={'schema':plan['schema'] if plan['schema'] in FOLLOWUP_SCHEMAS else SCHEMA,
                'approval_sha256':approval_sha256,'phase':'PREPARED','service_identities':before}
        rt.write(journal_path,record)
        rt.write(guard.STORE/'CONTROL_UPGRADE_ACTIVE.json',{'schema':'R25_CORE_CONTROLLER_UPGRADE_ACTIVE_V1','approval_sha256':approval_sha256})
        engine.watchdog_stop_locked();record['phase']='INSTALL_INTENT';rt.write(journal_path,record)
        try:
            # Policy/handoff last; mixed state cannot launch applications.
            for name,entry in entries(plan).items():
                path,owner,mode=targets(plan)[name]
                rt.storage.atomic_copy_file(source(bundle,name),path,entry['after_sha256'],replace=entry['before_sha256']is not None,
                    expected_existing_sha256=entry['before_sha256'],target_owner=owner,mode=mode,existing_owner=owner,existing_mode=mode)
            verify(rt,plan,bundle,after=True)
            if plan['schema'] in (SUCCESSOR_SCHEMA,*FOLLOWUP_SCHEMAS):
                # Do not fork watchdog with pre-upgrade guard modules cached.
                engine=rt.engine();rt.verify_current(rt.current())
            must(service_identities(engine)==before,'assistant_upgrade_unexpected_service_restart')
            engine.frontend('status',rt.fd)
            engine.watchdog_start_locked();must(engine.watchdog_identity(),'assistant_upgrade_watchdog')
            record['phase']='INSTALLED';rt.write(journal_path,record);return 'INSTALLED'
        except BaseException:
            record['phase']='RECOVERY_REQUIRED';rt.write(journal_path,record);raise


def recover(rt,plan,bundle,*,approval_sha256):
    with upgrade_lease(rt):
        folder=approval(rt,plan,approval_sha256)
        if plan['schema'] in FOLLOWUP_SCHEMAS and not (folder/'journal.json').exists():return 'CORE_FOLLOWUP_NOT_STARTED'
        record=rt.read(folder/'journal.json')
        must(record['approval_sha256']==approval_sha256 and record['phase']in PHASES,'assistant_upgrade_recovery_phase')
        if record['phase']=='INSTALLED':verify(rt,plan,bundle,after=True);return 'INSTALLED_NO_RECOVERY'
        verify(rt,plan,bundle,mixed=True)
        # Mixed controllers cannot pass normal engine.load(). Use the exact
        # recorded lifecycle before-image solely for owned stop/recovery.
        path=folder/'before/lifecycle'
        raw=guard.read_file(path,owner=tuple(plan['private_owner']),expected=plan['operational']['lifecycle']['before_sha256'])
        import types
        module=types.ModuleType('assistant_controller_recovery_lifecycle');module.__file__=str(path)
        exec(compile(raw,str(path),'exec',dont_inherit=True),module.__dict__)
        os.environ['R25_ROOT']=str(guard.ROOT)
        os.environ['ARTLIVE_R25_HANDOFF_CONFIG']=str(LIVE_FILES['handoff'])
        engine=module.production(rt.fd)
        engine.v=env_values(guard.read_file(folder/'before/handoff',owner=tuple(plan['private_owner']),expected=plan['operational']['handoff']['before_sha256']))
        engine.kind=engine.v.get('R25_LIFECYCLE_RELEASE_KIND')
        if plan['schema'] in (SUCCESSOR_SCHEMA,*FOLLOWUP_SCHEMAS):
            # Full load is unsafe while controller files are mixed. Critic
            # ownership still needs this existing read-only policy loader.
            engine.load_execution_policy()
        engine.watchdog_stop_locked()
        record['phase']='RESTORE_INTENT';rt.write(folder/'journal.json',record)
        for name,entry in entries(plan).items():
            path,owner,mode=targets(plan)[name]
            if not path.exists():continue
            actual=rt.identity(path,expected_owner=owner,expected_mode=mode)['sha256']
            if entry['before_sha256']is None:
                rt.storage.remove_bound_file(path,entry['after_sha256'],expected_owner=owner)
            else:
                rt.storage.atomic_copy_file(folder/'before'/name,path,entry['before_sha256'],replace=True,expected_existing_sha256=actual,
                    target_owner=owner,mode=mode,existing_owner=owner,existing_mode=mode)
        must(service_identities(engine)==record['service_identities'],'assistant_upgrade_service_drift')
        engine=rt.engine();rt.verify_current(rt.current())
        engine.watchdog_start_locked();must(engine.watchdog_identity(),'assistant_upgrade_watchdog')
        record['phase']='RESTORED';rt.write(folder/'journal.json',record);return 'RESTORED'


def validate_launcher_repair(plan):
    must(type(plan)is dict and set(plan)=={'schema','deploy','installed_control','current_release','preserved','ai',
        'services','next_owner','controller_owner','private_owner','files','review_evidence_sha256'},'launcher_repair_plan_fields')
    must(plan['schema']==REPAIR_SCHEMA and plan['next_owner']=='c5ea','launcher_repair_schema')
    deployment=plan['deploy']
    must(type(deployment)is dict and set(deployment)=={'repository','ref','commit','run_id','run_attempt',
        'archive_sha256','manifest_sha256','tool_sha256'},'launcher_repair_deploy_fields')
    must(deployment['repository']=='bailangguihun/auralink-deploy' and deployment['ref']=='refs/heads/release','launcher_repair_formal_source')
    guard.digest_value(deployment['commit'],40)
    for key in ('run_id','run_attempt'):
        must(type(deployment[key])is str and re.fullmatch('[1-9][0-9]{0,19}',deployment[key]),'launcher_repair_formal_run')
    for key in ('archive_sha256','manifest_sha256','tool_sha256'):guard.digest_value(deployment[key])
    guard.digest_value(plan['review_evidence_sha256'])
    must(type(plan['installed_control'])is dict and set(plan['installed_control'])=={'approval_sha256','journal_sha256'},'launcher_repair_installed_binding')
    for value in plan['installed_control'].values():guard.digest_value(value)
    must(type(plan['current_release'])is dict and set(plan['current_release'])=={'commit_sha','artifact_sha256','accepted_record_sha256'},'launcher_repair_core_binding')
    for key,value in plan['current_release'].items():guard.digest_value(value,40 if key=='commit_sha' else 64)
    must(type(plan['preserved'])is dict and set(plan['preserved'])=={'release-jar','preview-jar','manifest','application'},'launcher_repair_preserved_inventory')
    for value in plan['preserved'].values():guard.digest_value(value)
    must(plan['preserved']['release-jar']==plan['preserved']['preview-jar']==plan['current_release']['artifact_sha256'],'launcher_repair_preserved_jar_binding')
    ai=plan['ai']
    must(type(ai)is dict and set(ai)=={'transaction_id','active_sha256','journal_sha256','snapshot_sha256','current_sha256','source_tree_sha256'} and ai['transaction_id']==REPAIR_TX,'launcher_repair_exact_ai_transaction')
    for key,value in ai.items():
        if key!='transaction_id':guard.digest_value(value)
    for key in ('controller_owner','private_owner'):
        must(type(plan[key])is list and len(plan[key])==2 and all(type(x)is int and x>=0 for x in plan[key]),'launcher_repair_owner')
    must(type(plan['services'])is dict and set(plan['services'])=={'guide','critic'},'launcher_repair_service_inventory')
    for identity in plan['services'].values():
        must(type(identity)is dict and set(identity)=={'pid','ticks'} and type(identity['pid'])is int and identity['pid']>1 and
            type(identity['ticks'])is str and re.fullmatch('[1-9][0-9]*',identity['ticks']),'launcher_repair_process_identity')
    must(type(plan['files'])is dict and set(plan['files'])==set(REPAIR_FILES),'launcher_repair_three_files_only')
    for entry in plan['files'].values():
        must(type(entry)is dict and set(entry)=={'before_sha256','after_sha256'},'launcher_repair_file_fields')
        for value in entry.values():guard.digest_value(value)
        must(entry['before_sha256']!=entry['after_sha256'],'launcher_repair_changed_files_required')
    return plan


def launcher_repair_targets(plan):
    owner=tuple(plan['private_owner'])
    return {'assistant_release.py':(guard.ROOT/'operations/r25-publisher/assistant_release.py',owner,0o600),
        'policy':(guard.STORE/'POLICY.json',owner,0o600),'handoff':(LIVE_FILES['handoff'],owner,0o600)}


def launcher_repair_folder(approval_sha256):
    return guard.STORE/'launcher-import-repairs'/guard.digest_value(approval_sha256)


def verify_launcher_repair_bundle(rt,plan,bundle):
    must(bundle==guard.ROOT/'operations/r25-controller-candidates'/plan['deploy']['commit'],'launcher_repair_fixed_bundle')
    identity=plan['deploy']
    manifest=rt.read(bundle/'r25-controller-manifest.json',identity['manifest_sha256'])
    must(set(manifest)=={'schema','repository','ref','commit','run_id','run_attempt','status','installation_performed','archive','files'} and
        manifest.get('schema')==1 and all(manifest.get(k)==identity[k] for k in ('repository','ref','commit','run_id','run_attempt')) and
        manifest.get('status')=='NOT_DEPLOYED' and manifest.get('installation_performed')is False,'launcher_repair_formal_manifest')
    archive=guard.read_file(bundle/'r25-controller.zip',owner=tuple(plan['private_owner']),expected=identity['archive_sha256'],limit=26*1024*1024)
    must(manifest.get('archive')=={'name':'r25-controller.zip','bytes':len(archive),'sha256':identity['archive_sha256']},'launcher_repair_archive_binding')
    items=manifest.get('files');must(type(items)is list and len(items)==len(REPAIR_BUNDLE_FILES),'launcher_repair_bundle_inventory')
    by_name={x.get('path'):x for x in items if type(x)is dict}
    must(set(by_name)==REPAIR_BUNDLE_FILES and len(by_name)==len(items),'launcher_repair_bundle_inventory')
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        must(zipped.namelist()==sorted(REPAIR_BUNDLE_FILES),'launcher_repair_archive_inventory')
        payload={}
        for name,item in by_name.items():
            must(set(item)=={'path','bytes','sha256'} and type(item['bytes'])is int and 0<item['bytes']<=1024*1024,'launcher_repair_bundle_entry')
            guard.digest_value(item['sha256'])
            entry=zipped.getinfo(name)
            must(not entry.is_dir() and entry.file_size==item['bytes'] and entry.compress_type==zipfile.ZIP_STORED,'launcher_repair_archive_file')
            raw=zipped.read(name);must(sha(raw)==item['sha256'],'launcher_repair_archive_member')
            payload[name]=raw
            rt.identity(bundle/name,expected_sha256=item['sha256'],expected_bytes=item['bytes'],expected_owner=tuple(plan['private_owner']),expected_mode=0o600)
            guard.reject_legacy_bytecode(bundle/name)
    # Same canonical archive contract as scripts/package-r25-release.py:
    # no alternate timestamps, permissions, headers, comments or trailing bytes.
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_STORED) as zipped:
        for name in sorted(REPAIR_BUNDLE_FILES):
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.create_system=3
            info.external_attr=(stat.S_IFREG|0o600)<<16;zipped.writestr(info,payload[name])
    must(archive==output.getvalue(),'launcher_repair_archive_not_canonical')
    must(by_name['upgrade_assistant_controller.py']['sha256']==identity['tool_sha256'] and
        by_name['assistant_release.py']['sha256']==plan['files']['assistant_release.py']['after_sha256'],'launcher_repair_tool_binding')
    return manifest


def launcher_repair_bindings(rt,plan,bundle,folder):
    """The saved before-image is authoritative after any partial copy."""
    raw={}
    for name,(target,owner,mode) in launcher_repair_targets(plan).items():
        before=folder/'before'/name
        path=before if before.exists() else target
        raw[name]=guard.read_file(path,owner=owner,mode=mode,expected=plan['files'][name]['before_sha256'])
        rt.identity(source(bundle,name),expected_sha256=plan['files'][name]['after_sha256'],expected_owner=owner,expected_mode=mode)
    code=raw['assistant_release.py']
    must(code.count(REPAIR_OLD)==1 and sha(code.replace(REPAIR_OLD,REPAIR_NEW))==plan['files']['assistant_release.py']['after_sha256'],
        'launcher_repair_exact_import_patch')
    old=guard.strict_json(raw['policy']);new=rt.read(bundle/'POLICY.next.json',plan['files']['policy']['after_sha256'])
    must(old.get('schema')=='R25_CORE_BUSINESS_POLICY_V2' and old.get('upgrade',{}).get('capability')=='assistant-v1' and
        old['installation']['controller_owner']==plan['controller_owner'] and old['installation']['private_owner']==plan['private_owner'] and
        old['upgrade']['baseline_release']==plan['current_release'] and
        old['upgrade']['assistant']['compatible_recovery_jar_sha256']==plan['current_release']['artifact_sha256'],'launcher_repair_installed_policy')
    expected=copy.deepcopy(old)
    must(expected['runtime_modules']['assistant_release.py']==plan['files']['assistant_release.py']['before_sha256'],'launcher_repair_old_runtime_binding')
    expected['runtime_modules']['assistant_release.py']=plan['files']['assistant_release.py']['after_sha256']
    expected['upgrade']['assistant']['artifacts']['deploy']={k:plan['deploy'][k] for k in ('repository','commit')}
    expected['upgrade']['assistant']['artifacts']['deploy']['sha256']=plan['deploy']['archive_sha256']
    expected['upgrade']['assistant']['review_evidence_sha256']=plan['review_evidence_sha256']
    must(new==expected,'launcher_repair_policy_other_changes')
    must(plan['preserved']['application']==old['upgrade']['configuration']['before_sha256'],'launcher_repair_preserved_application_binding')
    before_env=env_values(raw['handoff'])
    must(before_env.get('R25_CORE_RELEASE_POLICY_SHA256')==plan['files']['policy']['before_sha256'],'launcher_repair_old_handoff_binding')
    after=replace_env(raw['handoff'],{'R25_CORE_RELEASE_POLICY_SHA256':plan['files']['policy']['after_sha256']})
    must(sha(after)==plan['files']['handoff']['after_sha256'],'launcher_repair_handoff_other_changes')
    return old,raw


def launcher_repair_frontend_bindings(rt,plan,engine,module):
    """Initialize only load()'s immutable frontend bindings, never backend load.

    The ordinary load() also validates the live backend policy and cannot run
    between this correction's compare-and-swap writes. These are its existing
    fixed selection/source/lock checks, with the same source-bound selector.
    """
    rt.require_lease()
    paths={'R25_FRONTEND_SELECTOR':guard.ROOT/'operations/frontend_lifecycle_selector.py',
        'R25_FRONTEND_SELECTION_REFERENCE':guard.ROOT/'runtime/config/frontend-selection.reference',
        'R25_FRONTEND_SELECTION':guard.ROOT/'runtime/config/frontend-selection.json'}
    must(all(engine.v.get(key)==str(path) for key,path in paths.items()),'launcher_repair_frontend_fixed_bindings')
    must(engine.deployment_root/'runtime/frontend-deployment.lock'==guard.LOCK,'launcher_repair_frontend_lock_path')
    selector=paths['R25_FRONTEND_SELECTOR'];reference=paths['R25_FRONTEND_SELECTION_REFERENCE']
    must(engine.v.get('R25_FRONTEND_SELECTOR_SHA256')==module.SELECTOR,'launcher_repair_selector_binding')
    guard.reject_legacy_bytecode(selector)
    source=guard.read_file(selector,owner=tuple(plan['controller_owner']),mode=0o700,expected=module.SELECTOR)
    values={}
    for line in guard.read_file(reference,owner=tuple(plan['private_owner'])).decode().splitlines():
        if not line or line.startswith('#'):continue
        must('='in line,'launcher_repair_selection_reference_syntax');key,value=line.split('=',1)
        must(key not in values and re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',key),'launcher_repair_selection_reference_syntax')
        values[key]=value
    must(values.get('schema')=='ARTLIVE_FRONTEND_SELECTION_REFERENCE_V1','launcher_repair_selection_reference')
    selection_sha=guard.digest_value(values.get('selection_sha256'))
    selected=guard.strict_json(guard.read_file(paths['R25_FRONTEND_SELECTION'],owner=tuple(plan['private_owner']),expected=selection_sha))
    must(selected.get('deployment_root')==str(engine.deployment_root) and selected.get('shared_lock')==str(guard.LOCK),
        'launcher_repair_selection_lock_binding')
    rt.identity(guard.LOCK,expected_owner=tuple(plan['private_owner']),expected_mode=0o600)
    held=os.fstat(rt.fd);actual=guard.LOCK.stat()
    must((held.st_dev,held.st_ino)==(actual.st_dev,actual.st_ino),'launcher_repair_selection_lease_inode')
    engine.selection_sha=selection_sha;engine.lock=guard.LOCK
    engine.selector_module=guard.source_module(selector,'assistant_launcher_repair_bound_selector',source)


def launcher_repair_engine(rt,plan,old,raw):
    """Read process ownership via unchanged, bound lifecycle during mixed policy.

    No normal engine.load(), stop/start, fallback source, or global sys.path
    alteration. All three targets may be mixed only under this repair lease.
    """
    for name,digest in old['controllers'].items():
        rt.identity(guard.ROOT/'operations'/name,expected_sha256=digest,expected_owner=tuple(plan['controller_owner']),expected_mode=0o700)
    for name,digest in old['runtime_modules'].items():
        if name=='assistant_release.py':continue
        path=guard.ROOT/'operations/r25-publisher'/name
        guard.reject_legacy_bytecode(path)
        rt.identity(path,expected_sha256=digest,expected_owner=tuple(plan['private_owner']),expected_mode=0o600)
    path=guard.ROOT/'operations/lifecycle_adapter.py'
    value=guard.read_file(path,owner=tuple(plan['controller_owner']),mode=0o700,expected=old['controllers']['lifecycle_adapter.py'])
    module=guard.source_module(path,'assistant_launcher_repair_bound_lifecycle',value)
    os.environ['R25_ROOT']=str(guard.ROOT)
    os.environ['ARTLIVE_R25_HANDOFF_CONFIG']=str(LIVE_FILES['handoff'])
    engine=module.production(rt.fd)
    engine.v=env_values(raw['handoff']);engine.kind=engine.v.get('R25_LIFECYCLE_RELEASE_KIND')
    engine.load_execution_policy()
    launcher_repair_frontend_bindings(rt,plan,engine,module)
    return engine


def launcher_repair_context(rt,plan,old,raw):
    rt.require_lease()
    must(asdict(rt.current())==plan['current_release'],'launcher_repair_core_current_drift')
    for name,digest in plan['preserved'].items():
        rt.identity(LIVE_FILES[name],expected_sha256=digest,expected_owner=tuple(plan['private_owner']),expected_mode=0o600)
    manifest=rt.read(LIVE_FILES['manifest'],plan['preserved']['manifest'])
    backends=[x for x in manifest.get('entries',[]) if type(x)is dict and x.get('path')=='artifacts/backend.jar']
    must(len(backends)==1 and backends[0].get('sha256')==plan['current_release']['artifact_sha256'],'launcher_repair_preserved_manifest_binding')
    must(env_values(raw['handoff']).get('R25_BACKEND_SHA256')==plan['current_release']['artifact_sha256'],'launcher_repair_preserved_handoff_backend')
    control=plan['installed_control'];control_folder=guard.STORE/'controller-upgrades'/control['approval_sha256']
    original=rt.read(control_folder/'approval.json',control['approval_sha256'])
    active=rt.read(guard.STORE/'CONTROL_UPGRADE_ACTIVE.json')
    journal=rt.read(control_folder/'journal.json',control['journal_sha256'])
    must(original.get('schema')==SCHEMA and original.get('current_release')==plan['current_release'] and
        original.get('operational',{}).get('policy',{}).get('after_sha256')==plan['files']['policy']['before_sha256'] and
        original.get('operational',{}).get('handoff',{}).get('after_sha256')==plan['files']['handoff']['before_sha256'] and
        original.get('files',{}).get('assistant_release.py',{}).get('after_sha256')==plan['files']['assistant_release.py']['before_sha256'] and
        active=={'schema':'R25_CORE_CONTROLLER_UPGRADE_ACTIVE_V1','approval_sha256':control['approval_sha256']} and
        journal.get('schema')==SCHEMA and journal.get('approval_sha256')==control['approval_sha256'] and journal.get('phase')=='INSTALLED','launcher_repair_installed_control_drift')
    # Only this exact failed AI transaction is exempted; all other work stays terminal.
    core_active=guard.STORE/'ACTIVE.json'
    if core_active.exists():
        marker=rt.read(core_active);tx=marker.get('transaction_id')
        must(type(tx)is str and re.fullmatch('[a-f0-9]{32}',tx),'launcher_repair_core_active')
        item=rt.read(guard.STORE/'transactions'/tx/'journal.json')
        must(item.get('schema')=='R25_CORE_TRANSACTION_V1' and item.get('transaction_id')==tx and item.get('phase')in {'COMMITTED','RESTORED'},'launcher_repair_other_core_transaction')
    backup=guard.ROOT/'runtime/assistant-backups/assistant-daily-v1/ACTIVE.json'
    if backup.exists():
        marker=rt.read(backup);day=marker.get('day')
        must(type(day)is str and re.fullmatch('20[0-9]{2}-[0-9]{2}-[0-9]{2}',day),'launcher_repair_backup_day')
        item=rt.read(backup.parent/'runs'/(day+'.json'))
        must(item.get('schema')=='R25_ASSISTANT_SCHEDULED_BACKUP_V1' and item.get('day')==day and
            item.get('phase')in {'COMMITTED','FAILED_RESUMED','RECOVERED_WITHOUT_REPLAY','PURGED'},'launcher_repair_other_backup_transaction')
    import ai_runtime
    ai=ai_runtime.AIRuntime();ai.core=rt
    ai_store=guard.ROOT/'runtime/ai-publications';bound=plan['ai']
    marker=rt.read(ai_store/'ACTIVE.json',bound['active_sha256'])
    rt.identity(ai_store/'CURRENT.json',expected_sha256=bound['current_sha256'])
    rt.identity(ai_store/'transactions'/REPAIR_TX/'journal.json',expected_sha256=bound['journal_sha256'])
    must(marker=={'schema':'R25_AI_ACTIVE_V1','transaction_id':REPAIR_TX},'launcher_repair_ai_active')
    journal,snapshot=ai.validate_recovery(REPAIR_TX)
    must(journal['phase']=='RECOVERY_REQUIRED' and journal['snapshot_sha256']==bound['snapshot_sha256'] and
        snapshot['core']==plan['current_release'] and ai.current()==snapshot['current'] and
        snapshot['current']['source_tree_sha256']==bound['source_tree_sha256'] and snapshot['changed_roles']==['critic'] and
        [x['phase'] for x in journal['history']][-2:]==['RESTORE_START_INTENT','RECOVERY_REQUIRED'],
        'launcher_repair_exact_failed_restore_required')
    ai_runtime.guard.verify_live(snapshot['current'])
    engine=launcher_repair_engine(rt,plan,old,raw)
    must(engine.active('java',19080)is None and engine.watchdog_identity()is None,'launcher_repair_quiescence_drift')
    for role,port in (('guide',19084),('critic',19085)):
        value=engine.active(role,port)
        must(value is not None and {'pid':value['pid'],'ticks':str(value['ticks'])}==plan['services'][role],'launcher_repair_service_drift')
    must(plan['services']['guide']=={'pid':snapshot['before_health']['guide_pid'],'ticks':str(snapshot['before_health']['guide_start_ticks'])},'launcher_repair_original_guide_required')
    engine.frontend('status',rt.fd)


def verify_launcher_repair_targets(rt,plan,*,mixed=False,after=False):
    for name,(path,owner,mode) in launcher_repair_targets(plan).items():
        guard.reject_legacy_bytecode(path)
        actual=rt.identity(path,expected_owner=owner,expected_mode=mode)['sha256']
        entry=plan['files'][name]
        allowed={entry['after_sha256']} if after else ({entry['before_sha256'],entry['after_sha256']} if mixed else {entry['before_sha256']})
        must(actual in allowed,'launcher_repair_foreign_target_drift')


def launcher_repair_approval(rt,plan,approval_sha256,folder):
    source_record=folder/'approval.json' if (folder/'approval.json').exists() else guard.STORE/'LAUNCHER_IMPORT_REPAIR_APPROVAL.json'
    must(rt.read(source_record,approval_sha256)==plan,'launcher_repair_private_approval_binding')
    review=folder/'review.json' if (folder/'review.json').exists() else guard.STORE/'LAUNCHER_IMPORT_REPAIR_REVIEW.json'
    must(type(rt.read(review,plan['review_evidence_sha256']))is dict,'launcher_repair_review_record')
    pointer=guard.STORE/'LAUNCHER_IMPORT_REPAIR_ACTIVE.json'
    if pointer.exists():must(rt.read(pointer)=={'schema':REPAIR_SCHEMA,'approval_sha256':approval_sha256},'launcher_repair_other_active_plan')


def repair_launcher_import(rt,plan,bundle,*,approval_sha256,recovery=False):
    validate_launcher_repair(plan);folder=launcher_repair_folder(approval_sha256);journal_path=folder/'journal.json'
    with upgrade_lease(rt):
        launcher_repair_approval(rt,plan,approval_sha256,folder)
        verify_launcher_repair_bundle(rt,plan,bundle)
        record=rt.read(journal_path) if journal_path.exists() else None
        if recovery:must(record is not None,'launcher_repair_not_started')
        if record is not None:
            must(record.get('schema')==REPAIR_SCHEMA and record.get('approval_sha256')==approval_sha256 and record.get('phase')in REPAIR_PHASES,'launcher_repair_journal')
            must(all((folder/'before'/name).exists() for name in REPAIR_FILES),'launcher_repair_preimages_required')
        old,raw=launcher_repair_bindings(rt,plan,bundle,folder)
        launcher_repair_context(rt,plan,old,raw)
        if record is not None and record['phase']=='INSTALLED':
            verify_launcher_repair_targets(rt,plan,after=True);return 'LAUNCHER_IMPORT_REPAIR_INSTALLED_NOOP'
        if record is not None and record['phase']=='RESTORED':
            verify_launcher_repair_targets(rt,plan)
            if recovery:return 'LAUNCHER_IMPORT_REPAIR_RESTORED_NOOP'
            # A deliberate repair after exact restoration may reuse the same
            # immutable approval/preimages; never silently resume partial copy.
        if not recovery:must(record is None or record['phase']=='RESTORED','launcher_repair_explicit_recovery_required')
        verify_launcher_repair_targets(rt,plan,mixed=recovery)
        if record is None:
            rt.storage.mkdir_private(folder.parent);rt.storage.mkdir_private(folder);rt.storage.mkdir_private(folder/'before')
            for name in REPAIR_FILES:
                target,owner,mode=launcher_repair_targets(plan)[name]
                saved=folder/'before'/name
                if saved.exists():rt.identity(saved,expected_sha256=plan['files'][name]['before_sha256'],expected_owner=owner,expected_mode=mode)
                else:rt.storage.atomic_copy_file(target,saved,plan['files'][name]['before_sha256'],source_owner=owner,source_mode=mode)
            for incoming,saved,digest in (('LAUNCHER_IMPORT_REPAIR_APPROVAL.json','approval.json',approval_sha256),
                    ('LAUNCHER_IMPORT_REPAIR_REVIEW.json','review.json',plan['review_evidence_sha256'])):
                if (folder/saved).exists():rt.identity(folder/saved,expected_sha256=digest)
                else:rt.storage.atomic_copy_file(guard.STORE/incoming,folder/saved,digest)
            record={'schema':REPAIR_SCHEMA,'approval_sha256':approval_sha256,'phase':'PREPARED','history':['PREPARED']}
            rt.write(journal_path,record)
            rt.write(guard.STORE/'LAUNCHER_IMPORT_REPAIR_ACTIVE.json',{'schema':REPAIR_SCHEMA,'approval_sha256':approval_sha256})
        def phase(value):
            record['phase']=value;record['history'].append(value);rt.write(journal_path,record)
        phase('RESTORE_INTENT' if recovery else 'INSTALL_INTENT')
        try:
            for name in REPAIR_FILES:
                # Revalidate exact old AI/current/process/other transaction state before each CAS.
                launcher_repair_context(rt,plan,old,raw)
                verify_launcher_repair_targets(rt,plan,mixed=True)
                target,owner,mode=launcher_repair_targets(plan)[name]
                entry=plan['files'][name];actual=rt.identity(target,expected_owner=owner,expected_mode=mode)['sha256']
                digest=entry['before_sha256'] if recovery else entry['after_sha256']
                if actual==digest:continue
                src=folder/'before'/name if recovery else source(bundle,name)
                rt.storage.atomic_copy_file(src,target,digest,replace=True,expected_existing_sha256=actual,
                    target_owner=owner,mode=mode,existing_owner=owner,existing_mode=mode)
            verify_launcher_repair_targets(rt,plan,after=not recovery)
            launcher_repair_context(rt,plan,old,raw)
            phase('RESTORED' if recovery else 'INSTALLED')
            return 'LAUNCHER_IMPORT_REPAIR_RESTORED' if recovery else 'LAUNCHER_IMPORT_REPAIR_INSTALLED'
        except BaseException:
            phase('RECOVERY_REQUIRED');raise


def main():
    import argparse
    import json
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('upgrade','recover','preflight-successor','upgrade-successor','recover-successor',
        'prepare-core-followup','preflight-core-followup','bind-core-followup','recover-core-followup',
        'prepare-managed-vmm-core','preflight-managed-vmm-core','bind-managed-vmm-core','recover-managed-vmm-core','inspect-managed-vmm-work','repair-launcher-import','recover-launcher-import'))
    parser.add_argument('--approval-sha256')
    parser.add_argument('--request-sha256')
    args=parser.parse_args()
    try:
        rt=ControlUpgradeRuntime()
        if args.command in ('prepare-core-followup','prepare-managed-vmm-core'):
            must(args.approval_sha256 is None,'core_followup_prepare_not_approval')
            request=rt.read(guard.STORE/('MANAGED_VMM_CORE_REQUEST.json' if args.command=='prepare-managed-vmm-core' else 'CORE_FOLLOWUP_REQUEST.json'),guard.digest_value(args.request_sha256))
            bundle=guard.ROOT/'operations/r25-controller-candidates'/guard.digest_value(request['deploy']['commit'],40)
            must(Path(__file__).absolute()==bundle/'upgrade_assistant_controller.py','core_followup_trusted_bundle_location')
            rt.identity(Path(__file__),expected_sha256=request['deploy']['tool_sha256'])
            print(json.dumps(prepare_core_followup(rt,request,args.request_sha256,bundle,managed=args.command=='prepare-managed-vmm-core'),sort_keys=True));return 0
        must(args.request_sha256 is None,'core_followup_request_only_for_prepare')
        guard.digest_value(args.approval_sha256)
        if args.command in ('repair-launcher-import','recover-launcher-import'):
            folder=launcher_repair_folder(args.approval_sha256)
            recorded=folder/'approval.json'
            approval_file=recorded if recorded.exists() else guard.STORE/'LAUNCHER_IMPORT_REPAIR_APPROVAL.json'
            plan=validate_launcher_repair(rt.read(approval_file,args.approval_sha256))
            bundle=guard.ROOT/'operations/r25-controller-candidates'/plan['deploy']['commit']
            must(Path(__file__).absolute()==bundle/'upgrade_assistant_controller.py','launcher_repair_trusted_bundle_location')
            rt.identity(Path(__file__),expected_sha256=plan['deploy']['tool_sha256'])
            result=repair_launcher_import(rt,plan,bundle,approval_sha256=args.approval_sha256,recovery=args.command=='recover-launcher-import')
            print(json.dumps({'outcome':result,'application_restart':False,'database_writes':False,'ai_transaction_replayed':False}));return 0
        if args.command=='inspect-managed-vmm-work':
            recorded=guard.STORE/'controller-upgrades'/args.approval_sha256/'approval.json'
            plan=validate(rt.read(recorded,args.approval_sha256))
            must(plan['schema']==MANAGED_SCHEMA,'managed_vmm_core_cli_contract')
            bundle=guard.ROOT/'operations/r25-controller-candidates'/plan['deploy']['commit']
            must(Path(__file__).absolute()==bundle/'upgrade_assistant_controller.py','core_followup_trusted_bundle_location')
            verify_launcher_repair_bundle(rt,plan,bundle)
            with upgrade_lease(rt):
                rt.identity(guard.STORE/'POLICY.json',expected_sha256=plan['operational']['policy']['after_sha256'])
                value=guard.policy()
                counts=managed_vmm_inventory(rt,value,require_clear=False)
                # Print anonymous aggregates even when the gate must remain shut.
                print(json.dumps({'outcome':'MANAGED_VMM_WORK_INSPECTED','counts':counts,'generation_requests':0},sort_keys=True))
                import core_acceptance
                core_acceptance.verify_managed_vmm_inventory(counts)
            return 0
        recorded=guard.STORE/'controller-upgrades'/args.approval_sha256/'approval.json'
        approval_file=recorded if args.command in ('recover','recover-successor','recover-core-followup','recover-managed-vmm-core') and recorded.exists() else guard.STORE/'CONTROL_UPGRADE_APPROVAL.json'
        plan=validate(rt.read(approval_file,args.approval_sha256))
        bundle=guard.ROOT/'operations/r25-controller-candidates'/plan['deploy']['commit']
        must(Path(__file__).absolute()==bundle/'upgrade_assistant_controller.py','assistant_upgrade_trusted_bundle_location')
        rt.identity(Path(__file__),expected_sha256=plan['files']['upgrade_assistant_controller.py']['after_sha256'])
        if args.command.endswith('managed-vmm-core'):
            must(plan['schema']==MANAGED_SCHEMA,'managed_vmm_core_cli_contract')
        elif args.command.endswith('core-followup'):
            must(plan['schema']==FOLLOWUP_SCHEMA,'core_followup_cli_contract')
        elif args.command.endswith('successor'):
            must(plan['schema'] == SUCCESSOR_SCHEMA, 'successor_cli_contract')
        else:must(plan['schema'] == SCHEMA, 'legacy_upgrade_cli_contract')
        if args.command in ('preflight-successor','preflight-core-followup','preflight-managed-vmm-core'):
            with upgrade_lease(rt):
                approval(rt,plan,args.approval_sha256);verify(rt,plan,bundle);rendered_bindings(rt,plan,bundle)
                rt.verify_current(rt.current());rt.require_no_active_work()
                must(service_identities(rt.engine())==plan['services'], 'successor_process_context_drift')
            result='CORE_FOLLOWUP_PREFLIGHT_VERIFIED' if plan['schema'] in FOLLOWUP_SCHEMAS else 'SUCCESSOR_PREFLIGHT_VERIFIED'
        else:
            result=(install if args.command in ('upgrade','upgrade-successor','bind-core-followup','bind-managed-vmm-core') else recover)(rt,plan,bundle,approval_sha256=args.approval_sha256)
        print(json.dumps({'outcome':result,'application_restart':False,'database_writes':False}));return 0
    except Exception as error:
        print(json.dumps({'outcome':'ASSISTANT_CONTROLLER_REFUSED_OR_RECOVERY_REQUIRED','failure_class':type(error).__name__,
            'reason':str(error) if re.fullmatch('[a-z0-9_]+',str(error)) else 'private_diagnostic_required',
            **({'managed_vmm_counts':error.managed_vmm_counts} if hasattr(error,'managed_vmm_counts') else {})}),file=sys.stderr);return 65


if __name__=='__main__':raise SystemExit(main())
