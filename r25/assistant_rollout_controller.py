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
    _fields(plan, {
        'schema', 'from_policy_schema', 'deploy', 'current_release', 'current_ai_release',
        'installed_control', 'frontend', 'preserved', 'services', 'next_owner', 'controller_owner',
        'private_owner', 'files', 'operational', 'review_evidence_sha256', 'assistant_rollout',
    }, 'assistant_rollout_plan_fields')
    must(plan['schema'] == SCHEMA and plan['from_policy_schema'] == guard.BUSINESS_POLICY_SCHEMA
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
    _fields(plan['files'], set(FILES), 'assistant_rollout_files')
    _fields(plan['operational'], set(OPERATIONAL), 'assistant_rollout_operational')
    for name, entry in entries(plan).items():
        _fields(entry, {'before_sha256', 'after_sha256'}, 'assistant_rollout_file_identity')
        guard.digest_value(entry['after_sha256'])
        if entry['before_sha256'] is None:
            must(name == 'assistant_rollout_binding.py', 'assistant_rollout_existing_control')
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
    return plan


def entries(plan):
    return {name: plan['files'][name] for name in FILES} | {
        name: plan['operational'][name] for name in OPERATIONAL
    }


def targets(plan):
    operations = guard.ROOT / 'operations'
    private = owner(plan['private_owner'])
    controller = owner(plan['controller_owner'])
    return {
        **{name: (operations / 'r25-publisher' / name, private, 0o600) for name in FILES},
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
    active = pointers['control_active_sha256']
    observed = rt.identity(active, expected_owner=private)['sha256']
    before = rollout['control_active_sha256']
    after = hashlib.sha256(guard.encoded(_active_record(approval_sha256))).hexdigest()
    allowed = {'before': {before}, 'after': {after}, 'mixed': {before, after}}[side]
    must(observed in allowed, 'assistant_rollout_controller_pointer_drift')
    expected = _active_record(prior['approval_sha256'] if observed == before else approval_sha256)
    must(rt.read(active, observed) == expected, 'assistant_rollout_controller_pointer_identity')
    return observed


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


def _record(rt, path, approval_sha256, phase, *, prior=None, failure=None):
    history = [] if prior is None else rt.read(path, prior)['history']
    event = {'phase': phase, 'at_unix': int(time.time())}
    if failure is not None:
        event['failure_class'] = type(failure).__name__
    history.append(event)
    must(len(history) <= 1000, 'assistant_rollout_journal_history_limit')
    raw = guard.encoded({'schema': SCHEMA, 'approval_sha256': approval_sha256,
                         'phase': phase, 'history': history})
    return rt.storage.atomic_write_bytes(path, raw, replace=prior is not None,
                                         expected_existing_sha256=prior)['sha256']


def _read_journal(rt, path, approval_sha256):
    if not path.exists() and not path.is_symlink():
        rt.storage.validate_target(path, exists=False)
        return None, None
    digest = rt.identity(path)['sha256']
    value = rt.read(path, digest)
    phase = value.get('phase')
    history = value.get('history')
    must(value.keys() == {'schema', 'approval_sha256', 'phase', 'history'}
         and value['schema'] == SCHEMA and value['approval_sha256'] == approval_sha256
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


def _failure(rt, path, approval_sha256, error):
    try:
        phase, digest = _read_journal(rt, path, approval_sha256)
        if phase not in {'INSTALLED', 'RESTORED'}:
            _record(rt, path, approval_sha256, 'RECOVERY_REQUIRED', prior=digest, failure=error)
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
        phase, journal_sha = _read_journal(rt, path, approval_sha256)
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
    journal_sha = _record(rt, path, approval_sha256, 'PREPARED')
    try:
        active = _pointer_paths()['control_active_sha256']
        rt.storage.atomic_write_bytes(active, guard.encoded(_active_record(approval_sha256)), replace=True,
                                      expected_existing_sha256=old_pointer)
        journal_sha = _record(rt, path, approval_sha256, 'WATCHDOG_STOP_INTENT', prior=journal_sha)
        engine.watchdog_stop_locked()
        must(engine.watchdog_identity() is None, 'assistant_rollout_watchdog_not_stopped')
        journal_sha = _record(rt, path, approval_sha256, 'INSTALL_INTENT', prior=journal_sha)
        _replace(rt, plan, folder, observed, 'after')
        _check_context(rt, plan, approval_sha256, 'after')
        _target_hashes(rt, plan, 'after')
        journal_sha = _record(rt, path, approval_sha256, 'VERIFY_INTENT', prior=journal_sha)
        engine = _verify_services(rt, plan, folder)
        _record(rt, path, approval_sha256, 'INSTALLED', prior=journal_sha)
        _resume_watchdog(engine)
        return 'INSTALLED'
    except BaseException as error:
        _failure(rt, path, approval_sha256, error)
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
        journal_sha = _record(rt, path, approval_sha256, 'RESTORE_INTENT', prior=journal_sha)
        _replace(rt, plan, folder, observed, 'before')
        if control != old_pointer:
            active = _pointer_paths()['control_active_sha256']
            rt.storage.atomic_copy_file(folder / 'before/CONTROL_UPGRADE_ACTIVE.json', active, old_pointer,
                                        replace=True, expected_existing_sha256=control)
        _check_context(rt, plan, approval_sha256, 'before')
        _target_hashes(rt, plan, 'before')
        engine = _verify_services(rt, plan, folder)
        _record(rt, path, approval_sha256, 'RESTORED', prior=journal_sha)
        _resume_watchdog(engine)
        return 'RESTORED'
    except BaseException as error:
        _failure(rt, path, approval_sha256, error)
        raise
