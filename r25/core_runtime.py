"""Fixed-host Linux adapter for publisher_transaction. No configurable hooks.

Only files listed in LIVE_FILES can be replaced. The shared R25 lease spans
snapshot, owned Java/watchdog stop, paired installation, HTTP/PG acceptance,
CURRENT commit and any recovery. Database, media, credentials and AI/Web code
are never written here. Controller installation is a separate reviewed action.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time

import core_release_guard as guard
from core_acceptance import IDLE_SQL, verify_http, verify_knowledge_http, authenticated_request, verify_gallery_followup_http, verify_managed_vmm_closed_http
from publisher_transaction import Release, RecoveryPlan, Refused
from r25_storage import R25Storage

ROOT, PREVIEW, STORE = guard.ROOT, guard.PREVIEW, guard.STORE
LOCK = Path('/root/autodl-tmp/auralink-web-deploy/runtime/frontend-deployment.lock')
LIVE_FILES = {'release-jar': ROOT / 'release/artifacts/backend.jar',
              'preview-jar': PREVIEW / 'release/backend.jar',
              'manifest': ROOT / 'release/MANIFEST.json',
              'handoff': ROOT / 'runtime/handoff-control.env',
              'application': ROOT / 'runtime/config/application.yml'}
ACCESS_SHA = 'b47b2c0846615cd5ea308368603b47a3ef7387095b3034d18501d2cfbd83c1ab'
PHASES = {'PREPARED', 'STOP_INTENT', 'STOPPED', 'INSTALL_INTENT', 'INSTALLED',
          'START_INTENT', 'ACCEPTED', 'CURRENT_COMMIT_INTENT', 'COMMITTED',
          'RECOVERY_REQUIRED', 'RESTORE_STOP_INTENT', 'RESTORE_STOPPED',
          'RESTORE_INTENT', 'RESTORED'}


def must(ok, reason):
    if not ok:
        raise Refused(reason)


def load_module(path, name, expected_sha256, *, owner=(1000, 1000), mode=0o700):
    raw = guard.read_file(path, owner=owner, mode=mode, expected=expected_sha256)
    return guard.source_module(path, name, raw)


def env_values(raw):
    result = {}
    for line in raw.decode('utf-8').splitlines():
        if not line or line.startswith('#'):
            continue
        must('=' in line, 'handoff_syntax')
        key, value = line.split('=', 1)
        must(key not in result and re.fullmatch('[A-Z][A-Z0-9_]*', key), 'handoff_duplicate')
        result[key] = value
    return result


def replace_env(raw, updates):
    values = env_values(raw)
    must(set(updates).issubset(values), 'handoff_update_missing_key')
    lines = []
    for line in raw.decode('utf-8').splitlines():
        key = line.split('=', 1)[0]
        lines.append(key + '=' + updates[key] if key in updates else line)
    return ('\n'.join(lines) + '\n').encode()


class CoreRuntime:
    def __init__(self):
        must(sys.platform.startswith('linux') and os.geteuid() == 0, 'linux_root_required')
        self.storage = R25Storage([str(ROOT), str(PREVIEW), str(LOCK.parent),
                                   '/root/autodl-tmp/auralink-core-deploy'], private_roots=[str(STORE)])
        self.fd = None
        self.last_http = None
        self.tx = None

    def identity(self, path, **kwargs):
        return self.storage.file_identity(path, **kwargs)

    def read(self, path, expected=None):
        return self.storage.read_private_record(path, expected_sha256=expected)

    def write(self, path, value):
        old = self.identity(path)['sha256'] if path.exists() else None
        return self.storage.atomic_write_json(path, value, replace=old is not None,
                                              expected_existing_sha256=old)

    @contextmanager
    def lease(self):
        import fcntl
        must(self.fd is None, 'nested_runtime_lease')
        self.identity(LOCK)
        fd = os.open(LOCK, os.O_RDWR | os.O_NOFOLLOW)
        try:
            opened, named = os.fstat(fd), LOCK.lstat()
            must((opened.st_dev, opened.st_ino) == (named.st_dev, named.st_ino), 'lease_inode')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise Refused('shared_publication_busy') from error
            self.fd = fd
            self.verify_controls()
            yield
        finally:
            self.fd = None
            os.close(fd)

    def require_lease(self):
        must(self.fd is not None, 'lease_required')
        active_guard = guard.production()
        (active_guard.verify_lease if isinstance(active_guard, guard.BusinessGuard) else
         active_guard.legacy.verify_lease)(self.fd)

    def verify_controls(self):
        policy = guard.policy()
        files = policy.get('controllers')
        must(type(files) is dict and set(files) == {
            'lifecycle_adapter.py', 'r25-production-java-launch.py', 'core_release_guard.py'},
            'controller_policy')
        owner = ((1000, 1000) if policy.get('schema') != guard.BUSINESS_POLICY_SCHEMA else
                 tuple(policy['installation']['controller_owner']))
        for name, digest in files.items():
            self.identity(ROOT / 'operations' / name, expected_sha256=digest,
                          expected_owner=owner, expected_mode=0o700)
        if policy.get('schema') == guard.BUSINESS_POLICY_SCHEMA:
            for name, digest in policy['runtime_modules'].items():
                module_path = ROOT / 'operations/r25-publisher' / name
                guard.reject_legacy_bytecode(module_path)
                self.identity(module_path, expected_sha256=digest)
            return
        self.identity(ROOT / 'operations/huaniao_recovery_guard.py',
                      expected_sha256=guard.LEGACY_GUARD_SHA, expected_owner=(1000, 1000), expected_mode=0o700)
        self.identity(ROOT / 'operations/postgres_access.py', expected_sha256=ACCESS_SHA,
                      expected_owner=(1000, 1000), expected_mode=0o700)

    def engine(self, *, partial_recovery=False):
        self.verify_controls()
        operations = str(ROOT / 'operations')
        if operations not in sys.path:
            sys.path.insert(0, operations)
        module = load_module(ROOT / 'operations/lifecycle_adapter.py', 'normal_release_lifecycle',
                             guard.policy()['controllers']['lifecycle_adapter.py'],
                             owner=tuple(guard.policy().get('installation', {}).get('controller_owner', [1000, 1000])))
        os.environ['R25_ROOT'] = str(ROOT)
        os.environ['ARTLIVE_R25_HANDOFF_CONFIG'] = str(ROOT / 'runtime/handoff-control.env')
        engine = module.production(self.fd)
        if partial_recovery:
            # Deliberately avoid a mixed JAR/MANIFEST load during recovery. Only
            # the unchanged, snapshotted handoff identity keys feed owned stop.
            engine.v = env_values(guard.read_file(ROOT / 'runtime/handoff-control.env'))
            engine.kind = engine.v.get('R25_LIFECYCLE_RELEASE_KIND')
        else:
            engine.load()
        return engine

    def query(self, sql):
        self.require_lease()
        value = guard.policy()
        if value.get('schema') == guard.BUSINESS_POLICY_SCHEMA:
            return guard.business_module(value).readonly_query(value, sql, self.fd)
        return self._legacy_query(sql)

    def _legacy_query(self, sql):
        """Exact V1 helper path; caller must establish its installation context."""
        self.require_lease()
        path = ROOT / 'operations/postgres_access.py'
        self.identity(path, expected_sha256=ACCESS_SHA, expected_owner=(1000, 1000), expected_mode=0o700)
        access = load_module(path, 'normal_release_postgres_access', ACCESS_SHA)
        credential, _ = access.trusted_pgpass(ROOT)
        legacy = guard.production().legacy
        result = subprocess.run([str(legacy.psql), '-X', '-qAt', '-v', 'ON_ERROR_STOP=1',
                                 '-h', '127.0.0.1', '-p', '55640', '-U', 'artlive_r25_app',
                                 '-d', 'artlive_r25_production'], input=sql, text=True,
                                capture_output=True, timeout=20,
                                env=access.database_environment(credential), pass_fds=(self.fd,))
        must(result.returncode == 0 and len(result.stdout) <= 65536, 'readonly_database_unknown')
        return result.stdout.strip()

    def current(self):
        document = self.read(STORE / 'CURRENT.json')
        must(document.get('schema') == 'R25_CORE_CURRENT_V1', 'current_schema')
        return Release(**document['release'])

    def release_path(self, release):
        guard.digest_value(release.artifact_sha256)
        guard.digest_value(release.commit_sha, 40)
        guard.digest_value(release.accepted_record_sha256)
        return STORE / 'releases' / (release.artifact_sha256 + '-' + release.commit_sha)

    def verify_candidate(self, release):
        self.require_lease()
        folder = self.release_path(release)
        record = guard.release_record(release.artifact_sha256, release.accepted_record_sha256, release.commit_sha)
        must(record['commit_sha'] == release.commit_sha, 'candidate_commit')
        jar = folder / 'backend.jar'
        self.identity(jar, expected_sha256=release.artifact_sha256, expected_bytes=record['artifact_bytes'])
        must(guard.payload_fingerprint(jar) == record['payload_fingerprint'], 'candidate_payload')

    def require_compatible(self, target):
        self.verify_candidate(target)
        value = guard.policy()
        if value.get('schema') == guard.BUSINESS_POLICY_SCHEMA:
            module = guard.business_module(value)
            module.verify_compatibility(value, self.query)
            if target.artifact_sha256 == value['upgrade']['candidate']['artifact_sha256']:
                module.verify_assets(value)
            return
        # V1 still grants precisely the independently equivalent capability.
        guard.production().legacy.require_backend_eligible(guard.BASELINE, self.fd)

    def require_no_active_work(self):
        value = self.query(IDLE_SQL)
        must(value.isdecimal() and int(value) == 0, 'active_work_or_unknown')
        policy=guard.policy()
        if policy.get('upgrade',{}).get('capability')=='assistant-v1':
            module=guard.business_module(policy)
            ledger=guard.strict_json(self.query(module.ASSISTANT_LEDGER_SQL))['ledger']
            if len(ledger)>=15:
                active=self.query(module.ASSISTANT_IDLE_SQL)
                must(active.isdecimal() and int(active)==0,'assistant_executing_or_uploading_work')

    def verify_current(self, release):
        self.require_lease()
        must(self.current() == release, 'current_pointer_changed')
        self.verify_candidate(release)
        engine = self.engine()
        must(engine.backend == release.artifact_sha256, 'live_backend_binding')
        guard.production().require_backend_eligible(release.artifact_sha256, self.fd)
        process = engine.active('java', 19080)
        must(process is not None, 'current_java_not_running')
        engine.frontend('status', self.fd)
        engine.tunnel.status()
        for name, port in (('guide', 19084), ('critic', 19085)):
            must(engine.active(name, port) is not None, 'paired_service_unavailable')
        self.verify_process_contract(process, release)
        self.last_http = self.acceptance(release)

    def verify_process_contract(self, process, release):
        value = guard.policy()
        if value.get('schema') == guard.BUSINESS_POLICY_SCHEMA:
            record = guard.release_record(release.artifact_sha256, release.accepted_record_sha256, release.commit_sha)
            ticks = process.get('ticks')
            must((type(ticks) is int and ticks > 0) or
                 (type(ticks) is str and ticks.isdecimal() and int(ticks) > 0), 'business_process_start_ticks')
            guard.business_module(value).verify_process(value, process['pid'], int(ticks),
                LIVE_FILES['application'], require_isolated=record.get('schema') == guard.BUSINESS_RECORD_SCHEMA)

    def verify_rollback_source(self, release):
        """Verify the exact owned V2 source without requiring its feature health.

        An expired acceptance session or lost external asset must not prevent
        stopping a still-identifiable committed process. This does not authorize
        starting it, relax target compatibility, or accept unknown live bytes.
        V1 retains its complete existing verify_current prerequisite.
        """
        self.require_lease()
        value = guard.policy()
        record = guard.release_record(release.artifact_sha256, release.accepted_record_sha256, release.commit_sha)
        if value.get('schema') != guard.BUSINESS_POLICY_SCHEMA or record.get('schema') != guard.BUSINESS_RECORD_SCHEMA:
            return self.verify_current(release)
        if 'successor' in value.get('upgrade',{}):
            must(guard.successor_configuration_phase(value) == 'bridge', 'successor_restore_ai_before_core')
            ai_current = self.read(ROOT/'runtime/ai-publications/CURRENT.json')
            must(ai_current.get('release') == value['upgrade']['successor']['ai_before'], 'successor_restore_ai_before_core')
        must(self.tx is not None, 'business_rollback_transaction_required')
        plan = self.load_recovery(self.tx)
        must(plan.phase == 'COMMITTED' and plan.candidate == release and self.current() == release,
             'business_rollback_source_binding')
        self.validate_recovery(plan)
        document = self.snapshot_document(plan)
        for name, path in LIVE_FILES.items():
            self.identity(path, expected_sha256=document['files'][name]['after_sha256'])
        self.verify_controls()
        handoff = env_values(guard.read_file(LIVE_FILES['handoff'],
            expected=document['files']['handoff']['after_sha256']))
        must(handoff.get('R25_BACKEND_SHA256') == release.artifact_sha256, 'business_rollback_handoff')
        engine = self.engine(partial_recovery=True)
        process = engine.active('java', 19080)
        must(process is not None, 'business_rollback_java_identity_required')
        ticks = process.get('ticks')
        must((type(ticks) is int and ticks > 0) or
             (type(ticks) is str and ticks.isdecimal() and int(ticks) > 0), 'business_process_start_ticks')
        guard.business_module(value).verify_process(value, process['pid'], int(ticks),
            LIVE_FILES['application'], require_isolated=False)

    def acceptance(self, release, minimum_total=1):
        receipt = verify_http(self.query, minimum_total=minimum_total)
        record = guard.release_record(release.artifact_sha256, release.accepted_record_sha256, release.commit_sha)
        if record.get('schema') == guard.BUSINESS_RECORD_SCHEMA:
            value = guard.policy()
            token_record = self.read(STORE / 'ACCEPTANCE_SESSION.json')
            token = token_record.get('bearer')
            # This is an existing separately provisioned acceptance session, not
            # login/token issuance. Invalid/expired credentials fail acceptance.
            receipt['knowledge'] = verify_knowledge_http(
                self.query, value, get_authenticated=authenticated_request(token))
            if (value['upgrade'].get('capability')=='assistant-v1' and
                    release.artifact_sha256==value['upgrade']['candidate']['artifact_sha256']):
                path=ROOT/'operations/r25-publisher/assistant_acceptance.py'
                module=guard.source_module(path,'r25_bound_assistant_acceptance',guard.read_file(path,expected=value['runtime_modules']['assistant_acceptance.py']))
                receipt['assistant']=module.verify_assistant_http(value,get_authenticated=authenticated_request(token))
                if 'core_followup' in value['upgrade']:
                    must(value['upgrade']['core_followup'].get('acceptance') in {'gallery-daily-v1','managed-vmm-closed-v1'},
                         'core_followup_http_contract')
                    receipt['gallery_daily'] = verify_gallery_followup_http(get_authenticated=authenticated_request(token))
                    if value['upgrade']['core_followup']['acceptance']=='managed-vmm-closed-v1':
                        receipt['managed_vmm']=verify_managed_vmm_closed_http(get_authenticated=authenticated_request(token))
        return receipt

    def snapshot(self, current, candidate):
        self.require_lease()
        self.require_terminal_component_transactions()
        self.verify_current(current)
        self.verify_candidate(candidate)
        # Two old JAR copies + staged metadata + conservative rollback reserve.
        total = sum(self.identity(path)['bytes'] for path in LIVE_FILES.values())
        must(shutil.disk_usage(STORE).free > total * 2 + 512 * 1024 * 1024, 'snapshot_disk_space')
        transaction_id = secrets.token_hex(16)
        folder = STORE / 'transactions' / transaction_id
        self.storage.mkdir_private(folder)
        self.storage.mkdir_private(folder / 'before')
        self.storage.mkdir_private(folder / 'after')
        files = {}
        for name, path in LIVE_FILES.items():
            identity = self.identity(path)
            self.storage.atomic_copy_file(path, folder / 'before' / name, identity['sha256'])
            files[name] = identity
        manifest = self.read(LIVE_FILES['manifest'])
        entries = [entry for entry in manifest.get('entries', []) if entry.get('path') == 'artifacts/backend.jar']
        must(len(entries) == 1 and entries[0]['sha256'] == current.artifact_sha256, 'manifest_backend_binding')
        record = guard.release_record(candidate.artifact_sha256, candidate.accepted_record_sha256, candidate.commit_sha)
        entries[0]['sha256'], entries[0]['size'] = candidate.artifact_sha256, record['artifact_bytes']
        manifest['normal_core_publication'] = {'schema': 'R25_CORE_NORMAL_PROVENANCE_V1',
                                                'commit_sha': candidate.commit_sha,
                                                'artifact_sha256': candidate.artifact_sha256,
                                                'accepted_record_sha256': candidate.accepted_record_sha256}
        self.storage.atomic_write_json(folder / 'after/manifest', manifest)
        handoff = replace_env(guard.read_file(LIVE_FILES['handoff']),
                              {'R25_BACKEND_SHA256': candidate.artifact_sha256})
        self.storage.atomic_write_bytes(folder / 'after/handoff', handoff)
        if record.get('schema') == guard.BUSINESS_RECORD_SCHEMA:
            value = guard.policy()
            must(asdict(current) == value['upgrade']['baseline_release'], 'business_upgrade_baseline_changed')
            after = guard.business_module(value).render_configuration(
                guard.read_file(LIVE_FILES['application'], expected=files['application']['sha256']), value)
            self.storage.atomic_write_bytes(folder / 'after/application', after)
        else:
            self.storage.atomic_copy_file(LIVE_FILES['application'], folder / 'after/application', files['application']['sha256'])
        for name in ('release-jar', 'preview-jar'):
            # Backups are separate complete files. Candidate reference remains
            # immutable in releases/ and is rechecked before every copy.
            files[name]['after_sha256'] = candidate.artifact_sha256
        for name in ('manifest', 'handoff', 'application'):
            files[name]['after_sha256'] = self.identity(folder / 'after' / name)['sha256']
        document = {'schema': 'R25_CORE_SNAPSHOT_V1', 'transaction_id': transaction_id,
                    'current': asdict(current), 'candidate': asdict(candidate), 'files': files,
                    'before_current': self.read(STORE / 'CURRENT.json'),
                    'minimum_total': self.last_http['gallery_total']}
        self.storage.atomic_write_json(folder / 'snapshot.json', document)
        return transaction_id

    def begin(self, current, candidate, snapshot_id):
        self.require_lease()
        self.require_terminal_component_transactions()
        folder = self.transaction_folder(snapshot_id)
        snapshot = self.read(folder / 'snapshot.json')
        must(snapshot['current'] == asdict(current) and snapshot['candidate'] == asdict(candidate), 'snapshot_releases')
        self.storage.atomic_write_json(folder / 'journal.json', {
            'schema': 'R25_CORE_TRANSACTION_V1', 'transaction_id': snapshot_id,
            'snapshot_id': snapshot_id, 'snapshot_sha256': self.identity(folder / 'snapshot.json')['sha256'],
            'current': asdict(current), 'candidate': asdict(candidate), 'phase': 'PREPARED', 'history': []})
        active_path = STORE / 'ACTIVE.json'
        if active_path.exists():
            active = self.read(active_path)
            previous = self.read(self.transaction_folder(active['transaction_id']) / 'journal.json')
            must(previous['phase'] in {'COMMITTED', 'RESTORED'}, 'previous_recovery_required')
        self.write(active_path, {'schema': 'R25_CORE_ACTIVE_V1', 'transaction_id': snapshot_id})
        self.tx = snapshot_id
        return snapshot_id

    def require_terminal_component_transactions(self):
        self.require_terminal_application_transactions()
        backup_active = ROOT / 'runtime/assistant-backups/assistant-daily-v1/ACTIVE.json'
        if backup_active.exists():
            module = self.assistant_backup_module()
            must(module is not None, 'assistant_backup_controller_required')
            module.require_terminal(self)
        active_path = STORE / 'CONTROL_UPGRADE_ACTIVE.json'
        if active_path.exists():
            active = self.read(active_path)
            must(active.get('schema') == 'R25_CORE_CONTROLLER_UPGRADE_ACTIVE_V1', 'controller_upgrade_active_schema')
            approval = guard.digest_value(active.get('approval_sha256'))
            journal = self.read(STORE / 'controller-upgrades' / approval / 'journal.json')
            must(journal.get('approval_sha256') == approval and journal.get('phase') in {'INSTALLED', 'RESTORED'},
                 'controller_upgrade_recovery_required_before_publication')

    def require_terminal_application_transactions(self):
        self.require_lease()
        for component in ('core', 'ai'):
            root = ROOT / 'runtime' / (component + '-publications')
            active_path = root / 'ACTIVE.json'
            if not active_path.exists():
                continue
            self.storage.validate_target(root, exists=True, directory=True,
                                         expected_owner=(0, 0), expected_mode=0o700)
            active = self.read(active_path)
            identity = active.get('transaction_id')
            must(type(identity) is str and re.fullmatch('[0-9a-f]{32}', identity), 'paired_active_transaction')
            journal = self.read(root / 'transactions' / identity / 'journal.json')
            must(journal.get('transaction_id') == identity and journal.get('phase') in {'COMMITTED', 'RESTORED'},
                 component + '_recovery_required_before_core_publication')

    def transaction_folder(self, identity):
        must(type(identity) is str and re.fullmatch('[0-9a-f]{32}', identity), 'transaction_id')
        return STORE / 'transactions' / identity

    def record(self, transaction_id, phase, **evidence):
        self.require_lease()
        must(phase in PHASES and set(evidence).issubset({'failure_class', 'recovery_failure_class'}), 'journal_fields')
        path = self.transaction_folder(transaction_id) / 'journal.json'
        document = self.read(path)
        document['phase'] = phase
        document['history'].append({'phase': phase, 'at_unix': int(time.time()), **evidence})
        must(len(document['history']) <= 1000, 'journal_history_limit')
        self.write(path, document)
        self.tx = transaction_id

    def stop_owned_java(self, current):
        self.verify_current(current)
        engine = self.engine()
        engine.watchdog_stop_locked()
        engine.stop_role('java', 19080)

    def snapshot_document(self, plan):
        folder = self.transaction_folder(plan.transaction_id)
        journal = self.read(folder / 'journal.json')
        document = self.read(folder / 'snapshot.json', journal['snapshot_sha256'])
        must(document['current'] == asdict(plan.current) and document['candidate'] == asdict(plan.candidate)
             and set(document['files']) == set(LIVE_FILES), 'snapshot_binding')
        for name, expected in document['files'].items():
            must(expected['path'] == str(LIVE_FILES[name]), 'snapshot_fixed_path')
            self.identity(folder / 'before' / name, expected_sha256=expected['sha256'])
        return document

    def assistant_module(self):
        value=guard.policy()
        if value.get('upgrade',{}).get('capability') != 'assistant-v1':
            return None
        # Pin the imported helper before loading its consumer; Python -B alone
        # would still accept preexisting .pyc. Never import candidate code.
        for name in ('assistant_release.py','assistant_runtime.py'):
            path=ROOT/'operations/r25-publisher'/name
            module=guard.source_module(path,name[:-3],guard.read_file(path,expected=value['runtime_modules'][name]))
        return module

    def assistant_backup_module(self):
        if self.assistant_module() is None:
            return None
        policy = guard.policy()
        path = ROOT / 'operations/r25-publisher/assistant_backup.py'
        return guard.source_module(path, 'assistant_backup', guard.read_file(
            path, expected=policy['runtime_modules']['assistant_backup.py']))

    def prepare_forward_schema(self,current,candidate,snapshot_id):
        module=self.assistant_module()
        if module is not None:
            module.before_install(self,current,candidate,snapshot_id)

    def verify_forward_recovery(self,plan):
        module=self.assistant_module()
        if module is not None:
            module.recovery(self,plan)

    def install_candidate(self, candidate, snapshot_id):
        self.require_lease()
        plan = self.load_recovery(snapshot_id)
        must(plan.candidate == candidate, 'install_candidate_binding')
        document = self.snapshot_document(plan)
        for name, path in LIVE_FILES.items():
            expected = document['files'][name]
            source = (self.release_path(candidate) / 'backend.jar') if name.endswith('-jar') else self.transaction_folder(snapshot_id) / 'after' / name
            self.storage.atomic_copy_file(source, path, expected['after_sha256'], replace=True,
                                          expected_existing_sha256=expected['sha256'])

    def start_and_verify(self, release):
        self.require_compatible(release)
        engine = self.engine()
        must(engine.backend == release.artifact_sha256, 'start_release_binding')
        engine.start_java_locked(self.fd)
        process = engine.active('java', 19080)
        must(process is not None, 'started_java_identity')
        self.verify_process_contract(process, release)
        minimum = self.read(self.transaction_folder(self.tx) / 'snapshot.json')['minimum_total']
        receipt = self.acceptance(release, minimum_total=minimum)
        process = engine.active('java', 19080)
        must(process is not None, 'started_java_identity')
        receipt.update({'commit_sha': release.commit_sha, 'artifact_sha256': release.artifact_sha256,
                        'java_pid': process['pid'], 'java_start_ticks': process['ticks']})
        self.write(self.transaction_folder(self.tx) / 'http-acceptance.json', receipt)
        engine.watchdog_start_locked()
        must(engine.watchdog_identity() is not None, 'watchdog_start_identity')

    def commit_current(self, new, expected):
        self.replace_current({'schema': 'R25_CORE_CURRENT_V1', 'release': asdict(new),
                              'previous': asdict(expected), 'transaction_id': self.tx}, {expected})

    def replace_current(self, document, allowed):
        self.require_lease()
        path = STORE / 'CURRENT.json'
        observed_sha = self.identity(path)['sha256']
        previous = self.read(path, observed_sha)
        must(previous.get('schema') == 'R25_CORE_CURRENT_V1' and
             Release(**previous['release']) in allowed, 'current_compare_and_swap')
        # The digest used for the comparison remains the digest used for the
        # write; a later observation must never adopt external pointer drift.
        self.storage.atomic_write_json(path, document, replace=True,
                                       expected_existing_sha256=observed_sha)

    def load_recovery(self, transaction_id):
        document = self.read(self.transaction_folder(transaction_id) / 'journal.json')
        must(document.get('schema') == 'R25_CORE_TRANSACTION_V1' and
             document.get('transaction_id') == transaction_id and document.get('snapshot_id') == transaction_id
             and document.get('phase') in PHASES, 'transaction_schema')
        self.tx = transaction_id
        return RecoveryPlan(transaction_id, Release(**document['current']), Release(**document['candidate']),
                            transaction_id, document['phase'])

    def validate_recovery(self, plan):
        self.require_lease()
        active = self.read(STORE / 'ACTIVE.json')
        must(active.get('transaction_id') == plan.transaction_id, 'recovery_not_active_transaction')
        must(self.current() in (plan.current, plan.candidate), 'recovery_current_drift')
        document = self.snapshot_document(plan)
        self.verify_candidate(plan.current)
        self.verify_candidate(plan.candidate)
        for name, path in LIVE_FILES.items():
            expected = document['files'][name]
            must(self.identity(path)['sha256'] in {expected['sha256'], expected['after_sha256']}, 'foreign_live_file_drift')

    def stop_for_restore(self, plan):
        self.validate_recovery(plan)
        engine = self.engine(partial_recovery=True)
        engine.watchdog_stop_locked()
        engine.stop_role('java', 19080)

    def restore_snapshot(self, plan):
        self.validate_recovery(plan)
        document = self.snapshot_document(plan)
        for name, path in LIVE_FILES.items():
            actual = self.identity(path)['sha256']
            expected = document['files'][name]
            self.storage.atomic_copy_file(self.transaction_folder(plan.transaction_id) / 'before' / name,
                                          path, expected['sha256'], replace=True, expected_existing_sha256=actual)
        self.start_and_verify(plan.current)
        self.replace_current(document['before_current'], {plan.current, plan.candidate})

    def verify_restored(self, plan):
        self.verify_current(plan.current)
        document = self.snapshot_document(plan)
        for name, path in LIVE_FILES.items():
            self.identity(path, expected_sha256=document['files'][name]['sha256'])
