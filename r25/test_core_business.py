"""Synthetic contract fixtures only; no production credentials, assets or SQL."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import pwd
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import core_business as business


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def fixture_policy(root, profile='map-enabled', isolation='nonroot-posix'):
    """Reusable explicit test approval; never usable as a production approval."""
    root = Path(root)
    account = pwd.getpwuid(os.getuid())
    before = b'auralink:\n  critic:\n    policy: configured-provider\n'
    knowledge = {'snapshot-root': str(root / 'snapshot'), 'graph-sha256': '1'*64,
                 'stats-sha256': '2'*64, 'map-enabled': profile == 'map-enabled'}
    evidence = {'review_id':'SYNTHETIC-REVIEW-ONLY', 'compatibility_sha256':'3'*64, 'acceptance_sha256':'4'*64}
    if profile == 'map-enabled':
        knowledge['china-sha256'] = '3'*64
        evidence['map_permission_sha256'] = '5'*64
    if isolation == 'sealed-memfd-capdrop':
        knowledge['snapshot-transport'] = 'sealed-memfd-v1'
    after = before + b'\n# R25 reviewed Knowledge configuration\n' + b''.join(
        ('auralink.knowledge.' + key + ': ' + json.dumps(knowledge[key]) + '\n').encode() for key in business.config_keys(profile, isolation))
    legacy = {'schema': 'R25_CORE_EQUIVALENT_PAYLOAD_POLICY_V1', 'repository': business.REPOSITORY,
              'baseline_sha256': 'a'*64, 'baseline_commit': 'a'*40, 'payload_fingerprint': 'b'*64,
              'application_configuration_sha256': business.sha(before)}
    policy = {'schema': business.SCHEMA, 'repository': business.REPOSITORY,
        'legacy_policy': legacy, 'legacy_policy_sha256': business.sha(encoded(legacy)),
        'controllers': {name:'a'*64 for name in ('lifecycle_adapter.py', 'r25-production-java-launch.py', 'core_release_guard.py')},
        'runtime_modules': {name:'a'*64 for name in business.RUNTIME_MODULES},
        'installation': {'private_owner':[os.getuid(), os.getgid()], 'controller_owner':[os.getuid(), os.getgid()],
            'service': {'account':'nobody', 'uid':65534, 'gid':65534, 'groups':[65534], 'isolation':'nonroot-posix'},
            'postgres': {'psql':str(root / 'psql'), 'psql_sha256':business.sha(b'fixture-only'),
                'psql_owner':[os.getuid(),os.getgid()], 'psql_mode':0o700,
                'host':'127.0.0.1', 'port':25432, 'database':'fixture', 'user':'fixture',
                'pgpass':str(root / 'fixture.pgpass'), 'credential_account':account.pw_name,
                'credential_uid':os.getuid(), 'credential_gid':os.getgid(),
                'native_configuration_path':str(root / 'native-control.env'),
                'native_configuration_sha256':business.sha(('R25_NATIVE_SERVICE_USER='+account.pw_name+'\n').encode())}},
        'upgrade': {'capability':'knowledge-read-v1', 'release_profile':profile,
            'candidate': {'repository':business.REPOSITORY, 'commit_sha':'c'*40, 'github_run_id':'1234', 'github_run_attempt':'1',
                          'artifact_sha256':'d'*64, 'artifact_bytes':1234, 'payload_fingerprint':'e'*64},
            'baseline_release': {'commit_sha':'a'*40, 'artifact_sha256':'a'*64, 'accepted_record_sha256':'f'*64},
            'web_commit':'7'*40,
            'configuration': {'before_sha256':business.sha(before), 'after_sha256':business.sha(after), 'knowledge':knowledge},
            'assets': {'manifest_path':str(root/'assets.json'), 'manifest_sha256':'1'*64,
                       'owner_uid':os.getuid(), 'owner_gid':os.getgid()},
            'compatibility': {'retention_manifest_path':str(root/'retention.json'), 'retention_manifest_sha256':'2'*64},
            'evidence': evidence}}
    if isolation == 'sealed-memfd-capdrop':
        policy['installation']['service'] = {'account':'root','uid':0,'gid':0,'groups':[0], 'isolation':isolation}
        custodian = pwd.getpwnam('nobody')
        policy['upgrade']['assets']['sealed_owner'] = {'account':custodian.pw_name,'uid':custodian.pw_uid,'gid':0}
    return policy



class BusinessContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='r25-business-fixture-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.policy = fixture_policy(self.root)

    def write(self, name, raw):
        target = self.root / name
        target.write_bytes(raw)
        target.chmod(0o600)
        return target

    def retention(self):
        media = self.write('retained-image.bin', b'synthetic image fixture')
        manifest = {'schema':'R25_CORE_RETAINED_DATA_V1', 'database':'fixture', 'port':25432, 'major':16,
                    'versions':['V%03d'%n for n in range(1,14)],
                    'tables':{name:{'rows':3, 'md5':'a'*32} for name in business.TABLES},
                    'media':[{'path':str(media), 'bytes':media.stat().st_size, 'sha256':business.sha(media.read_bytes())}]}
        path = self.write('retention.json', encoded(manifest))
        self.policy['upgrade']['compatibility']['retention_manifest_sha256'] = business.sha(path.read_bytes())
        current = {k:v for k,v in manifest.items() if k not in ('schema','media')}
        current['active_work'] = 0
        return manifest, current

    def query_inputs(self):
        pg = self.policy['installation']['postgres']
        acceptance = Path(business.__file__).with_name('core_acceptance.py').read_bytes()
        self.write('core_acceptance.py', acceptance)
        self.policy['runtime_modules']['core_acceptance.py'] = business.sha(acceptance)
        location = patch.object(business, '__file__', str(self.root / 'core_business.py'))
        location.start()
        self.addCleanup(location.stop)
        self.write('psql', b'fixture-only').chmod(0o700)
        self.write('fixture.pgpass', b'localhost:25432:fixture:fixture:synthetic-not-a-secret\n')
        self.write('native-control.env', ('R25_NATIVE_SERVICE_USER='+pg['credential_account']+'\n').encode())

    def test_acceptance_sql_loads_without_publisher_import_path_or_module_execution(self):
        import subprocess
        import sys
        self.query_inputs()
        source = Path(__file__).with_name('core_business.py')
        self.write('core_business.py', source.read_bytes())
        self.write('policy.json', encoded(self.policy))
        # A fresh process loads the business module like the official launcher.
        # The acceptance source's imports would fail without the publisher path.
        script = """import importlib.util,json,pathlib,sys
