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


def policy():
    values = {}
    for line in read_file(ROOT / 'runtime/handoff-control.env').decode().splitlines():
        if not line or line.startswith('#'):
            continue
        must('=' in line, 'handoff_syntax')
        key, value = line.split('=', 1)
        must(key not in values, 'handoff_duplicate')
        values[key] = value
    result = private_record(STORE / 'POLICY.json', digest_value(values.get('R25_CORE_RELEASE_POLICY_SHA256')))
    if result.get('schema') == BUSINESS_POLICY_SCHEMA:
        business_module(result).validate_policy(result)
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
    if 'core_followup' in upgrade:
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
    binding=core_business.validate_core_followup(value)
    prior_sha=binding['predecessor_policy_sha256']
    if predecessor is None:
        predecessor=private_record(STORE/'core-followup-baselines'/(prior_sha+'.json'),prior_sha)
    if binding['schema'] in {'R25_MANAGED_VMM_CORE_SUCCESSOR_V1','R25_MANAGED_VMM_CORE_PUBLICATION_V1'}:
        # One finite successor of the committed managed-Core installation.
        # Keep its original AI snapshot and accepted records immutable; do not
        # turn this into a recursively extensible arbitrary release chain.
        previous=core_business.validate_core_followup(predecessor)
        expected_schema=('R25_MANAGED_VMM_CORE_SUCCESSOR_V1' if binding['schema']=='R25_MANAGED_VMM_CORE_PUBLICATION_V1' else 'R25_MANAGED_VMM_CORE_FOLLOWUP_V1')
        must(previous['schema']==expected_schema,'managed_vmm_successor_predecessor_contract')
        up=value['upgrade'];old=predecessor['upgrade']
        before=old['configuration'];after=up['configuration']
        expected=dict(before,before_sha256=before['after_sha256'])
        retained=STORE/'core-followup-baselines'/(prior_sha+'.json')
        bound=private_record(retained if retained.exists() else STORE/'POLICY.json',prior_sha)
        must(bound==predecessor and
             all(old['candidate'].get(k)==up['baseline_release'].get(k) for k in ('commit_sha','artifact_sha256')) and
             after==expected and
             all(binding[k]==previous[k] for k in binding if k not in {'schema','predecessor_policy_sha256'}),
             'managed_vmm_successor_predecessor_binding')
        return verify_core_followup_context(predecessor,validator=core_business)
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
        return verify_core_followup_context(predecessor,validator=core_business)
    prior=core_business.validate_successor(predecessor)
    up=value['upgrade']; old=predecessor['upgrade']
    must(predecessor.get('schema')==BUSINESS_POLICY_SCHEMA and
         all(old['candidate'].get(k)==up['baseline_release'].get(k) for k in ('commit_sha','artifact_sha256')) and
         prior['core_final_sha256']==up['configuration']['before_sha256'] and
         prior['ai_configuration']['after_sha256']==binding['ai_configuration_sha256'], 'core_followup_predecessor_binding')
    store=ROOT/'runtime/ai-publications'; tx=binding['ai_transaction_id']
    ai_policy=private_record(store/'POLICY.json',binding['ai_policy_sha256'])
    current=private_record(store/'CURRENT.json',binding['ai_current_sha256'])
    active=private_record(store/'ACTIVE.json',binding['ai_active_sha256'])
    journal=private_record(store/'transactions'/tx/'journal.json',binding['ai_journal_sha256'])
    snapshot=private_record(store/'transactions'/tx/'snapshot.json',binding['ai_snapshot_sha256'])
    ref=binding['ai_release']
    must(current.get('schema')=='R25_AI_CURRENT_V1' and current.get('release')==ref and
         active=={'schema':'R25_AI_ACTIVE_V1','transaction_id':tx}, 'core_followup_ai_current')
    must(journal.get('schema')=='R25_AI_TRANSACTION_V1' and journal.get('transaction_id')==tx and
         journal.get('phase')=='COMMITTED' and journal.get('current')==prior['ai_before'] and
         journal.get('candidate')==ref and journal.get('snapshot_sha256')==binding['ai_snapshot_sha256'], 'core_followup_ai_not_committed')
    transition=snapshot.get('configuration_transition')
    must(snapshot.get('schema')=='R25_AI_SNAPSHOT_V1' and snapshot.get('transaction_id')==tx and
         snapshot.get('current')==prior['ai_before'] and snapshot.get('candidate')==ref and
         snapshot.get('core')==up['baseline_release'] and type(transition) is dict and
         transition.get('policy_sha256')==prior_sha, 'core_followup_ai_snapshot')
    record=private_record(store/'releases'/(ref['source_tree_sha256']+'-'+ref['commit_sha'])/'record.json',ref['record_sha256'])
    must(record.get('schema')=='R25_AI_ACCEPTED_SOURCE_V2' and record.get('policy_sha256')==binding['ai_policy_sha256'] and
         all(record.get(k)==v for k,v in prior['ai_candidate'].items()) and
         record.get('commit_sha')==ref['commit_sha'] and record.get('source_tree_sha256')==ref['source_tree_sha256'],
         'core_followup_ai_provenance')
    # This guard is also loaded standalone by the Java launcher. Do not import
    # sibling publisher modules from ambient sys.path/module caches.
    paths=tuple(sorted(('guide_service/__init__.py','guide_service/__main__.py','guide_service/app.py',
        'guide_service/config.py','guide_service/diagnostics.py','guide_service/env_loader.py','guide_service/qwen_client.py',
        'guide_service/schema.py','guide_service/requirements.txt','critic-service/app/__init__.py','critic-service/app/config.py',
        'critic-service/app/dimensions.py','critic-service/app/image_utils.py','critic-service/app/llm.py','critic-service/app/main.py',
        'critic-service/app/orchestrator.py','critic-service/app/prompts.py','critic-service/app/schemas.py','critic-service/requirements.txt')+
        tuple('critic-service/app/assistant/'+name for name in ('__init__.py','config.py','schemas.py','router.py',
            'execution.py','providers.py','retrieval.py','prompts.py'))))
    must(ai_policy.get('schema')=='R25_AI_SOURCE_POLICY_V2' and ai_policy.get('source_paths')==list(paths),
         'core_followup_ai_policy')
    files=record.get('files')
    must(type(files) is list and all(type(item) is dict for item in files) and [item.get('path') for item in files]==list(paths) and
         hashlib.sha256(encoded(files).rstrip(b'\n')).hexdigest()==ref['source_tree_sha256'], 'core_followup_ai_inventory')
    for item in files:
        must(set(item)=={'path','bytes','sha256'} and type(item['bytes']) is int and 0<=item['bytes']<=1024*1024,
             'core_followup_ai_file')
        raw=read_file(ROOT/'release'/item['path'],owner=core_business.owner(ai_policy.get('source_owner')),mode=0o600,
                      expected=digest_value(item['sha256']))
        must(len(raw)==item['bytes'], 'core_followup_ai_bytes')
    read_file(ROOT/'runtime/config/assistant-private/assistant-runtime.json',
              owner=tuple(value['installation']['private_owner']),mode=0o600,expected=binding['ai_configuration_sha256'])
    return ref



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
    require_release_configuration(policy(), record)
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
