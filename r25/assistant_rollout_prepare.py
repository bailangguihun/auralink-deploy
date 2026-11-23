#!/usr/bin/env python3
"""Prepare the finite Assistant controller approval from formal release inputs."""
from __future__ import annotations

import argparse
import copy
from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import zipfile

import ai_publish
import assistant_backup
import assistant_rollout_controller as controller
import core_release_guard as guard
import core_publish
from core_runtime import LIVE_FILES, env_values, replace_env
from publisher_transaction import Refused
from upgrade_assistant_controller import service_identities
from upgrade_core_controller import ControlUpgradeRuntime, upgrade_lease


REQUEST = 'R25_ASSISTANT_ILLUSTRATION_CORE_PUBLICATION_REQUEST_V1'
REF_FIELDS = ('repository', 'ref', 'commit', 'run_id', 'run_attempt')
CONTROLLER_FILES = {
    'ai-baseline.json', 'ai_publish.py', 'ai_release_candidate.py', 'ai_release_guard.py', 'ai_runtime.py',
    'assistant_acceptance.py', 'assistant_backup.py', 'assistant_release.py', 'assistant_rollout_binding.py',
    'assistant_rollout_controller.py', 'assistant_rollout_prepare.py', 'assistant_runtime.py',
    'assistant_runtime_overlay.py', 'control_overlay.py', 'core-release-candidate.py', 'core_acceptance.py',
    'core_asset_sandbox.py', 'core_business.py', 'core_publish.py', 'core_release_guard.py', 'core_runtime.py',
    'install_ai_controller.py', 'install_core_controller.py', 'publisher_transaction.py', 'r25_storage.py',
    'upgrade_assistant_controller.py', 'upgrade_core_controller.py', 'verify-r25-artifact.py',
}
VMM_FILES = ('core_release_guard.py', 'vmm_controller.py', 'vmm_lifecycle.py', 'vmm_service.py')
COMMUNITY_FILES = ('community_bundle.py', 'community_lifecycle.py', 'community_host.py', 'community_host_cli.py',
                   'community_migration.py', 'community_supervision.py', 'r25_storage.py')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def put(rt, path, raw):
    digest = sha(raw)
    if path.exists() or path.is_symlink():
        rt.identity(path, expected_sha256=digest)
    else:
        rt.storage.atomic_write_bytes(path, raw)
    return digest


def approval_input(rt, path, identity, name, expected, *, controller_manifest=False):
    """Read a canonical archive, with an explicit reviewed exact inventory."""
    manifest = rt.read(path / ('r25-controller-manifest.json' if controller_manifest else 'manifest.json'),
                       guard.digest_value(identity['manifest_sha256']))
    raw = guard.read_file(path / name, expected=guard.digest_value(identity['archive_sha256']), limit=32*1024*1024)
    want = {key: identity[key] for key in REF_FIELDS}
    expected_schema = {'r25-controller.zip': 1, 'vmm-extension.zip': 'R25_VMM_CONTROLLER_BUNDLE_V1',
                       'community-controller.zip': 'ARTLIVE_COMMUNITY_CONTROLLER_BUNDLE_V1'}[name]
    controller_fields = {'status', 'installation_performed', 'archive'}
    keys = set(REF_FIELDS) | {'schema', 'files'} | (controller_fields if controller_manifest else {'archive_sha256'})
    guard.must(manifest.keys() == keys and manifest['schema'] == expected_schema and
               all(manifest[key] == value for key, value in want.items()), 'illustration_formal_manifest')
    if controller_manifest:
        guard.must(manifest['status'] == 'NOT_DEPLOYED' and manifest['installation_performed'] is False and
                   manifest['archive'] == {'name': name, 'bytes': len(raw), 'sha256': sha(raw)},
                   'illustration_controller_archive_identity')
    else:
        guard.must(manifest['archive_sha256'] == sha(raw), 'illustration_formal_archive_identity')
    items = manifest['files']
    guard.must(type(items) is list and all(type(item) is dict and set(item) == {'path', 'bytes', 'sha256'} for item in items)
               and tuple(item['path'] for item in items) == tuple(expected), 'illustration_formal_file_inventory')
    rebuilt = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive, zipfile.ZipFile(rebuilt, 'w', compression=zipfile.ZIP_STORED) as output:
        guard.must(archive.namelist() == list(expected), 'illustration_formal_archive_inventory')
        files = {}
        for item, entry in zip(items, archive.infolist(), strict=True):
            size = item['bytes']
            guard.must(type(size) is int and 0 < size <= 1024 * 1024 and entry.file_size == size and
                       entry.compress_type == zipfile.ZIP_STORED, 'illustration_formal_archive_member')
            content = archive.read(entry)
            guard.must(sha(content) == guard.digest_value(item['sha256']), 'illustration_formal_archive_digest')
            files[item['path']] = content
            member = zipfile.ZipInfo(item['path'], (1980, 1, 1, 0, 0, 0))
            member.create_system = 3
            member.external_attr = (stat.S_IFREG | 0o600) << 16
            output.writestr(member, content)
    guard.must(rebuilt.getvalue() == raw, 'illustration_formal_archive_not_canonical')
    return files