root=pathlib.Path(sys.argv[1])
sys.modules['core_acceptance']=object()
spec=importlib.util.spec_from_file_location('bound_business',root/'core_business.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
queries=module.acceptance_queries(json.loads((root/'policy.json').read_text()))
assert len(queries)==2 and all(q.startswith('BEGIN READ ONLY;') for q in queries)
assert sys.modules['core_acceptance'].__class__ is object
"""
        result = subprocess.run([sys.executable, '-I', '-B', '-c', script, str(self.root)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_acceptance_sql_source_hash_permissions_and_literal_shape_fail_closed(self):
        self.query_inputs()
        path = self.root / 'core_acceptance.py'
        original = path.read_bytes()
        path.write_bytes(original + b'\n# changed source\n')
        with self.assertRaisesRegex(business.Refused, 'file_digest'):
            business.acceptance_queries(self.policy)
        path.write_bytes(original); path.chmod(0o644)
        with self.assertRaisesRegex(business.Refused, 'file_mode'):
            business.acceptance_queries(self.policy)
        path.chmod(0o600)
        for raw in (b'IDLE_SQL="x"\n', b'IDLE_SQL=1\nPRIVATE_SAMPLE_SQL="y"\n',
                    b'IDLE_SQL="x"\nIDLE_SQL="z"\nPRIVATE_SAMPLE_SQL="y"\n',
                    b'IDLE_SQL=make_query()\nPRIVATE_SAMPLE_SQL="y"\n'):
            with self.subTest(source=raw):
                path.write_bytes(raw)
                self.policy['runtime_modules']['core_acceptance.py'] = business.sha(raw)
                with self.assertRaisesRegex(business.Refused, 'acceptance_sql_literal'):
                    business.acceptance_queries(self.policy)

    def test_policy_and_candidate_bind_every_identity_field(self):
        self.assertIs(business.validate_policy(self.policy), self.policy)
        candidate = self.policy['upgrade']['candidate']
        self.assertEqual(business.candidate_matches(self.policy, candidate), candidate)
        for key in candidate:
            changed = dict(candidate)
            changed[key] = 5432 if key == 'artifact_bytes' else 'unexpected'
            with self.subTest(key=key), self.assertRaisesRegex(business.Refused, 'candidate_not_approved'):
                business.candidate_matches(self.policy, changed)

    def test_transferred_and_historical_candidates_keep_exact_policy_repository(self):
        legacy = deepcopy(self.policy['legacy_policy'])
        for repository in business.VERIFIED_REPOSITORIES:
            with self.subTest(repository=repository):
                value = deepcopy(self.policy)
                value['repository'] = value['upgrade']['candidate']['repository'] = repository
                business.validate_policy(value)
                business.candidate_matches(value, value['upgrade']['candidate'])
                self.assertEqual(value['legacy_policy'], legacy)
                other = next(x for x in business.VERIFIED_REPOSITORIES if x != repository)
                value['upgrade']['candidate']['repository'] = other
                with self.assertRaisesRegex(business.Refused, 'business_candidate_identity'):
                    business.validate_policy(value)

    def test_foreign_repositories_and_relabelled_legacy_policy_are_rejected(self):
        for repository in ('fork/auralink-core-api', 'Bailangguihun/auralink-core-api',
                           business.CURRENT_REPOSITORY + '.git'):
            value = deepcopy(self.policy)
            value['repository'] = value['upgrade']['candidate']['repository'] = repository
            with self.subTest(repository=repository), self.assertRaisesRegex(
                    business.Refused, 'business_policy_identity'):
                business.validate_policy(value)
        self.policy['legacy_policy']['repository'] = business.CURRENT_REPOSITORY
        with self.assertRaisesRegex(business.Refused, 'business_legacy_policy'):
            business.validate_policy(self.policy)

    def test_missing_review_map_permission_and_self_declared_capability_refuse(self):
        for field, value in (('capability','any-business'), ('web_commit','main')):
            policy = deepcopy(self.policy); policy['upgrade'][field] = value
            with self.assertRaises(business.Refused): business.validate_policy(policy)
        del self.policy['upgrade']['evidence']['map_permission_sha256']
        with self.assertRaisesRegex(business.Refused, 'review_evidence'): business.validate_policy(self.policy)

    def test_controller_owner_and_service_are_declared_not_historical_numbers(self):
        self.policy['installation']['controller_owner'] = [40123,40124]
        business.validate_policy(self.policy)
        self.policy['installation']['service']['uid'] = 0
        with self.assertRaisesRegex(business.Refused, 'service_isolation'): business.validate_policy(self.policy)

    def test_configuration_preserves_all_old_bytes_and_limits_map_profile_properties(self):
        before = b'auralink:\n  critic:\n    policy: configured-provider\n'
        result = business.render_configuration(before, self.policy)
        self.assertTrue(result.startswith(before))
        self.assertEqual(result.count(b'auralink.knowledge.'), 5)
        self.assertIn(b'policy: configured-provider', result)
        with self.assertRaisesRegex(business.Refused, 'configuration_before'):
            business.render_configuration(before.replace(b'configured-provider', b'changed-policy'), self.policy)
        self.policy['upgrade']['configuration']['knowledge']['provider'] = 'not-permitted'
        with self.assertRaisesRegex(business.Refused, 'configuration_keys'):
            business.render_configuration(before, self.policy)

    def test_guide_knowledge_root_is_preserved_not_confused_with_new_properties(self):
        before = b'auralink:\n  catalog-guide:\n    knowledge-root: /approved/guide-knowledge\n'
        config = self.policy['upgrade']['configuration']
        config['before_sha256'] = self.policy['legacy_policy']['application_configuration_sha256'] = business.sha(before)
        expected = before + b'\n# R25 reviewed Knowledge configuration\n' + b''.join(
            ('auralink.knowledge.'+key+': '+json.dumps(config['knowledge'][key])+'\n').encode() for key in business.CONFIG_KEYS)
        config['after_sha256'] = business.sha(expected)
        self.assertEqual(business.render_configuration(before,self.policy),expected)

    def test_existing_knowledge_and_ambiguous_yaml_fail_closed(self):
        for before in (b'auralink:\n  knowledge: {}\n', b'---\nauralink: {}\n', b'"know\\u006cedge": {}\n'):
            policy = deepcopy(self.policy)
            value = business.sha(before)
            policy['upgrade']['configuration']['before_sha256'] = value
            policy['legacy_policy']['application_configuration_sha256'] = value
            with self.assertRaisesRegex(business.Refused, 'configuration_unsupported'):
                business.render_configuration(before, policy)

    def test_rollback_is_exact_approved_baseline(self):
        baseline = self.policy['upgrade']['baseline_release']
        self.assertEqual(business.config_for_release(self.policy, baseline), self.policy['upgrade']['configuration']['before_sha256'])
        with self.assertRaisesRegex(business.Refused, 'rollback_not_approved'):
            business.config_for_release(self.policy, {**baseline, 'commit_sha':'0'*40})

    def test_file_reader_refuses_links_hardlinks_changes_permissions(self):
        target = self.write('file', b'hello')
        self.assertEqual(business.read_bound(target, expected=business.sha(b'hello'), mode=0o600), b'hello')
        link = self.root/'link'; link.symlink_to(target)
        with self.assertRaises(OSError): business.read_bound(link)
        hard = self.root/'hard'; os.link(target, hard)
        with self.assertRaisesRegex(business.Refused, 'file_identity'): business.read_bound(target)
        hard.unlink(); target.chmod(0o644)
        with self.assertRaisesRegex(business.Refused, 'file_mode'): business.read_bound(target, mode=0o600)

    def test_retained_data_checks_all_rows_and_media_without_legacy_baseline(self):
        _, current = self.retention()
        calls = []
        def query(sql): calls.append(sql); return json.dumps(current)
        result = business.verify_compatibility(self.policy, query)
        self.assertEqual(result['tables_checked'], 11)
        self.assertEqual(result['media_checked'], 1)
        self.assertEqual(result['database_writes'], 0)
        self.assertEqual(calls, [business.RETENTION_SQL])
        self.assertIn('catalog_batch_current_revisions', business.RETENTION_SQL)
        self.assertNotIn('WHERE', business.RETENTION_SQL.split("'active_work'")[0])
        self.assertNotIn('UPDATE ', business.RETENTION_SQL)

    def test_illustration_core_requires_the_already_committed_migration_ledger(self):
        from assistant_release import schema_prefixes

        _, current = self.retention()
        value = deepcopy(self.policy)
        migrations = [{'version': f'V{index:03d}', 'sha256': 'a' * 64} for index in range(1, 18)]
        value['upgrade'].update(capability='assistant-v1', assistant={'migrations': migrations},
                                core_followup={'schema': 'R25_ASSISTANT_ILLUSTRATION_CORE_PUBLICATION_V1'})
        helper = SimpleNamespace(migration_inventory=lambda entries: entries, schema_prefixes=schema_prefixes)

        def query(sql):
            if sql == business.ASSISTANT_LEDGER_SQL:
                return json.dumps({'ledger': ledger})
            self.assertEqual(sql, business.RETENTION_SQL)
            return json.dumps(dict(current, versions=[entry['version'] for entry in ledger]))

        with patch.object(business, 'validate_policy', return_value=value), \
                patch.object(business, 'assistant_module', return_value=helper):
            ledger = migrations[:16]
            with self.assertRaisesRegex(business.Refused, 'illustration_core_requires_committed_schema'):
                business.verify_compatibility(value, query)
            ledger = migrations
            self.assertEqual(business.verify_compatibility(value, query)['database_writes'], 0)

    def test_retained_row_changes_unknown_schema_and_active_work_refuse(self):
        _, current = self.retention()
        for change in ('table', 'schema', 'work', 'database'):
            bad = deepcopy(current)
            if change == 'table': bad['tables']['catalog_source_records']['md5'] = 'b'*32
            elif change == 'schema': bad['versions'] = ['V001']
            elif change == 'work': bad['active_work'] = 1
            else: bad['database'] = 'production'
            with self.subTest(change=change), self.assertRaises(business.Refused):
                business.verify_compatibility(self.policy, lambda _:json.dumps(bad))
        with self.assertRaisesRegex(business.Refused, 'query_unknown'):
            business.verify_compatibility(self.policy, lambda _: (_ for _ in ()).throw(OSError('fixture database unavailable')))

    def test_media_missing_changed_or_untrusted_manifest_refuse(self):
        _, current = self.retention()
        (self.root/'retained-image.bin').write_bytes(b'changed')
        with self.assertRaisesRegex(business.Refused, 'file_digest'):
            business.verify_compatibility(self.policy, lambda _:json.dumps(current))
        (self.root/'retained-image.bin').unlink()
        with self.assertRaises(FileNotFoundError): business.verify_compatibility(self.policy, lambda _:json.dumps(current))
        (self.root/'retention.json').write_bytes(b'{}')
        with self.assertRaisesRegex(business.Refused, 'file_digest'):
            business.verify_compatibility(self.policy, lambda _:json.dumps(current))

    def test_query_uses_installation_owner_minimal_environment_and_read_only_flags(self):
        self.query_inputs()
        with patch.object(business.subprocess, 'run', return_value=SimpleNamespace(returncode=0,stdout='0\n')) as run:
            self.assertEqual(business.readonly_query(self.policy, business.RETENTION_SQL, 7), '0')
        args, kwargs = run.call_args
        self.assertIn('-X', args[0]); self.assertIn('ON_ERROR_STOP=1', args[0])
        self.assertEqual(kwargs['pass_fds'][0],7)
        self.assertEqual(len(kwargs['pass_fds']),2)
        self.assertEqual(args[0][0],'/proc/self/fd/'+str(kwargs['pass_fds'][1]))
        self.assertEqual(set(kwargs['env']), {'PATH','LANG','PGPASSFILE','PGOPTIONS','PGCONNECT_TIMEOUT'})
        self.assertEqual(kwargs['env']['PGOPTIONS'], '-c default_transaction_read_only=on')
        self.assertNotIn('PGPASSWORD', kwargs['env'])

    def test_psql_exec_uses_sealed_verified_bytes_despite_path_replacement(self):
        import fcntl
        self.query_inputs()
        original=(self.root/'psql').read_bytes()
        def execute(args,**kwargs):
            executable=kwargs['pass_fds'][1]
            (self.root/'psql').write_bytes(b'replacement must never execute')
            self.assertEqual(os.pread(executable,len(original),0),original)
            self.assertEqual(fcntl.fcntl(executable,fcntl.F_GET_SEALS),
                fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
            with self.assertRaises(OSError): os.pwrite(executable,b'x',0)
            return SimpleNamespace(returncode=0,stdout='0')
        with patch.object(business.subprocess,'run',side_effect=execute):
            self.assertEqual(business.readonly_query(self.policy,business.RETENTION_SQL,7),'0')

    def test_wrong_pgpass_owner_and_native_configuration_refuse_before_psql(self):
        self.query_inputs()
        self.policy['installation']['postgres']['credential_uid'] += 1
        with patch.object(business.subprocess, 'run') as run:
            with self.assertRaisesRegex(business.Refused, 'pgpass_installation_owner'):
                business.readonly_query(self.policy, business.RETENTION_SQL, 7)
            run.assert_not_called()

    def test_query_failures_are_safe_codes_without_database_stderr(self):
        self.query_inputs()
        with patch.object(business.subprocess, 'run', return_value=SimpleNamespace(returncode=1,stdout='',stderr='private fixture detail')):
            with self.assertRaisesRegex(business.Refused, '^business_readonly_database_unknown$'):
                business.readonly_query(self.policy, business.RETENTION_SQL, 7)
        for sql in ('DELETE FROM catalog_paintings;', 'BEGIN READ ONLY; COMMIT; DELETE FROM catalog_paintings; ROLLBACK;'):
            with self.assertRaisesRegex(business.Refused, 'readonly_sql_contract'):
                business.readonly_query(self.policy, sql, 7)

    def test_actual_process_identity_overrides_and_capabilities_refuse(self):
        pid = self.root / '4321'; pid.mkdir()
        # stat field 22 is starttime; the process name may contain spaces.
        (pid/'stat').write_text('4321 (synthetic java) S ' + '0 '*18 + '123 0\n')
        service = self.policy['installation']['service']
        safe_status = 'Uid: 65534 65534 65534 65534\nGid: 65534 65534 65534 65534\nGroups: 65534\nCapEff: 0000000000000000\n'
        (pid/'status').write_text(safe_status)
        (pid/'environ').write_bytes(b'PATH=/usr/bin:/bin\0')
        config = str(self.root/'application.yml')
        (pid/'cmdline').write_bytes(('java\0-jar\0fixture.jar\0--spring.config.additional-location=file:'+config+'\0').encode())
        with patch.object(business, '_service', return_value=service):
            self.assertEqual(business.verify_process(self.policy,4321,123,config,proc_root=self.root)['service_uid'],65534)
            (pid/'environ').write_bytes(b'SPRING_APPLICATION_JSON={}\0')
            with self.assertRaisesRegex(business.Refused, 'configuration_override'):
                business.verify_process(self.policy,4321,123,config,proc_root=self.root)
            (pid/'environ').write_bytes(b'PATH=/usr/bin:/bin\0')
            (pid/'status').write_text(safe_status.replace('CapEff: 0000000000000000','CapEff: 0000000000000002'))
            with self.assertRaisesRegex(business.Refused, 'service_identity'):
                business.verify_process(self.policy,4321,123,config,proc_root=self.root)

    def test_root_service_requires_explicit_filesystem_isolation_and_bound_helper(self):
        service = self.policy['installation']['service']
        service.update(account='root',uid=0,gid=0,groups=[0],isolation='readonly-filesystem-capdrop')
        business.validate_policy(self.policy)
        helper = Path(business.__file__).with_name('core_asset_sandbox.py')
        self.policy['runtime_modules']['core_asset_sandbox.py'] = business.sha(helper.read_bytes())
        loaded = business.sandbox_module(self.policy)
        self.assertTrue(callable(loaded.prepare))
        self.policy['runtime_modules']['core_asset_sandbox.py'] = '0'*64
        with self.assertRaisesRegex(business.Refused,'file_digest'): business.sandbox_module(self.policy)

    def test_legacy_root_baseline_and_capdropped_candidate_have_distinct_process_contracts(self):
        service = self.policy['installation']['service']
        service.update(account='root',uid=0,gid=0,groups=[0],isolation='readonly-filesystem-capdrop')
        helper = Path(business.__file__).with_name('core_asset_sandbox.py')
        self.policy['runtime_modules']['core_asset_sandbox.py'] = business.sha(helper.read_bytes())
        pid = self.root/'9876';pid.mkdir()
        (pid/'stat').write_text('9876 (java) S '+'0 '*18+'456 0\n')
        status = 'Uid: 0 0 0 0\nGid: 0 0 0 0\nGroups: 0\n'+''.join(key+': 0000000000000001\n' for key in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb'))+'NoNewPrivs: 0\n'
        (pid/'status').write_text(status)
        (pid/'environ').write_bytes(b'PATH=/usr/bin:/bin\0')
        config = str(self.root/'application.yml')
        (pid/'cmdline').write_bytes(('java\0-jar\0fixture.jar\0--spring.config.additional-location=file:'+config+'\0').encode())
        asset_root = self.policy['upgrade']['configuration']['knowledge']['snapshot-root']
        (pid/'mountinfo').write_text('72 26 0:51 / '+asset_root+' ro,nosuid,nodev,noexec - tmpfs tmpfs ro,size=16384k\n')
        with patch.object(business,'_service',return_value=service):
            baseline = business.verify_process(self.policy,9876,456,config,proc_root=self.root,require_isolated=False)
            self.assertFalse(baseline['candidate_isolation_verified'])
            with self.assertRaisesRegex(RuntimeError,'capabilities_remaining'):
                business.verify_process(self.policy,9876,456,config,proc_root=self.root)
            (pid/'status').write_text(status.replace('0000000000000001','0000000000000000').replace('NoNewPrivs: 0','NoNewPrivs: 1'))
            candidate = business.verify_process(self.policy,9876,456,config,proc_root=self.root)
            self.assertTrue(candidate['candidate_isolation_verified'])
            self.assertTrue(candidate['filesystem_isolation']['superblock_read_only'])

    def test_asset_hash_missing_unsafe_permissions_are_checked(self):
        # Effective identity mocks here exercise branch decisions only. Actual
        # non-root namespace file access is a separate isolated acceptance.
        self.policy['upgrade']['assets']['manifest_sha256'] = business.sha(b'{}')
        self.write('assets.json', b'{}')
        with patch.object(business, '_service', return_value=self.policy['installation']['service']):
            with self.assertRaisesRegex(business.Refused, 'asset_manifest'):
                business.verify_assets(self.policy)
        (self.root/'assets.json').unlink()
        with patch.object(business, '_service', return_value=self.policy['installation']['service']):
            with self.assertRaises(FileNotFoundError): business.verify_assets(self.policy)


if __name__ == '__main__':
    unittest.main()
