"""Identity checks for one approved, source-only Assistant rollout.

Callers hold the original publication lease and independently validate the
policy and controller approval. The pure binders consume verified documents.
The Core verifier also reads its fixed protected records and live JARs. Callers
verify process ownership before any start.
"""

from collections.abc import Mapping
from pathlib import Path
import re


class BindingRefused(RuntimeError):
    pass


AI_REFERENCE = {'commit_sha', 'source_tree_sha256', 'record_sha256'}
CORE_REFERENCE = {'commit_sha', 'artifact_sha256', 'accepted_record_sha256'}
RUN_FIELDS = {'github_run_id', 'github_run_attempt'}
AI_PROVENANCE = {
    'commit_sha', 'source_tree_sha256', 'candidate_manifest_sha256', 'archive_sha256', 'policy_sha256',
} | RUN_FIELDS
CORE_PROVENANCE = {
    'repository', 'commit_sha', 'artifact_sha256', 'artifact_bytes', 'payload_fingerprint',
    'candidate_manifest_sha256', 'policy_sha256',
} | RUN_FIELDS
BEFORE_PHASES = {'PREPARED', 'QUIESCE_INTENT', 'QUIESCED', 'STOP_INTENT', 'STOPPED'}
CANDIDATE_PHASES = {'START_INTENT', 'ACCEPTED', 'CURRENT_COMMIT_INTENT', 'COMMITTED'}
RECOVERY_PHASES = {'RECOVERY_REQUIRED', 'RESTORE_STOP_INTENT', 'RESTORE_STOPPED', 'RESTORE_INTENT'}
TRANSITION_PHASES = {'INSTALL_INTENT', 'INSTALLED'} | RECOVERY_PHASES
CORE_BEFORE_PHASES = {'PREPARED', 'STOP_INTENT', 'STOPPED'}
CORE_JARS = {'release-jar', 'preview-jar'}
CORE_ROOT = Path('/root/autodl-tmp/artlive-r25-native-pg16/handoff-production-20260923T062101Z')
CORE_PREVIEW = Path('/root/autodl-tmp/artlive/backend-preview/r25-isolated-preview-7694f4069c83c06c')


def _require(condition, reason):
    if not condition:
        raise BindingRefused(reason)


def _digest(value, length=64):
    return isinstance(value, str) and re.fullmatch(f'[a-f0-9]{{{length}}}', value) is not None


def _reference(value, fields):
    _require(
        isinstance(value, Mapping) and value.keys() == fields
        and all(_digest(digest, 40 if key == 'commit_sha' else 64) for key, digest in value.items()),
        'rollout_release_reference',
    )
    return dict(value)


def _bind_accepted(approved, accepted, record_sha256, *, fields, reference, record_key, schema, repository):
    _require(
        isinstance(approved, Mapping) and approved.keys() in (fields, fields | {record_key})
        and _digest(record_sha256) and approved.get(record_key, record_sha256) == record_sha256,
        'rollout_approved_candidate_identity',
    )
    for key, value in approved.items():
        if key in RUN_FIELDS:
            _require(isinstance(value, str) and re.fullmatch('[1-9][0-9]{0,19}', value), 'rollout_candidate_run')
        elif key == 'repository':
            _require(value == repository, 'rollout_candidate_repository')
        elif key == 'artifact_bytes':
            _require(type(value) is int and 0 < value < 512 * 1024 * 1024, 'rollout_candidate_size')
        else:
            _require(_digest(value, 40 if key == 'commit_sha' else 64), 'rollout_candidate_digest')
    _require(
        isinstance(accepted, Mapping) and accepted.get('schema') == schema
        and accepted.get('repository') == repository
        and all(type(accepted.get(key)) is type(approved[key]) and accepted[key] == approved[key] for key in fields),
        'rollout_accepted_candidate_drift',
    )
    return {key: record_sha256 if key == record_key else approved[key] for key in reference}


def bind_ai_candidate(approved, accepted, *, record_sha256):
    """Return the staged V2 release reference for independently approved provenance."""
    return _bind_accepted(
        approved, accepted, record_sha256, fields=AI_PROVENANCE, reference=AI_REFERENCE,
        record_key='record_sha256', schema='R25_AI_ACCEPTED_SOURCE_V2',
        repository='bailangguihun/auralink-ai-service',
    )


def bind_core_candidate(approved, accepted, *, record_sha256):
    """Return the staged Core release reference for independently approved provenance."""
    return _bind_accepted(
        approved, accepted, record_sha256, fields=CORE_PROVENANCE, reference=CORE_REFERENCE,
        record_key='accepted_record_sha256', schema='R25_CORE_BUSINESS_RELEASE_V2',
        repository='bailangguihun/auralink-core-api',
    )


