"""Trusted R25 AI source registry guard used by the shared lifecycle controller.

Candidate JSON is never an authorization source. Only root-private accepted
records referenced by CURRENT or a phase-bound ACTIVE transaction authorize
the exact 19 live files. Importing this module does not execute candidate code.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re

import core_release_guard as core

ROOT = core.ROOT
STORE = ROOT / 'runtime/ai-publications'
REPOSITORY = 'Hang-666-star/auralink-ai-service'
SOURCE_PATHS = tuple(sorted([
    'guide_service/__init__.py', 'guide_service/__main__.py', 'guide_service/app.py',
    'guide_service/config.py', 'guide_service/diagnostics.py', 'guide_service/env_loader.py',
    'guide_service/qwen_client.py', 'guide_service/schema.py', 'guide_service/requirements.txt',
    'critic-service/app/__init__.py', 'critic-service/app/config.py', 'critic-service/app/dimensions.py',
    'critic-service/app/image_utils.py', 'critic-service/app/llm.py', 'critic-service/app/main.py',
    'critic-service/app/orchestrator.py', 'critic-service/app/prompts.py', 'critic-service/app/schemas.py',
    'critic-service/requirements.txt',
]))
ASSISTANT_PATHS = tuple(sorted(SOURCE_PATHS + tuple('critic-service/app/assistant/' + name for name in (
    '__init__.py','config.py','schemas.py','router.py','execution.py','providers.py','retrieval.py','prompts.py'))))


def paths_for(record):
    schema = record.get('schema')
    must(schema in ('R25_AI_ACCEPTED_SOURCE_V1','R25_AI_ACCEPTED_SOURCE_V2'), 'ai_record_schema')
    return SOURCE_PATHS if schema.endswith('_V1') else ASSISTANT_PATHS


def source_owner():
    value=policy()
    return tuple(value.get('source_owner', [1000,1000]))


DEPENDENCIES = ('guide_service/requirements.txt', 'critic-service/requirements.txt')
OLD_PHASES = {'PREPARED', 'QUIESCE_INTENT', 'QUIESCED', 'STOP_INTENT', 'STOPPED', 'RESTORE_START_INTENT', 'RESTORED'}
NEW_PHASES = {'START_INTENT', 'ACCEPTED', 'CURRENT_COMMIT_INTENT', 'COMMITTED'}
must, digest_value, read_file, private_record = core.must, core.digest_value, core.read_file, core.private_record


def canonical(value):
    # Same encoding as the immutable AI candidate contract (no trailing LF).
    return core.encoded(value).rstrip(b'\n')


def reference(value):
    must(type(value) is dict and set(value) == {'commit_sha', 'source_tree_sha256', 'record_sha256'}, 'ai_reference')
    digest_value(value['commit_sha'], 40)
    digest_value(value['source_tree_sha256'])
    digest_value(value['record_sha256'])
    return value


def release_folder(ref):
    reference(ref)
    return STORE / 'releases' / (ref['source_tree_sha256'] + '-' + ref['commit_sha'])


def handoff_values():
    values = {}
    for line in read_file(ROOT / 'runtime/handoff-control.env').decode().splitlines():
        if not line or line.startswith('#'):
            continue
        must('=' in line, 'ai_handoff_syntax')
        key, value = line.split('=', 1)
        must(key not in values, 'ai_handoff_duplicate')
        values[key] = value
    return values


def policy():
    values = handoff_values()
    expected = digest_value(values.get('R25_AI_RELEASE_POLICY_SHA256'))
    result = private_record(STORE / 'POLICY.json', expected)
    v2 = result.get('schema') == 'R25_AI_SOURCE_POLICY_V2'
    must(result.get('schema') in ('R25_AI_SOURCE_POLICY_V1','R25_AI_SOURCE_POLICY_V2') and
         result.get('repository') == ('bailangguihun/auralink-ai-service' if v2 else REPOSITORY) and
         result.get('source_paths') == list(ASSISTANT_PATHS if v2 else SOURCE_PATHS), 'ai_policy_identity')
    if v2:
        digest_value(result.get('legacy_policy_sha256'))
        digest_value(result.get('review_evidence_sha256'))
        must(type(result.get('source_owner')) is list and len(result['source_owner']) == 2 and all(type(x) is int and x >= 0 for x in result['source_owner']), 'ai_source_owner')
    deps = result.get('dependency_sha256')
    must(type(deps) is dict and set(deps) == set(DEPENDENCIES), 'ai_policy_dependencies')
    for value in deps.values():
        digest_value(value)
    return result


def accepted(ref):
    folder = release_folder(ref)
    record = private_record(folder / 'record.json', ref['record_sha256'])
    paths = paths_for(record)
    must(record.get('repository') == (REPOSITORY if paths == SOURCE_PATHS else 'bailangguihun/auralink-ai-service')
         and record.get('commit_sha') == ref['commit_sha'] and record.get('source_tree_sha256') == ref['source_tree_sha256'],
         'ai_record_binding')
    files = record.get('files')
    must(type(files) is list and len(files) == len(paths)
         and all(type(x) is dict and set(x) == {'path', 'bytes', 'sha256'} for x in files)
         and [x['path'] for x in files] == list(paths), 'ai_record_inventory')
    for item in files:
        must(type(item['bytes']) is int and 0 <= item['bytes'] <= 1024 * 1024, 'ai_record_size')
        digest_value(item['sha256'])
    must(sum(x['bytes'] for x in files) <= 5 * 1024 * 1024 and
         hashlib.sha256(canonical(files)).hexdigest() == ref['source_tree_sha256'], 'ai_record_fingerprint')
    policy_value = policy()
    approved = {hashlib.sha256(read_file(STORE / 'POLICY.json')).hexdigest()}
    if paths == SOURCE_PATHS and policy_value.get('schema') == 'R25_AI_SOURCE_POLICY_V2':
        approved.add(policy_value['legacy_policy_sha256'])
    must(record.get('policy_sha256') in approved, 'ai_record_policy')
    hashes = {x['path']: x['sha256'] for x in files}
    must(record.get('dependency_sha256') == policy_value['dependency_sha256']
         == {name: hashes[name] for name in DEPENDENCIES}, 'ai_dependencies_require_separate_environment_review')
    return record


def selected_reference():
    values = handoff_values()
    if 'R25_CORE_RELEASE_POLICY_SHA256' in values:
        core_policy = core.assistant_rollout_policy(values)
        if core_policy is not None:
            path = ROOT / 'operations/core_release_guard.py'
            digest = digest_value(values.get('R25_CORE_RELEASE_GUARD_SHA256'))
            owner = core_policy['installation'].get('controller_owner')
            must(values.get('R25_CORE_RELEASE_GUARD') == str(path) and
                 type(owner) is list and len(owner) == 2 and all(type(x) is int and x >= 0 for x in owner),
                 'illustration_ai_core_guard_binding')
            raw = read_file(path, owner=tuple(owner), mode=0o700, expected=digest)
            module = core.source_module(path, 'r25_bound_ai_illustration_core_guard', raw)
            ref = module.verify_assistant_rollout_ai(core_policy)
            return reference(ref)
    current = private_record(STORE / 'CURRENT.json')
    must(current.get('schema') == 'R25_AI_CURRENT_V1', 'ai_current_schema')
    ref = reference(current.get('release'))
    try:
        active = private_record(STORE / 'ACTIVE.json')
    except FileNotFoundError:
        return ref
    transaction_id = active.get('transaction_id')
    must(active.get('schema') == 'R25_AI_ACTIVE_V1' and type(transaction_id) is str
         and re.fullmatch('[0-9a-f]{32}', transaction_id), 'ai_active_identity')
    journal = private_record(STORE / 'transactions' / transaction_id / 'journal.json')
    must(journal.get('schema') == 'R25_AI_TRANSACTION_V1' and journal.get('transaction_id') == transaction_id,
         'ai_journal_identity')
    before, after = reference(journal.get('current')), reference(journal.get('candidate'))
    must(ref in (before, after), 'ai_active_current_drift')
    phase = journal.get('phase')
    must(phase in OLD_PHASES | NEW_PHASES, 'ai_incomplete_source_install_requires_recovery')
    if phase in {'COMMITTED', 'RESTORED'}:
        must(ref == (after if phase == 'COMMITTED' else before), 'ai_terminal_current_binding')
    return after if phase in NEW_PHASES else before


def verify_live(ref):
    record = accepted(ref)
    paths = paths_for(record)
    # -B prevents writing bytecode, but Python can still read an old .pyc.
    # Refuse an untracked execution surface rather than deleting caches or
    # silently executing bytes outside the accepted source fingerprint.
    for relative in ('guide_service', 'critic-service/app'):
        for folder, directories, files in os.walk(ROOT / 'release' / relative, followlinks=False):
            for name in directories:
                must(not (Path(folder) / name).is_symlink(), 'ai_runtime_symlink_directory')
            for name in files:
                path = Path(folder) / name
                must(not path.is_symlink(), 'ai_runtime_symlink_file')
                if name.endswith(('.pyc', '.pyo', '.so', '.pyd')):
                    must(False, 'ai_untracked_bytecode_or_native_extension')
                if name.endswith('.py'):
                    must(path.relative_to(ROOT / 'release').as_posix() in paths, 'ai_untracked_runtime_module')
    for item in record['files']:
        raw = read_file(ROOT / 'release' / item['path'], owner=source_owner(), mode=0o600, expected=item['sha256'])
        must(len(raw) == item['bytes'], 'ai_live_size')
    return record


def validate_critic_identity(root, name):
    must(Path(root) == ROOT and name in {'main.py', 'config.py', 'llm.py'}, 'ai_fixed_runtime_path')
    record = verify_live(selected_reference())
    path = 'critic-service/app/' + name
    return next(item['sha256'] for item in record['files'] if item['path'] == path)
