#!/usr/bin/env python3
"""Rehearse exact Community forward migrations in fresh private PG16 clusters.

No existing PostgreSQL cluster, production service, user account or provider is
contacted. Each predecessor gets its own initdb, private Unix socket and data
directory. Fixture data and sanitized evidence remain after the clusters stop.
The default database adapter runs only against these disposable clusters. The
lost-reply case discards a response after a real COMMIT, then reconciles with
read-only queries. It does not establish production package acceptance.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'r25'))
import community_migration as migration
from community_host import ReferenceDatabase, identity

POST = '11111111-1111-4111-8111-111111111111'
COMMENT = '22222222-2222-4222-8222-222222222222'
REQUEST = '33333333-3333-4333-8333-333333333333'
FOUNDATION_OPERATION = 'f' * 32
PRIOR_LEARNING_OPERATION = 'c' * 32
FAILED_OPERATION = 'b' * 32
OPERATION = 'a' * 32
ADAPTER_USER = 'community_reference_fixture_backup'


def scram(password):
    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 4096)
    client = hmac.new(key, b'Client Key', hashlib.sha256).digest()
    b64 = lambda raw: base64.b64encode(raw).decode()
    return ('SCRAM-SHA-256$4096:' + b64(salt) + '$' +
            b64(hashlib.sha256(client).digest()) + ':' +
            b64(hmac.new(key, b'Server Key', hashlib.sha256).digest()))


class Fixture:
    def __init__(self, args, versions):
        self.args, self.versions = args, versions
        self.root = args.fixture / ('from-schema-' + str(versions[-1]))
        self.root.mkdir(mode=0o700)
        self.data = self.root / 'data'
        # Unix sockaddr paths are short even when --fixture is a long stage path.
        self.socket = Path(tempfile.mkdtemp(prefix='artlive-c-ref-'))
        self.socket.chmod(0o700)
        with socket.socket() as opened:
            opened.bind(('127.0.0.1', 0))
            self.port = opened.getsockname()[1]
        self.password = secrets.token_hex(24)
        self.verifier = scram(self.password)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith('PG')}
        if args.pg_lib:
            self.env['LD_LIBRARY_PATH'] = str(args.pg_lib)
        self.env.update(PGHOST=str(self.socket), PGPORT=str(self.port),
                        PGUSER='community_reference_fixture_admin', PGDATABASE='postgres')
        self.checks, self.events = [], []
        self.transition_executions = 0

    def run(self, label, command, raw=None, *, success=True, extra=None):
        result = subprocess.run([str(value) for value in command], input=raw,
                                capture_output=True, env=self.env | (extra or {}), timeout=70)
        for extension, value in [('stdout', result.stdout), ('stderr', result.stderr)]:
            clean = value.replace(self.password.encode(), b'<fixture-secret-redacted>')
            clean = clean.replace(self.verifier.encode(), b'<fixture-verifier-redacted>')
            (self.root / (label + '.' + extension)).write_bytes(clean)
        if success and result.returncode:
            raise RuntimeError(label + ' failed; inspect private fixture evidence')
        return result

    def sql(self, label, query, *, database=None, runtime=False, success=True):
        extra = {'PGUSER': migration.RUNTIME, 'PGPASSWORD': self.password} if runtime else {}
        return self.run(label, [self.args.pg_bin / 'psql', '-X', '-w', '-qAt',
                               '-v', 'ON_ERROR_STOP=1', '-d', database or migration.DATABASE],
                        query.encode() if isinstance(query, str) else query,
                        success=success, extra=extra)

    def read(self, label, query):
        return self.sql(label, 'BEGIN READ ONLY;\n' + query + '\nCOMMIT;').stdout.decode().strip()

    def ok(self, name):
        self.checks.append({'name': name, 'result': 'PASS'})

    def journal(self, phase):
        self.events.append({'phase': phase, 'operation': OPERATION})
        (self.root / 'journal.json').write_text(json.dumps(self.events, indent=2) + '\n')

    def snapshot(self, label):
        # Columns that the original schema already owned remain byte-for-byte
        # comparable even though the migration adds columns and an AI identity.
        value = self.read(label, """SELECT jsonb_build_object(
          'profiles',(SELECT jsonb_agg(jsonb_build_array(user_id,display_name) ORDER BY user_id)
                      FROM community.profiles WHERE user_id>0),
          'posts',(SELECT jsonb_agg(jsonb_build_array(id,author_id,content,gallery_artwork_id,status,created_at) ORDER BY id)
                   FROM community.posts WHERE id='""" + POST + """'),
          'comments',(SELECT jsonb_agg(jsonb_build_array(id,post_id,author_id,content,created_at) ORDER BY id)
                      FROM community.comments WHERE id='""" + COMMENT + """'),
          'reactions',(SELECT jsonb_agg(to_jsonb(r) ORDER BY post_id,user_id,kind) FROM community.reactions r),
          'operations',(SELECT jsonb_agg(to_jsonb(o) ORDER BY user_id,request_id) FROM community.operations o)
        )::text;""")
        return json.loads(value)

    def setup(self, foundation, learning):
        self.run('initdb', [self.args.pg_bin / 'initdb', '-D', self.data,
                           '--username=community_reference_fixture_admin', '--auth=trust',
                           '--encoding=UTF8', '--locale=C'])
        hba = self.data / 'pg_hba.conf'
        hba.write_text('local ' + migration.DATABASE + ' ' + migration.RUNTIME + ',' + ADAPTER_USER +
                       ' scram-sha-256\n' + hba.read_text())
        self.run('start', [self.args.pg_bin / 'pg_ctl', '-D', self.data, '-l',
                          self.root / 'postgres.log', '-o',
                          "-h '' -p " + str(self.port) + ' -k ' + str(self.socket), '-w', 'start'])
        self.sql('roles', migration.provision_sql(self.verifier), database='postgres')
        self.sql('database', migration.database_sql(), database='postgres')
        self.sql('foundation', migration.schema_sql(foundation, FOUNDATION_OPERATION))
        self.sql('legacy-data', """INSERT INTO community.profiles VALUES(101,'fixture one'),(202,'fixture two');
        INSERT INTO community.posts(id,author_id,content,created_at)
        VALUES('""" + POST + """',101,'preserved UUID post','2026-11-12 00:00:00+00');
        INSERT INTO community.comments(id,post_id,author_id,content,created_at)
        VALUES('""" + COMMENT + """','""" + POST + """',202,'preserved UUID comment','2026-11-12 00:01:00+00');
        INSERT INTO community.reactions VALUES('""" + POST + """',202,'like');
        INSERT INTO community.operations(user_id,request_id,fingerprint,result,created_at)
        VALUES(101,'""" + REQUEST + """',repeat('9',64),'{"kind":"preserved"}','2026-11-12 00:02:00+00');""")
        if self.versions == [1, 2]:
            self.sql('prior-learning', migration.learning_schema_sql(learning, PRIOR_LEARNING_OPERATION))
            self.sql('growth-data', """INSERT INTO community.media_assets
              (id,owner_id,filename,original_key,preview_key,mime_type,bytes,width,height,sha256)
              VALUES('44444444-4444-4444-8444-444444444444',101,'kept.jpg','kept.jpg','kept.webp',
                     'image/jpeg',42,1,1,repeat('8',64));
              INSERT INTO community.post_images(post_id,asset_id,position)
              VALUES('""" + POST + """','44444444-4444-4444-8444-444444444444',0);
              INSERT INTO community.artwork_tasks(id,post_id,source_comment_id,content,dimension)
              VALUES('55555555-5555-4555-8555-555555555555','""" + POST + """','""" + COMMENT + """','kept task','composition');
              INSERT INTO community.notifications(id,user_id,actor_id,post_id,kind)
              VALUES('66666666-6666-4666-8666-666666666666',101,202,'""" + POST + """','comment');""")
        assert self.read('actual-predecessor', 'SELECT array_agg(version ORDER BY version)::text FROM community.schema_version;') == '{' + ','.join(map(str, self.versions)) + '}'
        self.ok('fresh-private-cluster-with-exact-predecessor')

    def growth_snapshot(self, label):
        if self.versions != [1, 2]:
            return None
        return self.read(label, """SELECT jsonb_build_object(
          'media',(SELECT jsonb_agg(to_jsonb(t) ORDER BY id) FROM community.media_assets t),
          'images',(SELECT jsonb_agg(to_jsonb(t) ORDER BY post_id,position) FROM community.post_images t),
          'tasks',(SELECT jsonb_agg(to_jsonb(t) ORDER BY id) FROM community.artwork_tasks t),
          'notifications',(SELECT jsonb_agg(to_jsonb(t) ORDER BY id) FROM community.notifications t)
        )::text;""")

    def receipts(self, label):
        return json.loads(self.read(label, """SELECT jsonb_build_object(
          'versions',(SELECT jsonb_agg(version ORDER BY version) FROM community.schema_version),
          'foundation',(SELECT jsonb_agg(to_jsonb(t)) FROM community.publication_receipt t),
          'learning',(SELECT jsonb_agg(to_jsonb(t)) FROM community.learning_publication_receipt t),
          'reference',(SELECT jsonb_agg(to_jsonb(t)) FROM community.reference_publication_receipt t)
        )::text;"""))

    def verify_receipts(self, value):
        assert value['versions'] == [1, 2, 3]
        assert value['foundation'] == [{'operation_id': FOUNDATION_OPERATION,
                                        'foundation_sha256': migration.FOUNDATION_SHA}]
        prior = OPERATION if self.versions == [1] else PRIOR_LEARNING_OPERATION
        assert value['learning'] == [{'operation_id': prior, 'migration_sha256': migration.LEARNING_SHA,
                                     'schema_version': 2}]
        assert value['reference'] == [{'operation_id': OPERATION, 'migration_sha256': migration.REFERENCE_SHA,
                                      'schema_version': 3}]

    def permissions(self):
        assert self.sql('runtime-login', 'SELECT current_user;', runtime=True).stdout.strip().decode() == migration.RUNTIME
        self.sql('runtime-positive', """INSERT INTO community.v2_users(id,username,nickname)
            VALUES(303,'runtime_fixture','runtime fixture');
            INSERT INTO community.v2_posts(user_id,content) VALUES(303,'minimal-role post');
            INSERT INTO community.v2_operations(user_id,request_id,fingerprint,state,status,result)
            VALUES(303,'99999999-9999-4999-8999-999999999999','fixture','COMMITTED',200,'{}');
            INSERT INTO community.v2_coin_ledger
              (id,user_id,amount,balance_after,entry_type,source_type,description,idempotency_key)
            VALUES('fixture-ledger',303,1,1,'earn','fixture','fixture only','fixture-ledger');""", runtime=True)
        denied = {
            'ddl': 'CREATE TABLE community.must_not_create(id integer);',
            'public-ddl': 'CREATE TABLE public.must_not_create(id integer);',
            'temp': 'CREATE TEMP TABLE must_not_create(id integer);',
            'old-operation-delete': 'DELETE FROM community.operations WHERE false;',
            'new-operation-delete': 'DELETE FROM community.v2_operations WHERE false;',
            'ledger-update': 'UPDATE community.v2_coin_ledger SET amount=amount WHERE false;',
            'ledger-delete': 'DELETE FROM community.v2_coin_ledger WHERE false;',
            'receipt-update': 'UPDATE community.reference_publication_receipt SET schema_version=3 WHERE false;',
            'truncate': 'TRUNCATE community.v2_operations;',
        }
        for name, query in denied.items():
            result = self.sql('runtime-denied-' + name, query, runtime=True, success=False)
            assert result.returncode != 0 and b'permission denied' in result.stderr.lower(), name
        self.ok('real-scram-runtime-can-write-business-but-not-ddl-receipts-or-ledger-history')

    def database_adapter(self):
        # The controller lease is a fixture boundary; its real FD ownership and
        # inheritance contract are exercised by the separate native Node suite.
        host = SimpleNamespace(root=self.root / 'adapter-host',
                               folder=self.root / 'adapter-host',
                               lease=SimpleNamespace(require=lambda: None))
        self.sql('adapter-limited-principal', 'CREATE ROLE ' + ADAPTER_USER +
                 " LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '" + self.verifier + "';\n" +
                 'GRANT CONNECT ON DATABASE ' + migration.DATABASE + ' TO ' + ADAPTER_USER + ';\n' +
                 'GRANT USAGE ON SCHEMA community TO ' + ADAPTER_USER + ';\n' +
                 'GRANT SELECT ON ALL TABLES IN SCHEMA community TO ' + ADAPTER_USER + ';\n' +
                 'GRANT SELECT ON ALL SEQUENCES IN SCHEMA community TO ' + ADAPTER_USER + ';\n' +
                 'GRANT ' + migration.MIGRATOR + ' TO ' + ADAPTER_USER + ';\n' +
                 'ALTER DEFAULT PRIVILEGES FOR ROLE ' + migration.MIGRATOR +
                 ' IN SCHEMA community GRANT SELECT ON TABLES TO ' + ADAPTER_USER + ';\n' +
                 'ALTER DEFAULT PRIVILEGES FOR ROLE ' + migration.MIGRATOR +
                 ' IN SCHEMA community GRANT SELECT ON SEQUENCES TO ' + ADAPTER_USER + ';')
        passfile = self.root / 'adapter.pgpass'
        passfile.write_text(str(self.socket) + ':' + str(self.port) + ':' +
                            migration.DATABASE + ':' + ADAPTER_USER + ':' +
                            self.password + '\n')
        passfile.chmod(0o600)
        account = pwd.getpwuid(os.geteuid())
        libraries = ({entry.name: {'target': entry.resolve(strict=True).name,
                                  'identity': identity(entry.resolve(strict=True))}
                      for entry in self.args.pg_lib.iterdir()}
                     if self.args.pg_lib else {})
        plan = {'schema': 'ARTLIVE_COMMUNITY_REFERENCE_DATABASE_V1',
                'versions': self.versions, 'operation_id': OPERATION, 'snapshot': {},
                'postgres': {
                    'binary_dir': str(self.args.pg_bin),
                    'binaries': {name: identity(self.args.pg_bin / name)
                                 for name in ['psql', 'pg_dump', 'pg_restore']},
                    'library_dir': str(self.args.pg_lib) if self.args.pg_lib else '',
                    'library_identity': libraries,
                    'host': str(self.socket), 'port': self.port,
                    'user': ADAPTER_USER,
                    'passfile': {'path': str(passfile), 'identity': identity(passfile)},
                    'credential': {'account': account.pw_name, 'uid': account.pw_uid,
                                   'gid': account.pw_gid,
                                   'groups': sorted(set(os.getgrouplist(account.pw_name, account.pw_gid)))}}}
        database = ReferenceDatabase(host, plan)
        assert database.read('SELECT to_json(current_user);') == ADAPTER_USER
        try:
            database.query('CREATE TABLE community.adapter_must_not_create(id integer);')
        except ValueError as error:
            assert 'psql_failed' in str(error)
        else:
            raise AssertionError('NOINHERIT fixture backup principal unexpectedly acquired DDL')
        plan['snapshot'] = database.snapshot()
        assert plan['snapshot']['versions'] == self.versions
        assert database.status() == 'NOT_APPLIED'
        (self.root / 'adapter-plan.json').write_text(json.dumps(plan, indent=2) + '\n')
        backup = database.backup(self.root / 'pre-transition.dump')
        assert backup['bytes'] > 0 and backup['mode'] == 0o600
        self.ok('default-adapter-limited-scram-principal-verifies-preimage-and-readable-private-backup')
        return database

    def adapter_drift(self, database):
        self.sql('adapter-grant-forbidden', 'GRANT DELETE ON community.v2_operations TO ' + migration.RUNTIME + ';')
        assert database.status(preserved=False) == 'UNKNOWN'
        self.sql('adapter-revoke-forbidden', 'REVOKE DELETE ON community.v2_operations FROM ' + migration.RUNTIME + ';')
        assert database.status(preserved=False) == 'COMMITTED'
        self.sql('adapter-grant-schema-create', 'GRANT CREATE ON SCHEMA community TO ' + migration.RUNTIME + ';')
        try:
            database.snapshot()
        except ValueError as error:
            assert 'runtime_privilege_drift' in str(error)
        else:
            raise AssertionError('The default adapter accepted runtime DDL privileges')
        self.sql('adapter-revoke-schema-create', 'REVOKE CREATE ON SCHEMA community FROM ' + migration.RUNTIME + ';')
        self.ok('default-adapter-refuses-runtime-ddl-and-operation-delete-privilege-drift')
        # Only the disposable cluster's superuser bypasses synchronization to
        # inject a missing projection. Ordinary runtime writes keep both sides
        # synchronized and would otherwise repair this deliberate corruption.
        for table, original in [('v2_posts', POST), ('v2_comments', COMMENT)]:
            row = self.sql('adapter-unlink-' + table, 'BEGIN; SET LOCAL session_replication_role=replica; UPDATE community.' + table +
                           " SET legacy_id=NULL WHERE legacy_id='" + original + "' RETURNING id; COMMIT;").stdout.decode().strip()
            assert row.isdigit()
            assert database.status(preserved=False) == 'UNKNOWN'
            self.sql('adapter-relink-' + table, 'BEGIN; SET LOCAL session_replication_role=replica; UPDATE community.' + table +
                     " SET legacy_id='" + original + "' WHERE id=" + row + '; COMMIT;')
            assert database.status(preserved=False) == 'COMMITTED'
        self.ok('default-adapter-refuses-missing-canonical-post-and-comment-projections')

    def exercise(self, foundation, learning, reference):
        self.setup(foundation, learning)
        before = self.snapshot('legacy-before')
        growth = self.growth_snapshot('growth-before')
        failed = migration.reference_transition_sql(learning, reference, FAILED_OPERATION, self.versions)
        needle = b'INSERT INTO community.schema_version(version) VALUES(3);'
        assert failed.count(needle) == 1
        failed = failed.replace(needle, b'SELECT 1/0;\n' + needle, 1)
        assert self.sql('injected-reference-error', failed, success=False).returncode != 0
        actual = self.read('rollback-version', 'SELECT array_agg(version ORDER BY version)::text FROM community.schema_version;')
        assert actual == '{' + ','.join(map(str, self.versions)) + '}'
        assert self.read('rollback-new-relations', "SELECT to_regclass('community.v2_users') IS NULL AND to_regclass('community.reference_publication_receipt') IS NULL;") == 't'
        if self.versions == [1]:
            assert self.read('rollback-learning-relations', "SELECT to_regclass('community.media_assets') IS NULL AND to_regclass('community.learning_publication_receipt') IS NULL;") == 't'
        assert before == self.snapshot('legacy-after-rollback')
        assert growth == self.growth_snapshot('growth-after-rollback')
        self.ok('sql-error-in-reference-body-rolls-back-all-schema-data-and-receipts')

        rendered = migration.reference_transition_sql(learning, reference, OPERATION, self.versions)
        database = self.database_adapter()
        original_query = database.query
        def discard_commit_reply(sql):
            value = original_query(sql)
            if sql == rendered.decode():
                self.transition_executions += 1
                raise InterruptedError('fixture discards the successful client reply after real COMMIT')
            return value
        database.query = discard_commit_reply
        self.journal('MIGRATION_COMMIT_INTENT')
        try:
            database.migrate(learning, reference)
        except InterruptedError:
            self.journal('MIGRATION_RESULT_UNKNOWN')
        finally:
            database.query = original_query
        assert database.status() == 'COMMITTED'
        reconciled = self.receipts('read-only-reconcile')
        self.verify_receipts(reconciled)
        self.journal('FORWARD_SCHEMA_VERIFIED')
        assert before == self.snapshot('legacy-after-commit')
        assert growth == self.growth_snapshot('growth-after-commit')
        assert self.read('legacy-id-projection', "SELECT count(*) FROM community.v2_posts WHERE legacy_id='" + POST + "';") == '1'
        assert self.read('legacy-comment-projection', "SELECT count(*) FROM community.v2_comments WHERE legacy_id='" + COMMENT + "';") == '1'
        self.ok('both-migrations-commit-with-correct-original-operation-receipts')
        self.ok('legacy-uuid-operation-and-existing-growth-data-preserved')
        assert self.receipts('second-read-only-reconcile') == reconciled
        assert database.status() == 'COMMITTED'
        assert self.transition_executions == 1
        self.ok('default-adapter-lost-commit-reply-reconciled-twice-read-only-with-no-replay')
        self.permissions()
        self.adapter_drift(database)

    def execute(self, foundation, learning, reference):
        outcome = 'FAIL'
        try:
            self.exercise(foundation, learning, reference)
            outcome = 'PASS'
        finally:
            if (self.data / 'postmaster.pid').exists():
                self.run('stop', [self.args.pg_bin / 'pg_ctl', '-D', self.data, '-m', 'fast', '-w', 'stop'])
            report = {'result': outcome, 'predecessor': self.versions, 'checks': self.checks,
                      'successfulTransitionExecutions': self.transition_executions,
                      'socketDirectory': str(self.socket), 'fixture': str(self.root),
                      'production': False, 'existingClusterConnections': 0,
                      'applicationProcessesStarted': False, 'providerCalls': 0,
                      'databaseAdapter': 'ReferenceDatabase with explicit fixture principal and private cluster',
                      'boundary': 'Fresh native PG16 and default database adapter; not production package acceptance.'}
            (self.root / 'RESULT.json').write_text(json.dumps(report, indent=2) + '\n')
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['pg-bin', 'core-source', 'fixture']:
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--pg-lib', type=Path)
    args = parser.parse_args()
    args.pg_bin, args.core_source, args.fixture = [path.resolve() for path in
                                                  [args.pg_bin, args.core_source, args.fixture]]
    if args.pg_lib:
        args.pg_lib = args.pg_lib.resolve()
    if os.geteuid() == 0:
        raise RuntimeError('Run this disposable initdb fixture as a non-root user')
    os.umask(0o077)
    args.fixture.mkdir(mode=0o700, parents=True, exist_ok=False)
    env = {key: value for key, value in os.environ.items() if not key.startswith('PG')}
    if args.pg_lib:
        env['LD_LIBRARY_PATH'] = str(args.pg_lib)
    for tool in ['initdb', 'pg_ctl', 'psql', 'pg_dump', 'pg_restore']:
        result = subprocess.run([str(args.pg_bin / tool), '--version'], env=env,
                                capture_output=True, timeout=5, check=True)
        if not re.search(rb'\b16\.[0-9]+', result.stdout):
            raise RuntimeError('Native PostgreSQL 16 tools are required')
    source = args.core_source / 'community-service' / 'db'
    foundation = (source / '001_foundation.sql').read_bytes()
    learning = (source / '002_learning_community.sql').read_bytes()
    reference = (source / '003_reference_community.sql').read_bytes()
    for raw, digest in [(foundation, migration.FOUNDATION_SHA), (learning, migration.LEARNING_SHA),
                        (reference, migration.REFERENCE_SHA)]:
        if hashlib.sha256(raw).hexdigest() != digest:
            raise RuntimeError('Core migration bytes differ from reviewed fixed migrations')
    reports = []
    try:
        for versions in [[1], [1, 2]]:
            reports.append(Fixture(args, versions).execute(foundation, learning, reference))
    finally:
        report = {'result': 'PASS' if len(reports) == 2 else 'FAIL', 'cases': reports,
                  'production': False, 'existingClusterConnections': 0, 'providerCalls': 0,
                  'migrationDigests': {'002': migration.LEARNING_SHA, '003': migration.REFERENCE_SHA}}
        (args.fixture / 'RESULT.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'result': report['result'], 'cases': len(reports),
                      'checks': sum(len(case['checks']) for case in reports),
                      'fixture': str(args.fixture), 'production': False}))


if __name__ == '__main__':
    main()
