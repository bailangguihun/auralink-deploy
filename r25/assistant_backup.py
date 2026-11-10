"""Scheduled Assistant backup through the existing CoreRuntime lease/lifecycle.

No daemon, provider call, migration, application installation or DB restore.
The fixed job owns only runtime/assistant-backups/assistant-daily-v1. Existing
publication backups and user attachments are never retention targets.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re

import assistant_release as release
import assistant_runtime
import core_release_guard as guard
from core_acceptance import verify_http
from publisher_transaction import Refused

POLICY = {'schema': 'R25_ASSISTANT_BACKUP_POLICY_V1', 'job_id': 'assistant-daily-v1',
          'daily': 7, 'weekly': 4, 'utc_hour': 19, 'utc_minute': 15, 'weekly_day': 5}
SCHEMA = 'R25_ASSISTANT_SCHEDULED_BACKUP_V1'
TERMINAL = {'COMMITTED', 'FAILED_RESUMED', 'RECOVERED_WITHOUT_REPLAY', 'PURGED'}
DATE = re.compile(r'20[0-9]{2}-[0-9]{2}-[0-9]{2}')


def must(ok, reason):
    if not ok:
        raise Refused(reason)


def require_terminal(rt):
    """Called by both Core and AI publishers before any new transaction."""
    path = guard.ROOT / 'runtime/assistant-backups/assistant-daily-v1/ACTIVE.json'
    if not path.exists():
        return
    active = rt.read(path)
    day = active.get('day')
    must(type(day) is str and DATE.fullmatch(day), 'assistant_backup_active_day')
    record = rt.read(path.parent / 'runs' / (day + '.json'))
    must(record.get('schema') == SCHEMA and record.get('day') == day and
         record.get('phase') in TERMINAL, 'assistant_backup_recovery_required')


class ScheduledBackup:
    def __init__(self, rt, contract, *, pg=None, now=None):
        rt.require_lease()
        must(contract.get('backup_policy') == POLICY, 'assistant_backup_policy_not_bound')
        self.rt, self.contract = rt, contract
        self.pg = pg if pg is not None else assistant_runtime.pg_control(contract)
        self.root = guard.ROOT / 'runtime/assistant-backups/assistant-daily-v1'
        self.now = now if now is not None else datetime.now(timezone.utc)
        must(self.now.tzinfo == timezone.utc, 'assistant_backup_utc_clock')
        self.day = self.now.strftime('%Y-%m-%d')

    def check_root(self, *, create=False):
        marker = self.root / 'JOB.json'
        if not self.root.exists():
            must(create, 'assistant_backup_not_started')
            self.rt.storage.mkdir_private(self.root.parent)
            self.rt.storage.mkdir_private(self.root)
            self.rt.storage.mkdir_private(self.root / 'runs')
            self.rt.storage.mkdir_private(self.root / 'sets')
            self.rt.storage.atomic_write_json(marker, POLICY)
        must(self.rt.read(marker) == POLICY, 'assistant_backup_namespace')
        for path in (self.root, self.root / 'runs', self.root / 'sets'):
            self.rt.storage.validate_target(path, exists=True, directory=True,
                                            expected_owner=self.rt.storage.owner, expected_mode=0o700)

    def record_path(self, day):
        must(type(day) is str and DATE.fullmatch(day), 'assistant_backup_day')
        return self.root / 'runs' / (day + '.json')

    def save(self, record, phase, **fields):
        record.update(phase=phase, **fields)
        record['history'].append({'phase': phase, 'at': datetime.now(timezone.utc).isoformat()})
        self.rt.write(self.record_path(record['day']), record)

    def identity(self, record):
        self.rt.require_lease()
        must(asdict(self.rt.current()) == record['release'], 'assistant_backup_current_drift')
        must(self.rt.identity(guard.STORE / 'POLICY.json')['sha256'] == record['policy_sha256'],
             'assistant_backup_policy_drift')
        self.rt.verify_candidate(self.rt.current())
        self.rt.require_compatible(self.rt.current())

    def health(self):
        """Existing public acceptance, not a new authenticated release approval."""
        engine = self.rt.engine()
        must(engine.backend == self.rt.current().artifact_sha256, 'assistant_backup_backend')
        process = engine.active('java', 19080)
        must(process is not None, 'assistant_backup_java_missing')
        self.rt.verify_process_contract(process, self.rt.current())
        engine.frontend('status', self.rt.fd)
        engine.tunnel.status()
        for role, port in (('guide', 19084), ('critic', 19085)):
            must(engine.active(role, port) is not None, 'assistant_backup_paired_service')
        return verify_http(self.rt.query)

    def quiet(self):
        self.rt.require_lease()
        self.rt.require_no_active_work()
        engine = self.rt.engine()
        must(engine.active('java', 19080) is None and engine.watchdog_identity() is None,
             'assistant_backup_not_quiescent')

    def resume(self, record):
        self.identity(record)
        engine = self.rt.engine()
        self.save(record, 'RESUME_INTENT')
        # Only the unchanged accepted artifact; start helper checks owned PID.
        if engine.active('java', 19080) is None:
            engine.start_java_locked(self.rt.fd)
        self.health()
        if engine.watchdog_identity() is None:
            engine.watchdog_start_locked()
        must(engine.watchdog_identity() is not None, 'assistant_backup_watchdog_missing')

    def run(self):
        self.rt.require_terminal_component_transactions()
        current = self.rt.current()
        self.rt.verify_candidate(current)
        self.rt.require_compatible(current)
        self.health()
        self.rt.require_no_active_work()
        must(len(release.inspect_schema(self.pg, self.contract['migrations'])['versions']) == len(self.contract['migrations']),
             'assistant_backup_final_schema_required')
        release.file_inventory(self.contract['attachment_root'])
        self.check_root(create=True)
        if self.record_path(self.day).exists():
            previous = self.rt.read(self.record_path(self.day))
            must(previous['phase'] == 'COMMITTED', 'assistant_backup_day_already_attempted')
            self.verified_files(previous)
            return {'outcome': 'BACKUP_ALREADY_VERIFIED', 'day': self.day}
        record = {'schema': SCHEMA, 'day': self.day, 'weekly': self.now.weekday() == POLICY['weekly_day'],
                  'release': asdict(current), 'policy_sha256': self.rt.identity(guard.STORE / 'POLICY.json')['sha256'],
                  'history': []}
        self.save(record, 'PREPARED')
        self.rt.write(self.root / 'ACTIVE.json', {'schema': SCHEMA, 'day': self.day})
        self.save(record, 'STOP_INTENT')
        try:
            engine = self.rt.engine()
            engine.watchdog_stop_locked()
            engine.stop_role('java', 19080)
            self.quiet()
            self.save(record, 'STOPPED')
            def journal(phase, **evidence):
                self.save(record, phase, **evidence)
            release.backup(self.pg, self.contract['attachment_root'], self.root / 'sets' / self.day,
                           require_quiescent=self.quiet, journal=journal)
            self.verified_files(record)
            self.resume(record)
            self.save(record, 'COMMITTED')
            return {'outcome': 'BACKUP_COMMITTED', 'day': self.day,
                    'manifest_sha256': record['manifest_sha256']}
        except BaseException as error:
            if record['phase'] == 'RESUME_INTENT':
                self.save(record, 'RECOVERY_REQUIRED', failure_class=type(error).__name__)
                raise Refused('assistant_backup_recovery_required') from error
            try:
                self.resume(record)
                self.save(record, 'FAILED_RESUMED', failure_class=type(error).__name__)
            except BaseException as recovery:
                self.save(record, 'RECOVERY_REQUIRED', failure_class=type(error).__name__,
                          recovery_failure_class=type(recovery).__name__)
                raise Refused('assistant_backup_recovery_required') from recovery
            raise Refused('assistant_backup_failed_services_resumed') from error

    def recover(self):
        self.check_root()
        active = self.rt.read(self.root / 'ACTIVE.json')
        record = self.rt.read(self.record_path(active['day']))
        must(record.get('schema') == SCHEMA and record.get('day') == active['day'], 'assistant_backup_journal')
        if record['phase'] in TERMINAL:
            return {'outcome': 'BACKUP_TERMINAL_NO_RECOVERY', 'phase': record['phase']}
        # Never repeats pg_dump, migration or provider dispatch after interruption.
        self.resume(record)
        self.save(record, 'RECOVERED_WITHOUT_REPLAY')
        return {'outcome': 'RECOVERED_WITHOUT_REPLAY', 'day': active['day']}

    def verified_files(self, record, *, missing=False):
        day = record['day']; self.record_path(day)
        base = self.root / 'sets' / day
        manifest = record.get('retention_files')
        if manifest is None:
            document = json.loads(release.regular(base / 'manifest.json', record['manifest_sha256']))
            must(document.get('schema') == 'R25_ASSISTANT_BACKUP_V1', 'assistant_backup_manifest_schema')
            manifest = {'database.dump': document['database_sha256'], 'manifest.json': record['manifest_sha256']}
            for item in document['files']:
                name = item['name']
                must(name in ('.assistant-attachments-v1', release.GENERATION_MARKER) or re.fullmatch(r'[a-f0-9-]{36}\.(blob|part)', name),
                     'assistant_backup_retention_filename')
                must('files/' + name not in manifest, 'assistant_backup_duplicate_file')
                manifest['files/' + name] = item['sha256']
        if missing and not base.exists():
            must(not base.is_symlink(), 'assistant_backup_retention_directory')
            return manifest
        must(base.is_dir() and not base.is_symlink(), 'assistant_backup_retention_directory')
        observed = set()
        for path in base.iterdir():
            if path.name == 'files':
                must(path.is_dir() and not path.is_symlink(), 'assistant_backup_retention_directory')
                for child in path.iterdir():
                    observed.add('files/' + child.name)
            else:
                observed.add(path.name)
        must(observed <= set(manifest) if missing else observed == set(manifest), 'assistant_backup_retention_foreign_file')
        for name in observed:
            # Stored intent is still constrained; no ../ or arbitrary target.
            must(name in ('database.dump', 'manifest.json') or
                 re.fullmatch(r'files/(?:\.assistant-attachments-v1|\.generation-suspended-v1|[a-f0-9-]{36}\.(?:blob|part))', name),
                 'assistant_backup_retention_filename')
            self.rt.identity(base / name, expected_sha256=manifest[name], expected_owner=self.rt.storage.owner, expected_mode=0o600)
        return manifest

    def retention(self, *, apply=False):
        self.check_root()
        require_terminal(self.rt)
        records = []
        for path in sorted((self.root / 'runs').iterdir()):
            must(path.suffix == '.json' and DATE.fullmatch(path.stem), 'assistant_backup_foreign_receipt')
            record = self.rt.read(path)
            must(record.get('schema') == SCHEMA and record.get('day') == path.stem, 'assistant_backup_journal')
            if record['phase'] in ('COMMITTED', 'DELETE_INTENT'):
                self.verified_files(record, missing=record['phase'] == 'DELETE_INTENT')
                records.append(record)
        complete = [x for x in records if x['phase'] == 'COMMITTED']
        keep = {x['day'] for x in complete[-POLICY['daily']:]} | {
            x['day'] for x in [v for v in complete if v['weekly']][-POLICY['weekly']:]}
        expired = [x for x in records if x['phase'] == 'DELETE_INTENT' or x['day'] not in keep]
        report = {'outcome': 'RETENTION_APPLIED' if apply else 'RETENTION_DRY_RUN',
                  'keep': sorted(keep), 'delete': [x['day'] for x in expired], 'daily': 7, 'weekly': 4}
        if apply:
            for record in expired:
                manifest = self.verified_files(record, missing=record['phase'] == 'DELETE_INTENT')
                if record['phase'] != 'DELETE_INTENT':
                    self.save(record, 'DELETE_INTENT', retention_files=manifest)
                base = self.root / 'sets' / record['day']
                for name, digest in sorted(manifest.items()):
                    if (base / name).exists():
                        self.rt.storage.remove_bound_file(base / name, digest, expected_owner=self.rt.storage.owner)
                if (base / 'files').exists():
                    self.rt.storage.remove_empty_directory(base / 'files', expected_owner=self.rt.storage.owner)
                if base.exists():
                    self.rt.storage.remove_empty_directory(base, expected_owner=self.rt.storage.owner)
                self.save(record, 'PURGED')
        return report


def generation_control(rt, contract, action):
    """Local operator control; no new API/daemon and no implicit resume."""
    rt.require_lease()
    rt.require_terminal_component_transactions()
    must(action in ('status', 'suspend', 'resume'), 'assistant_generation_action')
    root = Path(contract['attachment_root'])
    must(root.is_absolute() and root.is_dir() and
         not any(p.is_symlink() for p in (root, *root.parents)), 'assistant_attachment_root')
    must(root.stat().st_mode & 0o777 == 0o700, 'assistant_attachment_root_mode')
    must(release.regular(root / '.assistant-attachments-v1') == b'ArtLIVE Assistant private attachments v1\n',
         'assistant_attachment_namespace')
    marker = root / release.GENERATION_MARKER
    owner = (root.stat().st_uid, root.stat().st_gid)
    marker_digest = release.sha(release.GENERATION_MARKER_BYTES)
    must(not marker.is_symlink(), 'assistant_generation_marker_symlink')
    present = marker.exists()
    if present:
        rt.identity(marker, expected_sha256=marker_digest, expected_owner=owner, expected_mode=0o600)
    if action == 'status':
        return {'outcome': 'GENERATION_SUSPENDED' if present else 'GENERATION_NOT_SUSPENDED',
                'in_flight_may_complete': True}
    path = guard.STORE / 'ASSISTANT_GENERATION_CONTROL.json'
    previous = rt.read(path) if path.exists() else None
    desired = action == 'suspend'
    if previous is not None and previous['phase'] == 'INTENT':
        must(previous['action'] == action, 'assistant_generation_intent_requires_same_action')
    elif present == desired:
        return {'outcome': 'GENERATION_ALREADY_SUSPENDED' if desired else 'GENERATION_ALREADY_RESUMED',
                'in_flight_may_complete': True}
    record = {'schema': 'R25_ASSISTANT_GENERATION_CONTROL_V1', 'phase': 'INTENT', 'action': action,
              'before_sha256': marker_digest if present else None, 'after_sha256': marker_digest if desired else None,
              'at': datetime.now(timezone.utc).isoformat(), 'effective_uid': __import__('os').geteuid(),
              'prior_record_sha256': rt.identity(path)['sha256'] if path.exists() else None}
    # Keep the previous record, including its exact provenance; never forge a reviewer.
    if path.exists():
        folder = guard.STORE / 'generation-control-history'
        rt.storage.mkdir_private(folder)
        target = folder / (record['prior_record_sha256'] + '.json')
        if not target.exists():
            rt.storage.atomic_copy_file(path, target, record['prior_record_sha256'])
    rt.write(path, record)
    if desired and not present:
        rt.storage.atomic_write_bytes(marker, release.GENERATION_MARKER_BYTES, target_owner=owner, mode=0o600)
    elif not desired and present:
        rt.storage.remove_bound_file(marker, marker_digest, expected_owner=owner)
    must(marker.exists() == desired, 'assistant_generation_state')
    if desired:
        rt.identity(marker, expected_sha256=marker_digest, expected_owner=owner, expected_mode=0o600)
    record['phase'] = 'COMMITTED'
    rt.write(path, record)
    return {'outcome': 'GENERATION_SUSPENDED' if desired else 'GENERATION_RESUMED',
            'in_flight_may_complete': True}
