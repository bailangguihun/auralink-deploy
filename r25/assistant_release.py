"""Reviewed Assistant extension to the existing Core Publisher, not a launcher.

The caller is the installed, approval-bound adapter holding the R25/c5ea lease.
Only exact V001--V016 or the reviewed V017 extension is accepted; candidate-provided commands,
SQL outside the pinned migration set, schema downgrades and live DB restore
are deliberately absent. OS/process quiescence remains the existing lifecycle's
responsibility. These helpers never infer approval from successful tests.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import pwd
import shutil
import stat
import subprocess


class Refused(RuntimeError):
    """Standalone refusal for source-bound launcher validation.

    The Java launcher loads this helper by verified bytes without the private
    publisher directory on sys.path. Do not resolve a sibling transaction
    module or trust an ambient module cache merely to report a refusal.
    """

    pass

SCHEMA = 'R25_ASSISTANT_RELEASE_V1'
VERSIONS = tuple(f'V{i:03}' for i in range(1, 17))
PREFIX_LENGTHS = (13, 15, 16)
NEW_MIGRATIONS = ('V014__assistant_records.sql', 'V015__assistant_execution.sql',
                  'V016__assistant_storage_controls.sql')
# Fixed reviewed source bytes, not caller-rebound hashes. V016 is this local
# S4 candidate; changing it requires a new reviewed controller source.
MIGRATION_SHA256 = {'V001__catalog_contract.sql': '59f7f788f17fe68da8aa8b92c88de0cd71eaed3304116435752855cfe90dbe53', 'V002__catalog_users_and_favorites.sql': '6ac1175601354059cf385e1054af3d99ae6bacd6013013539054e42c738800cc', 'V003__private_assets_and_critic_tasks.sql': '2d9a8e37bdfbb8b949e18d8e31b2b622f0ce459b479b930b929465dc73673ce2', 'V004__painting_guides.sql': 'fc52fab82583bedd99ba03710bad675be511c491aa7a5d375b0e3070513169b7', 'V005__guide_attempt_history_retry_policy.sql': '9e04d4e74f74603b2d8671b3cfae752654ea52f193e4f66ac3e153bce725de5e', 'V006__creation_workflows_and_jpa_compatibility.sql': '9808de1458c9b4b788e2b110a90a68f0e1198c96c49d233c47d79484ecc7a05d', 'V007__creation_legacy_asset_provenance.sql': '14c8b21cccd3bb64965c8591ff8c27036cb3bc43cd11b80f6b02713b45423d4e', 'V008__creation_catalog_input_snapshots.sql': 'a19c6e4c2bd577c9058fca6838d74fbf7a2a7551045ec7c46c95c043eca47646', 'V009__unified_rehearsal_import_state.sql': '57264d52a9849c031a714177e23911d773113af94c65faff9e8df1d8ebd10cc6', 'V010__allow_audited_unified_noop_reruns.sql': 'c8635cff75eefa65d402c41f8a4b790a1ebafb10e6503c6af7182a6eb9a27f1a', 'V011__r24_legacy_media_provenance.sql': 'b08ef32fac1a434f16b56c897e57a1d635c3acadd79ed66b1deac388243e1ab2', 'V012__annotation_value_hash_index.sql': '38c3e10cef383e723c390d73ac97df00670711fe9cedfa39299c4b98e1bf853d', 'V013__r24_legacy_catalog_import_history.sql': '5b9213b6c58bdfb2fa1592ed19dc32a98fba1d94238b1e7c6270338819cb3e5d', 'V014__assistant_records.sql': '7f073ba6c5504e2aeb60b210474c27be81733ff34617ba81b82bfed4eb9b5bcd', 'V015__assistant_execution.sql': 'f4f53364d20a6e62c97de9978755944e238dd24600f7da0904b822bea4ba9685', 'V016__assistant_storage_controls.sql': '095aac0e5ff5f8187992d9fdf31c5b16507633a213653fc5d64537c9db60ebd8'}
STUDIO_MIGRATION = ('V017__creation_history_deletion.sql', 'f6ef87fbc9d4e8dcb6c6c93b249d58976f5fd5b68d27f3edfccb4b0a13d540ee')


def schema_prefixes(entries):
    # A Studio contract admits only the already-installed V016 prefix.
    return (16, 17) if len(entries) == 17 else PREFIX_LENGTHS


LEDGER_SQL = "SELECT coalesce(json_agg(json_build_object('version',version,'sha256',checksum) ORDER BY version),'[]'::json)::text FROM prototype_schema_migrations;"
# Exact tables, including deletion/request tombstones, survive backup/recovery.
ASSISTANT_TABLES = ('assistant_sessions','assistant_branches','assistant_messages',
    'assistant_requests','assistant_runs','assistant_attachments','assistant_message_attachments',
    'assistant_run_events','assistant_citations','assistant_interaction_selections','assistant_summaries',
    'assistant_stage_classifications','assistant_choice_cards')
GENERATION_MARKER = '.generation-suspended-v1'
GENERATION_MARKER_BYTES = b'ArtLIVE Assistant generation suspended v1\n'


def must(ok, reason):
    if not ok:
        raise Refused(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    must(type(value) is str and re.fullmatch('[a-f0-9]{64}', value), 'assistant_digest')
    return value


def regular(path, expected=None):
    path = Path(path)
    must(path.is_absolute() and not any(p.is_symlink() for p in (path, *path.parents)), 'assistant_path_symlink')
    before = path.lstat()
    must(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'assistant_regular_file')
    with path.open('rb') as stream:
        raw = stream.read()
    after = path.lstat()
    must((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns) ==
         (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns), 'assistant_file_drift')
    if expected is not None:
        must(sha(raw) == digest(expected), 'assistant_file_checksum')
    return raw


def migration_inventory(entries):
    must(type(entries) is list and len(entries) in (16, 17), 'assistant_migration_inventory')
    must([x.get('version') for x in entries] == list(VERSIONS) + (['V017'] if len(entries) == 17 else []), 'assistant_migration_versions')
    for entry in entries:
        must(set(entry) == {'version','name','sha256','path'}, 'assistant_migration_fields')
        must(re.fullmatch(entry['version'] + r'__[A-Za-z0-9_]+\.sql', entry['name']), 'assistant_migration_name')
        must(Path(entry['path']).name == entry['name'], 'assistant_migration_path')
        must(dict(MIGRATION_SHA256, **{STUDIO_MIGRATION[0]: STUDIO_MIGRATION[1]}).get(entry['name']) == entry['sha256'], 'assistant_unreviewed_migration_bytes')
        regular(entry['path'], entry['sha256'])
    must(tuple(x['name'] for x in entries[13:]) == NEW_MIGRATIONS + ((STUDIO_MIGRATION[0],) if len(entries) == 17 else ()), 'assistant_fixed_migrations')
    return entries


def validate_contract(value):
    must(type(value) is dict and value.get('schema') == SCHEMA, 'assistant_contract_schema')
    must(value.get('mode') in ('disabled','history-only','enabled'), 'assistant_mode')
    migration_inventory(value['migrations'])
    for role in ('core','ai','web','deploy'):
        identity = value['artifacts'][role]
        must(identity['repository'] == 'bailangguihun/auralink-' + {'core':'core-api','ai':'ai-service','web':'web','deploy':'deploy'}[role], 'assistant_repository')
        must(re.fullmatch('[a-f0-9]{40}', identity['commit']), 'assistant_commit')
        digest(identity['sha256'])
    # Explicit reviewed compatibility evidence, never arbitrary historical JAR.
    digest(value['compatible_recovery_jar_sha256'])
    digest(value['compatibility_evidence_sha256'])
    digest(value['configuration_sha256'])
    digest(value['storage_policy_sha256'])
    digest(value['baseline_policy_sha256'])
    digest(value['baseline_payload_fingerprint'])
    digest(value['review_evidence_sha256'])
    must(value.get('controller_before') == 'R25_CORE_BUSINESS_POLICY_V2', 'assistant_installed_v2_required')
    must(value.get('next_lifecycle_owner') == 'c5ea' and value.get('map_enabled') is False, 'assistant_lifecycle_or_map')
    must(value.get('cleanup_enabled') is False, 'assistant_production_cleanup_requires_policy_review')
    # Optional for already retained S4 fixtures/records; the first-release
    # candidate explicitly selects this closed profile. It is approval-bound
    # with the full AI configuration digest, not an environment toggle.
    must(value.get('retrieval_profile') in (None, 'artwork-only-v1'),
         'assistant_retrieval_profile')
    # No placeholder production quota/retention approval from isolated defaults.
    must(value.get('policy_status') in ('ISOLATED_TEST_ONLY','PRODUCTION_REVIEWED'), 'assistant_policy_status')
    if 'backup_policy' in value:
        must(value['backup_policy'] == {'schema':'R25_ASSISTANT_BACKUP_POLICY_V1','job_id':'assistant-daily-v1',
             'daily':7,'weekly':4,'utc_hour':19,'utc_minute':15,'weekly_day':5}, 'assistant_backup_policy')
    return value


class PostgresControl:
    """Real psql/pg_dump/pg_restore, using a separately reviewed private contract.

    PostgreSQL's credential file ownership belongs to its actual service user.
    No sudo, chown, ACL changes, initialization, or production-root guessing.
    Connection values never enter receipts. No automatic retries after writes.
    """
    def __init__(self, *, binary_dir, host, port, database, user, environment=None, credential_identity=None):
        self.binary = Path(binary_dir).absolute()
        must(host == '127.0.0.1' or (host.startswith('/') and not Path(host).is_symlink()), 'assistant_pg_loopback')
        must(type(port) is int and 1024 <= port <= 65535, 'assistant_pg_port')
        must(all(re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,62}', x) for x in (database,user)), 'assistant_pg_identifier')
        self.connection = ['-h',host,'-p',str(port),'-U',user,'-d',database]
        self.database = database
        self.credential_identity=credential_identity
        self.exec_identity={}
        if credential_identity is not None:
            account=pwd.getpwnam(credential_identity['account'])
            must((account.pw_uid,account.pw_gid)==(credential_identity['uid'],credential_identity['gid']),'assistant_pg_account_drift')
            groups=sorted(set(os.getgrouplist(account.pw_name,account.pw_gid)))
            must(groups==sorted(set(credential_identity['groups'])),'assistant_pg_groups_drift')
            must(os.geteuid() in (0,account.pw_uid),'assistant_pg_cannot_assume_identity')
            if os.geteuid()!=account.pw_uid:
                self.exec_identity={'user':account.pw_uid,'group':account.pw_gid,'extra_groups':groups}
        allowed = {'PATH','LANG','LC_ALL','LD_LIBRARY_PATH','PGPASSFILE','PGCONNECT_TIMEOUT'}
        self.env = {k:v for k,v in (environment or {}).items() if k in allowed}
        self.env.update(PGCONNECT_TIMEOUT='5')
        if 'PGPASSFILE' in self.env:
            path = Path(self.env['PGPASSFILE']); regular(path)
            must(path.stat().st_uid == (credential_identity['uid'] if credential_identity else os.geteuid()) and stat.S_IMODE(path.stat().st_mode) == 0o600,
                 'assistant_pgpass_actual_identity')
        self.identity = {name:sha(regular(self.binary/name)) for name in ('psql','pg_dump','pg_restore')}

    def run(self, name, arguments, *, input=None, timeout=120):
        must(name in self.identity, 'assistant_pg_binary')
        regular(self.binary/name, self.identity[name])
        result = subprocess.run([str(self.binary/name),*arguments],input=input,capture_output=True,
                                env=self.env,timeout=timeout,**self.exec_identity)
        must(result.returncode == 0, 'assistant_pg_' + name + '_failed')
        return result.stdout

    def query(self, sql):
        return self.run('psql',[*self.connection,'-X','-qAt','-v','ON_ERROR_STOP=1'],input=sql.encode()).decode().strip()

    def ledger(self):
        return json.loads(self.query(LEDGER_SQL))

    def dump(self, target):
        must(not Path(target).exists(), 'assistant_backup_target_exists')
        regular(self.binary/'pg_dump',self.identity['pg_dump'])
        # Open destination as controller, then pass only the writable FD to the
        # established DB account; do not chmod private backup ancestors.
        fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as out:
            result=subprocess.run([str(self.binary/'pg_dump'),*self.connection,'--format=custom','--no-owner','--no-acl'],stdout=out,stderr=subprocess.PIPE,env=self.env,timeout=120,**self.exec_identity)
            must(result.returncode==0,'assistant_pg_pg_dump_failed')
            out.flush();os.fsync(out.fileno())
        result=subprocess.run([str(self.binary/'pg_restore'),'--list',str(target)],capture_output=True,env=self.env,timeout=30)
        must(result.returncode==0,'assistant_pg_backup_invalid')

    def restore_disposable(self, dump, *, explicitly_disposable=False):
        must(explicitly_disposable is True, 'assistant_restore_isolated_only')
        must(self.query("SELECT count(*) FROM pg_tables WHERE schemaname='public';") == '0', 'assistant_restore_requires_empty_target')
        self.run('pg_restore',[*self.connection,'--exit-on-error','--single-transaction','--no-owner','--no-acl',str(dump)])


def inspect_schema(pg, entries):
    migration_inventory(entries)
    ledger = pg.ledger()
    must(len(ledger) in schema_prefixes(entries), 'assistant_unsupported_schema')
    expected = [{'version':x['version'],'sha256':x['sha256']} for x in entries[:len(ledger)]]
    must(ledger == expected, 'assistant_migration_checksum_conflict')
    if len(entries) == 17:
        studio = json.loads(pg.query("""SELECT json_build_object(
          'columns', (SELECT coalesce(json_agg(json_build_object('type',data_type,'nullable',is_nullable,'default',column_default)),'[]'::json)
            FROM information_schema.columns WHERE table_schema='public' AND table_name='creations' AND column_name='history_deleted_at'),
          'indexes', (SELECT coalesce(json_agg(json_build_object('definition',pg_get_indexdef(i.indexrelid),'valid',i.indisvalid,'ready',i.indisready)),'[]'::json)
            FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname='idx_creations_visible_history'))::text;"""))
        expected_studio = {'columns':[], 'indexes':[]}
        if len(ledger) == 17:
            expected_studio = {'columns':[{'type':'timestamp without time zone','nullable':'YES','default':None}],
                'indexes':[{'definition':'CREATE INDEX idx_creations_visible_history ON public.creations USING btree (user_id, created_at DESC, public_id DESC) WHERE (history_deleted_at IS NULL)',
                            'valid':True,'ready':True}]}
        must(studio == expected_studio, 'studio_physical_schema_conflict')
    return {'versions':[x['version'] for x in ledger], 'sha256':sha(json.dumps(ledger,sort_keys=True).encode())}


def migrate(pg, entries, *, require_quiescent, journal):
    """One atomic fixed additive transaction. Lost reply reconciles, never resends.

    A new invocation may proceed only from its fully verified allowed prefix. A
    committed final ledger is a verified no-op. An exception leaves SQL_RESULT
    UNKNOWN in the durable caller journal; recover checks the actual ledger.
    """
    require_quiescent()
    before = inspect_schema(pg, entries)
    if len(before['versions']) == len(entries):
        return {'outcome':'SCHEMA_CURRENT_VERIFIED','schema':before}
    pending = entries[len(before['versions']):]
    sql = ["BEGIN; SET LOCAL lock_timeout='5s'; SET LOCAL statement_timeout='120s'; SELECT pg_advisory_xact_lock(1732584193, 16);"]
    # Under DB lock recheck the complete original ledger to reject race/drift.
    expected = [{'version':x['version'],'sha256':x['sha256']} for x in entries[:len(before['versions'])]]
    raw_expected = json.dumps(expected,separators=(',',':')).replace("'","''")
    sql.append("DO $$ BEGIN IF (SELECT coalesce(json_agg(json_build_object('version',version,'sha256',checksum) ORDER BY version),'[]'::json)::jsonb FROM prototype_schema_migrations) <> '"+raw_expected+"'::jsonb THEN RAISE EXCEPTION 'assistant migration ledger drift'; END IF; END $$;")
    for item in pending:
        text = regular(item['path'],item['sha256']).decode()
        must(not re.search(r'(?im)^\s*(?:BEGIN|COMMIT|ROLLBACK)\s*;',text), 'assistant_nested_migration_transaction')
        sql.append(text)
        sql.append("INSERT INTO prototype_schema_migrations(version,checksum) VALUES ('"+item['version']+"','"+item['sha256']+"');")
    sql.append('COMMIT;')
    journal('MIGRATION_INTENT',before=before,after_versions=[x['version'] for x in entries])
    try:
        pg.query('\n'.join(sql))
    except BaseException:
        journal('MIGRATION_RESULT_UNKNOWN')
        raise
    after = inspect_schema(pg,entries)
    must(len(after['versions']) == len(entries), 'assistant_migration_incomplete')
    journal('MIGRATION_COMMITTED',after=after)
    return {'outcome':'SCHEMA_FORWARD_COMMITTED','schema':after}


def reconcile_migration(pg, entries):
    state = inspect_schema(pg,entries)
    return {'outcome':'FORWARD_SCHEMA_PRESERVED' if len(state['versions']) == len(entries) else 'MIGRATION_NOT_COMMITTED', 'schema':state,
            'automatic_replay':False,'database_restore':False}


def file_inventory(root):
    root = Path(root)
    must(root.is_absolute() and root.is_dir() and not any(p.is_symlink() for p in (root,*root.parents)), 'assistant_attachment_root')
    meta=root.stat()
    must(stat.S_IMODE(meta.st_mode)==0o700,'assistant_attachment_root_mode')
    marker=root/'.assistant-attachments-v1'
    must(regular(marker)==b'ArtLIVE Assistant private attachments v1\n','assistant_attachment_namespace')
    result=[]
    for path in sorted(root.iterdir()):
        # The private store is flat; never traverse another module or symlink.
        must(path.name in ('.assistant-attachments-v1', GENERATION_MARKER) or re.fullmatch(r'[a-f0-9-]{36}\.(blob|part)',path.name), 'assistant_unowned_attachment_file')
        raw=regular(path); meta=path.stat()
        if path.name == GENERATION_MARKER:
            must(raw == GENERATION_MARKER_BYTES and stat.S_IMODE(meta.st_mode) == 0o600, 'assistant_generation_marker')
        must(stat.S_IMODE(meta.st_mode) in (0o600,0o400) and (meta.st_uid,meta.st_gid)==(root.stat().st_uid,root.stat().st_gid),'assistant_attachment_permissions')
        result.append({'name':path.name,'bytes':len(raw),'sha256':sha(raw),'uid':meta.st_uid,'gid':meta.st_gid,'mode':stat.S_IMODE(meta.st_mode)})
    return result


def backup(pg, attachment_root, target, *, require_quiescent, journal):
    """Consistent only under stopped Assistant writers + shared lifecycle lease.

    Checks the complete DB logical snapshot before/after file copy. No real
    token/config is packaged. Database dump and private files stay private.
    """
    require_quiescent()
    target=Path(target); must(not target.exists(),'assistant_backup_target_exists')
    before_files=file_inventory(attachment_root)
    # pg_dump is transactionally consistent. A separate metadata-independent
    # logical dump hash proves the quiescence contract across DB+file copying.
    def logical():
        raw = pg.run('pg_dump',[*pg.connection,'--format=plain','--no-owner','--no-acl','--data-only'])
        # PG16 security backports randomize restrict keys per invocation.
        return sha(b'\n'.join(line for line in raw.splitlines() if not line.startswith((b'\\restrict ',b'\\unrestrict '))))
    before_db=logical()
    target.mkdir(mode=0o700,parents=False)
    (target/'files').mkdir(mode=0o700)
    journal('BACKUP_INTENT')
    try:
        pg.dump(target/'database.dump')
        for item in before_files:
            raw=regular(Path(attachment_root)/item['name'],item['sha256'])
            destination=target/'files'/item['name']
            with destination.open('xb') as out:
                out.write(raw); out.flush(); os.fsync(out.fileno())
            os.chmod(destination,0o600)
        require_quiescent()
        must(before_files==file_inventory(attachment_root) and before_db==logical(),'assistant_backup_drift')
        record={'schema':'R25_ASSISTANT_BACKUP_V1','database_sha256':sha(regular(target/'database.dump')),
                'files':before_files,'ledger':pg.ledger(),'logical_database_sha256':before_db,
                'restore_policy':'DISPOSABLE_TARGET_ONLY_NOT_APPLICATION_ROLLBACK'}
        path=target/'manifest.json'
        with path.open('x') as out:
            json.dump(record,out,sort_keys=True,indent=2); out.flush(); os.fsync(out.fileno())
        os.chmod(path,0o600)
        for directory_path in (target/'files',target,target.parent):
            descriptor=os.open(directory_path,os.O_DIRECTORY|os.O_RDONLY)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
        journal('BACKUP_VERIFIED',manifest_sha256=sha(regular(path)))
        return record
    except BaseException:
        journal('BACKUP_FAILED')
        raise


def restore_disposable(pg, backup_root, destination, *, expected_manifest_sha256, explicitly_disposable=False):
    must(explicitly_disposable is True,'assistant_restore_isolated_only')
    folder=Path(backup_root); manifest=json.loads(regular(folder/'manifest.json',expected_manifest_sha256))
    must(manifest.get('schema')=='R25_ASSISTANT_BACKUP_V1','assistant_backup_schema')
    regular(folder/'database.dump',manifest['database_sha256'])
    destination=Path(destination)
    must(not destination.exists(),'assistant_restore_files_target_exists')
    for item in manifest['files']:
        must(item['name'] in ('.assistant-attachments-v1', GENERATION_MARKER) or re.fullmatch(r'[a-f0-9-]{36}\.(blob|part)',item['name']),'assistant_backup_filename')
        must(item['mode'] in (0o600,0o400),'assistant_backup_file_mode')
        regular(folder/'files'/item['name'],item['sha256'])
    pg.restore_disposable(folder/'database.dump',explicitly_disposable=True)
    destination.mkdir(mode=0o700)
    for item in manifest['files']:
        raw=regular(folder/'files'/item['name'],item['sha256'])
        with (destination/item['name']).open('xb') as out:
            out.write(raw);out.flush();os.fsync(out.fileno())
        os.chmod(destination/item['name'],item['mode'])
    for item in manifest['files']:
        regular(destination/item['name'],item['sha256'])
    directory=os.open(destination,os.O_DIRECTORY|os.O_RDONLY)
    try: os.fsync(directory)
    finally: os.close(directory)
    must(pg.ledger()==manifest['ledger'],'assistant_restored_ledger')
    # Account/session deletion tombstones are part of the actual restored DB,
    # not inferred from whether a blob was physically retained in the backup.
    return {'outcome':'DISPOSABLE_RESTORE_VERIFIED','files':len(manifest['files']),'production_rollback':False}