def _before_reference(current, component, reference_fields):
    _require(
        isinstance(current, Mapping)
        and current.keys() == {'schema', 'release', 'previous', 'transaction_id'}
        and current['schema'] == f'R25_{component}_CURRENT_V1'
        and _digest(current['transaction_id'], 32),
        'rollout_predecessor_current',
    )
    if current['previous'] is not None:
        _reference(current['previous'], reference_fields)
    return _reference(current['release'], reference_fields)


def _active_transaction(active, component):
    _require(
        isinstance(active, Mapping) and active.keys() == {'schema', 'transaction_id'}
        and active['schema'] == f'R25_{component}_ACTIVE_V1' and _digest(active['transaction_id'], 32),
        'rollout_active_transaction',
    )
    return active['transaction_id']


def _is_predecessor(current, before_current, before, journal, tx):
    return current == before_current and (
        (journal['phase'] == 'COMMITTED' and journal.get('candidate') == before
         and before_current['transaction_id'] == tx)
        or (journal['phase'] == 'RESTORED' and journal.get('current') == before)
    )


def select_ai_reference(
    *, before_current, before_active, candidate, core_before, core_after, core_current,
    current, active, journal, snapshot=None, snapshot_sha256=None, for_start=True, web_approved=False,
):
    """Select a complete AI source side, or None for an explicit recovery inspection.

    The before documents come from the verified historical AI proof. Candidate
    comes from bind_ai_candidate, and web_approved from the verified Web
    publication. snapshot_sha256 is the digest of the protected snapshot bytes.
    """
    before = _before_reference(before_current, 'AI', AI_REFERENCE)
    old_tx = _active_transaction(before_active, 'AI')
    tx = _active_transaction(active, 'AI')
    _require(
        isinstance(journal, Mapping) and journal.get('schema') == 'R25_AI_TRANSACTION_V1'
        and journal.get('transaction_id') == tx and isinstance(journal.get('phase'), str),
        'rollout_ai_journal',
    )
    phase = journal['phase']
    if tx == old_tx:
        _require(
            _is_predecessor(current, before_current, before, journal, old_tx),
            'rollout_ai_predecessor_transaction',
        )
        return before

    core_before = _reference(core_before, CORE_REFERENCE)
    core_after = _reference(core_after, CORE_REFERENCE)
    _require(
        core_before['commit_sha'] != core_after['commit_sha']
        and core_before['artifact_sha256'] != core_after['artifact_sha256'],
        'rollout_ai_core_identity',
    )
    after = _reference(candidate, AI_REFERENCE)
    _require(
        after['commit_sha'] != before['commit_sha'] and after['source_tree_sha256'] != before['source_tree_sha256']
        and journal.get('current') == before and journal.get('candidate') == after,
        'rollout_ai_transaction_identity',
    )
    _require(
        _digest(snapshot_sha256) and journal.get('snapshot_sha256') == snapshot_sha256
        and isinstance(snapshot, Mapping) and snapshot.get('schema') == 'R25_AI_SNAPSHOT_V1'
        and snapshot.get('transaction_id') == tx
        and snapshot.get('current') == before and snapshot.get('candidate') == after
        and snapshot.get('before_current') == before_current and snapshot.get('core') == core_after
        and snapshot.get('changed_roles') == ['critic'] and snapshot.get('created_directories') == []
        and 'configuration_transition' not in snapshot,
        'rollout_ai_snapshot_identity',
    )
    new_current = {'schema': 'R25_AI_CURRENT_V1', 'release': after, 'previous': before, 'transaction_id': tx}
    if phase in BEFORE_PHASES | {'INSTALL_INTENT', 'INSTALLED', 'START_INTENT', 'ACCEPTED', 'RESTORED'}:
        _require(current == before_current, 'rollout_ai_current_phase')
    elif phase == 'COMMITTED':
        _require(current == new_current, 'rollout_ai_current_phase')
    else:
        _require(current in (before_current, new_current), 'rollout_ai_current_phase')

    if phase == 'RESTORED':
        return before
    _require(core_current == core_after, 'rollout_ai_core_after_required')
    if phase in BEFORE_PHASES | {'RESTORE_START_INTENT'}:
        return before
    if phase in CANDIDATE_PHASES:
        _require(for_start is False or web_approved is True, 'rollout_ai_web_after_required')
        return after
    _require(phase in TRANSITION_PHASES and for_start is False, 'rollout_ai_recovery_required')
    return None


