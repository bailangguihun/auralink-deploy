"""Install or recover the finite, preapproved Assistant controller transition."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import os
import re
import time

import core_release_guard as guard
from core_runtime import LIVE_FILES, env_values
from publisher_transaction import Refused
from upgrade_core_controller import immutable_copy, owner, upgrade_lease


SCHEMA = 'R25_ASSISTANT_ILLUSTRATION_CORE_PUBLICATION_BINDING_V1'
SUCCESSOR = guard.ASSISTANT_ILLUSTRATION_SUCCESSOR_APPROVAL
SUCCESSOR_FILES = ('core_publish.py',)
FILES = ('core_business.py', 'core_runtime.py', 'core_release_guard.py', 'ai_runtime.py',
         'ai_release_guard.py', 'vmm_controller.py', 'assistant_rollout_binding.py')
OPERATIONAL = ('guard', 'ai-guard', 'lifecycle', 'policy', 'handoff')
PREIMAGE_NAMES = {
    'core_current_sha256': 'ILLUSTRATION_CORE_CURRENT.json',
    'core_active_sha256': 'ILLUSTRATION_CORE_ACTIVE.json',
    'ai_current_sha256': 'ILLUSTRATION_AI_CURRENT.json',
    'ai_active_sha256': 'ILLUSTRATION_AI_ACTIVE.json',
    'control_active_sha256': 'CONTROL_UPGRADE_ACTIVE.json',
}
PRESERVED = {'release-jar', 'preview-jar', 'manifest', 'application'}
PHASES = {'PREPARED', 'WATCHDOG_STOP_INTENT', 'INSTALL_INTENT', 'VERIFY_INTENT',
          'INSTALLED', 'RESTORE_INTENT', 'RESTORED', 'RECOVERY_REQUIRED'}
SERVICES = {'java': 19080, 'guide': 19084, 'critic': 19085}


def must(condition, reason):
    if not condition:
        raise Refused(reason)


def _fields(value, expected, reason):
    must(type(value) is dict and value.keys() == expected, reason)


def _hashes(value, *, short=()):
    for name, digest in value.items():
        guard.digest_value(digest, 40 if name in short else 64)


def _run(value):
    for key in ('run_id', 'run_attempt'):
        item = value[key]
        must(type(item) is str and re.fullmatch('[1-9][0-9]{0,19}', item), 'assistant_rollout_run_identity')


def validate(plan):
    successor = plan.get('schema') == SUCCESSOR
    _fields(plan, {
        'schema', 'from_policy_schema', 'deploy', 'current_release', 'current_ai_release',
        'installed_control', 'frontend', 'preserved', 'services', 'next_owner', 'controller_owner',
        'private_owner', 'files', 'operational', 'review_evidence_sha256', 'assistant_rollout',
    } | ({'successor'} if successor else set()), 'assistant_rollout_plan_fields')
    must(plan['schema'] in {SCHEMA, SUCCESSOR} and plan['from_policy_schema'] == guard.BUSINESS_POLICY_SCHEMA
         and plan['next_owner'] == 'c5ea', 'assistant_rollout_plan_contract')
    owner(plan['controller_owner'])
    owner(plan['private_owner'])
    guard.digest_value(plan['review_evidence_sha256'])
    formal = plan['deploy']
    _fields(formal, {
        'repository', 'ref', 'commit', 'run_id', 'run_attempt', 'archive_sha256', 'manifest_sha256', 'tool_sha256',
    }, 'assistant_rollout_deploy_fields')
    must(formal['repository'] == 'bailangguihun/auralink-deploy' and formal['ref'] == 'refs/heads/release',
         'assistant_rollout_deploy_repository')
    _run(formal)
    _hashes({key: formal[key] for key in ('commit', 'archive_sha256', 'manifest_sha256', 'tool_sha256')},
            short=('commit',))
    for name, fields in (
        ('current_release', {'commit_sha', 'artifact_sha256', 'accepted_record_sha256'}),
        ('current_ai_release', {'commit_sha', 'source_tree_sha256', 'record_sha256'}),
        ('installed_control', {'approval_sha256', 'journal_sha256'}),
        ('frontend', {'selection_sha256', 'reference_sha256'}),
        ('preserved', PRESERVED),
    ):
        _fields(plan[name], fields, 'assistant_rollout_bound_identity')
        _hashes(plan[name], short=('commit_sha',))
    preserved = plan['preserved']
    must(preserved['release-jar'] == preserved['preview-jar'] == plan['current_release']['artifact_sha256'],
         'assistant_rollout_paired_jars')
    _fields(plan['services'], SERVICES.keys(), 'assistant_rollout_services')
    for identity in plan['services'].values():
        _fields(identity, {'pid', 'ticks'}, 'assistant_rollout_service_identity')
        must(type(identity['pid']) is int and identity['pid'] > 1 and
             type(identity['ticks']) in {int, str} and str(identity['ticks']).isdigit()
             and int(identity['ticks']) > 0, 'assistant_rollout_service_identity')
    _fields(plan['files'], set(files(plan)), 'assistant_rollout_files')
    _fields(plan['operational'], set(OPERATIONAL), 'assistant_rollout_operational')
    for name, entry in entries(plan).items():
        _fields(entry, {'before_sha256', 'after_sha256'}, 'assistant_rollout_file_identity')
        guard.digest_value(entry['after_sha256'])
        if entry['before_sha256'] is None:
            must(not successor and name == 'assistant_rollout_binding.py', 'assistant_rollout_existing_control')
        else:
            guard.digest_value(entry['before_sha256'])
    rollout = plan['assistant_rollout']
    _fields(rollout, set(PREIMAGE_NAMES) | {'core_candidate_manifest_sha256', 'web_commit', 'ai_candidate', 'community'},
            'assistant_rollout_binding_fields')
    _hashes({key: item for key, item in rollout.items() if key not in {'ai_candidate', 'community'}},
            short=('web_commit',))
    candidate = rollout['ai_candidate']
    ai_hashes = {'commit_sha', 'source_tree_sha256', 'candidate_manifest_sha256', 'archive_sha256', 'policy_sha256'}
    must(type(candidate) is dict and candidate.keys() in (
        ai_hashes | {'github_run_id', 'github_run_attempt'},
        ai_hashes | {'github_run_id', 'github_run_attempt', 'record_sha256'},
    ), 'assistant_rollout_ai_candidate')
    _hashes({key: value for key, value in candidate.items() if not key.startswith('github_run_')},
            short=('commit_sha',))
    _run({'run_id': candidate['github_run_id'], 'run_attempt': candidate['github_run_attempt']})
    community = rollout['community']
    _fields(community, {'code_controller_sha256', 'code_plan_sha256', 'deploy', 'cli_sha256'},
            'assistant_rollout_community')
    _hashes({key: value for key, value in community.items() if key != 'deploy'})
    deploy = community['deploy']
    _fields(deploy, formal.keys() - {'tool_sha256'}, 'assistant_rollout_community_deploy_fields')
    must(all(deploy[name] == formal[name] for name in ('repository', 'ref', 'commit', 'run_id', 'run_attempt')),
         'assistant_rollout_community_formal_run')
    _hashes({key: deploy[key] for key in ('archive_sha256', 'manifest_sha256')})
    if successor:
        validate_successor(plan['successor'])
    return plan


def validate_successor(value):
    _fields(value, {'candidate', 'web', 'core_journal_sha256', 'ai_journal_sha256', 'ai_snapshot_sha256'},
            'illustration_successor_fields')
    for key in ('core_journal_sha256', 'ai_journal_sha256', 'ai_snapshot_sha256'):
        guard.digest_value(value[key])
    candidate = value['candidate']
    _fields(candidate, {'repository', 'commit_sha', 'github_run_id', 'github_run_attempt',
                       'artifact_sha256', 'artifact_bytes', 'payload_fingerprint'}, 'illustration_successor_candidate')
    must(candidate['repository'] == 'bailangguihun/auralink-core-api' and
         type(candidate['artifact_bytes']) is int and 0 < candidate['artifact_bytes'] < 512 * 1024 * 1024,
         'illustration_successor_candidate_identity')
    _hashes({key: candidate[key] for key in ('commit_sha', 'artifact_sha256', 'payload_fingerprint')},
            short=('commit_sha',))
    _run({'run_id': candidate['github_run_id'], 'run_attempt': candidate['github_run_attempt']})
    web = value['web']
    _fields(web, {'commit_sha', 'selection_sha256', 'reference_sha256', 'github_run_id',
                  'github_run_attempt', 'archive_sha256', 'build_id', 'receipt_stamp', 'receipt_sha256'},
            'illustration_successor_web_fields')
    _hashes({name: web[name] for name in ('commit_sha', 'selection_sha256', 'reference_sha256',
                                         'archive_sha256', 'receipt_sha256')}, short=('commit_sha',))
    _run({'run_id': web['github_run_id'], 'run_attempt': web['github_run_attempt']})
    must(type(web['build_id']) is str and re.fullmatch('[A-Za-z0-9._-]+', web['build_id']) and
         type(web['receipt_stamp']) is str and re.fullmatch('[1-9][0-9]{17,19}', web['receipt_stamp']),
         'illustration_successor_web_identity')


def files(plan):
    return FILES + SUCCESSOR_FILES if plan.get('schema') == SUCCESSOR else FILES


def entries(plan):
    return {name: plan['files'][name] for name in files(plan)} | {
        name: plan['operational'][name] for name in OPERATIONAL
    }


def targets(plan):
    operations = guard.ROOT / 'operations'
    private = owner(plan['private_owner'])
    controller = owner(plan['controller_owner'])
    return {
        **{name: (operations / 'r25-publisher' / name, private, 0o600) for name in files(plan)},
        'guard': (operations / 'core_release_guard.py', controller, 0o700),
        'ai-guard': (operations / 'ai_release_guard.py', controller, 0o700),
        'lifecycle': (operations / 'lifecycle_adapter.py', controller, 0o700),
        'policy': (guard.STORE / 'POLICY.json', private, 0o600),
        'handoff': (LIVE_FILES['handoff'], private, 0o600),
    }


def transaction_path(approval_sha256):
    return guard.STORE / 'controller-upgrades' / guard.digest_value(approval_sha256)


def _pointer_paths():
    ai = guard.ROOT / 'runtime/ai-publications'
    return {
        'core_current_sha256': guard.STORE / 'CURRENT.json',
        'core_active_sha256': guard.STORE / 'ACTIVE.json',
        'ai_current_sha256': ai / 'CURRENT.json',
        'ai_active_sha256': ai / 'ACTIVE.json',
        'control_active_sha256': guard.STORE / 'CONTROL_UPGRADE_ACTIVE.json',
    }


def _active_record(approval_sha256):
    return {'schema': 'R25_CORE_CONTROLLER_UPGRADE_ACTIVE_V1', 'approval_sha256': approval_sha256}


def _check_context(rt, plan, approval_sha256, side):
    rt.require_lease()
    rt.require_terminal_application_transactions()
    private = owner(plan['private_owner'])
    rollout = plan['assistant_rollout']
    pointers = _pointer_paths()
    for name, path in pointers.items():
        if name != 'control_active_sha256':
            rt.identity(path, expected_sha256=rollout[name], expected_owner=private)
    must(asdict(rt.current()) == plan['current_release'] and
         rt.read(pointers['ai_current_sha256'])['release'] == plan['current_ai_release'],
         'assistant_rollout_application_reference')
    for name, digest in plan['preserved'].items():
        rt.identity(LIVE_FILES[name], expected_sha256=digest, expected_owner=private)
    for name, extension in (('selection_sha256', 'json'), ('reference_sha256', 'reference')):
        path = guard.ROOT / 'runtime/config' / ('frontend-selection.' + extension)
        rt.identity(path, expected_sha256=plan['frontend'][name], expected_owner=private)
    prior = plan['installed_control']
    prior_folder = transaction_path(prior['approval_sha256'])
    journal = rt.read(prior_folder / 'journal.json', prior['journal_sha256'])
    must(journal.get('approval_sha256') == prior['approval_sha256'] and journal.get('phase') == 'INSTALLED',
         'assistant_rollout_predecessor_controller')
    if plan['schema'] == SUCCESSOR:
        verify_successor_preimages(rt, plan, paths=pointers, predecessor=prior_folder, journal=journal)
    active = pointers['control_active_sha256']
    observed = rt.identity(active, expected_owner=private)['sha256']
    before = rollout['control_active_sha256']
    after = hashlib.sha256(guard.encoded(_active_record(approval_sha256))).hexdigest()
    allowed = {'before': {before}, 'after': {after}, 'mixed': {before, after}}[side]
    must(observed in allowed, 'assistant_rollout_controller_pointer_drift')
    expected = _active_record(prior['approval_sha256'] if observed == before else approval_sha256)
    must(rt.read(active, observed) == expected, 'assistant_rollout_controller_pointer_identity')
    return observed


def verify_successor_preimages(rt, plan, *, paths, predecessor, journal):
    prior = rt.read(predecessor / 'approval.json', plan['installed_control']['approval_sha256'])
    evidence = plan['successor']
    rollout = plan['assistant_rollout']
    must(prior.get('schema') == journal.get('schema') == SCHEMA and
         prior['operational']['policy']['after_sha256'] == plan['operational']['policy']['before_sha256'] and
         prior['assistant_rollout']['web_commit'] == rollout['web_commit'] and
         all(prior['assistant_rollout']['community'][name] == rollout['community'][name]
             for name in ('code_controller_sha256', 'code_plan_sha256')),
         'illustration_successor_controller_parent')
    verify_successor_web_preimage(plan['frontend'], evidence['web'])
    for name in ('core', 'ai'):
        active = rt.read(paths[name + '_active_sha256'], rollout[name + '_active_sha256'])
        tx = guard.digest_value(active.get('transaction_id'), 32)
        folder = paths[name + '_active_sha256'].parent / 'transactions' / tx
        receipt = rt.read(folder / 'journal.json', evidence[name + '_journal_sha256'])
        must(receipt.get('phase') == 'COMMITTED' and receipt.get('transaction_id') == tx and
             receipt.get('candidate') == plan['current_release' if name == 'core' else 'current_ai_release'],
             'illustration_successor_parent_commit')
        if name == 'ai':
            rt.identity(folder / 'snapshot.json', expected_sha256=evidence['ai_snapshot_sha256'])


def verify_successor_web_preimage(before, web):
    stage = guard.LOCK.parent / ('frontend-selection-transition-' + web['receipt_stamp'])
    state = guard.private_record(stage / 'state.json', web['receipt_sha256'])
    must(state.get('state') == 'COMMITTED' and state.get('expected_commit') == web['commit_sha'] and
         all(web[name] != before[name] for name in ('selection_sha256', 'reference_sha256')),
         'illustration_successor_web_preimage')
    guard.verify_rollout_web_receipt(stage, state, before, web, paths=guard.frontend_paths(), artifact=web)


def _target_sha(rt, path, target_owner, mode):
    if not path.exists() and not path.is_symlink():
        rt.storage.validate_target(path, exists=False)
        return None
    return rt.identity(path, expected_owner=target_owner, expected_mode=mode)['sha256']


def _target_hashes(rt, plan, side):
    result = {}
    for name, (path, target_owner, mode) in targets(plan).items():
        if name.endswith('.py') or mode == 0o700:
            guard.reject_legacy_bytecode(path)
        entry = entries(plan)[name]
        allowed = set(entry.values()) if side == 'mixed' else {entry[side + '_sha256']}
        observed = _target_sha(rt, path, target_owner, mode)
        must(observed in allowed, 'assistant_rollout_foreign_target:' + name)
        result[name] = observed
    return result


def _validate_materials(rt, plan, folder, *, before=False, after=True):
    rt.read(folder / 'review.json', plan['review_evidence_sha256'])
    for name, entry in entries(plan).items():
        if after:
            rt.identity(folder / 'after' / name, expected_sha256=entry['after_sha256'])
        if before and entry['before_sha256'] is not None:
            rt.identity(folder / 'before' / name, expected_sha256=entry['before_sha256'])
    if before:
        for key, name in PREIMAGE_NAMES.items():
            rt.identity(folder / 'before' / name, expected_sha256=plan['assistant_rollout'][key])


def _save_before(rt, plan, folder):
    rt.storage.mkdir_private(folder / 'before')
    for name, (path, target_owner, mode) in targets(plan).items():
        digest = entries(plan)[name]['before_sha256']
        if digest is not None:
            immutable_copy(rt, path, folder / 'before' / name, digest,
                           source_owner=target_owner, source_mode=mode)
    for key, path in _pointer_paths().items():
        immutable_copy(rt, path, folder / 'before' / PREIMAGE_NAMES[key], plan['assistant_rollout'][key])
    retained = guard.STORE / 'core-followup-baselines'
    rt.storage.mkdir_private(retained)
    digest = plan['operational']['policy']['before_sha256']
    immutable_copy(rt, folder / 'before/policy', retained / (digest + '.json'), digest)


def _record(rt, path, approval_sha256, phase, *, prior=None, failure=None, schema=SCHEMA):
    history = [] if prior is None else rt.read(path, prior)['history']
    event = {'phase': phase, 'at_unix': int(time.time())}
    if failure is not None:
        event['failure_class'] = type(failure).__name__
    history.append(event)
    must(len(history) <= 1000, 'assistant_rollout_journal_history_limit')
    raw = guard.encoded({'schema': schema, 'approval_sha256': approval_sha256,
                         'phase': phase, 'history': history})
    return rt.storage.atomic_write_bytes(path, raw, replace=prior is not None,
                                         expected_existing_sha256=prior)['sha256']


def _read_journal(rt, path, approval_sha256, schema=SCHEMA):
    if not path.exists() and not path.is_symlink():
        rt.storage.validate_target(path, exists=False)
        return None, None
    digest = rt.identity(path)['sha256']
    value = rt.read(path, digest)
    phase = value.get('phase')
    history = value.get('history')
    must(value.keys() == {'schema', 'approval_sha256', 'phase', 'history'}
         and value['schema'] == schema and value['approval_sha256'] == approval_sha256
         and phase in PHASES and type(history) is list and 0 < len(history) <= 1000
         and type(history[-1]) is dict and history[-1].get('phase') == phase,
         'assistant_rollout_controller_journal')
    return phase, digest


def _replace(rt, plan, folder, observed, side):
    order = tuple(entries(plan))
    if side == 'before':
        order = reversed(order)
    target_paths = targets(plan)
    for name in order:
        path, target_owner, mode = target_paths[name]
        wanted = entries(plan)[name][side + '_sha256']
        actual = observed[name]
        if wanted == actual:
            continue
        if wanted is None:
            rt.storage.remove_bound_file(path, actual, expected_owner=target_owner)
        else:
            rt.storage.atomic_copy_file(folder / side / name, path, wanted,
                                        replace=actual is not None, expected_existing_sha256=actual,
                                        target_owner=target_owner, mode=mode,
                                        existing_owner=target_owner, existing_mode=mode)


def _services(engine):
    observed = {}
    for name, port in SERVICES.items():
        value = engine.active(name, port)
        must(value is not None, 'assistant_rollout_service_missing:' + name)
        observed[name] = {'pid': value['pid'], 'ticks': value['ticks']}
    return observed


def _community(rt, plan, engine):
    must(_services(engine) == plan['services'], 'assistant_rollout_service_drift')
    engine.frontend('status', rt.fd)
    value = engine.community_action('preserve', rt.fd)
    must(type(value) is dict and value.get('state') == 'READY_PRESERVED', 'assistant_rollout_community_not_preserved')
    return value


def _verify_services(rt, plan, folder):
    rt.verify_current(rt.current())
    engine = rt.engine()
    expected = rt.read(folder / 'community-maintenance.json')
    must(_community(rt, plan, engine) == expected, 'assistant_rollout_community_drift')
    return engine


def _resume_watchdog(engine):
    if engine.watchdog_identity() is None:
        engine.watchdog_start_locked()
    must(engine.watchdog_identity() is not None, 'assistant_rollout_watchdog_not_running')


def _recovery_engine(rt, plan, folder):
    private = owner(plan['private_owner'])
    path = folder / 'before/lifecycle'
    original = guard.read_file(path, owner=private, expected=plan['operational']['lifecycle']['before_sha256'])
    module = guard.source_module(path, 'r25_assistant_rollout_recovery_lifecycle', original)
    os.environ['R25_ROOT'] = str(guard.ROOT)
    os.environ['ARTLIVE_R25_HANDOFF_CONFIG'] = str(LIVE_FILES['handoff'])
    engine = module.production(rt.fd)
    engine.v = env_values(guard.read_file(folder / 'before/handoff', owner=private,
                                         expected=plan['operational']['handoff']['before_sha256']))
    engine.kind = engine.v.get('R25_LIFECYCLE_RELEASE_KIND')
    engine.load_execution_policy()
    return engine


def _failure(rt, path, approval_sha256, error, schema):
    try:
        phase, digest = _read_journal(rt, path, approval_sha256, schema)
        if phase not in {'INSTALLED', 'RESTORED'}:
            _record(rt, path, approval_sha256, 'RECOVERY_REQUIRED', prior=digest, failure=error, schema=schema)
    except Exception as journal_error:
        if hasattr(error, 'add_note'):
            error.add_note('R25 recovery journal write failed: ' + type(journal_error).__name__)


def install(rt, *, approval_sha256):
    return _run_install(rt, approval_sha256, recover_only=False)


def recover(rt, *, approval_sha256):
    return _run_install(rt, approval_sha256, recover_only=True)


def _run_install(rt, approval_sha256, *, recover_only):
    folder = transaction_path(approval_sha256)
    path = folder / 'journal.json'
    with upgrade_lease(rt):
        plan = validate(rt.read(folder / 'approval.json', approval_sha256))
        phase, journal_sha = _read_journal(rt, path, approval_sha256, plan['schema'])
        _validate_materials(rt, plan, folder, before=phase is not None,
                            after=not recover_only or phase == 'INSTALLED')
        if phase in {'INSTALLED', 'RESTORED'}:
            must(phase == 'INSTALLED' or recover_only, 'assistant_rollout_approval_already_restored')
            side = 'after' if phase == 'INSTALLED' else 'before'
            _check_context(rt, plan, approval_sha256, side)
            _target_hashes(rt, plan, side)
            _resume_watchdog(_verify_services(rt, plan, folder))
            return phase + '_VERIFIED_NOOP'
        if recover_only:
            must(phase is not None, 'assistant_rollout_not_started')
            return _restore(rt, plan, folder, path, approval_sha256, journal_sha)
        must(phase is None, 'assistant_rollout_explicit_recovery_required')
        return _install(rt, plan, folder, path, approval_sha256)


def _install(rt, plan, folder, path, approval_sha256):
    old_pointer = _check_context(rt, plan, approval_sha256, 'before')
    observed = _target_hashes(rt, plan, 'before')
    rt.verify_current(rt.current())
    rt.require_no_active_work()
    engine = rt.engine()
    must(engine.watchdog_identity() is not None, 'assistant_rollout_original_watchdog_required')
    checkpoint = _community(rt, plan, engine)
    _save_before(rt, plan, folder)
    proof = folder / 'community-maintenance.json'
    if proof.exists():
        must(rt.read(proof) == checkpoint, 'assistant_rollout_community_checkpoint_drift')
    else:
        rt.storage.atomic_write_json(proof, checkpoint)
    journal_sha = _record(rt, path, approval_sha256, 'PREPARED', schema=plan['schema'])
    try:
        active = _pointer_paths()['control_active_sha256']
        rt.storage.atomic_write_bytes(active, guard.encoded(_active_record(approval_sha256)), replace=True,
                                      expected_existing_sha256=old_pointer)
        journal_sha = _record(rt, path, approval_sha256, 'WATCHDOG_STOP_INTENT', prior=journal_sha, schema=plan['schema'])
        engine.watchdog_stop_locked()
        must(engine.watchdog_identity() is None, 'assistant_rollout_watchdog_not_stopped')
        journal_sha = _record(rt, path, approval_sha256, 'INSTALL_INTENT', prior=journal_sha, schema=plan['schema'])
        _replace(rt, plan, folder, observed, 'after')
        _check_context(rt, plan, approval_sha256, 'after')
        _target_hashes(rt, plan, 'after')
        journal_sha = _record(rt, path, approval_sha256, 'VERIFY_INTENT', prior=journal_sha, schema=plan['schema'])
        engine = _verify_services(rt, plan, folder)
        _record(rt, path, approval_sha256, 'INSTALLED', prior=journal_sha, schema=plan['schema'])
        _resume_watchdog(engine)
        return 'INSTALLED'
    except BaseException as error:
        _failure(rt, path, approval_sha256, error, plan['schema'])
        raise


def _restore(rt, plan, folder, path, approval_sha256, journal_sha):
    control = _check_context(rt, plan, approval_sha256, 'mixed')
    observed = _target_hashes(rt, plan, 'mixed')
    old_pointer = plan['assistant_rollout']['control_active_sha256']
    try:
        engine = _recovery_engine(rt, plan, folder)
        must(_services(engine) == plan['services'], 'assistant_rollout_service_drift')
        if engine.watchdog_identity() is not None:
            engine.watchdog_stop_locked()
        must(engine.watchdog_identity() is None, 'assistant_rollout_watchdog_not_stopped')
        journal_sha = _record(rt, path, approval_sha256, 'RESTORE_INTENT', prior=journal_sha, schema=plan['schema'])
        _replace(rt, plan, folder, observed, 'before')
        if control != old_pointer:
            active = _pointer_paths()['control_active_sha256']
            rt.storage.atomic_copy_file(folder / 'before/CONTROL_UPGRADE_ACTIVE.json', active, old_pointer,
                                        replace=True, expected_existing_sha256=control)
        _check_context(rt, plan, approval_sha256, 'before')
        _target_hashes(rt, plan, 'before')
        engine = _verify_services(rt, plan, folder)
        _record(rt, path, approval_sha256, 'RESTORED', prior=journal_sha, schema=plan['schema'])
        _resume_watchdog(engine)
        return 'RESTORED'
    except BaseException as error:
        _failure(rt, path, approval_sha256, error, plan['schema'])
        raise
