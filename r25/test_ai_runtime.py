"""Real adapter/file transaction tests with synthetic process and HTTP boundaries.

These execute AIRuntime methods against temporary files, injecting failures
around every destructive phase. They never start providers or production PIDs.
R25Storage's separate Linux suite covers descriptor/owner/fsync guarantees.
"""
import contextlib
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import ai_release_candidate as candidate
import ai_release_guard as guard
import ai_runtime as runtime
import ai_publish
import install_ai_controller as installer
from publisher_transaction import Release


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class Files:
    """Filesystem test boundary: real bytes, exclusive creation and SHA CAS."""
    def file_identity(self, path, expected_sha256=None, expected_bytes=None, **_):
        path = Path(path)
        raw = path.read_bytes()
        if path.is_symlink() or (expected_sha256 is not None and sha(raw) != expected_sha256):
            raise RuntimeError('identity')
        if expected_bytes is not None and len(raw) != expected_bytes:
            raise RuntimeError('size')
        return {'path': str(path), 'sha256': sha(raw), 'bytes': len(raw)}

    def read_private_record(self, path, expected_sha256=None):
        self.file_identity(path, expected_sha256)
        return guard.core.strict_json(Path(path).read_bytes())

    def atomic_write_bytes(self, path, raw, replace=False, expected_existing_sha256=None, **_):
        path = Path(path)
        if path.exists():
            if not replace:
                raise FileExistsError(path)
            self.file_identity(path, expected_existing_sha256)
        elif replace:
            raise FileNotFoundError(path)
        path.write_bytes(raw)
        return self.file_identity(path)

    def atomic_write_json(self, path, value, **kwargs):
        return self.atomic_write_bytes(path, guard.core.encoded(value), **kwargs)

    def atomic_copy_file(self, source, target, expected_sha256, **kwargs):
        self.file_identity(source, expected_sha256, kwargs.get('expected_bytes'))
        return self.atomic_write_bytes(target, Path(source).read_bytes(), **kwargs)

    def mkdir_private(self, folder):
        Path(folder).mkdir(exist_ok=True)

    def validate_target(self, path, **_):
        if not Path(path).is_dir() or Path(path).is_symlink():
            raise RuntimeError('directory')


class Engine:
    def __init__(self, fixture):
        self.f = fixture
        self.v = {'R25_INTERNAL_TOKEN_FILE': str(fixture.root / 'runtime/config/internal-token'),
                  'R25_PROVIDER_ENV': 'private-provider-env-not-read', 'R25_GUIDE_PY': 'fixed-python'}
        self.tunnel = self

    def active(self, name, port):
        self.f.events.append('active:' + name)
        return self.f.processes.get(name)

    def watchdog_stop_locked(self):
        self.f.events.append('stop:watchdog')
        self.f.watchdog_running = False

    def watchdog_start_locked(self):
        self.f.events.append('start:watchdog')
        self.f.watchdog_running = True

    def watchdog_identity(self):
        return {'pid': 55, 'ticks': 55} if self.f.watchdog_running else None

    def stop_role(self, name, port):
        self.f.events.append('stop:' + name)
        if self.f.foreign == name:
            raise RuntimeError('foreign_pid_not_owned')
        self.f.processes.pop(name, None)

    def start_role(self, name, port, command, environment, url):
        self.f.events.append('start:' + name)
        self.f.commands.append((name, command, environment, url))
        self.f.processes[name] = {'pid': 200 + port, 'ticks': 300 + port}

    def start_critic_locked(self):
        self.start_role('critic', 19085, ['trusted-critic-wrapper'], {}, '/health')

    def start_java_locked(self, fd):
        self.f.events.append('start:java')
        self.f.processes['java'] = {'pid': 101, 'ticks': 102}

    def frontend(self, action, fd):
        self.f.events.append('frontend:' + action)

    def status(self):
        self.f.events.append('tunnel:status')

    def load_execution_policy(self):
        self.f.events.append('load:execution-policy')