def select_core_reference(
    *, before_current, before_active, candidate, current, active, journal, jar_sha256,
    snapshot=None, snapshot_sha256=None, preserve_only=False,
):
    """Select a stable Core release or inspect a bounded preservation phase.

    Pass digests read from both owned live JARs. A transitional match applies
    only to preserving an existing Community process. None permits inspecting
    known preimages without authorizing either Core release. Callers separately
    verify the running processes, recorded acceptance and the held lease.
    """
    before = _before_reference(before_current, 'CORE', CORE_REFERENCE)
    old_tx = _active_transaction(before_active, 'CORE')
    tx = _active_transaction(active, 'CORE')
    _require(
        isinstance(journal, Mapping) and journal.get('schema') == 'R25_CORE_TRANSACTION_V1'
        and journal.get('transaction_id') == journal.get('snapshot_id') == tx
        and isinstance(journal.get('phase'), str),
        'rollout_core_journal',
    )
    _require(isinstance(jar_sha256, Mapping) and jar_sha256.keys() == CORE_JARS, 'rollout_core_paired_jars')
    old_jar = before['artifact_sha256']
    old_bytes = all(digest == old_jar for digest in jar_sha256.values())
    phase = journal['phase']
    if tx == old_tx:
        _require(
            old_bytes and _is_predecessor(current, before_current, before, journal, old_tx),
            'rollout_core_predecessor_transaction',
        )
        return before

    after = _reference(candidate, CORE_REFERENCE)
    new_jar = after['artifact_sha256']
    _require(
        before['commit_sha'] != after['commit_sha'] and old_jar != new_jar
        and journal.get('current') == before and journal.get('candidate') == after,
        'rollout_core_transaction_identity',
    )
    _require(
        _digest(snapshot_sha256) and journal.get('snapshot_sha256') == snapshot_sha256
        and isinstance(snapshot, Mapping) and snapshot.get('schema') == 'R25_CORE_SNAPSHOT_V1'
        and snapshot.get('transaction_id') == tx
        and snapshot.get('current') == before and snapshot.get('candidate') == after
        and snapshot.get('before_current') == before_current,
        'rollout_core_snapshot_identity',
    )
    files = snapshot.get('files')
    _require(
        isinstance(files, Mapping) and all(
            isinstance(files.get(name), Mapping) and files[name].get('sha256') == old_jar
            and files[name].get('after_sha256') == new_jar
            for name in CORE_JARS
        ),
        'rollout_core_snapshot_jars',
    )
    _require(
        all(digest in (old_jar, new_jar) for digest in jar_sha256.values()),
        'rollout_core_foreign_jar',
    )
    new_bytes = all(digest == new_jar for digest in jar_sha256.values())
    new_current = {'schema': 'R25_CORE_CURRENT_V1', 'release': after, 'previous': before, 'transaction_id': tx}
    if phase == 'COMMITTED':
        _require(current == new_current and new_bytes, 'rollout_core_committed_current')
        return after
    if phase == 'RESTORED':
        _require(current == before_current and old_bytes, 'rollout_core_restored_current')
        return before

    _require(preserve_only is True, 'rollout_core_publication_in_flight')
    _require(current in (before_current, new_current), 'rollout_core_current_phase')
    if phase in CORE_BEFORE_PHASES:
        _require(current == before_current and old_bytes, 'rollout_core_before_install')
        return before
    if phase in {'START_INTENT', 'ACCEPTED', 'CURRENT_COMMIT_INTENT'}:
        _require(new_bytes and (current == before_current or phase == 'CURRENT_COMMIT_INTENT'),
                 'rollout_core_after_install')
        return after
    if phase in {'INSTALL_INTENT', 'INSTALLED'}:
        _require(current == before_current and (phase == 'INSTALL_INTENT' or new_bytes),
                 'rollout_core_install_phase')
        return None
    _require(phase in RECOVERY_PHASES, 'rollout_core_recovery_phase')
    if phase == 'RESTORE_INTENT' and old_bytes:
        return before
    return None


