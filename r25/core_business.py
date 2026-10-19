"""Explicit, installed Knowledge business contract; no production activation CLI.

Approval is a private, digest-bound installation input, never candidate metadata.
This module performs only reads and bounded read-only SQL. It cannot provision
assets, manufacture approval, import application code or restore database data.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import struct
import subprocess
import types

SCHEMA = 'R25_CORE_BUSINESS_POLICY_V2'
RECORD_SCHEMA = 'R25_CORE_BUSINESS_RELEASE_V2'
REPOSITORY = 'Hang-666-star/auralink-core-api'
CURRENT_REPOSITORY = 'bailangguihun/auralink-core-api'
VERIFIED_REPOSITORIES = (CURRENT_REPOSITORY, REPOSITORY)
CONFIG_KEYS = ('snapshot-root', 'graph-sha256', 'stats-sha256', 'map-enabled', 'china-sha256')
ASSETS = {'poetry-graph.json': 'graph-sha256', 'poetry-stats.json': 'stats-sha256',
          'china.json': 'china-sha256'}
RUNTIME_MODULES = frozenset(('core_acceptance.py', 'core_business.py', 'core_publish.py',
    'core_release_guard.py', 'core_runtime.py', 'core-release-candidate.py',
    'publisher_transaction.py', 'r25_storage.py', 'upgrade_core_controller.py', 'verify-r25-artifact.py',
    'core_asset_sandbox.py', 'control_overlay.py'))
TABLES = ('catalog_batches', 'catalog_batch_revisions', 'catalog_batch_current_revisions', 'catalog_source_records',
          'catalog_paintings', 'catalog_media_assets', 'catalog_identity_reservations',
          'catalog_import_claims', 'catalog_import_attempts',
          'catalog_annotation_field_definitions', 'catalog_annotation_revisions')
# Full media inventories are larger than policy/journal metadata. This bound is
# specific to the retention manifest; the ordinary 1 MiB reader limit remains.
RETENTION_MANIFEST_LIMIT = 64 * 1024 * 1024
# Full retained rows, not Gallery heads: withdrawn/failed/missing-image rows and
# reservations continue to constrain rollback. A changed data window refuses;
# a later rollback needs a separately reviewed refreshed contract, never a DB restore.
RETENTION_SQL = "BEGIN READ ONLY; SET LOCAL statement_timeout='8000ms';\nSELECT jsonb_build_object(" + \
    "'database',current_database(),'port',inet_server_port(),'major',current_setting('server_version_num')::int/10000," + \
    "'versions',(SELECT jsonb_agg(version ORDER BY version) FROM prototype_schema_migrations),'tables',jsonb_build_object(" + \
    ','.join("'" + name + "',(SELECT jsonb_build_object('rows',count(*),'md5',md5(coalesce(string_agg(row_value,E'\\n' ORDER BY row_value),''))) FROM (SELECT to_jsonb(t)::text AS row_value FROM " + name + " t) s)" for name in TABLES) + \
    "),'active_work',(SELECT count(*) FROM creations WHERE status IN ('QUEUED','RUNNING'))+(SELECT count(*) FROM catalog_painting_critic_tasks WHERE status IN ('QUEUED','RUNNING'))+(SELECT count(*) FROM catalog_painting_guide_generation_attempts WHERE status IN ('DISPATCHING','POST_RESPONSE_UNCERTAIN')))::text;\nROLLBACK;"


ASSISTANT_IDLE_SQL = "BEGIN READ ONLY; SET LOCAL statement_timeout='8000ms'; SELECT (SELECT count(*) FROM assistant_runs WHERE status='EXECUTING')+(SELECT count(*) FROM assistant_attachments WHERE status='UPLOADING'); ROLLBACK;"
ASSISTANT_LEDGER_SQL = "BEGIN READ ONLY; SELECT jsonb_build_object('ledger',coalesce(jsonb_agg(jsonb_build_object('version',version,'sha256',checksum) ORDER BY version),'[]'::jsonb))::text FROM prototype_schema_migrations; ROLLBACK;"


class Refused(RuntimeError):
    pass


def must(value, reason):
    if not value:
        raise Refused(reason)


def config_keys(profile, isolation=None):
    must(profile in ('poetry-only', 'map-enabled'), 'business_release_profile')
    keys = CONFIG_KEYS if profile == 'map-enabled' else CONFIG_KEYS[:-1]
    if isolation == 'sealed-memfd-capdrop':
        must(profile == 'poetry-only', 'business_sealed_profile')
        return keys + ('snapshot-transport',)
    return keys


def isolation_mode(policy):
    installation = policy.get('installation')
    service = installation.get('service') if type(installation) is dict else None
    return service.get('isolation') if type(service) is dict else None


def required_assets(profile):
    config_keys(profile)
    return ASSETS if profile == 'map-enabled' else {key: value for key, value in ASSETS.items() if key != 'china.json'}


def digest(value, length=64):
    must(type(value) is str and re.fullmatch('[a-f0-9]{' + str(length) + '}', value), 'business_digest_format')
    return value


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    def pairs(items):
        values = {}
        for key, value in items:
            must(key not in values, 'business_duplicate_json_key')
            values[key] = value
        return values
    def invalid(_):
        raise Refused('business_nonfinite_json')
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    except (ValueError, UnicodeError) as error:
        raise Refused('business_json_invalid') from error


def absolute(value):
    must(type(value) is str and value.startswith('/') and Path(value).as_posix() == value and
         '..' not in Path(value).parts and not any(x in value for x in ('\0', '\n', '\r', '${')), 'business_fixed_path')
    return Path(value)


def owner(value):
    must(type(value) is list and len(value) == 2 and all(type(x) is int and x >= 0 for x in value),
         'business_owner_contract')
    return tuple(value)


def read_bound(path, *, expected=None, expected_owner=None, mode=None, limit=1024*1024):
    """Descriptor traversal rejects links and pins bytes to the opened inode."""
    path = absolute(str(path))
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            before = os.fstat(fd)
            must(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 <= before.st_size <= limit,
                 'business_file_identity')
            if expected_owner is not None:
                must((before.st_uid, before.st_gid) == tuple(expected_owner), 'business_file_owner')
            if mode is not None:
                must(stat.S_IMODE(before.st_mode) == mode, 'business_file_mode')
            chunks, size = [], 0
            while True:
                chunk = os.read(fd, min(limit+1, 1024*1024))
                if not chunk:
                    break
                size += len(chunk)
                must(size <= limit, 'business_file_limit')
                chunks.append(chunk)
            after = os.fstat(fd)
            must((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                 (after.st_size, after.st_mtime_ns, after.st_ctime_ns) and size == before.st_size,
                 'business_file_changed')
            raw = b''.join(chunks)
            if expected is not None:
                must(sha(raw) == digest(expected), 'business_file_digest')
            return raw
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def validate_policy(policy):
    must(type(policy) is dict and policy.get('schema') == SCHEMA and
         policy.get('repository') in VERIFIED_REPOSITORIES,
         'business_policy_identity')
    legacy = policy.get('legacy_policy')
    must(type(legacy) is dict and legacy.get('schema') == 'R25_CORE_EQUIVALENT_PAYLOAD_POLICY_V1' and
         legacy.get('repository') == REPOSITORY, 'business_legacy_policy')
    digest(policy.get('legacy_policy_sha256'))
    for key in ('baseline_sha256', 'payload_fingerprint', 'application_configuration_sha256'):
        digest(legacy.get(key))
    digest(legacy.get('baseline_commit'), 40)
    for key in ('controllers', 'runtime_modules'):
        files = policy.get(key)
        must(type(files) is dict and files and all(type(x) is str and re.fullmatch('[a-zA-Z0-9_-]+\\.py', x)
                                                   for x in files), 'business_controller_contract')
        for value in files.values():
            digest(value)
    must(set(policy['controllers']) == {'lifecycle_adapter.py', 'r25-production-java-launch.py', 'core_release_guard.py'},
         'business_controller_contract')
    assistant=policy.get('upgrade',{}).get('capability')=='assistant-v1'
    expected_modules=RUNTIME_MODULES | ({'assistant_release.py','assistant_runtime.py','assistant_runtime_overlay.py','assistant_acceptance.py','assistant_backup.py','upgrade_assistant_controller.py'} if assistant else set())
    must(set(policy['runtime_modules']) == expected_modules, 'business_module_binding')
    up = policy.get('upgrade')
    must(type(up) is dict and up.get('capability') in ('knowledge-read-v1','assistant-v1'), 'business_capability')
    keys = config_keys(up.get('release_profile'), isolation_mode(policy))
    map_enabled = up['release_profile'] == 'map-enabled'
    identity = up.get('candidate')
    must(type(identity) is dict and set(identity) == {'repository', 'commit_sha', 'github_run_id', 'github_run_attempt',
         'artifact_sha256', 'artifact_bytes', 'payload_fingerprint'} and
         identity['repository'] == policy['repository'],
         'business_candidate_identity')
    digest(identity['commit_sha'], 40)
    for key in ('artifact_sha256', 'payload_fingerprint'):
        digest(identity[key])
    must(type(identity['artifact_bytes']) is int and 0 < identity['artifact_bytes'] < 512*1024*1024,
         'business_artifact_size')
    for key in ('github_run_id', 'github_run_attempt'):
        must(type(identity[key]) is str and re.fullmatch('[1-9][0-9]{0,19}', identity[key]), 'business_run_identity')
    baseline = up.get('baseline_release')
    must(type(baseline) is dict and set(baseline) == {'commit_sha', 'artifact_sha256', 'accepted_record_sha256'},
         'business_baseline_reference')
    for key, value in baseline.items():
        digest(value, 40 if key == 'commit_sha' else 64)
    must(baseline['artifact_sha256'] != identity['artifact_sha256'] and
         legacy['payload_fingerprint'] != identity['payload_fingerprint'], 'business_not_equivalent_repackaging')
    digest(up.get('web_commit'), 40)
    config = up.get('configuration')
    must(type(config) is dict and set(config) == ({'before_sha256', 'after_sha256', 'knowledge','assistant'} if assistant else {'before_sha256', 'after_sha256', 'knowledge'}), 'business_configuration')
    digest(config['before_sha256']); digest(config['after_sha256'])
    must((assistant or config['before_sha256'] == legacy['application_configuration_sha256']) and
         (config['before_sha256'] != config['after_sha256'] or
          (assistant and ('successor' in up or 'core_followup' in up) and config['before_sha256'] == config['after_sha256'])),
         'business_configuration_baseline')
    must(type(config['knowledge']) is dict and set(config['knowledge']) == set(keys), 'business_configuration_keys')
    must(config['knowledge']['map-enabled'] is map_enabled, 'business_configuration_profile')
    if isolation_mode(policy) == 'sealed-memfd-capdrop':
        must(config['knowledge'].get('snapshot-transport') == 'sealed-memfd-v1',
             'business_sealed_transport')
    absolute(config['knowledge']['snapshot-root'])
    for key in required_assets(up['release_profile']).values():
        digest(config['knowledge'][key])
    for part in ('assets', 'compatibility'):
        value = up.get(part)
        must(type(value) is dict, 'business_' + part + '_contract')
        prefix = 'manifest' if part == 'assets' else 'retention_manifest'
        absolute(value.get(prefix + '_path')); digest(value.get(prefix + '_sha256'))
    asset = up['assets']
    owner([asset.get('owner_uid'), asset.get('owner_gid')])
    evidence = up.get('evidence')
    evidence_keys = {'review_id', 'compatibility_sha256', 'acceptance_sha256'}
    if map_enabled:
        evidence_keys.add('map_permission_sha256')
    must(type(evidence) is dict and set(evidence) == evidence_keys,
         'business_review_evidence')
    must(type(evidence['review_id']) is str and re.fullmatch('[a-zA-Z0-9_-]{8,128}', evidence['review_id']), 'business_review_identity')
    for key in evidence_keys - {'review_id'}:
        digest(evidence[key])
    installation = policy.get('installation')
    must(type(installation) is dict, 'business_installation_contract')
    owner(installation.get('controller_owner')); owner(installation.get('private_owner'))
    service = installation.get('service')
    must(type(service) is dict and type(service.get('account')) is str and
         re.fullmatch('[a-zA-Z_][a-zA-Z0-9_-]*', service['account']), 'business_service_contract')
    owner([service.get('uid'), service.get('gid')])
    must(type(service.get('groups')) is list and
         all(type(x) is int and x >= 0 for x in service['groups']) and service['gid'] in service['groups'],
         'business_service_readonly_identity')
    must((service.get('isolation') == 'nonroot-posix' and service['uid'] > 0) or
         (service.get('isolation') in ('readonly-filesystem-capdrop', 'sealed-memfd-capdrop') and service['uid'] == 0),
         'business_service_isolation_contract')
    if service['isolation'] == 'sealed-memfd-capdrop':
        sealed = asset.get('sealed_owner')
        must(type(sealed) is dict and set(sealed) == {'account', 'uid', 'gid'} and
             type(sealed['account']) is str and re.fullmatch('[a-zA-Z_][a-zA-Z0-9_-]*', sealed['account']) and
             type(sealed['uid']) is int and sealed['uid'] > 0 and
             type(sealed['gid']) is int and sealed['gid'] == service['gid'],
             'business_sealed_owner_contract')
    else:
        must('sealed_owner' not in asset, 'business_sealed_owner_mode')
    pg = installation.get('postgres')
    must(type(pg) is dict and pg.get('host') == '127.0.0.1' and type(pg.get('port')) is int and
         1024 <= pg['port'] <= 65535, 'business_postgres_endpoint')
    for key in ('database', 'user', 'credential_account'):
        must(type(pg.get(key)) is str and re.fullmatch('[a-zA-Z_][a-zA-Z0-9_-]{0,62}', pg[key]), 'business_postgres_identifier')
    for key in ('psql', 'pgpass', 'native_configuration_path'):
        absolute(pg.get(key))
    for key in ('psql_sha256', 'native_configuration_sha256'):
        digest(pg.get(key))
    owner([pg.get('credential_uid'), pg.get('credential_gid')])
    owner(pg.get('psql_owner'))
    must(pg.get('psql_mode') in {0o500,0o555,0o700,0o755}, 'business_psql_mode')
    if 'ancestor_traversal_acl' in asset:
        acl = asset['ancestor_traversal_acl']
        must(service['isolation'] == 'sealed-memfd-capdrop' and service['uid'] == 0,
             'business_traversal_acl_mode')
        must(type(acl) is dict and set(acl) == {'path', 'sha256', 'credential_account', 'credential_uid'} and
             acl.get('path') == '/root', 'business_traversal_acl_contract')
        digest(acl['sha256'])
        must(acl['credential_account'] == pg['credential_account'] and
             type(acl['credential_uid']) is int and 0 < acl['credential_uid'] < 0xffffffff and
             acl['credential_uid'] == pg['credential_uid'], 'business_traversal_acl_credential')
        must(Path('/root') in absolute(config['knowledge']['snapshot-root']).parents,
             'business_traversal_acl_ancestor')
    if 'successor' in up:
        validate_successor(policy)
    if 'core_followup' in up:
        validate_core_followup(policy)
    if assistant:
        module=assistant_module(policy)
        module.validate_contract(up.get('assistant'))
        must(up['assistant'].get('policy_status') == 'PRODUCTION_REVIEWED',
             'assistant_isolated_policy_not_installable')
        must(up['assistant']['artifacts']['core']['sha256']==identity['artifact_sha256'] and
             up['assistant']['configuration_sha256']==config['after_sha256'] and
             up['assistant']['compatible_recovery_jar_sha256']==baseline['artifact_sha256'],'assistant_policy_binding')
    return policy


def assistant_module(policy):
    import core_release_guard as guard
    path=Path(__file__).with_name('assistant_release.py')
    raw=read_bound(path,expected=policy['runtime_modules']['assistant_release.py'],expected_owner=policy['installation']['private_owner'],mode=0o600)
    return guard.source_module(path,'r25_bound_assistant_release',raw)


def candidate_matches(policy, record):
    expected = validate_policy(policy)['upgrade']['candidate']
    must(type(record) is dict and all(record.get(key) == value for key, value in expected.items()),
         'business_candidate_not_approved')
    return expected


def config_for_release(policy, record):
    up = validate_policy(policy)['upgrade']
    if record.get('artifact_sha256') == up['candidate']['artifact_sha256']:
        candidate_matches(policy, record)
        if 'successor' in up:
            import core_release_guard as guard
            phase = guard.successor_configuration_phase(policy)
            must(phase != 'transition', 'successor_configuration_not_startable')
            return up['successor']['core_final_sha256'] if phase == 'final' else up['configuration']['after_sha256']
        return up['configuration']['after_sha256']
    must(all(record.get(key) == up['baseline_release'][key] for key in ('commit_sha', 'artifact_sha256')),
         'business_rollback_not_approved')
    return up['configuration']['before_sha256']


def render_configuration(before, policy):
    config = validate_policy(policy)['upgrade']['configuration']
    must(type(before) is bytes and sha(before) == config['before_sha256'], 'business_configuration_before')
    try:
        text = before.decode('utf-8')
    except UnicodeError as error:
        raise Refused('business_configuration_encoding') from error
    if 'successor' in policy['upgrade'] or 'core_followup' in policy['upgrade']:
        (validate_successor if 'successor' in policy['upgrade'] else validate_core_followup)(policy)
        validate_existing_assistant_configuration(before, policy)
        must(sha(before) == config['after_sha256'], 'successor_bridge_configuration_unchanged')
        return before
    if policy['upgrade']['capability']=='assistant-v1':
        values=config['assistant']
        allowed={'enabled','history-only','attachment-root','execution.enabled','execution.ai-base-url','execution.internal-token-file','execution.expected-config-sha256','execution.allow-loopback-http','storage.quota-bytes','storage.draft-lifetime','storage.minimum-free-bytes','storage.cleanup-enabled'}
        must(type(values) is dict and set(values)==allowed,'assistant_configuration_keys')
        must(values['storage.cleanup-enabled'] is False and type(values['enabled']) is bool and type(values['history-only']) is bool and type(values['execution.enabled']) is bool and type(values['execution.allow-loopback-http']) is bool,'assistant_configuration_mode')
        storage={key:value for key,value in values.items() if key.startswith('storage.')}
        must(sha(json.dumps(storage,sort_keys=True,separators=(',',':')).encode())==policy['upgrade']['assistant']['storage_policy_sha256'],'assistant_storage_policy_binding')
        must(values['attachment-root']==policy['upgrade']['assistant']['attachment_root'],'assistant_storage_root_binding')
        mode=policy['upgrade']['assistant']['mode']
        must(values['enabled']==(mode!='disabled') and values['history-only']==(mode=='history-only') and values['execution.enabled']==(mode=='enabled'),'assistant_configuration_mode')
        must(not re.search(r'(?im)^\s*(?:assistant|auralink\.assistant(?:\.[a-zA-Z0-9_-]+)*)\s*:',text),'assistant_configuration_existing')
        # Knowledge is retained exactly; no duplicate aliases or map enablement.
        for key,value in config['knowledge'].items():
            line='auralink.knowledge.'+key+': '+json.dumps(value,ensure_ascii=True)
            must(text.splitlines().count(line)==1,'assistant_existing_knowledge_configuration')
        after=before+(b'' if before.endswith(b'\n') else b'\n')+b'\n# R25 reviewed Assistant configuration\n'
        for key,value in sorted(values.items()):
            after+=('auralink.assistant.'+key+': '+json.dumps(value,ensure_ascii=True)+'\n').encode()
        must(sha(after)==config['after_sha256'],'business_configuration_after')
        return after
    # Finite first-install transformation, not a permissive YAML editor. Dotted
    # Spring property names avoid rewriting the existing nested auralink block.
    # Refuse preexisting aliases of knowledge, YAML documents/directives and
    # escaped keys; unknown syntax requires a separately reviewed transformer.
    existing_key = re.search(r'''(?:^|[\s{,])["']?(?:knowledge|auralink\.knowledge(?:\.[a-zA-Z0-9_-]+)*)["']?\s*:''', text, re.I)
    must(existing_key is None and not any(x in text for x in ('\\', '\t', '\0')) and
         not any(line.startswith(('---', '...', '%')) for line in text.splitlines()),
         'business_configuration_unsupported')
    after = before + (b'' if before.endswith(b'\n') else b'\n') + b'\n# R25 reviewed Knowledge configuration\n'
    for key in config_keys(policy['upgrade']['release_profile'], isolation_mode(policy)):
        after += ('auralink.knowledge.' + key + ': ' + json.dumps(config['knowledge'][key], ensure_ascii=True) + '\n').encode()
    must(sha(after) == config['after_sha256'], 'business_configuration_after')
    return after



def validate_core_followup(policy):
    """Core-only replacement after the completed finite Assistant successor.

    The old transition is retained as immutable evidence, never re-authorized
    against a different Core/POLICY. Runtime additionally checks its exact AI
    CURRENT, COMMITTED journal, snapshot, accepted source and G1 bytes.
    """
    up = policy['upgrade']; value = up['core_followup']
    must(up.get('capability') == 'assistant-v1' and 'successor' not in up and type(value) is dict and set(value) == {
        'schema','acceptance','predecessor_policy_sha256','ai_release','ai_policy_sha256','ai_transaction_id',
        'ai_active_sha256','ai_current_sha256','ai_journal_sha256','ai_snapshot_sha256','ai_configuration_sha256'},
        'core_followup_contract_fields')
    must(value['schema'] == 'R25_ASSISTANT_CORE_FOLLOWUP_V1', 'core_followup_contract_schema')
    must(value['acceptance']=='gallery-daily-v1','core_followup_acceptance_contract')
    for key in ('predecessor_policy_sha256','ai_policy_sha256','ai_active_sha256','ai_current_sha256',
                'ai_journal_sha256','ai_snapshot_sha256','ai_configuration_sha256'):digest(value[key])
    must(type(value['ai_transaction_id']) is str and re.fullmatch('[a-f0-9]{32}',value['ai_transaction_id']), 'core_followup_ai_transaction')
    ref=value['ai_release']
    must(type(ref) is dict and set(ref)=={'commit_sha','source_tree_sha256','record_sha256'}, 'core_followup_ai_reference')
    for key,item in ref.items():digest(item,40 if key=='commit_sha' else 64)
    config=up['configuration']
    must(config['before_sha256']==config['after_sha256'] and
         config['assistant']['execution.expected-config-sha256']==value['ai_configuration_sha256'] and
         up['assistant']['baseline_policy_sha256']==value['predecessor_policy_sha256'], 'core_followup_configuration_binding')
    return value


def validate_successor(policy):
    """Finite C1/G0 -> C1/G1 contract; not an arbitrary configuration editor."""
    up = policy['upgrade']; value = up['successor']
    must(up.get('capability') == 'assistant-v1' and type(value) is dict and set(value) == {
        'schema', 'ai_before', 'ai_candidate', 'ai_configuration', 'core_final_sha256'},
        'successor_contract_fields')
    must(value['schema'] == 'R25_ASSISTANT_SUCCESSOR_CONFIGURATION_V1', 'successor_contract_schema')
    before = value['ai_before']
    must(type(before) is dict and set(before) == {'commit_sha','source_tree_sha256','record_sha256'}, 'successor_ai_before')
    for key, item in before.items(): digest(item, 40 if key == 'commit_sha' else 64)
    candidate = value['ai_candidate']
    must(type(candidate) is dict and set(candidate) == {'repository','commit_sha','github_run_id',
        'github_run_attempt','source_tree_sha256','archive_sha256'} and
        candidate['repository'] == 'bailangguihun/auralink-ai-service', 'successor_ai_candidate')
    for key in ('commit_sha','source_tree_sha256','archive_sha256'):
        digest(candidate[key], 40 if key == 'commit_sha' else 64)
    for key in ('github_run_id','github_run_attempt'):
        must(type(candidate[key]) is str and re.fullmatch('[1-9][0-9]{0,19}',candidate[key]), 'successor_ai_run')
    must(candidate['source_tree_sha256'] != before['source_tree_sha256'], 'successor_ai_changed_source_required')
    config = value['ai_configuration']
    must(type(config) is dict and set(config) == {'before_sha256','after_sha256'}, 'successor_ai_config_fields')
    for item in config.values(): digest(item)
    digest(value['core_final_sha256'])
    bridge = up['configuration']
    must(bridge['before_sha256'] == bridge['after_sha256'] and
        bridge['assistant']['execution.expected-config-sha256'] == config['before_sha256'] and
        config['before_sha256'] != config['after_sha256'] and
        value['core_final_sha256'] != bridge['after_sha256'], 'successor_configuration_bindings')
    return value


def validate_existing_assistant_configuration(raw, policy):
    """Require the exact already installed dotted-property representation."""
    text = raw.decode('utf-8'); values = policy['upgrade']['configuration']['assistant']
    must(not any(x in text for x in ('\x00','\t')) and not any(
        x.startswith(('---','...','%')) for x in text.splitlines()), 'successor_configuration_syntax')
    actual = {}
    for line in text.splitlines():
        if re.search(r'(?i)(?:assistant|auralink\.assistant)', line) and not line.lstrip().startswith('#'):
            match = re.fullmatch(r'auralink\.assistant\.([a-zA-Z0-9_.-]+): (.+)', line)
            must(match is not None and match[1] not in actual, 'successor_configuration_alias')
            actual[match[1]] = strict_json(match[2])
    must(actual == values, 'successor_configuration_values')
    for key, value in policy['upgrade']['configuration']['knowledge'].items():
        must(text.splitlines().count('auralink.knowledge.' + key + ': ' + json.dumps(value,ensure_ascii=True)) == 1,
             'successor_knowledge_configuration')


def render_successor_configuration(before, policy):
    transition = validate_successor(policy)
    must(sha(before) == policy['upgrade']['configuration']['after_sha256'], 'successor_core_config_before')
    validate_existing_assistant_configuration(before, policy)
    config = transition['ai_configuration']
    prefix = b'auralink.assistant.execution.expected-config-sha256: '
    old = prefix + json.dumps(config['before_sha256']).encode() + b'\n'
    new = prefix + json.dumps(config['after_sha256']).encode() + b'\n'
    must(before.count(old) == 1, 'successor_core_config_anchor')
    after = before.replace(old, new)
    must(sha(after) == transition['core_final_sha256'], 'successor_core_config_after')
    return after


def validate_successor_ai_configuration(before, after, policy):
    """Only tested DashScope Beijing Flash thinking override may differ."""
    binding = validate_successor(policy)['ai_configuration']
    must(sha(before) == binding['before_sha256'] and sha(after) == binding['after_sha256'],
         'successor_ai_config_digest')
    old, new = strict_json(before), strict_json(after)
    import copy
    expected = copy.deepcopy(old)
    must(old.get('ragflow',{}).get('enabled') is False and type(old.get('models')) is list,
         'successor_ai_config_profile')
    selected = [x for x in expected['models'] if x.get('model') == 'qwen3.8-flash']
    must(len(selected) == 1 and selected[0].get('provider') == 'dashscope' and
         selected[0].get('baseUrl') == 'https://dashscope.aliyuncs.com/compatible-mode/v1' and
         selected[0].get('thinkingMode', 'provider-default') == 'provider-default',
         'successor_flash_route')
    selected[0]['thinkingMode'] = 'disabled'
    must(new == expected, 'successor_ai_config_unrelated_change')
    return new


def _service(policy):
    service = policy['installation']['service']
    account = pwd.getpwnam(service['account'])
    must((account.pw_uid, account.pw_gid) == (service['uid'], service['gid']) and
         sorted(set(os.getgrouplist(service['account'], service['gid']))) == sorted(set(service['groups'])),
         'business_service_account_drift')
    return service


def sealed_owner(policy):
    value = validate_policy(policy)['upgrade']['assets'].get('sealed_owner')
    must(isolation_mode(policy) == 'sealed-memfd-capdrop' and type(value) is dict,
         'business_sealed_owner_contract')
    account = pwd.getpwnam(value['account'])
    must(account.pw_uid == value['uid'] and account.pw_uid != policy['installation']['service']['uid'],
         'business_sealed_owner_drift')
    return {'owner_uid': value['uid'], 'owner_gid': value['gid']}


def _mode_access(info, service):
    if info.st_uid == service['uid']:
        return (info.st_mode >> 6) & 7
    if info.st_gid in service['groups']:
        return (info.st_mode >> 3) & 7
    return info.st_mode & 7


def asset_specs(policy):
    """Read the exact approved manifest; no source-path or transport substitution."""
    up = validate_policy(policy)['upgrade']
    asset = up['assets']
    assets = required_assets(up['release_profile'])
    manifest = strict_json(read_bound(asset['manifest_path'], expected=asset['manifest_sha256'],
        expected_owner=policy['installation']['private_owner'], mode=0o600))
    must(type(manifest) is dict and manifest.get('schemaVersion') == 2 and
         manifest.get('releaseProfile') == up['release_profile'] and
         type(manifest.get('artifacts')) is list and len(manifest['artifacts']) == len(assets), 'business_asset_manifest')
    entries = {x.get('filename'): x for x in manifest['artifacts'] if type(x) is dict}
    must(set(entries) == set(assets), 'business_asset_file_set')
    specs = {}
    for name, key in assets.items():
        item = entries[name]
        must(type(item.get('bytes')) is int and 0 < item['bytes'] <= 32*1024*1024 and
             item.get('sha256') == up['configuration']['knowledge'][key], 'business_asset_manifest_binding')
        specs[name] = {'bytes': item['bytes'], 'sha256': item['sha256']}
    return specs


def _verify_parent_acl(policy, parent, info):
    """Permit only an explicitly bound PG execute-only /root traversal ACL.

    The exception never applies to the asset directory/files or other parents.
    It grants no source read/write permission and does not change sealed-object
    protection. Opening the actual ancestor pins metadata and ACL to one inode.
    """
    acl = policy['upgrade']['assets'].get('ancestor_traversal_acl')
    if acl is None or parent != Path('/root'):
        must(not any(x.startswith('system.posix_acl_') for x in os.listxattr(parent)),
             'business_asset_acl_requires_review')
        return
    pg = policy['installation']['postgres']
    try:
        account = pwd.getpwnam(pg['credential_account'])
    except KeyError as error:
        raise Refused('business_traversal_acl_account_drift') from error
    must((account.pw_uid, account.pw_gid) == (pg['credential_uid'], pg['credential_gid']),
         'business_traversal_acl_account_drift')
    fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        must((before.st_dev, before.st_ino, before.st_ctime_ns) ==
             (info.st_dev, info.st_ino, info.st_ctime_ns) and stat.S_ISDIR(before.st_mode) and
             (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode)) == (0, 0, 0o710),
             'business_traversal_acl_directory_identity')
        must({x for x in os.listxattr(fd) if x.startswith('system.posix_acl_')} ==
             {'system.posix_acl_access'}, 'business_traversal_acl_attributes')
        raw = os.getxattr(fd, 'system.posix_acl_access')
        must(sha(raw) == acl['sha256'], 'business_traversal_acl_digest')
        # Linux POSIX ACL xattr v2: owner rwx, the bound PG credential uid --x,
        # owning group ---, mask --x, other ---. No named groups/extra users.
        entries = ((1, 7, 0xffffffff), (2, 1, pg['credential_uid']),
                   (4, 0, 0xffffffff), (16, 1, 0xffffffff), (32, 0, 0xffffffff))
        expected = struct.pack('<I', 2) + b''.join(struct.pack('<HHI', *entry) for entry in entries)
        must(raw == expected, 'business_traversal_acl_entries')
        after = os.fstat(fd)
        must((before.st_dev, before.st_ino, before.st_ctime_ns) ==
             (after.st_dev, after.st_ino, after.st_ctime_ns), 'business_traversal_acl_changed')
    finally:
        os.close(fd)


def _verified_assets(policy, *, include_bytes=False):
    up = validate_policy(policy)['upgrade']
    service = _service(policy)
    root = absolute(up['configuration']['knowledge']['snapshot-root'])
    filesystem_mode = service['isolation'] == 'readonly-filesystem-capdrop'
    sealed_mode = service['isolation'] == 'sealed-memfd-capdrop'
    protected_runtime = filesystem_mode or sealed_mode
    filesystem = sandbox_module(policy).filesystem_proof(root) if filesystem_mode else None
    if sealed_mode:
        sealed_owner(policy)
    # Sealed mode verifies the durable source at every preflight/relaunch. It
    # explicitly does NOT call a root-owned source directory globally read-only.
    # Only the inherited kernel-sealed anonymous byte objects have that guarantee.
    asset = up['assets']
    specs = asset_specs(policy)
    for parent in (root, *root.parents):
        info = parent.lstat()
        must(stat.S_ISDIR(info.st_mode) and not parent.is_symlink() and _mode_access(info, service) & 1,
             'business_asset_parent_permissions')
        if not protected_runtime:
            must(info.st_uid != service['uid'] and not _mode_access(info, service) & 2, 'business_asset_parent_permissions')
        _verify_parent_acl(policy, parent, info)
    info = root.lstat()
    must((info.st_uid, info.st_gid) == (asset['owner_uid'], asset['owner_gid']) and
         stat.S_IMODE(info.st_mode) == 0o550 and set(x.name for x in root.iterdir()) == set(specs),
         'business_asset_root_identity')
    material = {}
    for name, item in specs.items():
        path = root / name
        info = path.lstat()
        must(not any(x.startswith('system.posix_acl_') for x in os.listxattr(path)) and
             _mode_access(info, service) & 4 and not _mode_access(info, service) & 2, 'business_asset_file_permissions')
        if not protected_runtime:
            must(info.st_uid != service['uid'], 'business_asset_file_permissions')
        raw = read_bound(path, expected=item['sha256'], expected_owner=(asset['owner_uid'], asset['owner_gid']),
                         mode=0o440, limit=32*1024*1024)
        must(len(raw) == item['bytes'], 'business_asset_size')
        if include_bytes:
            material[name] = {**item, 'raw': raw}
    receipt = {'schema': 'R25_KNOWLEDGE_ASSETS_VERIFIED_V2', 'manifest_sha256': asset['manifest_sha256'],
               'release_profile': up['release_profile'], 'files': len(specs), 'service_readonly': not protected_runtime,
               'filesystem': filesystem, 'candidate_process_capability_proof_required': protected_runtime}
    if sealed_mode:
        receipt.update({'transport': 'sealed-memfd-v1', 'durable_source_readonly': False,
                        'candidate_sealed_descriptors_required': True})
    return receipt, material


def verify_assets(policy):
    return _verified_assets(policy)[0]


def acceptance_queries(policy):
    """Read the two approved SQL literals without importing a sibling module.

    The official Java launcher loads this guard by its bound file path; the
    publisher directory is deliberately absent from its Python import path.
    Parsing the verified source also avoids executing an ambient cached module.
    """
    import ast
    raw = read_bound(Path(__file__).with_name('core_acceptance.py'),
                     expected=policy['runtime_modules']['core_acceptance.py'],
                     expected_owner=policy['installation']['private_owner'], mode=0o600)
    try:
        tree = ast.parse(raw.decode('utf-8'))
    except (UnicodeError, SyntaxError) as error:
        raise Refused('business_acceptance_sql_source') from error
    expected = {'IDLE_SQL', 'PRIVATE_SAMPLE_SQL'}
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = {target.id for target in node.targets if isinstance(target, ast.Name)} & expected
            for name in names:
                must(name not in values and len(node.targets) == 1 and
                     isinstance(node.value, ast.Constant) and type(node.value.value) is str,
                     'business_acceptance_sql_literal')
                values[name] = node.value.value
    must(set(values) == expected, 'business_acceptance_sql_literals_missing')
    return frozenset(values.values())


def readonly_query(policy, sql, lease_fd):
    validate_policy(policy)
    pg = policy['installation']['postgres']
    must(type(sql) is str and sql in ({RETENTION_SQL, *acceptance_queries(policy)} | ({ASSISTANT_LEDGER_SQL,ASSISTANT_IDLE_SQL} if policy['upgrade']['capability']=='assistant-v1' else set())), 'business_readonly_sql_contract')
    must(type(lease_fd) is int and lease_fd >= 0, 'business_lease_required')
    # Credential owner comes from the approved native installation and actual
    # account database, never from the credential file's current stat alone.
    native = read_bound(pg['native_configuration_path'], expected=pg['native_configuration_sha256'],
                        expected_owner=policy['installation']['private_owner'], mode=0o600)
    lines = native.decode().splitlines()
    fields = {}
    for line in lines:
        if not line or line.startswith('#'):
            continue
        must('=' in line, 'business_native_configuration')
        key, value = line.split('=', 1)
        must(key not in fields, 'business_native_configuration')
        fields[key] = value
    account = pwd.getpwnam(pg['credential_account'])
    must(fields.get('R25_NATIVE_SERVICE_USER') == pg['credential_account'] and
         (account.pw_uid, account.pw_gid) == (pg['credential_uid'], pg['credential_gid']),
         'business_pgpass_installation_owner')
    psql_bytes = read_bound(pg['psql'], expected=pg['psql_sha256'], expected_owner=pg['psql_owner'],
                            mode=pg['psql_mode'], limit=32*1024*1024)
    read_bound(pg['pgpass'], expected_owner=(account.pw_uid, account.pw_gid), mode=0o600, limit=65536)
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'PGPASSFILE': pg['pgpass'],
           'PGOPTIONS': '-c default_transaction_read_only=on', 'PGCONNECT_TIMEOUT': '5'}
    # Execute the verified bytes, not a pathname that another process can
    # replace between verification and exec. The sealed memfd is immutable even
    # to its owner; its only lifetime is this bounded psql subprocess. Dynamic
    # libraries/interpreter remain part of the trusted native installation.
    import fcntl
    executable = os.memfd_create('r25-verified-psql', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        remaining = memoryview(psql_bytes)
        while remaining:
            written = os.write(executable,remaining)
            must(written > 0, 'business_psql_copy_failed')
            remaining = remaining[written:]
        os.fchmod(executable,0o500)
        fcntl.fcntl(executable,fcntl.F_ADD_SEALS,
                    fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        try:
            result = subprocess.run(['/proc/self/fd/'+str(executable), '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-h', pg['host'],
                                    '-p', str(pg['port']), '-U', pg['user'], '-d', pg['database']],
                                   input=sql, text=True, capture_output=True, env=env, timeout=20,
                                   pass_fds=(lease_fd,executable))
        except (OSError, subprocess.SubprocessError) as error:
            raise Refused('business_readonly_database_unknown') from error
    finally:
        os.close(executable)
    must(result.returncode == 0 and len(result.stdout) <= 65536, 'business_readonly_database_unknown')
    return result.stdout.strip()


def verify_compatibility(policy, query):
    up = validate_policy(policy)['upgrade']
    contract = up['compatibility']
    manifest = strict_json(read_bound(contract['retention_manifest_path'], expected=contract['retention_manifest_sha256'],
                          expected_owner=policy['installation']['private_owner'], mode=0o600,
                          limit=RETENTION_MANIFEST_LIMIT))
    must(type(manifest) is dict and set(manifest) == {'schema', 'database', 'port', 'major', 'versions', 'tables', 'media'} and
         manifest['schema'] == 'R25_CORE_RETAINED_DATA_V1', 'business_retention_manifest')
    pg = policy['installation']['postgres']
    must(manifest['database'] == pg['database'] and manifest['port'] == pg['port'] and manifest['major'] == 16 and
         (manifest['versions'] == ['V%03d' % x for x in range(1, 14)] if up['capability']=='knowledge-read-v1' else manifest['versions'] in [['V%03d' % x for x in range(1,n+1)] for n in (13,15,16)]) and type(manifest['tables']) is dict and
         set(manifest['tables']) == set(TABLES), 'business_retention_identity')
    for value in manifest['tables'].values():
        must(type(value) is dict and set(value) == {'rows', 'md5'} and type(value['rows']) is int and value['rows'] >= 0,
             'business_retention_table')
        digest(value['md5'], 32)
    try:
        current = strict_json(query(RETENTION_SQL))
    except Exception as error:
        raise Refused('business_retention_query_unknown') from error
    must(type(current) is dict and set(current) == {'database', 'port', 'major', 'versions', 'tables', 'active_work'} and
         type(current['active_work']) is int and current['active_work'] == 0, 'business_active_work_or_unknown')
    if up['capability']=='assistant-v1':
        helper=assistant_module(policy)
        ledger=strict_json(query(ASSISTANT_LEDGER_SQL))['ledger']
        entries=up['assistant']['migrations']; helper.migration_inventory(entries)
        must(len(ledger) in helper.PREFIX_LENGTHS and ledger==[{'version':x['version'],'sha256':x['sha256']} for x in entries[:len(ledger)]],'assistant_live_schema_binding')
        must(current['versions']==[x['version'] for x in ledger],'assistant_schema_observation')
    comparison=('database','port','major','tables') if up['capability']=='assistant-v1' else ('database','port','major','versions','tables')
    must(all(current[key] == manifest[key] for key in comparison),
         'business_retained_data_drift')
    # Exact media inventory in an approved private manifest is intentionally
    # strict; arbitrary historic JARs and missing/changed media cannot qualify.
    media = manifest['media']
    must(type(media) is list and 0 < len(media) <= 100000, 'business_media_manifest')
    names = set()
    for item in media:
        must(type(item) is dict and set(item) == {'path', 'bytes', 'sha256'}, 'business_media_entry')
        path = str(absolute(item['path']))
        must(path not in names and type(item['bytes']) is int and 0 <= item['bytes'] <= 128*1024*1024,
             'business_media_entry')
        names.add(path)
        raw = read_bound(path, expected=item['sha256'], limit=128*1024*1024)
        must(len(raw) == item['bytes'], 'business_media_size')
    return {'schema': 'R25_CORE_READONLY_COMPATIBILITY_V2', 'retention_manifest_sha256': contract['retention_manifest_sha256'],
            'tables_checked': len(TABLES), 'media_checked': len(media), 'active_work': 0,
            'candidate_artifact_sha256': up['candidate']['artifact_sha256'], 'database_writes': 0}


def verify_process(policy, pid, start_ticks, configuration_path, *, proc_root=Path('/proc'), require_isolated=True):
    """Attest the service that actually ran; approval cannot invent its UID.

    The lifecycle engine already binds executable/cwd/port. This supplements
    that identity with Linux credentials, zero effective capabilities and the
    absence of Java/Spring/Knowledge environment or command-line overrides.
    proc_root is a fixture API, never a CLI/runtime setting.
    """
    validate_policy(policy)
    service = _service(policy)
    must(type(pid) is int and pid > 0 and type(start_ticks) is int and start_ticks > 0,
         'business_process_identity')
    config = str(absolute(str(configuration_path)))
    root = Path(proc_root) / str(pid)
    def read(name):
        fd = os.open(root / name, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            chunks, size = [], 0
            while True:
                value = os.read(fd, 65536)
                if not value:
                    break
                size += len(value)
                must(size <= 1024*1024, 'business_process_limit')
                chunks.append(value)
            return b''.join(chunks)
        finally:
            os.close(fd)
    def ticks():
        value = read('stat').decode()
        must(value.startswith(str(pid) + ' (') and ') ' in value, 'business_process_identity')
        return int(value.rsplit(') ', 1)[1].split()[19])
    must(ticks() == start_ticks, 'business_process_identity')
    status = {}
    for line in read('status').decode().splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            status[key] = value.strip()
    must(status.get('Uid', '').split() == [str(service['uid'])] * 4 and
         status.get('Gid', '').split() == [str(service['gid'])] * 4 and
         sorted(set(int(x) for x in status.get('Groups', '').split()) | {service['gid']}) == sorted(set(service['groups'])),
         'business_process_service_identity')
    isolation = None
    if service['isolation'] in ('readonly-filesystem-capdrop', 'sealed-memfd-capdrop'):
        if require_isolated:
            if service['isolation'] == 'sealed-memfd-capdrop':
                isolation = sandbox_module(policy).verify_sealed(pid, asset_specs(policy), proc_root=proc_root, **sealed_owner(policy))
            else:
                isolation = sandbox_module(policy).verify(pid, policy['upgrade']['configuration']['knowledge']['snapshot-root'],
                                                        proc_root=proc_root)
    else:
        must(status.get('CapEff') is not None and int(status['CapEff'], 16) == 0, 'business_process_service_identity')
    environment = read('environ').split(b'\0')
    keys = [entry.partition(b'=')[0].decode('ascii', errors='strict') for entry in environment if entry]
    must(len(keys) == len(set(keys)) and not any(key.startswith(('SPRING_', 'AURALINK_KNOWLEDGE_') + (('AURALINK_ASSISTANT_',) if policy['upgrade']['capability']=='assistant-v1' else ())) or
         key in {'JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS', 'JDK_JAVA_OPTIONS'} for key in keys),
         'business_process_configuration_override')
    args = [x.decode('utf-8') for x in read('cmdline').split(b'\0') if x]
    required = '--spring.config.additional-location=file:' + config
    must(args.count(required) == 1 and not any(
        (arg.startswith(('--spring.config', '-Dspring.', '--auralink.knowledge', '-Dauralink.') + (('--auralink.assistant',) if policy['upgrade']['capability']=='assistant-v1' else ())) and arg != required)
        for arg in args), 'business_process_configuration_override')
    must(ticks() == start_ticks, 'business_process_identity')
    return {'service_uid': service['uid'], 'service_gid': service['gid'],
            'effective_capabilities': int(status['CapEff'],16), 'configuration_overrides': False,
            'candidate_isolation_verified': require_isolated, 'filesystem_isolation': isolation}


def sandbox_module(policy):
    """Read the exact installed helper source; never import a reusable .pyc."""
    path = Path(__file__).with_name('core_asset_sandbox.py')
    raw = read_bound(path, expected=policy['runtime_modules']['core_asset_sandbox.py'])
    module = types.ModuleType('r25_bound_core_asset_sandbox')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec', dont_inherit=True), module.__dict__)
    return module


def prepare_asset_sandbox(policy):
    """Launcher-child-only privilege transition, after all approval checks.

    A verified non-root service already uses POSIX separation. The legacy root
    launcher uses a separately provisioned, globally read-only tmpfs and drops
    all privileges in its existing PID. Sealed mode instead creates only anonymous inherited kernel objects. No asset mount, disk file or policy is written.
    """
    validate_policy(policy)
    service = _service(policy)
    must((os.geteuid(), os.getegid()) == (service['uid'], service['gid']), 'business_launcher_service_identity')
    if service['isolation'] == 'sealed-memfd-capdrop':
        receipt, material = _verified_assets(policy, include_bytes=True)
        sandbox_module(policy).prepare_sealed(material, **sealed_owner(policy))
    else:
        receipt = verify_assets(policy)
        if service['isolation'] == 'readonly-filesystem-capdrop':
            sandbox_module(policy).prepare(policy['upgrade']['configuration']['knowledge']['snapshot-root'])
    return receipt
