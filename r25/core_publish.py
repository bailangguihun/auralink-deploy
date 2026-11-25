#!/usr/bin/env python3
"""R25 normal Core CLI; install controller once before enabling release Actions.

All runtime paths are fixed. Incoming identity is passed independently by the
authenticated workflow; the manifest may not choose its own expected run/SHA.
No executable supplied by a candidate is imported or invoked by this program.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import signal
import sys

# Direct CLI execution must not import stale sibling bytecode before policy
# validation. -B alone suppresses writes but still reads existing caches.
if __name__ == '__main__':
    folder = Path(__file__).absolute().parent
    cache = folder / '__pycache__'
    sources = ('core_release_guard.py', 'core_runtime.py', 'core_acceptance.py',
               'publisher_transaction.py', 'r25_storage.py')
    candidates = []
    for name in sources:
        path = folder / name
        candidates.extend((path.with_suffix('.pyc'), path.with_suffix('.pyo')))
        if cache.exists():
            candidates.extend(cache.glob(path.stem + '.*.pyc'))
            candidates.extend(cache.glob(path.stem + '.*.pyo'))
        candidates.extend(Path(importlib.util.cache_from_source(str(path), optimization=level))
                          for level in ('', '1', '2'))
    if cache.is_symlink() or any(path.exists() or path.is_symlink() for path in candidates):
        print(json.dumps({'outcome': 'REFUSED', 'reason': 'controller_bundle_bytecode_cache'}), file=sys.stderr)
        raise SystemExit(65)
    sys.dont_write_bytecode = True

import core_release_guard as guard
from core_runtime import CoreRuntime, must
from publisher_transaction import Publisher, Release, PublicationFailed

INCOMING = Path('/root/autodl-tmp/auralink-core-deploy/incoming')


def incoming_path(commit, run_id, attempt):
    guard.digest_value(commit, 40)
    must(all(type(value) is str and re.fullmatch('[1-9][0-9]{0,19}', value)
             for value in (run_id, attempt)), 'github_run_identity')
    return INCOMING / (commit + '-' + run_id + '-' + attempt)


def prepare_incoming(rt, commit, run_id, attempt):
    path = incoming_path(commit, run_id, attempt)
    with rt.lease():
        rt.storage.mkdir_private(INCOMING)
        rt.storage.mkdir_private(path)
    return {'outcome': 'INCOMING_READY', 'path': str(path)}


def stage(rt, commit, run_id, attempt):
    folder = incoming_path(commit, run_id, attempt)
    with rt.lease():
        rt.require_terminal_component_transactions()
        rt.storage.validate_target(folder, exists=True, directory=True,
                                   expected_owner=(0, 0), expected_mode=0o700)
        jar = folder / 'auralink-backend-0.0.1-SNAPSHOT.jar'
        report = folder / 'r25-artifact-verification.json'
        manifest = folder / 'core-release-candidate.json'
        for path in (jar, report, manifest):
            rt.identity(path)
        verifier_path = Path(__file__).with_name('core-release-candidate.py')
        verifier = guard.source_module(verifier_path, 'trusted_core_candidate', guard.read_file(verifier_path))
        policy = guard.policy()
        business = policy.get('schema') == guard.BUSINESS_POLICY_SCHEMA
        # The installed approval, never the incoming manifest, supplies the
        # expected repository. V1 remains bound to its historical repository.
        expected_repository = (policy['upgrade']['candidate']['repository'] if business
                               else guard.REPOSITORY)
        candidate = verifier.verify(manifest, jar, report, commit, run_id, attempt,
                                    expected_repository=expected_repository)
        digest = candidate['artifact']['sha256']
        fingerprint = guard.payload_fingerprint(jar)
        business_identity = {'repository': candidate['repository'], 'commit_sha': commit,
            'github_run_id': run_id, 'github_run_attempt': attempt, 'artifact_sha256': digest,
            'artifact_bytes': candidate['artifact']['bytes'], 'payload_fingerprint': fingerprint}
        manifest_sha256 = rt.identity(manifest)['sha256']
        if business:
            guard.verify_assistant_rollout_successor_manifest(policy, manifest_sha256)
            module = guard.business_module(policy)
            module.candidate_matches(policy, business_identity)
            module.verify_assets(policy)
            module.verify_compatibility(policy, rt.query)
        else:
            must(fingerprint == policy['payload_fingerprint'], 'executable_change_requires_new_reviewed_acceptance_contract')
        release_dir = guard.STORE / 'releases' / (digest + '-' + commit)
        rt.storage.mkdir_private(release_dir)
        target = release_dir / 'backend.jar'
        if target.exists():
            rt.identity(target, expected_sha256=digest, expected_bytes=candidate['artifact']['bytes'])
        else:
            rt.storage.atomic_copy_file(jar, target, digest, expected_bytes=candidate['artifact']['bytes'])
        document = {'schema': guard.BUSINESS_RECORD_SCHEMA if business else guard.RECORD_SCHEMA,
                    'repository': candidate['repository'],
                    'commit_sha': commit, 'github_run_id': run_id, 'github_run_attempt': attempt,
                    'artifact_sha256': digest, 'artifact_bytes': candidate['artifact']['bytes'],
                    'candidate_manifest_sha256': manifest_sha256,
                    'policy_sha256': rt.identity(guard.STORE / 'POLICY.json')['sha256'],
                    'payload_fingerprint': fingerprint,
                    'scope': ('explicit knowledge-read-v1 business upgrade; authenticated acceptance before CURRENT' if business else
                              'reviewed executable payload equivalence; live HTTP acceptance occurs before CURRENT commit')}
        record_path = release_dir / 'record.json'
        if record_path.exists():
            # A repeat workflow can submit the same independently verified
            # commit/JAR. Keep the first immutable run receipt for that pair.
            previous = rt.read(record_path)
            must(all(previous.get(key) == document[key] for key in (
                'schema', 'repository', 'commit_sha', 'artifact_sha256',
                'artifact_bytes', 'policy_sha256', 'payload_fingerprint',
                *(['github_run_id', 'github_run_attempt'] if business else []))), 'release_record_conflict')
        else:
            rt.storage.atomic_write_json(record_path, document)
        release = Release(commit, digest, rt.identity(record_path)['sha256'])
        rt.verify_candidate(release)
        return release


def main(argv=None):
    def interrupted(_signum, _frame):
        raise InterruptedError('publication_interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare-incoming', 'stage', 'publish'):
        command = modes.add_parser(name)
        for option in ('commit', 'run-id', 'run-attempt'):
            command.add_argument('--' + option, required=True)
    for name in ('recover', 'rollback'):
        modes.add_parser(name).add_argument('--transaction', required=True)
    modes.add_parser('status')
    for name in ('assistant-backup-run', 'assistant-backup-recover', 'assistant-backup-status'):
        modes.add_parser(name)
    modes.add_parser('assistant-backup-retention').add_argument('--apply', action='store_true')
    modes.add_parser('assistant-generation').add_argument('action', choices=('status', 'suspend', 'resume'))
    args = parser.parse_args(argv)
    try:
        rt = CoreRuntime()
        if args.command == 'prepare-incoming':
            result = prepare_incoming(rt, args.commit, args.run_id, args.run_attempt)
        elif args.command in {'stage', 'publish'}:
            release = stage(rt, args.commit, args.run_id, args.run_attempt)
            result = ({'outcome': 'COMPATIBLE_CANDIDATE_STAGED', 'release': asdict(release)}
                      if args.command == 'stage' else asdict(Publisher(rt).publish(release)))
        elif args.command in {'recover', 'rollback'}:
            result = asdict(getattr(Publisher(rt), args.command)(args.transaction))
        elif args.command.startswith('assistant-'):
            with rt.lease():
                module = rt.assistant_backup_module()
                must(module is not None, 'assistant_controller_required')
                contract = guard.policy()['upgrade']['assistant']
                if args.command == 'assistant-generation':
                    result = module.generation_control(rt, contract, args.action)
                elif args.command == 'assistant-backup-status':
                    path = guard.ROOT / 'runtime/assistant-backups/assistant-daily-v1/ACTIVE.json'
                    result = {'outcome': 'NOT_STARTED'} if not path.exists() else rt.read(path)
                    if path.exists():
                        module.require_terminal(rt)
                        result['outcome'] = 'BACKUP_TERMINAL_VERIFIED'
                else:
                    job = module.ScheduledBackup(rt, contract)
                    if args.command == 'assistant-backup-run':
                        result = job.run()
                    elif args.command == 'assistant-backup-recover':
                        result = job.recover()
                    else:
                        result = job.retention(apply=args.apply)
        else:
            with rt.lease():
                release = rt.current()
                rt.verify_current(release)
                result = {'outcome': 'CURRENT_VERIFIED', 'release': asdict(release)}
        print(json.dumps(result, sort_keys=True))
        return 0
    except PublicationFailed as error:
        print(json.dumps({'outcome': 'PUBLICATION_FAILED', 'transaction_id': error.transaction_id,
                          'restored': error.recovered, 'primary_failure': error.primary_failure,
                          'recovery_failure': error.recovery_failure}, sort_keys=True), file=sys.stderr)
        return 70
    except Exception as error:
        # Existing diagnostics remain private on-host. SQL/config/provider
        # exception text must not enter public Actions logs.
        print(json.dumps({'outcome': 'REFUSED', 'failure_class': type(error).__name__,
                          'reason': (str(error) if type(error).__name__ in {'Refused', 'StorageRefused'} and
                                     re.fullmatch('[a-z][a-z0-9_]{0,100}', str(error)) else 'private_diagnostic_required')}), file=sys.stderr)
        return 65


if __name__ == '__main__':
    raise SystemExit(main())