def verify_assistant_rollout_core(
    value, approval, folder, *, preserve_only=False, storage_type=None, storage=None, _layout=None,
):
    """Read exact Core transaction proof under the caller's original lease.

    The caller validates the raw Core policy, the approved controller plan and
    its active installation phase independently. Pass a verified storage type
    when loading this module from pinned source in a standalone guard.
    """
    store = CORE_ROOT / 'runtime/core-publications'
    jars = {
        'release-jar': CORE_ROOT / 'release/artifacts/backend.jar',
        'preview-jar': CORE_PREVIEW / 'release/backend.jar',
    }
    if _layout is not None:
        _require(storage is not None, 'rollout_core_fixture_storage')
        store, jars = _layout
    if storage is None:
        if storage_type is None:
            from r25_storage import R25Storage
            storage_type = R25Storage
        storage = storage_type((str(CORE_ROOT), str(CORE_PREVIEW)), private_roots=(str(store),))

    try:
        return _read_core_proof(value, approval, Path(folder), store, jars, storage, preserve_only)
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise BindingRefused('rollout_core_evidence_unreadable') from error


def _read_core_proof(value, approval, folder, store, jars, storage, preserve_only):
    read = storage.read_private_record
    _require(folder.parent == store / 'controller-upgrades' and _digest(folder.name),
             'rollout_core_controller_folder')
    _require(approval['schema'] == 'R25_ASSISTANT_ILLUSTRATION_CORE_PUBLICATION_BINDING_V1'
             and read(folder / 'approval.json', expected_sha256=folder.name) == approval,
             'rollout_core_controller_approval')
    policy_sha256 = approval['operational']['policy']['after_sha256']
    upgrade = value['upgrade']
    _require(_digest(policy_sha256) and value['schema'] == 'R25_CORE_BUSINESS_POLICY_V2'
             and upgrade['core_followup']['schema'] == 'R25_ASSISTANT_ILLUSTRATION_CORE_PUBLICATION_V1'
             and read(store / 'POLICY.json', expected_sha256=policy_sha256) == value,
             'rollout_core_approved_policy')

    plan = approval['assistant_rollout']
    _require(all(_digest(plan[key]) for key in (
        'core_current_sha256', 'core_active_sha256', 'core_candidate_manifest_sha256',
    )), 'rollout_core_approval_digests')
    before = folder / 'before'
    before_current = read(before / 'ILLUSTRATION_CORE_CURRENT.json', expected_sha256=plan['core_current_sha256'])
    before_active = read(before / 'ILLUSTRATION_CORE_ACTIVE.json', expected_sha256=plan['core_active_sha256'])
    before_release = _before_reference(before_current, 'CORE', CORE_REFERENCE)
    old_tx = _active_transaction(before_active, 'CORE')
    _require(before_release == upgrade['baseline_release'] == approval['current_release'],
             'rollout_core_approved_baseline')

    current = read(store / 'CURRENT.json')
    active = read(store / 'ACTIVE.json')
    tx = _active_transaction(active, 'CORE')
    transaction = store / 'transactions' / tx
    journal = read(transaction / 'journal.json')
    snapshot = snapshot_sha256 = candidate = None
    if tx != old_tx:
        approved = upgrade['candidate']
        reference = _reference(journal['candidate'], CORE_REFERENCE)
        _require(reference['commit_sha'] == approved['commit_sha']
                 and reference['artifact_sha256'] == approved['artifact_sha256'],
                 'rollout_core_unapproved_transaction')
        record_sha256 = reference['accepted_record_sha256']
        release_dir = reference['artifact_sha256'] + '-' + reference['commit_sha']
        record = read(store / 'releases' / release_dir / 'record.json', expected_sha256=record_sha256)
        candidate = bind_core_candidate(
            {**approved, 'candidate_manifest_sha256': plan['core_candidate_manifest_sha256'],
             'policy_sha256': policy_sha256},
            record, record_sha256=record_sha256,
        )
        snapshot_sha256 = journal['snapshot_sha256']
        _require(_digest(snapshot_sha256), 'rollout_core_snapshot_digest')
        snapshot = read(transaction / 'snapshot.json', expected_sha256=snapshot_sha256)
        _require(all(snapshot['files'][name]['path'] == str(path) for name, path in jars.items()),
                 'rollout_core_snapshot_jar_paths')

    jar_sha256 = {}
    for name, path in jars.items():
        observed = storage.file_identity(path)
        jar_sha256[name] = observed['sha256']
        if candidate is not None and observed['sha256'] == candidate['artifact_sha256']:
            _require(observed['bytes'] == upgrade['candidate']['artifact_bytes'], 'rollout_core_live_jar_size')
    return select_core_reference(
        before_current=before_current, before_active=before_active, candidate=candidate, current=current,
        active=active, journal=journal, jar_sha256=jar_sha256, snapshot=snapshot,
        snapshot_sha256=snapshot_sha256, preserve_only=preserve_only,
    )