def release_inputs(rt, request, old, files, bundle):
    core = request['core']
    incoming = core_publish.incoming_path(core['commit'], core['run_id'], core['run_attempt'])
    jar = incoming / 'auralink-backend-0.0.1-SNAPSHOT.jar'
    report = incoming / 'r25-artifact-verification.json'
    manifest = incoming / 'core-release-candidate.json'
    for path, key in ((jar, 'artifact_sha256'), (report, 'report_sha256'), (manifest, 'manifest_sha256')):
        rt.identity(path, expected_sha256=guard.digest_value(core[key]))
    name = 'core-release-candidate.py'
    rt.identity(guard.ROOT/'operations/r25-publisher'/name, expected_sha256=old['runtime_modules'][name])
    verifier = guard.source_module(guard.ROOT/'operations/r25-publisher'/name, 'illustration_core_candidate',
                                   guard.read_file(guard.ROOT/'operations/r25-publisher'/name,
                                                   expected=old['runtime_modules'][name]))
    identity = verifier.verify(manifest, jar, report, core['commit'], core['run_id'], core['run_attempt'],
                               expected_repository='bailangguihun/auralink-core-api')
    candidate = {'repository': identity['repository'], 'commit_sha': core['commit'],
                 'github_run_id': core['run_id'], 'github_run_attempt': core['run_attempt'],
                 'artifact_sha256': identity['artifact']['sha256'], 'artifact_bytes': identity['artifact']['bytes'],
                 'payload_fingerprint': guard.payload_fingerprint(jar)}
    ai = request['ai']
    folder = ai_publish.incoming_path(ai['commit'], ai['run_id'], ai['run_attempt'])
    manifest_raw = guard.read_file(folder/'ai-release-candidate.json', expected=ai['manifest_sha256'])
    archive_raw = guard.read_file(folder/'ai-source.zip', expected=ai['archive_sha256'], limit=16*1024*1024)
    ai_verifier = guard.source_module(bundle/'ai_release_candidate.py', 'illustration_formal_ai',
                                      files['ai_release_candidate.py'])
    verified = ai_verifier.verify(guard.strict_json(manifest_raw), archive_raw, ai['commit'], ai['run_id'], ai['run_attempt'])
    ai_manifest = guard.strict_json(manifest_raw)
    guard.must(ai_manifest['schema'] == 'AURALINK_R25_AI_CANDIDATE_V2' and
               ai_manifest['repository'] == 'bailangguihun/auralink-ai-service', 'illustration_ai_formal_v2')
    ai_policy_sha = old['upgrade']['core_followup']['ai_policy_sha256']
    ai_policy = guard.private_record(guard.ROOT/'runtime/ai-publications/POLICY.json', ai_policy_sha)
    guard.must(ai_policy['schema'] == 'R25_AI_SOURCE_POLICY_V2' and
               ai_policy['dependency_sha256'] == verified['dependency_sha256'], 'illustration_ai_dependency_binding')
    return candidate, {'commit_sha': ai['commit'], 'github_run_id': ai['run_id'],
                       'github_run_attempt': ai['run_attempt'], 'source_tree_sha256': verified['source_tree_sha256'],
                       'candidate_manifest_sha256': sha(manifest_raw), 'archive_sha256': sha(archive_raw),
                       'policy_sha256': ai_policy_sha}


