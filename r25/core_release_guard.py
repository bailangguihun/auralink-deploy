"""Independent R25 normal-release start guard. No CLI, SQL writes or secrets output.

Version 1 permits repackaging the reviewed production application, with exactly
the same executable payload. A code/dependency/profile change requires a new
reviewed acceptance contract; a self-declared capability never grants access.
The original migration guard remains unchanged and is invoked only after this
independent equivalence proof, with its established 60c5 capability baseline.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import zipfile

ROOT = Path('/root/autodl-tmp/artlive-r25-native-pg16/handoff-production-20260923T062101Z')
PREVIEW = Path('/root/autodl-tmp/artlive/backend-preview/r25-isolated-preview-7694f4069c83c06c')
BASELINE = '60c5b95529e027ec2cd3634cc6fe190fe43b42c48eb402e2d9ba0f1ce7caaecb'
BASELINE_COMMIT = '98af96843760f7a44627a7284cdb9ffccaf02aa2'
LEGACY_GUARD_SHA = 'f746354860fa422abffec67514791f08732aeb660145a6e7a66e7481f4bfe6f3'
STORE = ROOT / 'runtime/core-publications'
REPOSITORY = 'Hang-666-star/auralink-core-api'
POLICY_SCHEMA = 'R25_CORE_EQUIVALENT_PAYLOAD_POLICY_V1'
RECORD_SCHEMA = 'R25_CORE_COMPATIBLE_RELEASE_V1'
BUSINESS_POLICY_SCHEMA = 'R25_CORE_BUSINESS_POLICY_V2'
BUSINESS_RECORD_SCHEMA = 'R25_CORE_BUSINESS_RELEASE_V2'
ASSISTANT_ILLUSTRATION_CORE_SCHEMA = 'R25_ASSISTANT_ILLUSTRATION_CORE_PUBLICATION_V1'
ASSISTANT_ILLUSTRATION_APPROVAL_SCHEMA = 'R25_ASSISTANT_ILLUSTRATION_CORE_PUBLICATION_BINDING_V1'
ASSISTANT_AI_PATHS = tuple(sorted((
    'guide_service/__init__.py', 'guide_service/__main__.py', 'guide_service/app.py',
    'guide_service/config.py', 'guide_service/diagnostics.py', 'guide_service/env_loader.py',
    'guide_service/qwen_client.py', 'guide_service/schema.py', 'guide_service/requirements.txt',
    'critic-service/app/__init__.py', 'critic-service/app/config.py', 'critic-service/app/dimensions.py',
    'critic-service/app/image_utils.py', 'critic-service/app/llm.py', 'critic-service/app/main.py',
    'critic-service/app/orchestrator.py', 'critic-service/app/prompts.py', 'critic-service/app/schemas.py',
    'critic-service/requirements.txt',
) + tuple('critic-service/app/assistant/' + name for name in (
    '__init__.py', 'config.py', 'schemas.py', 'router.py', 'execution.py',
    'providers.py', 'retrieval.py', 'prompts.py',
))))
AI_DEPENDENCY_FILES = ('guide_service/requirements.txt', 'critic-service/requirements.txt')
CORE_FOLLOWUP_PREDECESSOR = {
    'R25_MANAGED_VMM_CORE_SUCCESSOR_V1': 'R25_MANAGED_VMM_CORE_FOLLOWUP_V1',
    'R25_MANAGED_VMM_CORE_PUBLICATION_V1': 'R25_MANAGED_VMM_CORE_SUCCESSOR_V1',
    'R25_MANAGED_VMM_CORE_PUBLICATION_SUCCESSOR_V1': 'R25_MANAGED_VMM_CORE_PUBLICATION_V1',
    'R25_MANAGED_VMM_CORE_PUBLICATION_REPAIR_V1': 'R25_MANAGED_VMM_CORE_PUBLICATION_SUCCESSOR_V1',
    'R25_KNOWLEDGE_CORE_PUBLICATION_V1': 'R25_MANAGED_VMM_CORE_PUBLICATION_REPAIR_V1',
    'R25_STUDIO_CORE_PUBLICATION_V1': 'R25_KNOWLEDGE_CORE_PUBLICATION_V1',
    'R25_REFERENCE_CORE_PUBLICATION_V1': 'R25_STUDIO_CORE_PUBLICATION_V1',
    ASSISTANT_ILLUSTRATION_CORE_SCHEMA: 'R25_REFERENCE_CORE_PUBLICATION_V1',
}
LOCK = Path('/root/autodl-tmp/auralink-web-deploy/runtime/frontend-deployment.lock')
START_PHASES = {'START_INTENT', 'ACCEPTED', 'CURRENT_COMMIT_INTENT', 'COMMITTED',
                'RESTORE_INTENT', 'RESTORED'}


class Refused(RuntimeError):
    pass


def must(ok, reason):
    if not ok:
        raise Refused(reason)


def digest_value(value, size=64):
    must(type(value) is str and re.fullmatch('[0-9a-f]{' + str(size) + '}', value), 'digest_format')
    return value


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            must(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    def invalid(_):
        raise Refused('nonfinite_json')
    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    must(type(result) is dict, 'record_object')
    return result


def read_file(path, *, owner=(0, 0), mode=0o600, limit=1024 * 1024, expected=None):
    """Traverse every parent by descriptor; never follow a last-component link."""
    path = Path(path)
    must(path.is_absolute() and '..' not in path.parts, 'absolute_fixed_path')
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        target = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        try:
            before = os.fstat(target)
            must(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                 (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode)) == (*owner, mode), 'file_identity')
            must(before.st_size <= limit, 'file_size_limit')
            chunks, size = [], 0
            while True:
                chunk = os.read(target, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                must(size <= limit, 'file_size_limit')
            after = os.fstat(target)
            must((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                 (after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'file_changed')
            raw = b''.join(chunks)
            must(len(raw) == before.st_size, 'file_changed')
            if expected is not None:
                must(hashlib.sha256(raw).hexdigest() == digest_value(expected), 'file_digest')
            return raw
        finally:
            os.close(target)
    finally:
        os.close(descriptor)


def private_record(path, expected=None):
    return strict_json(read_file(path, expected=expected))


def payload_fingerprint(path):
    """Hash every executable/resource/dependency entry, independently of ZIP time.

    Only Maven's descriptive pom.properties/pom.xml and top manifest build
    labels may vary; all effective launcher attributes are included. Extra
    executable entries, class changes and library changes change this digest.
    """
    entries = {}
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        must(len(infos) < 50000 and len({x.filename for x in infos}) == len(infos), 'jar_duplicate_or_excess_entries')
        total = 0
        for info in infos:
            name = info.filename
            must(not name.startswith('/') and '\\' not in name and '..' not in name.split('/'), 'jar_entry_path')
            if info.is_dir():
                continue
            must(info.file_size <= 128 * 1024 * 1024, 'jar_entry_size')
            total += info.file_size
            must(total <= 512 * 1024 * 1024, 'jar_expansion_limit')
            raw = archive.read(info)
            if name == 'META-INF/MANIFEST.MF':
                attributes = {}
                unfolded = raw.decode('utf-8').replace('\r\n ', '').replace('\n ', '')
                for line in unfolded.splitlines():
                    if not line:
                        continue
                    must(': ' in line, 'jar_manifest_syntax')
                    key, value = line.split(': ', 1)
                    must(key not in attributes, 'jar_manifest_duplicate')
                    attributes[key] = value
                # These labels have no runtime semantics; all other attributes
                # (including Main-Class, Start-Class and Class-Path) are bound.
                for key in ('Built-By', 'Build-Jdk', 'Build-Jdk-Spec', 'Created-By'):
                    attributes.pop(key, None)
                raw = encoded(attributes)
            elif name in {'META-INF/maven/com.auralink/auralink-backend/pom.properties',
                          'META-INF/maven/com.auralink/auralink-backend/pom.xml'}:
                continue
            entries[name] = hashlib.sha256(raw).hexdigest()
    must('BOOT-INF/classes/application-catalog-postgres-production.yml' in entries and
         'BOOT-INF/classes/com/auralink/Application.class' in entries,
         'jar_expected_application')
    return hashlib.sha256(encoded(entries)).hexdigest()


def handoff_values():
    values = {}
    for line in read_file(ROOT / 'runtime/handoff-control.env').decode().splitlines():
        if not line or line.startswith('#'):
            continue
        must('=' in line, 'handoff_syntax')
        key, value = line.split('=', 1)
        must(key not in values, 'handoff_duplicate')
        values[key] = value
    return values


def is_assistant_rollout(value):
    return value.get('upgrade', {}).get('core_followup', {}).get('schema') == ASSISTANT_ILLUSTRATION_CORE_SCHEMA


def assistant_rollout_policy(values):
    """Read the handoff-bound Core policy without recursively checking live AI."""
    digest = values.get('R25_CORE_RELEASE_POLICY_SHA256')
    if digest is None:
        return None
    value = private_record(STORE / 'POLICY.json', digest_value(digest))
    return value if is_assistant_rollout(value) else None


def policy():
    values = handoff_values()
    result = private_record(STORE / 'POLICY.json', digest_value(values.get('R25_CORE_RELEASE_POLICY_SHA256')))
    if result.get('schema') == BUSINESS_POLICY_SCHEMA:
        business_module(result).validate_policy(result)
        if is_assistant_rollout(result):
            must('R25_VMM_CONTROLLER' in values, 'illustration_vmm_controller_required')
        validate_configuration_context(result)
        if 'R25_VMM_CONTROLLER' in values:
            # Optional reviewed controller-only overlay. Original POLICY bytes
            # and accepted-record hashes remain authoritative and unchanged.
            path = ROOT / 'operations/r25-publisher/vmm_controller.py'
            must(values.get('R25_VMM_CONTROLLER') == str(path), 'vmm_controller_path')
            raw = read_file(path, expected=digest_value(values.get('R25_VMM_CONTROLLER_SHA256')))
            module = source_module(path, 'r25_bound_vmm_controller', raw)
            result = module.apply_policy_overlay(result, values, ROOT)
        return result
    must(result.get('schema') == POLICY_SCHEMA and result.get('baseline_sha256') == BASELINE and
         result.get('baseline_commit') == BASELINE_COMMIT and
         result.get('repository') == REPOSITORY, 'normal_policy_identity')
    digest_value(result.get('payload_fingerprint'))
    read_file(ROOT / 'runtime/config/application.yml',
              expected=digest_value(result.get('application_configuration_sha256')))
    return result


def business_module(value):
    """Load the installed, policy-bound validator from verified source bytes."""
    path = ROOT / 'operations/r25-publisher/core_business.py'
    digest = digest_value(value.get('runtime_modules', {}).get('core_business.py'))
    return source_module(path, 'r25_bound_core_business', read_file(path, expected=digest))


def validate_configuration_context(value):
    upgrade = value['upgrade']
    baseline = upgrade['baseline_release']
    candidate = upgrade['candidate']
    current = private_record(STORE / 'CURRENT.json')
    must(current.get('schema') == 'R25_CORE_CURRENT_V1', 'business_current_schema')
    reference = current.get('release')
    def side(ref):
        if ref == baseline:
            return 'before_sha256'
        must(type(ref) is dict and ref.get('commit_sha') == candidate['commit_sha'] and
             ref.get('artifact_sha256') == candidate['artifact_sha256'], 'business_current_identity')
        digest_value(ref.get('accepted_record_sha256'))
        return 'after_sha256'
    allowed = {side(reference)}
    try:
        active = private_record(STORE / 'ACTIVE.json')
    except FileNotFoundError:
        active = None
    if active is not None:
        tx = active.get('transaction_id')
        must(type(tx) is str and re.fullmatch('[0-9a-f]{32}', tx), 'business_active_identity')
        journal = private_record(STORE / 'transactions' / tx / 'journal.json')
        phase = journal.get('phase')
        must(journal.get('transaction_id') == tx and
             reference in (journal.get('current'), journal.get('candidate')), 'business_active_current')
        # A prior completed V1 transaction may remain ACTIVE during controller
        # installation. It cannot grant the new configuration any authority.
        if phase not in {'COMMITTED', 'RESTORED'}:
            must(journal.get('current') == baseline and side(journal.get('candidate')) == 'after_sha256',
                 'business_transaction_releases')
            if phase in {'PREPARED', 'STOP_INTENT', 'STOPPED'}:
                allowed = {'before_sha256'}
            elif phase in {'INSTALLED', 'START_INTENT', 'ACCEPTED', 'CURRENT_COMMIT_INTENT'}:
                allowed = {'after_sha256'}
            else:
                must(phase in {'INSTALL_INTENT', 'RECOVERY_REQUIRED', 'RESTORE_STOP_INTENT',
                               'RESTORE_STOPPED', 'RESTORE_INTENT'}, 'business_configuration_phase')
                allowed = {'before_sha256', 'after_sha256'}
    hashes = {upgrade['configuration'][name] for name in allowed}
    if is_assistant_rollout(value):
        verify_assistant_rollout_ai(value, for_start=False)
    elif 'core_followup' in upgrade:
        verify_core_followup_context(value)
    if 'successor' in upgrade:
        phase = successor_configuration_phase(value)
        if side(reference) == 'before_sha256':
            must(phase == 'bridge', 'successor_restore_ai_before_core')
        if phase == 'final': hashes = {upgrade['successor']['core_final_sha256']}
        elif phase == 'transition': hashes.add(upgrade['successor']['core_final_sha256'])
    observed = hashlib.sha256(read_file(ROOT / 'runtime/config/application.yml')).hexdigest()
    must(observed in hashes, 'business_configuration_state')


def verify_core_followup_context(value, predecessor=None, validator=None):
    """Freeze a genuinely committed AI/G1 while Core changes or recovers.

    The pre-install caller may supply predecessor bytes already SHA-bound by
    its approved plan. Installed operation always reads the immutable copy at
    a fixed content-addressed path. No old journal, CURRENT or record is edited.
    """
    core_business=validator if validator is not None else business_module(value)
    return _verify_core_followup(value, predecessor, core_business, verify_live_ai=True)


def verify_core_followup_history(value, *, validator=None):
    """Verify retained evidence for an independently authenticated Core policy.

    The returned reference identifies the old committed AI release. Its current
    policy, pointers, installed source and runtime configuration require a
    separate live verification before use.
    """
    core_business=validator if validator is not None else business_module(value)
    return _verify_core_followup(value, None, core_business, verify_live_ai=False)


def _verify_core_followup(value, predecessor, core_business, *, verify_live_ai):
    binding=core_business.validate_core_followup(value)
    prior_sha=binding['predecessor_policy_sha256']
    if predecessor is None:
        predecessor=private_record(STORE/'core-followup-baselines'/(prior_sha+'.json'),prior_sha)
    schema = binding['schema']
    if schema in CORE_FOLLOWUP_PREDECESSOR:
        # One finite successor of the committed managed-Core installation.
        # Keep its original AI snapshot and accepted records immutable; do not
        # turn this into a recursively extensible arbitrary release chain.
        previous=core_business.validate_core_followup(predecessor)
        repair=schema=='R25_MANAGED_VMM_CORE_PUBLICATION_REPAIR_V1'
        must(previous['schema']==CORE_FOLLOWUP_PREDECESSOR[schema],'managed_vmm_successor_predecessor_contract')
        up=value['upgrade'];old=predecessor['upgrade']
        before=old['configuration'];after=up['configuration']
        if schema in {'R25_MANAGED_VMM_CORE_PUBLICATION_REPAIR_V1', 'R25_KNOWLEDGE_CORE_PUBLICATION_V1',
                      'R25_STUDIO_CORE_PUBLICATION_V1', 'R25_REFERENCE_CORE_PUBLICATION_V1',
                      ASSISTANT_ILLUSTRATION_CORE_SCHEMA}:
            must(up['candidate']['commit_sha']!=old['candidate']['commit_sha'] and
                 up['candidate']['artifact_sha256']!=old['candidate']['artifact_sha256'],'publication_repair_requires_new_candidate')
        expected=dict(before,before_sha256=before['after_sha256'])
        retained=STORE/'core-followup-baselines'/(prior_sha+'.json')
        bound=private_record(STORE/'POLICY.json' if verify_live_ai and not retained.exists() else retained,prior_sha)
        must(bound==predecessor and
             (old['baseline_release']==up['baseline_release'] if repair else all(old['candidate'].get(k)==up['baseline_release'].get(k) for k in ('commit_sha','artifact_sha256'))) and
             after==expected and
             all(binding[k]==previous[k] for k in binding if k not in {'schema','predecessor_policy_sha256'}),
             'managed_vmm_successor_predecessor_binding')
        if repair:
            must(all(up['assistant'][key]==old['assistant'][key] for key in
                     ('baseline_policy_sha256','baseline_payload_fingerprint')),
                 'publication_repair_accepted_baseline_binding')
        if schema == ASSISTANT_ILLUSTRATION_CORE_SCHEMA:
            _verify_illustration_core_baseline(up, old, prior_sha)
        return _verify_core_followup(predecessor, None, core_business, verify_live_ai=verify_live_ai)
    if binding['schema']=='R25_MANAGED_VMM_CORE_FOLLOWUP_V1':
        previous=core_business.validate_core_followup(predecessor)
        must(previous['schema']=='R25_ASSISTANT_CORE_FOLLOWUP_V1','managed_vmm_predecessor_contract')
        up=value['upgrade'];old=predecessor['upgrade']
        must(all(old['candidate'].get(k)==up['baseline_release'].get(k) for k in ('commit_sha','artifact_sha256')) and
             old['configuration']['after_sha256']==up['configuration']['before_sha256'] and
             all(binding[k]==previous[k] for k in binding if k not in {'schema','acceptance','predecessor_policy_sha256'}),
             'managed_vmm_predecessor_binding')
        # The immutable AI snapshot still names its original Core transition,
        # never rewrite it to impersonate this later compatible Core release.
        return _verify_core_followup(predecessor, None, core_business, verify_live_ai=verify_live_ai)
    prior=core_business.validate_successor(predecessor)
    up=value['upgrade']; old=predecessor['upgrade']
    must(predecessor.get('schema')==BUSINESS_POLICY_SCHEMA and
         all(old['candidate'].get(k)==up['baseline_release'].get(k) for k in ('commit_sha','artifact_sha256')) and
         prior['core_final_sha256']==up['configuration']['before_sha256'] and
         prior['ai_configuration']['after_sha256']==binding['ai_configuration_sha256'], 'core_followup_predecessor_binding')
    return _verify_core_followup_ai(value, binding, prior, core_business, verify_live_ai=verify_live_ai)


def _verify_illustration_core_baseline(upgrade, predecessor, predecessor_sha):
    old = predecessor['candidate']
    baseline = upgrade['baseline_release']
    assistant = upgrade['assistant']
    must(upgrade['candidate']['payload_fingerprint'] != old['payload_fingerprint'],
         'illustration_core_requires_new_payload')
    must(assistant['migrations'] == predecessor['assistant']['migrations'] and
         upgrade['compatibility'] == predecessor['compatibility'], 'illustration_core_retention_changed')
    must(assistant['compatible_recovery_jar_sha256'] == baseline['artifact_sha256'] and
         assistant['baseline_payload_fingerprint'] == old['payload_fingerprint'],
         'illustration_core_rollback_binding')
    digest = digest_value(baseline['artifact_sha256'])
    commit = digest_value(baseline['commit_sha'], 40)
    record = private_record(STORE / 'releases' / (digest + '-' + commit) / 'record.json',
                            digest_value(baseline['accepted_record_sha256']))
    must(record.get('schema') == BUSINESS_RECORD_SCHEMA and record.get('policy_sha256') == predecessor_sha and
         all(record.get(key) == value for key, value in old.items()), 'illustration_core_baseline_record')


def _verify_core_followup_ai(value, binding, prior, core_business, *, verify_live_ai):
    store=ROOT/'runtime/ai-publications'; tx=binding['ai_transaction_id']
    if verify_live_ai:
        ai_policy=private_record(store/'POLICY.json',binding['ai_policy_sha256'])
        current=private_record(store/'CURRENT.json',binding['ai_current_sha256'])
        active=private_record(store/'ACTIVE.json',binding['ai_active_sha256'])
    journal=private_record(store/'transactions'/tx/'journal.json',binding['ai_journal_sha256'])
    snapshot=private_record(store/'transactions'/tx/'snapshot.json',binding['ai_snapshot_sha256'])
    ref=binding['ai_release']
    if verify_live_ai:
        must(current.get('schema')=='R25_AI_CURRENT_V1' and current.get('release')==ref and
             active=={'schema':'R25_AI_ACTIVE_V1','transaction_id':tx}, 'core_followup_ai_current')
    must(journal.get('schema')=='R25_AI_TRANSACTION_V1' and journal.get('transaction_id')==tx and
         journal.get('phase')=='COMMITTED' and journal.get('current')==prior['ai_before'] and
         journal.get('candidate')==ref and journal.get('snapshot_sha256')==binding['ai_snapshot_sha256'], 'core_followup_ai_not_committed')
    transition=snapshot.get('configuration_transition')
    must(snapshot.get('schema')=='R25_AI_SNAPSHOT_V1' and snapshot.get('transaction_id')==tx and
         snapshot.get('current')==prior['ai_before'] and snapshot.get('candidate')==ref and
         snapshot.get('core')==value['upgrade']['baseline_release'] and type(transition) is dict and
         transition.get('policy_sha256')==binding['predecessor_policy_sha256'], 'core_followup_ai_snapshot')
    record=private_record(store/'releases'/(ref['source_tree_sha256']+'-'+ref['commit_sha'])/'record.json',ref['record_sha256'])
    must(record.get('schema')=='R25_AI_ACCEPTED_SOURCE_V2' and record.get('policy_sha256')==binding['ai_policy_sha256'] and
         all(record.get(k)==v for k,v in prior['ai_candidate'].items()) and
         record.get('commit_sha')==ref['commit_sha'] and record.get('source_tree_sha256')==ref['source_tree_sha256'],
         'core_followup_ai_provenance')
    # This guard is also loaded standalone by the Java launcher. Do not import
    # sibling publisher modules from ambient sys.path/module caches.
    if verify_live_ai:
        must(ai_policy.get('schema')=='R25_AI_SOURCE_POLICY_V2' and ai_policy.get('source_paths')==list(ASSISTANT_AI_PATHS),
             'core_followup_ai_policy')
    files=record.get('files')
    must(type(files) is list and all(type(item) is dict for item in files) and [item.get('path') for item in files]==list(ASSISTANT_AI_PATHS) and
         hashlib.sha256(encoded(files).rstrip(b'\n')).hexdigest()==ref['source_tree_sha256'], 'core_followup_ai_inventory')
    for item in files:
        must(set(item)=={'path','bytes','sha256'} and type(item['bytes']) is int and 0<=item['bytes']<=1024*1024,
             'core_followup_ai_file')
        file_digest=digest_value(item['sha256'])
        if verify_live_ai:
            raw=read_file(ROOT/'release'/item['path'],owner=core_business.owner(ai_policy.get('source_owner')),mode=0o600,
                          expected=file_digest)
            must(len(raw)==item['bytes'], 'core_followup_ai_bytes')
    if verify_live_ai:
        read_file(ROOT/'runtime/config/assistant-private/assistant-runtime.json',
                  owner=tuple(value['installation']['private_owner']),mode=0o600,expected=binding['ai_configuration_sha256'])
    return ref


def assistant_rollout_binding_module(approval):
    path = ROOT / 'operations/r25-publisher/assistant_rollout_binding.py'
    digest = digest_value(approval['files'][path.name]['after_sha256'])
    return source_module(path, 'r25_bound_assistant_rollout', read_file(path, expected=digest))


def load_assistant_rollout_approval(value, expected_sha256=None):
    marker = private_record(STORE / 'CONTROL_UPGRADE_ACTIVE.json')
    digest = digest_value(marker.get('approval_sha256'))
    must(marker == {'schema': 'R25_CORE_CONTROLLER_UPGRADE_ACTIVE_V1', 'approval_sha256': digest} and
         expected_sha256 in (None, digest), 'illustration_active_approval_binding')
    folder = STORE / 'controller-upgrades' / digest
    approval = private_record(folder / 'approval.json', digest)
    receipt = private_record(folder / 'journal.json')
    phase = receipt.get('phase')
    must(approval.get('schema') == receipt.get('schema') == ASSISTANT_ILLUSTRATION_APPROVAL_SCHEMA and
         receipt.get('approval_sha256') == digest and phase in {'VERIFY_INTENT', 'INSTALLED'},
         'illustration_controller_installation')
    private_record(folder / 'review.json', digest_value(approval.get('review_evidence_sha256')))
    policy_sha = digest_value(approval['operational']['policy']['after_sha256'])
    raw = private_record(STORE / 'POLICY.json', policy_sha)
    installation = raw.get('installation', {})
    must(raw.get('schema') == value.get('schema') == BUSINESS_POLICY_SCHEMA and is_assistant_rollout(raw) and
         raw.get('upgrade') == value.get('upgrade') and installation == value.get('installation') and
         all(approval.get(field) == installation.get(field) for field in ('private_owner', 'controller_owner')),
         'illustration_approved_policy_binding')
    return approval, folder, phase


def verify_assistant_rollout_core(value, approval, folder, *, preserve_only=False):
    module = assistant_rollout_binding_module(approval)
    value = private_record(STORE / 'POLICY.json', approval['operational']['policy']['after_sha256'])
    path = ROOT / 'operations/r25-publisher/r25_storage.py'
    digest = digest_value(value['runtime_modules'][path.name])
    storage = source_module(path, 'r25_bound_rollout_storage', read_file(path, expected=digest))
    try:
        return module.verify_assistant_rollout_core(value, approval, folder, preserve_only=preserve_only,
                                                   storage_type=storage.R25Storage)
    except module.BindingRefused as error:
        raise Refused(str(error)) from None


def require_community_rollout(approval_sha256, lease_fd, *, preserve_only=False):
    BusinessGuard().verify_lease(lease_fd)
    policy = assistant_rollout_policy(handoff_values())
    must(policy is not None, 'illustration_community_core_policy')
    approval, folder, _ = load_assistant_rollout_approval(policy, digest_value(approval_sha256))
    return verify_assistant_rollout_core(policy, approval, folder, preserve_only=preserve_only)


def _rollout_web_commit_receipt(selection, reference, *, commit, before, core_at):
    selection_sha, reference_sha = (hashlib.sha256(read_file(path)).hexdigest() for path in (selection, reference))
    runtime = LOCK.parent
    for stage in sorted(runtime.glob('frontend-selection-transition-*'), reverse=True):
        match = re.fullmatch(r'frontend-selection-transition-([1-9][0-9]{17,19})', stage.name)
        if match is None or int(match[1]) // 1_000_000_000 <= core_at:
            continue
        try:
            state = private_record(stage / 'state.json')
        except FileNotFoundError:
            continue
        if not (state.get('state') == 'COMMITTED' and state.get('expected_commit') == commit and
                state.get('new_selection_sha') == selection_sha and state.get('new_reference_sha') == reference_sha):
            continue
        must(state.get('stage') == str(stage) and state.get('selection') == str(selection) and
             state.get('reference') == str(reference) and state.get('old_selection_sha') == before['selection_sha256'] and
             state.get('old_reference_sha') == before['reference_sha256'], 'illustration_web_transition_binding')
        for side, hashes in (('old', before), ('new', {'selection_sha256': selection_sha, 'reference_sha256': reference_sha})):
            for name in ('selection', 'reference'):
                read_file(stage / (side + '.' + name), expected=hashes[name + '_sha256'])
        return selection_sha
    raise Refused('illustration_web_commit_receipt_required')


def verify_assistant_rollout_web(value, approval):
    commit = digest_value(approval['assistant_rollout']['web_commit'], 40)
    core = private_record(STORE / 'CURRENT.json')
    tx = digest_value(core.get('transaction_id'), 32)
    journal = private_record(STORE / 'transactions' / tx / 'journal.json')
    history = journal.get('history')
    must(journal.get('schema') == 'R25_CORE_TRANSACTION_V1' and journal.get('transaction_id') == tx and
         journal.get('phase') == 'COMMITTED' and journal.get('candidate') == core.get('release') and
         type(history) is list and history and type(history[-1]) is dict and history[-1].get('phase') == 'COMMITTED' and
         type(history[-1].get('at_unix')) is int and history[-1]['at_unix'] > 0, 'illustration_web_core_commit_order')
    selection = ROOT / 'runtime/config/frontend-selection.json'
    reference = ROOT / 'runtime/config/frontend-selection.reference'
    approval_before = approval['frontend']
    selection_sha = _rollout_web_commit_receipt(selection, reference, commit=commit, before=approval_before,
                                                core_at=history[-1]['at_unix'])
    selected = private_record(selection, selection_sha)
    build_id = selected.get('build_id')
    provenance = selected.get('provenance')
    must(selected.get('commit_sha') == commit and type(provenance) is dict and provenance.get('kind') == 'git_commit' and
         type(build_id) is str and re.fullmatch('[A-Za-z0-9._-]+', build_id), 'illustration_web_git_commit')
    wanted_reference = ('schema=ARTLIVE_FRONTEND_SELECTION_REFERENCE_V1\nselection_sha256=' + selection_sha +
                        '\ncommit_sha=' + commit + '\nbuild_id=' + build_id + '\n').encode()
    must(read_file(reference) == wanted_reference, 'illustration_web_selection_reference')
    handoff = handoff_values()
    path = ROOT / 'operations/frontend_lifecycle_selector.py'
    must(handoff.get('R25_FRONTEND_SELECTOR') == str(path) and
         handoff.get('R25_FRONTEND_SELECTION') == str(selection) and
         handoff.get('R25_FRONTEND_SELECTION_REFERENCE') == str(reference), 'illustration_web_handoff')
    digest = digest_value(handoff.get('R25_FRONTEND_SELECTOR_SHA256'))
    raw = read_file(path, owner=tuple(value['installation']['controller_owner']), mode=0o700, expected=digest)
    module = source_module(path, 'r25_bound_rollout_web_selector', raw)
    try:
        observed = module.Engine(selection, selection_sha, LOCK.parent.parent).load()
    except module.Refused as error:
        raise Refused(str(error)) from None
    must(observed == selected, 'illustration_web_verified_selection')
    return True


def _rollout_record_path(ref):
    tree = digest_value(ref.get('source_tree_sha256'))
    commit = digest_value(ref.get('commit_sha'), 40)
    return ROOT / 'runtime/ai-publications/releases' / (tree + '-' + commit)


def _rollout_ai_inventory(record, ref, policy, policy_sha):
    files = record.get('files')
    must(record.get('schema') == 'R25_AI_ACCEPTED_SOURCE_V2' and
         record.get('repository') == 'bailangguihun/auralink-ai-service' and
         record.get('policy_sha256') == policy_sha and
         all(record.get(key) == ref[key] for key in ('commit_sha', 'source_tree_sha256')) and
         type(files) is list and all(type(item) is dict and set(item) == {'path', 'bytes', 'sha256'} and
                                    type(item['bytes']) is int and 0 <= item['bytes'] <= 1024 * 1024 for item in files) and
         [item['path'] for item in files] == list(ASSISTANT_AI_PATHS) and
         sum(item['bytes'] for item in files) <= 5 * 1024 * 1024 and
         hashlib.sha256(encoded(files).rstrip(b'\n')).hexdigest() == ref['source_tree_sha256'],
         'illustration_ai_inventory')
    result = {item['path']: item for item in files}
    for item in files:
        digest_value(item['sha256'])
    must(record.get('dependency_sha256') == policy.get('dependency_sha256') ==
         {path: result[path]['sha256'] for path in AI_DEPENDENCY_FILES}, 'illustration_ai_dependencies')
    return result


def _rollout_ai_candidate(module, approved, ai_policy, ai_policy_sha):
    folder = _rollout_record_path(approved)
    raw = read_file(folder / 'record.json')
    record = strict_json(raw)
    try:
        ref = module.bind_ai_candidate(approved, record, record_sha256=hashlib.sha256(raw).hexdigest())
    except module.BindingRefused as error:
        raise Refused(str(error)) from None
    files = _rollout_ai_inventory(record, ref, ai_policy, ai_policy_sha)
    read_file(folder / 'candidate.json', expected=record['candidate_manifest_sha256'])
    read_file(folder / 'ai-source.zip', expected=record['archive_sha256'], limit=6 * 1024 * 1024)
    for path, item in files.items():
        must(len(read_file(folder / 'source' / path, expected=item['sha256'])) == item['bytes'],
             'illustration_staged_ai_bytes')
    return ref, files


def _verify_rollout_live_ai(files, owner):
    release = ROOT / 'release'
    for relative in ('guide_service', 'critic-service/app'):
        for folder, directories, names in os.walk(release / relative, followlinks=False):
            must(all(not (Path(folder) / name).is_symlink() for name in directories), 'illustration_ai_symlink')
            for name in names:
                path = Path(folder) / name
                must(not path.is_symlink() and not name.endswith(('.pyc', '.pyo', '.so', '.pyd')) and
                     (not name.endswith('.py') or path.relative_to(release).as_posix() in files),
                     'illustration_ai_untracked_runtime_file')
    for path, allowed in files.items():
        raw = read_file(release / path, owner=owner)
        actual = (hashlib.sha256(raw).hexdigest(), len(raw))
        must(any(actual == (item['sha256'], item['bytes']) for item in allowed), 'illustration_ai_live_source')


def verify_assistant_rollout_ai(value, *, candidate=None, for_start=True):
    """Verify a complete AI side for startup, or known bytes during recovery."""
    approval, folder, controller_phase = load_assistant_rollout_approval(value)
    if controller_phase == 'VERIFY_INTENT':
        must(candidate is None, 'illustration_ai_controller_not_installed')
        return verify_core_followup_context(value)
    must(controller_phase == 'INSTALLED', 'illustration_ai_controller_not_installed')
    module = assistant_rollout_binding_module(approval)
    historical = value['upgrade']['core_followup']
    old_ref = verify_core_followup_history(value)
    before_current = private_record(folder / 'before/ILLUSTRATION_AI_CURRENT.json', historical['ai_current_sha256'])
    before_active = private_record(folder / 'before/ILLUSTRATION_AI_ACTIVE.json', historical['ai_active_sha256'])
    must(before_current.get('release') == old_ref, 'illustration_ai_before_reference')
    store = ROOT / 'runtime/ai-publications'
    ai_policy_sha = digest_value(historical['ai_policy_sha256'])
    must(handoff_values().get('R25_AI_RELEASE_POLICY_SHA256') == ai_policy_sha, 'illustration_ai_handoff_policy')
    ai_policy = private_record(store / 'POLICY.json', ai_policy_sha)
    owner = ai_policy.get('source_owner')
    must(ai_policy.get('schema') == 'R25_AI_SOURCE_POLICY_V2' and
         ai_policy.get('repository') == 'bailangguihun/auralink-ai-service' and
         ai_policy.get('source_paths') == list(ASSISTANT_AI_PATHS) and
         type(owner) is list and len(owner) == 2 and all(type(x) is int and x >= 0 for x in owner),
         'illustration_ai_policy')
    read_file(ROOT / 'runtime/config/assistant-private/assistant-runtime.json',
              owner=tuple(value['installation']['private_owner']), expected=historical['ai_configuration_sha256'])
    old_record = private_record(_rollout_record_path(old_ref) / 'record.json', digest_value(old_ref['record_sha256']))
    old_files = _rollout_ai_inventory(old_record, old_ref, ai_policy, ai_policy_sha)
    current = private_record(store / 'CURRENT.json')
    active = private_record(store / 'ACTIVE.json')
    tx = digest_value(active.get('transaction_id'), 32)
    path = store / 'transactions' / tx
    original_transaction = active == before_active
    journal = private_record(path / 'journal.json', historical['ai_journal_sha256'] if original_transaction else None)
    phase = journal.get('phase')
    snapshot = snapshot_sha = core_after = live_core = new_ref = None
    new_files = old_files
    web_approved = False
    if candidate is not None or not original_transaction:
        new_ref, new_files = _rollout_ai_candidate(module, approval['assistant_rollout']['ai_candidate'],
                                                  ai_policy, ai_policy_sha)
        changed = [name for name in ASSISTANT_AI_PATHS if old_files[name] != new_files[name]]
        must(changed and all(name.startswith('critic-service/app/') for name in changed),
             'illustration_critic_source_only')
    if not original_transaction:
        snapshot_sha = digest_value(journal.get('snapshot_sha256'))
        snapshot = private_record(path / 'snapshot.json', snapshot_sha)
        core_after = snapshot.get('core')
        must(type(core_after) is dict and
             all(core_after.get(key) == value['upgrade']['candidate'][key] for key in ('commit_sha', 'artifact_sha256')) and
             snapshot.get('source_owner') == owner and
             snapshot.get('files') == {name: {'before': old_files[name], 'after': new_files[name]} for name in ASSISTANT_AI_PATHS},
             'illustration_ai_snapshot_binding')
    if candidate is not None or (not original_transaction and phase != 'RESTORED'):
        live_core = verify_assistant_rollout_core(value, approval, folder)
        must(type(live_core) is dict and
             all(live_core.get(key) == value['upgrade']['candidate'][key] for key in ('commit_sha', 'artifact_sha256')),
             'illustration_ai_committed_core_required')
    if candidate is not None or (for_start and not original_transaction and phase in module.CANDIDATE_PHASES):
        web_approved = verify_assistant_rollout_web(value, approval)
        must(web_approved is True, 'illustration_ai_committed_web_required')
    try:
        selected = module.select_ai_reference(
            before_current=before_current, before_active=before_active, candidate=new_ref,
            core_before=value['upgrade']['baseline_release'], core_after=core_after, core_current=live_core,
            current=current, active=active, journal=journal, snapshot=snapshot, snapshot_sha256=snapshot_sha,
            for_start=for_start, web_approved=web_approved,
        )
    except module.BindingRefused as error:
        raise Refused(str(error)) from None
    if candidate is not None:
        must(candidate == new_ref and selected == old_ref and (original_transaction or phase == 'RESTORED'),
             'illustration_ai_publication_selection')
    if selected is None:
        allowed_files = {name: (old_files[name], new_files[name]) for name in ASSISTANT_AI_PATHS}
    else:
        selected_files = new_files if selected == new_ref else old_files
        allowed_files = {name: (item,) for name, item in selected_files.items()}
    _verify_rollout_live_ai(allowed_files, tuple(owner))
    return selected



def successor_configuration_phase(value):
    """Authorize final configuration only through the exact AI journal.

    A digest on disk alone does not authorize G1. Stable phases must agree with
    AI CURRENT, pinned snapshot, C1 identity, policy and source provenance.
    Mixed phases permit recovery inspection but never application startup.
    """
    binding = value['upgrade'].get('successor')
    if binding is None: return 'bridge'
    def checked(phase):
        expected = binding['ai_configuration']
        raw = read_file(ROOT/'runtime/config/assistant-private/assistant-runtime.json',
                        owner=tuple(value['installation']['private_owner']),mode=0o600)
        permitted = set(expected.values()) if phase == 'transition' else {expected['after_sha256' if phase == 'final' else 'before_sha256']}
        must(hashlib.sha256(raw).hexdigest() in permitted, 'successor_ai_configuration_state')
        return phase
    store = ROOT / 'runtime/ai-publications'
    current = private_record(store / 'CURRENT.json')
    must(current.get('schema') == 'R25_AI_CURRENT_V1', 'successor_ai_current')
    try: active = private_record(store / 'ACTIVE.json')
    except FileNotFoundError:
        must(current.get('release') == binding['ai_before'], 'successor_ai_before_current')
        return checked('bridge')
    tx = active.get('transaction_id')
    must(active.get('schema') == 'R25_AI_ACTIVE_V1' and type(tx) is str and
        re.fullmatch('[0-9a-f]{32}',tx), 'successor_ai_active')
    journal = private_record(store / 'transactions' / tx / 'journal.json')
    must(journal.get('transaction_id') == tx and journal.get('schema') == 'R25_AI_TRANSACTION_V1',
         'successor_ai_journal')
    if journal.get('phase') in {'COMMITTED','RESTORED'} and current.get('release') == binding['ai_before']:
        expected = journal.get('candidate' if journal['phase'] == 'COMMITTED' else 'current')
        must(expected == binding['ai_before'], 'successor_ai_prior_journal')
        return checked('bridge')
    snapshot = private_record(store / 'transactions' / tx / 'snapshot.json',journal['snapshot_sha256'])
    transition = snapshot.get('configuration_transition')
    must(type(transition) is dict and transition.get('policy_sha256') ==
        hashlib.sha256(read_file(STORE / 'POLICY.json')).hexdigest() and
        snapshot.get('schema') == 'R25_AI_SNAPSHOT_V1' and snapshot.get('transaction_id') == tx and
        snapshot.get('current') == journal.get('current') == binding['ai_before'] and
        snapshot.get('candidate') == journal.get('candidate'), 'successor_ai_snapshot')
    ref = journal['candidate']; candidate = binding['ai_candidate']
    must(ref.get('commit_sha') == candidate['commit_sha'] and
        ref.get('source_tree_sha256') == candidate['source_tree_sha256'], 'successor_ai_candidate_binding')
    release = private_record(store / 'releases' / (ref['source_tree_sha256']+'-'+ref['commit_sha']) / 'record.json',
                             digest_value(ref.get('record_sha256')))
    must(all(release.get(k) == v for k,v in candidate.items()), 'successor_ai_candidate_provenance')
    core = private_record(STORE / 'CURRENT.json').get('release')
    must(core == snapshot.get('core') and core.get('artifact_sha256') == value['upgrade']['candidate']['artifact_sha256'] and
        core.get('commit_sha') == value['upgrade']['candidate']['commit_sha'], 'successor_core_bridge_required')
    phase = journal['phase']; ref_now = current.get('release')
    must(ref_now in (binding['ai_before'], ref), 'successor_ai_current_drift')
    if phase in {'PREPARED','QUIESCE_INTENT','QUIESCED','STOP_INTENT','STOPPED','RESTORE_START_INTENT','RESTORED'}:
        if phase == 'RESTORED': must(ref_now == binding['ai_before'], 'successor_ai_restored_pointer')
        return checked('bridge')
    if phase in {'START_INTENT','ACCEPTED','CURRENT_COMMIT_INTENT','COMMITTED'}:
        if phase == 'COMMITTED': must(ref_now == ref, 'successor_ai_committed_pointer')
        return checked('final')
    must(phase in {'INSTALL_INTENT','INSTALLED','RECOVERY_REQUIRED','RESTORE_STOP_INTENT','RESTORE_STOPPED','RESTORE_INTENT'},
         'successor_ai_phase')
    return checked('transition')


def require_release_configuration(value, record):
    if value.get('schema') == BUSINESS_POLICY_SCHEMA:
        expected = business_module(value).config_for_release(value, record)
        read_file(ROOT / 'runtime/config/application.yml', expected=expected)


def release_record(artifact_sha, expected_record=None, commit=None):
    artifact_sha = digest_value(artifact_sha)
    commit = digest_value(commit, 40)
    record = private_record(STORE / 'releases' / (artifact_sha + '-' + commit) / 'record.json', expected_record)
    expected = policy()
    if expected.get('schema') == BUSINESS_POLICY_SCHEMA:
        upgrade = expected['upgrade']
        baseline = upgrade['baseline_release']
        must(record.get('artifact_sha256') == artifact_sha and record.get('commit_sha') == commit,
             'business_record_identity')
        if artifact_sha == baseline['artifact_sha256'] and commit == baseline['commit_sha']:
            if upgrade.get('capability') == 'assistant-v1':
                prior=upgrade['assistant']
                must(expected_record==baseline['accepted_record_sha256'] and
                     record.get('schema')==BUSINESS_RECORD_SCHEMA and
                     record.get('repository') in ('bailangguihun/auralink-core-api',REPOSITORY) and
                     record.get('policy_sha256')==prior['baseline_policy_sha256'] and
                     record.get('payload_fingerprint')==prior['baseline_payload_fingerprint'],
                     'assistant_verified_v2_rollback_record')
            else:
                must(expected_record == baseline['accepted_record_sha256'] and
                     record.get('schema') == RECORD_SCHEMA and record.get('repository') == REPOSITORY and
                     record.get('policy_sha256') == expected['legacy_policy_sha256'] and
                     record.get('payload_fingerprint') == expected['legacy_policy']['payload_fingerprint'],
                     'business_rollback_record')
        else:
            must(record.get('schema') == BUSINESS_RECORD_SCHEMA and
                 record.get('policy_sha256') == hashlib.sha256(read_file(STORE / 'POLICY.json')).hexdigest(),
                 'business_record_policy')
            business_module(expected).candidate_matches(expected, record)
        must(type(record.get('artifact_bytes')) is int and 0 < record['artifact_bytes'] < 512 * 1024 * 1024,
             'release_artifact_size')
        return record
    must(record.get('schema') == RECORD_SCHEMA and record.get('repository') == REPOSITORY and
         record.get('artifact_sha256') == artifact_sha and
         record.get('payload_fingerprint') == expected['payload_fingerprint'] and
         record.get('policy_sha256') == hashlib.sha256(read_file(STORE / 'POLICY.json')).hexdigest(),
         'release_capability_binding')
    must(record.get('commit_sha') == commit, 'release_record_commit')
    must(type(record.get('artifact_bytes')) is int and 0 < record['artifact_bytes'] < 512 * 1024 * 1024,
         'release_artifact_size')
    return record


def validate_backend_identity(root, backend):
    must(Path(root) == ROOT, 'fixed_root')
    digest_value(backend)
    current = private_record(STORE / 'CURRENT.json')
    reference = current.get('release')
    if not isinstance(reference, dict) or reference.get('artifact_sha256') != backend:
        active = private_record(STORE / 'ACTIVE.json')
        transaction_id = active.get('transaction_id')
        must(type(transaction_id) is str and re.fullmatch('[0-9a-f]{32}', transaction_id), 'active_transaction')
        journal = private_record(STORE / 'transactions' / transaction_id / 'journal.json')
        must(journal.get('phase') in START_PHASES and journal.get('transaction_id') == transaction_id,
             'active_start_phase')
        reference = journal.get('current') if journal['phase'].startswith('RESTORE') else journal.get('candidate')
        must(isinstance(reference, dict) and reference.get('artifact_sha256') == backend, 'active_release_binding')
        must(current.get('release') in (journal.get('current'), journal.get('candidate')), 'active_current_binding')
    record = release_record(backend, digest_value(reference.get('accepted_record_sha256')), reference.get('commit_sha'))
    must(record['commit_sha'] == reference.get('commit_sha'), 'release_commit_binding')
    value = policy()
    if is_assistant_rollout(value):
        verify_assistant_rollout_ai(value)
    require_release_configuration(value, record)
    return record


class Guard:
    def __init__(self, legacy):
        self.legacy = legacy
        self.lock = legacy.lock

    def is_registered_backend(self, backend):
        digest_value(backend)
        try:
            validate_backend_identity(ROOT, backend)
        except FileNotFoundError:
            return False
        return True

    def require_backend_eligible(self, backend, fd):
        record = validate_backend_identity(ROOT, backend)
        for path in (ROOT / 'release/artifacts/backend.jar', PREVIEW / 'release/backend.jar'):
            # No candidate-controlled import occurs: JAR is only parsed.
            raw = read_file(path, limit=512 * 1024 * 1024, expected=backend)
            must(len(raw) == record['artifact_bytes'], 'live_artifact_size')
            must(payload_fingerprint(path) == record['payload_fingerprint'], 'live_capability_payload')
        # This maps an independently verified byte-equivalent capability to the
        # original guard, preserving its authoritative PG/retention/media test.
        return self.legacy.require_backend_eligible(BASELINE, fd)


def source_module(path, name, raw):
    """Execute only the already verified source bytes, never a .pyc cache."""
    specification = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(specification)
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        exec(compile(raw, str(path), 'exec', dont_inherit=True), module.__dict__)
    except BaseException:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
        raise
    module.__r25_source_sha256__ = hashlib.sha256(raw).hexdigest()
    return module


def reject_legacy_bytecode(path):
    """The unchanged legacy PG loader must have no reusable bytecode cache.

    This is a prerequisite check only; never delete production cache files.
    """
    path = Path(path)
    candidates = {path.with_suffix('.pyc'), path.with_suffix('.pyo')}
    cache = path.parent / '__pycache__'
    must(not cache.is_symlink(), 'legacy_postgres_bytecode_cache')
    if cache.exists():
        candidates.update(cache.glob(path.stem + '.*.pyc'))
        candidates.update(cache.glob(path.stem + '.*.pyo'))
    # cache_from_source honors a configured PYTHONPYCACHEPREFIX as well.
    for optimization in ('', '1', '2'):
        candidates.add(Path(importlib.util.cache_from_source(str(path), optimization=optimization)))
    must(not any(item.exists() or item.is_symlink() for item in candidates), 'legacy_postgres_bytecode_cache')


class BusinessGuard:
    """V2 checks actual candidate capability; never impersonate the V1 baseline."""
    lock = LOCK

    def verify_lease(self, fd):
        import fcntl
        must(type(fd) is int and fd >= 3, 'business_lease_descriptor')
        read_file(self.lock)
        opened, named = os.fstat(fd), self.lock.lstat()
        must((opened.st_dev, opened.st_ino) == (named.st_dev, named.st_ino), 'business_lease_inode')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise Refused('shared_publication_busy') from error

    def is_registered_backend(self, backend):
        validate_backend_identity(ROOT, backend)
        return True

    def prepare_launch(self, backend, fd):
        """Called only by the owned launcher immediately before exec(Java).

        Preserve the existing Java UID/PID and c5ea lifecycle contract. The
        separately delivered asset filesystem must already be globally read-only;
        this child permanently drops capabilities before running the new payload.
        The coordinator itself never calls this privilege transition.
        """
        self.verify_lease(fd)
        value = policy()
        record = validate_backend_identity(ROOT, backend)
        if record.get('schema') == BUSINESS_RECORD_SCHEMA:
            business_module(value).prepare_asset_sandbox(value)

    def require_backend_eligible(self, backend, fd):
        self.verify_lease(fd)
        value = policy()
        record = validate_backend_identity(ROOT, backend)
        for path in (ROOT / 'release/artifacts/backend.jar', PREVIEW / 'release/backend.jar'):
            raw = read_file(path, limit=512 * 1024 * 1024, expected=backend)
            must(len(raw) == record['artifact_bytes'] and
                 payload_fingerprint(path) == record['payload_fingerprint'], 'business_live_payload')
        module = business_module(value)
        module.verify_compatibility(value, lambda sql: module.readonly_query(value, sql, fd))
        if record.get('schema') == BUSINESS_RECORD_SCHEMA:
            module.verify_assets(value)
        return {'schema': 'R25_CORE_BUSINESS_ELIGIBILITY_V2', 'artifact_sha256': backend}


def production():
    must(os.geteuid() == 0, 'root_operator')
    # A first V1 installation has no POLICY yet; preserve that bootstrap path.
    if (ROOT / 'runtime/core-publications/POLICY.json').exists():
        raw_handoff = read_file(ROOT / 'runtime/handoff-control.env')
        if b'R25_CORE_RELEASE_POLICY_SHA256=' in raw_handoff and policy().get('schema') == BUSINESS_POLICY_SCHEMA:
            return BusinessGuard()
    path = ROOT / 'operations/huaniao_recovery_guard.py'
    raw = read_file(path, owner=(1000, 1000), mode=0o700, expected=LEGACY_GUARD_SHA)
    reject_legacy_bytecode(ROOT / 'operations/postgres_access.py')
    module = source_module(path, 'original_r25_huaniao_guard', raw)
    previous = sys.dont_write_bytecode
    try:
        # The original PG loader is retained, but must not create a cache that
        # would cause the next guard invocation to reject this same process.
        sys.dont_write_bytecode = True
        return Guard(module.production())
    finally:
        sys.dont_write_bytecode = previous
