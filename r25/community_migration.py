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
LEARNING_SHA = '5a1521f851773af9bd045499a6243220c3d3805b433d96b3db70e7d670489670'


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


def learning_schema_sql(migration, operation):
    """Render a finite schema-1 to schema-2 successor, never execute it.

    The operator must hold the original shared lease and drain Community before
    execution. A lost commit reply requires receipt inspection, never re-POST.
    No existing business row, role, database or media file is removed.
    """
    must(sha(migration) == LEARNING_SHA, 'community_reviewed_learning_migration_digest')
    must(re.fullmatch('[a-f0-9]{32}', operation) is not None, 'community_learning_operation')
    raw = migration.decode()
    must(raw.count('BEGIN;') == raw.count('COMMIT;') == 1, 'community_learning_transaction')
    body = raw.split('BEGIN;\n', 1)[1].rsplit('COMMIT;', 1)[0]
    return ("BEGIN;\nSET LOCAL lock_timeout='5s';\nSET LOCAL statement_timeout='30s';\n"
            f'SET LOCAL ROLE {MIGRATOR};\n'
            "DO $guard$ BEGIN\n"
            f"IF current_database()<>'{DATABASE}' OR "
            "(SELECT array_agg(version ORDER BY version) FROM community.schema_version) IS DISTINCT FROM ARRAY[1] "
            "THEN RAISE EXCEPTION 'Community learning upgrade requires the original schema 1 database'; END IF;\n"
            "END $guard$;\n" + body +
            'CREATE TABLE community.learning_publication_receipt(operation_id char(32) PRIMARY KEY, migration_sha256 char(64) NOT NULL, schema_version integer NOT NULL CHECK(schema_version=2));\n'
            f"INSERT INTO community.learning_publication_receipt VALUES('{operation}','{LEARNING_SHA}',2);\n" +
            f'GRANT SELECT ON ALL TABLES IN SCHEMA community TO {RUNTIME};\n'
            f'GRANT INSERT ON community.media_assets,community.post_images,community.artwork_revisions,community.artwork_tasks,community.notifications,community.knowledge_cases TO {RUNTIME};\n'
            f'GRANT UPDATE(adopted) ON community.comments TO {RUNTIME};\n'
            f'GRANT UPDATE(review_status) ON community.artwork_revisions TO {RUNTIME};\n'
            f'GRANT UPDATE(status) ON community.artwork_tasks TO {RUNTIME};\n'
            f'GRANT UPDATE(read_at) ON community.notifications TO {RUNTIME};\n'
            f'GRANT UPDATE(title,summary,status,updated_at,published_at) ON community.knowledge_cases TO {RUNTIME};\n'
            'COMMIT;\n').encode()


LEARNING_VERIFY_SQL = """BEGIN READ ONLY;
SELECT current_database();
SELECT version FROM community.schema_version ORDER BY version;
SELECT operation_id,migration_sha256,schema_version FROM community.learning_publication_receipt;
SELECT tablename,tableowner FROM pg_tables WHERE schemaname='community' ORDER BY tablename;
SELECT has_table_privilege('artlive_community_runtime','community.operations','DELETE') AS can_delete_receipts,
       has_schema_privilege('artlive_community_runtime','community','CREATE') AS can_create;
COMMIT;
"""


REFERENCE_SHA = 'f6dc7b8b7abfcb872f38fd396abb860e4c6e90c9e44e761dee190baa92b88c54'
REFERENCE_TABLES = ('users', 'posts', 'tags', 'post_tags', 'post_likes', 'favorites', 'comments', 'comment_likes', 'coin_wallets', 'coin_ledger', 'post_coin_contributions', 'notifications', 'ai_agents', 'ai_messages', 'ai_settings', 'rooms', 'room_members', 'artwork_assets', 'artwork_sessions', 'annotations', 'drawing_groups', 'room_messages', 'artwork_ai_contexts', 'ai_annotation_drafts', 'ownership_transfers', 'room_moderation_actions', 'room_entry_payments', 'room_share_requests', 'room_view_shares', 'room_income_debts', 'operations', 'uploads')