def render_policy(old, plan, installed, candidate, material):
    value = copy.deepcopy(old)
    update = value['upgrade']
    update['baseline_release'] = plan['current_release']
    update['candidate'] = candidate
    update['core_followup'].update(schema=guard.ASSISTANT_ILLUSTRATION_CORE_SCHEMA,
                                  predecessor_policy_sha256=installed)
    update['configuration']['before_sha256'] = update['configuration']['after_sha256']
    assistant = update['assistant']
    assistant.update(baseline_policy_sha256=installed,
                     baseline_payload_fingerprint=old['upgrade']['candidate']['payload_fingerprint'],
                     compatible_recovery_jar_sha256=plan['current_release']['artifact_sha256'],
                     review_evidence_sha256=plan['review_evidence_sha256'])
    assistant['artifacts']['core'] = {'repository': candidate['repository'], 'commit': candidate['commit_sha'],
                                      'sha256': candidate['artifact_sha256']}
    formal = plan['deploy']
    assistant['artifacts']['deploy'] = {'repository': formal['repository'], 'commit': formal['commit'],
                                       'sha256': formal['archive_sha256']}
    for name in ('core_business.py', 'core_runtime.py', 'core_release_guard.py', 'assistant_rollout_binding.py',
                 'ai_release_guard.py', 'ai_runtime.py'):
        value['runtime_modules'][name] = sha(material[name])
    value['controllers']['core_release_guard.py'] = sha(material['guard'])
    value['controllers']['lifecycle_adapter.py'] = sha(material['lifecycle'])
    return value


