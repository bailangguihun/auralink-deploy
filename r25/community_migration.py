"""Render only the finite, separately approved Community database migration.

Never connects, reads Core credentials, creates accounts or runs startup DDL.
Provisioning, database creation and schema commit have distinct receipts:
interruption requires inspection of the original operation, never blind retry.
"""
import re
from community_host import sha
from r25_storage import must

DATABASE = 'artlive_community'
MIGRATOR = 'artlive_community_migrator'
RUNTIME = 'artlive_community_runtime'
FOUNDATION_SHA = '04b59866146d82548d59baba3282e0cca11e99d7df48a472ed9a3f58cb39f2e8'


def provision_sql(scram_verifier):
    # A verifier derived from a new private random secret, never Core's secret.
    must(type(scram_verifier) is str and re.fullmatch(
        r'SCRAM-SHA-256\$4096:[A-Za-z0-9+/]+={0,2}\$[A-Za-z0-9+/]+={0,2}:[A-Za-z0-9+/]+={0,2}',scram_verifier),
        'community_new_scram_verifier_required')
    return ("BEGIN;\nSET LOCAL lock_timeout='5s';\nSET LOCAL statement_timeout='30s';\n"
            f'CREATE ROLE {MIGRATOR} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;\n'
            f"CREATE ROLE {RUNTIME} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 8 PASSWORD '{scram_verifier}';\n"
            'COMMIT;\n').encode()


def database_sql():
    # CREATE DATABASE cannot join the schema transaction. A lost receipt must
    # verify owner/encoding/schema absence; never DROP/recreate the database.
    return f"CREATE DATABASE {DATABASE} OWNER {MIGRATOR} TEMPLATE template0 ENCODING 'UTF8';\n".encode()


def schema_sql(foundation, operation):
    must(sha(foundation)==FOUNDATION_SHA,'community_reviewed_migration_digest')
    must(re.fullmatch('[a-f0-9]{32}',operation) is not None,'community_migration_operation')
    raw=foundation.decode()
    must(raw.count('BEGIN;')==raw.count('COMMIT;')==1,'community_migration_transaction')
    body=raw.split('BEGIN;\n',1)[1].rsplit('COMMIT;',1)[0]
    return ("BEGIN;\nSET LOCAL lock_timeout='5s';\nSET LOCAL statement_timeout='30s';\n"
            f'REVOKE ALL ON DATABASE {DATABASE} FROM PUBLIC;\n'
            f'GRANT CONNECT ON DATABASE {DATABASE} TO {RUNTIME};\n'
            'REVOKE CREATE ON SCHEMA public FROM PUBLIC;\n'
            f'SET LOCAL ROLE {MIGRATOR};\n'+body+
            'CREATE TABLE community.publication_receipt(operation_id char(32) PRIMARY KEY, foundation_sha256 char(64) NOT NULL);\n'
            f"INSERT INTO community.publication_receipt VALUES('{operation}','{FOUNDATION_SHA}');\n"
            'REVOKE ALL ON SCHEMA community FROM PUBLIC;\n'
            f'GRANT USAGE ON SCHEMA community TO {RUNTIME};\n'
            f'GRANT SELECT ON ALL TABLES IN SCHEMA community TO {RUNTIME};\n'
            f'GRANT INSERT,UPDATE ON community.profiles,community.posts,community.operations TO {RUNTIME};\n'
            f'GRANT INSERT ON community.comments TO {RUNTIME};\n'
            f'GRANT INSERT,DELETE ON community.reactions TO {RUNTIME};\n'
            'COMMIT;\n').encode()


VERIFY_SQL = """BEGIN READ ONLY;
SELECT current_database();
SELECT operation_id,foundation_sha256 FROM community.publication_receipt;
SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolinherit,rolcanlogin,rolreplication,rolbypassrls
FROM pg_roles WHERE rolname IN ('artlive_community_migrator','artlive_community_runtime') ORDER BY rolname;
SELECT has_database_privilege('artlive_community_runtime',current_database(),'CREATE') AS can_create,
       has_database_privilege('artlive_community_runtime',current_database(),'TEMP') AS can_temp;
SELECT nspname FROM pg_namespace WHERE has_schema_privilege('artlive_community_runtime',oid,'CREATE');
SELECT table_name,privilege_type FROM information_schema.role_table_grants
WHERE grantee='artlive_community_runtime' ORDER BY table_name,privilege_type;
COMMIT;
"""