def reference_schema_sql(migration, operation):
    """Render the pinned schema-2 to schema-3 transition; caller owns execution.

    Requires a drained managed process, original shared lease and the preceding
    schema-2 receipt. Never performs SQL, changes active markers or replays a
    missing commit reply. Read REFERENCE_VERIFY_SQL before recovery decisions.
    """
    must(sha(migration) == REFERENCE_SHA, 'community_reviewed_reference_migration_digest')
    must(re.fullmatch('[a-f0-9]{32}',operation) is not None, 'community_reference_operation')
    raw=migration.decode()
    must(raw.count('BEGIN;') == raw.count('COMMIT;') == 1, 'community_reference_transaction')
    body=raw.split('BEGIN;\n',1)[1].rsplit('COMMIT;',1)[0]
    grants=f'GRANT SELECT ON ALL TABLES IN SCHEMA community TO {RUNTIME};\n'
    for table in REFERENCE_TABLES:
        privileges='INSERT' if table in ('coin_ledger','room_moderation_actions','uploads','tags') else 'INSERT,UPDATE' if table in ('users','operations','notifications') else 'INSERT,UPDATE,DELETE'
        grants+=f'GRANT {privileges} ON community.v2_{table} TO {RUNTIME};\n'
    grants+=f'GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA community TO {RUNTIME};\n'
    grants+=f'GRANT UPDATE(content,reference_deleted) ON community.comments TO {RUNTIME};\n'
    grants+=f'GRANT DELETE ON community.post_images TO {RUNTIME};\n'
    return ("BEGIN;\nSET LOCAL lock_timeout='5s';\nSET LOCAL statement_timeout='60s';\n"
        f'SET LOCAL ROLE {MIGRATOR};\n'
        "DO $guard$ BEGIN\n"
        f"IF current_database()<>'{DATABASE}' OR "
        "(SELECT array_agg(version ORDER BY version) FROM community.schema_version) IS DISTINCT FROM ARRAY[1,2] "
        "THEN RAISE EXCEPTION 'Community reference upgrade requires the original schema 2 database'; END IF;\n"
        "END $guard$;\n"+body+
        'CREATE TABLE community.reference_publication_receipt(operation_id char(32) PRIMARY KEY, migration_sha256 char(64) NOT NULL, schema_version integer NOT NULL CHECK(schema_version=3));\n'
        f"INSERT INTO community.reference_publication_receipt VALUES('{operation}','{REFERENCE_SHA}',3);\n"+
        grants+'COMMIT;\n').encode()


REFERENCE_VERIFY_SQL = """BEGIN READ ONLY;
SELECT current_database();
SELECT version FROM community.schema_version ORDER BY version;
SELECT operation_id,migration_sha256,schema_version FROM community.reference_publication_receipt;
SELECT has_table_privilege('artlive_community_runtime','community.v2_operations','DELETE') AS can_delete_receipts,
       has_table_privilege('artlive_community_runtime','community.v2_coin_ledger','UPDATE') AS can_update_ledger,
       has_table_privilege('artlive_community_runtime','community.v2_coin_ledger','DELETE') AS can_delete_ledger,
       has_schema_privilege('artlive_community_runtime','community','CREATE') AS can_create;
SELECT (SELECT count(*) FROM community.posts) AS canonical_posts,
       (SELECT count(*) FROM community.v2_posts WHERE legacy_id IS NOT NULL) AS projected_posts;
COMMIT;
"""


def reference_transition_sql(learning, reference, operation, versions):
    """One exact forward transaction from the observed schema 1 or schema 2.

    The compatible new service must be drained by its lifecycle owner first.
    Both immutable migrations are bound even when schema 2 is already present.
    A lost commit reply is reconciled from the original operation receipts;
    this renderer neither executes SQL nor authorizes another attempt.
    """
    must(type(versions) is list and all(type(v) is int for v in versions)
         and versions in ([1], [1, 2]),
         'community_reference_exact_predecessor')
    must(sha(learning) == LEARNING_SHA, 'community_reviewed_learning_migration_digest')
    must(sha(reference) == REFERENCE_SHA, 'community_reviewed_reference_migration_digest')
    must(type(operation) is str and re.fullmatch('[a-f0-9]{32}', operation) is not None,
         'community_reference_operation')

    def body(raw):
        text = raw.decode('utf-8')
        must(text.count('BEGIN;') == text.count('COMMIT;') == 1,
             'community_reference_single_transaction')
        return text.split('BEGIN;\n', 1)[1].rsplit('COMMIT;', 1)[0]

    expected = ','.join(str(v) for v in versions)
    guard = ("DO $predecessor$ BEGIN\n"
             f"IF current_database()<>'{DATABASE}' OR "
             "(SELECT array_agg(version ORDER BY version) FROM community.schema_version) "
             f"IS DISTINCT FROM ARRAY[{expected}] THEN "
             "RAISE EXCEPTION 'Community reference predecessor changed'; END IF;\n"
             "END $predecessor$;\n")
    intermediate = body(learning_schema_sql(learning, operation)) if versions == [1] else ''
    final = body(reference_schema_sql(reference, operation))
    return ("BEGIN;\nSET LOCAL lock_timeout='5s';\n"
            "SET LOCAL statement_timeout='60s';\n" + guard + intermediate + final +
            "COMMIT;\n").encode('utf-8')