def prepare(rt, request_sha256):
    request_sha256 = guard.digest_value(request_sha256)
    request_folder = guard.STORE / 'assistant-rollout-requests' / request_sha256
    request = rt.read(request_folder/'request.json', request_sha256)
    guard.must(request.keys() == {'schema', 'deploy', 'vmm', 'community', 'core', 'ai', 'web_commit',
                                  'review_evidence_sha256'} and request['schema'] == REQUEST,
               'illustration_rollout_request')
    review = guard.read_file(request_folder/'review.json', expected=request['review_evidence_sha256'])
    guard.strict_json(review)
    formal = request['deploy']
    for item in (formal, request['vmm'], request['community']):
        guard.must(all(item[key] == formal[key] for key in REF_FIELDS) and
                   item['repository'] == 'bailangguihun/auralink-deploy' and item['ref'] == 'refs/heads/release',
                   'illustration_same_formal_deploy_run')
        guard.digest_value(item['commit'], 40)
    with upgrade_lease(rt):
        rt.require_terminal_component_transactions()
        assistant_backup.require_terminal(rt)
        guard.policy()
        old_sha = rt.identity(guard.STORE/'POLICY.json')['sha256']
        old = rt.read(guard.STORE/'POLICY.json', old_sha)
        guard.must(old['upgrade']['core_followup']['schema'] == 'R25_REFERENCE_CORE_PUBLICATION_V1',
                   'illustration_reference_predecessor')
        current_release = rt.current()
        rt.verify_current(current_release)
        rt.require_no_active_work()
        bundle = guard.ROOT/'operations/r25-controller-candidates'/formal['commit']
        files = approval_input(rt, bundle, formal, 'r25-controller.zip', sorted(CONTROLLER_FILES), controller_manifest=True)
        guard.must(sha(files[Path(__file__).name]) == formal['tool_sha256'] == sha(Path(__file__).read_bytes()),
                   'illustration_prepare_tool_binding')
        vmm_folder = guard.ROOT/'operations/r25-vmm-candidates'/formal['commit']
        vmm = approval_input(rt, vmm_folder, request['vmm'], 'vmm-extension.zip', VMM_FILES)
        guard.must(vmm['core_release_guard.py'] == files['core_release_guard.py'], 'illustration_same_core_guard')
        community_folder = guard.ROOT/'operations/community-controller'/guard.digest_value(request['community']['manifest_sha256'])
        community_archive = guard.ROOT/'operations/r25-community-candidates'/formal['commit']
        community_files = approval_input(rt, community_archive, request['community'], 'community-controller.zip', COMMUNITY_FILES)
        rt.identity(community_folder/'manifest.json', expected_sha256=request['community']['manifest_sha256'])
        guard.must({path.name for path in community_folder.iterdir()} == set(COMMUNITY_FILES) | {'manifest.json'},
                   'illustration_community_installed_inventory')
        for name, content in community_files.items():
            rt.identity(community_folder/name, expected_sha256=sha(content), expected_bytes=len(content))
            guard.reject_legacy_bytecode(community_folder/name)
        candidate, ai_candidate = release_inputs(rt, request, old, files, bundle)
        marker = guard.ROOT/'runtime/vmm-controller/COMMUNITY_CODE_PUBLICATION_ACTIVE.json'
        code_controller = rt.read(marker)
        code_id = guard.digest_value(code_controller['approval_sha256'])
        guard.must(code_controller == {'schema': 'R25_COMMUNITY_CODE_CONTROLLER_V1', 'approval_sha256': code_id},
                   'illustration_community_original_pointer')
        registration = rt.read(guard.ROOT/'runtime/community/WATCHDOG.json')
        installation = guard.ROOT/'runtime/community/installations'/guard.digest_value(registration['plan_sha256'])
        code_plan = rt.read(installation/'CODE_ACTIVE.json')
        code_plan_id = guard.digest_value(code_plan['plan_sha256'])
        guard.must(code_plan == {'schema': 'ARTLIVE_COMMUNITY_CODE_PUBLICATION_V1', 'plan_sha256': code_plan_id},
                   'illustration_community_code_active')
        prior_id = guard.digest_value(rt.read(guard.STORE/'CONTROL_UPGRADE_ACTIVE.json')['approval_sha256'])
        prior_folder = controller.transaction_path(prior_id)
        prior_journal_sha = rt.identity(prior_folder/'journal.json')['sha256']
        guard.must(rt.read(prior_folder/'journal.json', prior_journal_sha).get('phase') == 'INSTALLED',
                   'illustration_prior_controller')
        selection = guard.ROOT/'runtime/config/frontend-selection.json'
        plan = {'schema': controller.SCHEMA, 'from_policy_schema': old['schema'], 'deploy': formal,
                'current_release': asdict(current_release), 'current_ai_release': rt.read(controller._pointer_paths()['ai_current_sha256'])['release'],
                'installed_control': {'approval_sha256': prior_id, 'journal_sha256': prior_journal_sha},
                'frontend': {'selection_sha256': rt.identity(selection)['sha256'],
                             'reference_sha256': rt.identity(selection.with_suffix('.reference'))['sha256']},
                'preserved': {name: rt.identity(LIVE_FILES[name])['sha256'] for name in controller.PRESERVED},
                'services': service_identities(rt.engine()), 'next_owner': 'c5ea',
                'controller_owner': old['installation']['controller_owner'], 'private_owner': old['installation']['private_owner'],
                'files': {}, 'operational': {}, 'review_evidence_sha256': sha(review),
                'assistant_rollout': {**{name: rt.identity(path)['sha256'] for name, path in controller._pointer_paths().items()},
                                      'core_candidate_manifest_sha256': request['core']['manifest_sha256'],
                                      'web_commit': guard.digest_value(request['web_commit'], 40), 'ai_candidate': ai_candidate,
                                      'community': {'code_controller_sha256': code_id, 'code_plan_sha256': code_plan_id,
                                                    'deploy': request['community'],
                                                    'cli_sha256': sha(community_files['community_host_cli.py'])}}}
        original = controller.targets(plan)
        renderer = guard.source_module(bundle/'control_overlay.py', 'illustration_renderer', files['control_overlay.py'])
        material = {name: (vmm if name=='vmm_controller.py' else files)[name] for name in controller.FILES}
        material.update(guard=files['core_release_guard.py'], **{'ai-guard': files['ai_release_guard.py']})
        material['lifecycle'] = renderer.render_assistant_rollout_lifecycle(guard.read_file(
            original['lifecycle'][0], owner=original['lifecycle'][1], mode=original['lifecycle'][2]))
        next_policy = render_policy(old, plan, old_sha, candidate, material)
        validator = guard.source_module(bundle/'core_business.py', 'illustration_business_validator', files['core_business.py'])
        validator.validate_policy(next_policy)
        guard.verify_core_followup_context(next_policy, old, validator)
        material['policy'] = guard.encoded(next_policy)
        old_handoff = guard.read_file(LIVE_FILES['handoff'])
        handoff = env_values(old_handoff)
        guard.must(handoff['R25_CORE_RELEASE_POLICY_SHA256'] == old_sha, 'illustration_old_handoff_policy')
        material['handoff'] = replace_env(old_handoff, {
            'R25_CORE_RELEASE_POLICY_SHA256': sha(material['policy']),
            'R25_CORE_RELEASE_GUARD_SHA256': sha(material['guard']),
            'R25_AI_RELEASE_GUARD_SHA256': sha(material['ai-guard']),
            'R25_LIFECYCLE_ADAPTER_SHA256': sha(material['lifecycle']),
            'R25_VMM_CONTROLLER_SHA256': sha(material['vmm_controller.py']),
        })
        for name, (path, owner, mode) in original.items():
            before = rt.identity(path, expected_owner=owner, expected_mode=mode)['sha256'] if path.exists() or path.is_symlink() else None
            branch = 'files' if name in controller.FILES else 'operational'
            plan[branch][name] = {'before_sha256': before, 'after_sha256': sha(material[name])}
        controller.validate(plan)
        raw_plan = guard.encoded(plan)
        ident = sha(raw_plan)
        folder = controller.transaction_path(ident)
        rt.storage.mkdir_private(folder)
        rt.storage.mkdir_private(folder/'after')
        for name, raw in material.items():
            put(rt, folder/'after'/name, raw)
        put(rt, folder/'review.json', review)
        put(rt, folder/'UNSIGNED_PLAN.json', raw_plan)
    return {'outcome': 'ILLUSTRATION_CONTROLLER_PREPARED_NOT_INSTALLED', 'approval_sha256': ident,
            'unsigned_plan': str(folder/'UNSIGNED_PLAN.json')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'install', 'recover'))
    parser.add_argument('--request-sha256')
    parser.add_argument('--approval-sha256')
    args = parser.parse_args(argv)
    rt = ControlUpgradeRuntime()
    try:
        if args.action=='prepare' and args.request_sha256 and not args.approval_sha256:
            result = prepare(rt, args.request_sha256)
        elif args.action in {'install', 'recover'} and not args.request_sha256 and args.approval_sha256:
            result = getattr(controller, args.action)(rt, approval_sha256=args.approval_sha256)
        else:
            parser.error('pass the exact request or approval digest for the selected operation')
    except (OSError, ValueError, Refused, guard.Refused) as error:
        print(json.dumps({'outcome': 'ILLUSTRATION_CONTROLLER_REFUSED', 'reason': str(error)}), file=sys.stderr)
        return 65
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