class Core:
    def __init__(self, fixture):
        self.f = fixture
        self.fd = None
        self.release = Release('1' * 40, '2' * 64, '3' * 64)
        self.last_http = {'gallery_total': 40}

    @contextlib.contextmanager
    def lease(self):
        if self.fd is not None:
            raise RuntimeError('nested_lock')
        self.fd = 12
        self.f.events.append('lease:acquire')
        try:
            yield
        finally:
            self.f.events.append('lease:release')
            self.fd = None

    def require_lease(self):
        if self.fd != 12:
            raise RuntimeError('lease_required')

    def current(self):
        return self.release

    def verify_current(self, ref):
        if ref != self.release:
            raise RuntimeError('core_drift')
        if any(self.f.processes.get(name) is None for name in ('java', 'guide', 'critic')):
            raise RuntimeError('current_process_missing')
        self.f.events.append('verify:core')

    def engine(self, partial_recovery=False):
        self.require_lease()
        bound = b'R25_AI_RELEASE_GUARD=' in (self.f.root / 'runtime/handoff-control.env').read_bytes()
        if not partial_recovery and (self.f.hook_enabled or bound):
            guard.validate_critic_identity(self.f.root, 'main.py')
        return Engine(self.f)

    def require_no_active_work(self):
        self.f.events.append('verify:idle')
        if self.f.busy:
            raise RuntimeError('active_work')

    def query(self, sql):
        raise AssertionError('unexpected direct SQL in test')


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = self.root / 'runtime/ai-publications'
        self.core_store = self.root / 'runtime/core-publications'
        self.incoming = self.root / 'incoming'
        for path in (self.store / 'releases', self.store / 'transactions', self.core_store,
                     self.root / 'runtime/config', self.root / 'operations', self.incoming):
            path.mkdir(parents=True)
        (self.root / 'runtime/config/internal-token').write_text('a' * 72)
        self.events, self.commands = [], []
        self.busy, self.foreign, self.hook_enabled = False, None, True
        self.watchdog_running = True
        self.processes = {name: {'pid': n, 'ticks': n + 10} for n, name in enumerate(('guide', 'critic', 'java'), 10)}
        self.files = Files()
        self.patches = []
        for module, key, value in ((runtime, 'ROOT', self.root), (runtime, 'STORE', self.store),
            (runtime, 'INCOMING', self.incoming), (guard, 'ROOT', self.root), (guard, 'STORE', self.store),
            (guard.core, 'STORE', self.core_store), (ai_publish, 'STORE', self.store), (ai_publish, 'INCOMING', self.incoming),
            (installer, 'ROOT', self.root), (installer, 'STORE', self.store),
            (installer, 'BOOTSTRAP', self.store / 'bootstrap'),
            (installer, 'HANDOFF', self.root / 'runtime/handoff-control.env'),
            (installer, 'HOOK', self.root / 'operations/ai_release_guard.py')):
            self.patches.append(patch.object(module, key, value))
        self.patches += [patch.object(guard.core, 'policy', return_value={}),
                         patch.object(guard, 'read_file', self.read_file),
                         patch.object(guard, 'private_record', self.private_record),
                         patch.object(runtime, 'get_json', self.http),
                         patch.object(runtime, 'verify_http', lambda *a, **k: {'gallery_total': 40}),
                         patch.object(runtime.shutil, 'disk_usage', return_value=type('Disk', (), {'free': 1_000_000_000})())]
        for p in self.patches:
            p.start()
        self.rt = runtime.AIRuntime.__new__(runtime.AIRuntime)
        self.rt.core, self.rt.storage = Core(self), self.files
        self.source = {name: (b'# observed source\n' if name.endswith('.py') else b'package==1\n') for name in guard.SOURCE_PATHS}
        deps = {name: sha(self.source[name]) for name in guard.DEPENDENCIES}
        policy = {'schema': 'R25_AI_SOURCE_POLICY_V1', 'repository': guard.REPOSITORY,
                  'source_paths': list(guard.SOURCE_PATHS), 'dependency_sha256': deps}
        policy_sha = self.files.atomic_write_json(self.store / 'POLICY.json', policy)['sha256']
        (self.root / 'runtime/handoff-control.env').write_text('R25_AI_RELEASE_POLICY_SHA256=' + policy_sha + '\n')
        self.before = self.accepted(self.source, '4' * 40)
        newer = dict(self.source)
        newer['guide_service/app.py'] += b'# new reviewed behavior\n'
        newer['critic-service/app/main.py'] += b'# new reviewed behavior\n'
        self.after = self.accepted(newer, '5' * 40)
        for name, raw in self.source.items():
            path = self.root / 'release' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        self.files.atomic_write_json(self.store / 'CURRENT.json', {'schema': 'R25_AI_CURRENT_V1', 'release': self.before})

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def read_file(self, path, expected=None, **kwargs):
        path = Path(path)
        raw = path.read_bytes()
        if path.is_symlink() or (expected is not None and sha(raw) != expected):
            raise RuntimeError('hash_or_link')
        return raw

    def private_record(self, path, expected=None):
        return guard.core.strict_json(self.read_file(path, expected=expected))

    def accepted(self, source, commit):
        files = [{'path': name, 'bytes': len(source[name]), 'sha256': sha(source[name])} for name in guard.SOURCE_PATHS]
        ref = {'commit_sha': commit, 'source_tree_sha256': sha(guard.canonical(files)), 'record_sha256': '0' * 64}
        folder = guard.release_folder(ref)
        (folder / 'source').mkdir(parents=True)
        for name, raw in source.items():
            path = folder / 'source' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        record = {'schema': 'R25_AI_ACCEPTED_SOURCE_V1', 'repository': guard.REPOSITORY, 'commit_sha': commit,
                  'source_tree_sha256': ref['source_tree_sha256'], 'files': files,
                  'dependency_sha256': {name: sha(source[name]) for name in guard.DEPENDENCIES},
                  'policy_sha256': self.files.file_identity(self.store / 'POLICY.json')['sha256']}
        ref['record_sha256'] = self.files.atomic_write_json(folder / 'record.json', record)['sha256']
        return ref

    def http(self, port, path, token=None, expected_status=200):
        self.events.append('http:' + str(port) + path)
        if expected_status == 401:
            if token is not None:
                raise AssertionError('unauthorized probe sent token')
            return {'detail': 'unauthorized'}
        if port == 19084:
            return {'status': 'UP', 'providerConfigured': True, 'modelConfigured': True}
        if path == '/health':
            return {'status': 'ok', 'model_configured': True, 'vision_capable': True, 'provider': 'qwen', 'model': 'model'}
        self.assertEqual(token, 'a' * 72)
        return {'evaluation_available': True, 'execution_policy': 'configured-provider', 'provider': 'qwen', 'model': 'model'}

    def assert_original(self):
        self.assertEqual(self.rt.current(), self.before)
        for name, raw in self.source.items():
            self.assertEqual((self.root / 'release' / name).read_bytes(), raw)

    def test_real_source_switch_order_and_scoped_actions(self):
        result = runtime.publish(self.rt, self.after)
        self.assertEqual(result['outcome'], 'AI_SOURCE_PUBLISHED')
        self.assertEqual(self.rt.current(), self.after)
        self.assertEqual(guard.selected_reference(), self.after)
        self.assertEqual(self.events[0], 'lease:acquire')
        self.assertEqual(self.events[-1], 'lease:release')
        for earlier, later in [('stop:watchdog', 'stop:java'), ('stop:java', 'stop:guide'),
                               ('stop:guide', 'start:guide'), ('start:guide', 'start:java'), ('start:java', 'start:watchdog')]:
            self.assertLess(self.events.index(earlier), self.events.index(later))
        self.assertFalse(any(x in self.events for x in ('stop:frontend', 'start:frontend', 'stop:tunnel', 'start:tunnel')))
        self.assertEqual(self.commands[1][0], 'guide')
        self.assertEqual(self.commands[1][1], ['fixed-python', '-B', '-m', 'guide_service'])
        self.assertEqual(self.commands[1][2]['PYTHONPATH'], str(self.root / 'release'))

    def test_same_bytes_are_explicit_noop_registration(self):
        same = self.accepted(self.source, '6' * 40)
        result = runtime.publish(self.rt, same)
        self.assertEqual(result['outcome'], 'AI_IDENTICAL_SOURCE_REGISTERED')
        self.assertFalse(any(x.startswith(('stop:', 'start:')) for x in self.events))

    def test_only_changed_role_restarts(self):
        source = dict(self.source)
        source['guide_service/app.py'] += b'# guide only\n'
        result = runtime.publish(self.rt, self.accepted(source, '7' * 40))
        self.assertEqual(result['changed_roles'], ['guide'])
        self.assertNotIn('stop:critic', self.events)
        self.assertNotIn('start:critic', self.events)

    def test_failures_after_every_intent_restore_original_bytes(self):
        for phase in ('QUIESCE_INTENT', 'QUIESCED', 'STOP_INTENT', 'STOPPED', 'INSTALL_INTENT',
                      'INSTALLED', 'START_INTENT', 'ACCEPTED', 'CURRENT_COMMIT_INTENT', 'COMMITTED'):
            with self.subTest(phase=phase):
                actual_record, fired = self.rt.record, []
                def fail_once(tx, next_phase):
                    actual_record(tx, next_phase)
                    if next_phase == phase and not fired:
                        fired.append(True)
                        raise OSError('injected_after_durable_intent')
                with patch.object(self.rt, 'record', fail_once):
                    result = runtime.publish(self.rt, self.after)
                self.assertEqual(result['outcome'], 'AI_PUBLICATION_FAILED')
                self.assertTrue(result['restored'])
                self.assert_original()

    def test_partial_copy_failure_restores_every_original_file(self):
        copy = self.files.atomic_copy_file
        fired = []
        def fail(source, target, digest, **kwargs):
            result = copy(source, target, digest, **kwargs)
            if Path(target) == self.root / 'release/critic-service/app/main.py' and not fired:
                fired.append(True)
                raise OSError('after_first_source_replace')
            return result
        with patch.object(self.files, 'atomic_copy_file', fail):
            result = runtime.publish(self.rt, self.after)
        self.assertTrue(result['restored'])
        self.assert_original()

    def test_degraded_health_restores_source_and_core(self):
        real = self.rt.health
        calls = []
        def fail_once(engine):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError('candidate_unready')
            return real(engine)
        with patch.object(self.rt, 'health', fail_once):
            result = runtime.publish(self.rt, self.after)
        self.assertTrue(result['restored'])
        self.assert_original()

    def test_busy_preflight_never_stops_a_process(self):
        self.busy = True
        with self.assertRaises(RuntimeError):
            runtime.publish(self.rt, self.after)
        self.assertFalse(any(x.startswith('stop:') for x in self.events))

    def test_changed_dependency_blocked_before_stop(self):
        source = dict(self.source)
        source['guide_service/requirements.txt'] = b'package==2\n'
        changed = self.accepted(source, '8' * 40)
        with self.assertRaises(RuntimeError):
            runtime.publish(self.rt, changed)
        self.assertFalse(any(x.startswith('stop:') for x in self.events))

    def test_foreign_pid_never_forced_or_adopted(self):
        self.foreign = 'critic'
        result = runtime.publish(self.rt, self.after)
        self.assertEqual(result['outcome'], 'AI_PUBLICATION_FAILED')
        self.assertFalse(result['restored'])
        self.assertIsNotNone(self.processes['critic'])
        self.assertNotIn('start:critic', self.events)
        self.assert_original()

    def test_recovery_rejects_foreign_source_and_core_pointer_drift(self):
        with self.rt.core.lease():
            tx = self.rt.prepare(self.after)
            target = self.root / 'release/guide_service/app.py'
            target.write_bytes(b'# foreign bytes\n')
            with self.assertRaises(RuntimeError):
                runtime.recover_locked(self.rt, tx)
            target.write_bytes(self.source['guide_service/app.py'])
            self.rt.core.release = Release('a' * 40, 'b' * 64, 'c' * 64)
            with self.assertRaises(RuntimeError):
                runtime.recover_locked(self.rt, tx)

    def test_prepared_abort_does_not_stop_services(self):
        with self.rt.core.lease():
            tx = self.rt.prepare(self.after)
            runtime.recover_locked(self.rt, tx)
        self.assertFalse(any(x.startswith('stop:') for x in self.events))
        self.assertEqual(self.rt.load(tx)[0]['phase'], 'RESTORED')

    def test_unfinished_core_transaction_blocks_ai(self):
        tx = 'f' * 32
        path = self.core_store / 'transactions' / tx
        path.mkdir(parents=True)
        self.files.atomic_write_json(self.core_store / 'ACTIVE.json', {'transaction_id': tx})
        self.files.atomic_write_json(path / 'journal.json', {'schema': 'R25_CORE_TRANSACTION_V1', 'transaction_id': tx, 'phase': 'INSTALL_INTENT'})
        with self.assertRaises(RuntimeError):
            runtime.publish(self.rt, self.after)
        self.assertFalse(any(x.startswith('stop:') for x in self.events))

    def test_source_guard_rejects_mixed_or_self_declared_current(self):
        with self.rt.core.lease():
            tx = self.rt.prepare(self.after)
            self.rt.record(tx, 'INSTALL_INTENT')
            with self.assertRaises(RuntimeError):
                guard.validate_critic_identity(self.root, 'main.py')
        current = self.rt.read(self.store / 'CURRENT.json')
        current['release']['record_sha256'] = '9' * 64
        self.rt.write(self.store / 'CURRENT.json', current)
        with self.assertRaises(RuntimeError):
            guard.validate_critic_identity(self.root, 'main.py')

    def test_stage_verifies_real_zip_and_independent_expected_identity(self):
        identity = candidate.provenance(candidate.REPOSITORY, candidate.REF, 'a' * 40, '12', '1')
        manifest, blob = candidate.build(self.source, identity)
        folder = ai_publish.incoming_path('a' * 40, '12', '1')
        folder.mkdir()
        (folder / candidate.MANIFEST).write_bytes(candidate.canonical(manifest))
        (folder / candidate.ARCHIVE).write_bytes(blob)
        ref = ai_publish.stage(self.rt, 'a' * 40, '12', '1')
        self.assertEqual(ref['source_tree_sha256'], self.before['source_tree_sha256'])
        (folder / candidate.ARCHIVE).write_bytes(blob + b'tamper')
        with self.assertRaises(ValueError):
            ai_publish.stage(self.rt, 'a' * 40, '12', '1')

    def test_authenticated_readiness_contract_rejects_soft_degradation(self):
        good_guide = {'status': 'UP', 'providerConfigured': True, 'modelConfigured': True}
        critic = {'status': 'ok', 'model_configured': True, 'vision_capable': True, 'provider': 'qwen', 'model': 'qwen'}
        ready = {'evaluation_available': True, 'execution_policy': 'configured-provider', 'provider': 'qwen', 'model': 'qwen'}
        for changed in ({**critic, 'status': 'degraded'}, {**critic, 'vision_capable': False}):
            with self.assertRaises(RuntimeError):
                runtime.validate_health(good_guide, changed, ready)
        with self.assertRaises(RuntimeError):
            runtime.validate_health(good_guide, critic, {**ready, 'evaluation_available': False})

    def test_untracked_bytecode_and_extra_module_refused(self):
        for name in ('guide_service/rogue.py', 'guide_service/__pycache__/app.cpython-310.pyc'):
            path = self.root / 'release' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'# untracked execution bytes\n')
            with self.assertRaises(RuntimeError):
                guard.verify_live(self.before)
            path.unlink()

    def test_incomplete_ai_transaction_blocks_second_publish(self):
        with self.rt.core.lease():
            self.rt.prepare(self.after)
        with self.assertRaises(RuntimeError):
            runtime.publish(self.rt, self.after)

    def test_unchanged_role_pid_drift_blocks_install(self):
        source = dict(self.source)
        source['guide_service/app.py'] += b'# guide change\n'
        ref = self.accepted(source, '7' * 40)
        real = self.rt.record
        def record(tx, phase):
            real(tx, phase)
            if phase == 'QUIESCE_INTENT':
                self.processes['critic'] = {'pid': 999, 'ticks': 999}
        with patch.object(self.rt, 'record', record):
            result = runtime.publish(self.rt, ref)
        self.assertFalse(result['restored'])
        self.assert_original()
        self.assertNotIn('stop:critic', self.events)

    def test_failed_recovery_persists_nonterminal_guard(self):
        def fail(*_args, **_kwargs):
            raise OSError('persistent_source_write_failure')
        with patch.object(self.rt, 'copy_source', fail):
            result = runtime.publish(self.rt, self.after)
        self.assertFalse(result['restored'])
        self.assertEqual(self.rt.load(result['transaction_id'])[0]['phase'], 'RECOVERY_REQUIRED')
        with self.assertRaises(RuntimeError):
            guard.validate_critic_identity(self.root, 'main.py')

    def test_real_controller_install_and_recover_handoff_binding_only(self):
        # The production baseline loader is the only fixture: actual installer
        # registration, source copies, hook, handoff CAS and recovery all run.
        baseline = {'schema': 'R25_AI_OBSERVED_BASELINE_V1', 'source_commit': self.before['commit_sha'],
                    'source_tree_sha256': self.before['source_tree_sha256'], 'files': guard.accepted(self.before)['files']}
        self.hook_enabled = False
        # Start from unregistered old controls, retaining only live source.
        import shutil
        shutil.rmtree(self.store)
        self.store.parent.mkdir(exist_ok=True)
        handoff = self.root / 'runtime/handoff-control.env'
        handoff.write_bytes(b'EXISTING_CONTROL=untouched\n')
        old_read = Path.read_bytes
        def read(path):
            return guard.core.encoded(baseline) if path.name == 'ai-baseline.json' else old_read(path)
        with patch.object(Path, 'read_bytes', read):
            with self.rt.core.lease():
                result = installer.install(self.rt)
        self.assertEqual(result['outcome'], 'AI_CONTROLLER_INSTALLED')
        self.assertIn(b'R25_AI_RELEASE_GUARD=', handoff.read_bytes())
        guard.verify_live(self.rt.current())
        self.assertFalse(any(x in self.events for x in ('stop:java', 'stop:guide', 'stop:critic', 'start:java')))
        with self.rt.core.lease():
            restored = installer.recover_install(self.rt)
        self.assertEqual(restored['outcome'], 'AI_CONTROLLER_BINDING_RESTORED')
        self.assertEqual(handoff.read_bytes(), b'EXISTING_CONTROL=untouched\n')
        # A just-bound guard refusing old bytecode must still be recoverable;
        # recovery needs the validated partial engine, not the failing load().
        stale = self.root / 'release/guide_service/stale.pyc'
        stale.write_bytes(b'old bytecode')
        with patch.object(Path, 'read_bytes', read):
            with self.rt.core.lease(), self.assertRaises(RuntimeError):
                installer.install(self.rt)
        self.assertEqual(handoff.read_bytes(), b'EXISTING_CONTROL=untouched\n')
        self.assertEqual(self.rt.read(self.store / 'bootstrap/journal.json')['phase'], 'RESTORED')

    def test_registry_refuses_duplicate_json_keys(self):
        with self.assertRaises(RuntimeError):
            guard.core.strict_json(b'{"release":{},"release":{}}')

    def test_current_cas_rejects_drift_after_validation_before_write(self):
        with self.rt.core.lease():
            tx = self.rt.prepare(self.after)
            self.rt.record(tx, 'INSTALL_INTENT')
            self.rt.copy_source(tx)
            self.rt.record(tx, 'START_INTENT')
            original = self.files.atomic_write_json
            foreign = {'schema': 'R25_AI_CURRENT_V1', 'release': {**self.before, 'commit_sha': 'e' * 40}}
            def mutate(path, value, **kwargs):
                if Path(path) == self.store / 'CURRENT.json':
                    Path(path).write_bytes(guard.core.encoded(foreign))
                return original(path, value, **kwargs)
            with patch.object(self.files, 'atomic_write_json', mutate), self.assertRaises(RuntimeError):
                self.rt.commit(tx)
            self.assertEqual(self.rt.read(self.store / 'CURRENT.json'), foreign)

    def test_prepared_and_restored_refuse_success_when_a_process_disappears(self):
        for terminal in (False, True):
            for role_name in ('java', 'guide', 'critic', 'watchdog'):
                with self.subTest(terminal=terminal, role=role_name), self.rt.core.lease():
                    tx = self.rt.prepare(self.after)
                    if terminal:
                        runtime.recover_locked(self.rt, tx)
                    previous = self.processes.pop(role_name, None)
                    if role_name == 'watchdog':
                        self.watchdog_running = False
                    original_events = list(self.events)
                    with self.assertRaises(RuntimeError):
                        runtime.recover_locked(self.rt, tx)
                    self.assertFalse(any(x.startswith(('stop:', 'start:')) for x in self.events[len(original_events):]))
                    if previous is not None:
                        self.processes[role_name] = previous
                    self.watchdog_running = True
                    runtime.recover_locked(self.rt, tx)


if __name__ == '__main__':
    unittest.main()
