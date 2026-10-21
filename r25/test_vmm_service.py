"""CPU-only HTTP/WSGI and durable gate tests; no legacy/model import or GPU."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import wave
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server

import vmm_service as vmm

IMAGE = b'\x89PNG\r\n\x1a\n' + b'synthetic-image-fixture'
DATA = 'data:image/png;base64,' + base64.b64encode(IMAGE).decode()
TOKEN = 'synthetic-only-private-token-' + 'x' * 48


class ThreadedServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class Quiet(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='r25-vmm-cpu-')
        self.root = Path(self.temporary.name)
        self.state = self.root / 'state'
        self.state.mkdir(mode=0o700)
        self.output = self.root / 'output'
        self.output.mkdir()
        self.store = vmm.GateStore(self.state)
        self.count, self.delay, self.fail = 0, 0, False
        self.ready = True
        self.entered = threading.Event()
        self.wrapper = vmm.ManagedService(self.backend, lambda: self.ready, self.store, TOKEN, self.output)
        self.server = make_server('127.0.0.1', 0, self.wrapper, ThreadedServer, Quiet)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.temporary.cleanup()

    def backend(self, environ, start_response):
        self.count += 1
        self.entered.set()
        data = json.loads(environ['wsgi.input'].read(int(environ['CONTENT_LENGTH'])))
        self.assertEqual(data['image'], DATA)
        time.sleep(self.delay)
        if self.fail:
            raise RuntimeError('simulated lost model outcome')
        filename = 'vmm_generated_123456.wav'
        with wave.open(str(self.output / filename), 'wb') as sound:
            sound.setnchannels(1)
            sound.setsampwidth(2)
            sound.setframerate(8000)
            sound.writeframes(b'\x00\x00' * 8000 * data.get('duration', 10))
        raw = json.dumps({'success': True, 'fileName': filename, 'full_path': str(self.output / filename)}).encode()
        start_response('200 OK', [('Content-Type', 'application/json')])
        return [raw]

    def permit(self):
        return {'owner_id': 'account-a', 'source_painting_id': 'painting-a',
                'image_sha256': hashlib.sha256(IMAGE).hexdigest(), 'duration': 3}

    def call(self, path='/api/generate_with_image', request='request-1', body=None, token=TOKEN, headers=None):
        values = {'X-ArtLIVE-VMM-Token': token, 'Content-Type': 'application/json',
                  'X-ArtLIVE-VMM-Request-ID': request, 'X-ArtLIVE-VMM-Owner-ID': 'account-a',
                  'X-ArtLIVE-VMM-Source-Painting-ID': 'painting-a',
                  'X-ArtLIVE-VMM-Creation-ID': 'creation-a', 'X-ArtLIVE-VMM-Step-ID': 'step1',
                  'X-ArtLIVE-VMM-Retry-Version': '0'}
        values.update(headers or {})
        data = None if path in ('/health', '/internal/vmm/status') else json.dumps(body or {'image': DATA, 'duration': 3}).encode()
        req = urllib.request.Request(self.url + path, data=data, headers=values)
        try:
            response = self.opener.open(req, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def test_closed_and_authentication_prevent_every_legacy_route(self):
        self.assertEqual(self.call(token='wrong')[0], 401)
        self.assertEqual(self.call()[1]['code'], 'VMM_DISPATCH_CLOSED')
        self.assertEqual(self.call('/api/generate_with_image_and_text')[0], 404)
        health = self.call('/health', token='')[1]
        self.assertTrue(health['model_ready'])
        status = self.call('/internal/vmm/status')[1]
        self.assertFalse(status['dispatch_permitted'])
        self.assertEqual(self.count, 0)

    def test_one_qualified_acceptance_consumes_once_and_safe_wav_response(self):
        self.store.configure('ACCEPTANCE', self.permit())
        self.assertEqual(self.call(headers={'X-ArtLIVE-VMM-Owner-ID': 'account-b'})[0], 403)
        self.assertEqual(self.count, 0)
        self.assertEqual(self.store.snapshot()['attempts'], 0)
        status, value = self.call()
        self.assertEqual(status, 200)
        self.assertEqual(value, {'success': True, 'fileName': 'vmm_generated_123456.wav', 'request_id': 'request-1'})
        self.assertEqual(self.call()[1]['code'], 'VMM_REQUEST_ALREADY_DISPATCHED')
        self.assertEqual(self.call(request='request-2')[1]['code'], 'VMM_DISPATCH_CLOSED')
        self.assertEqual(self.count, 1)
        record = self.store.read()['requests']['request-1']
        self.assertEqual(record['state'], 'SUCCEEDED')
        self.assertEqual(record['result']['sha256'], hashlib.sha256((self.output / value['fileName']).read_bytes()).hexdigest())
        self.assertNotIn(DATA, json.dumps(record))

    def test_concurrent_calls_use_single_executing_slot_and_close_drains(self):
        self.store.configure('OPEN')
        self.delay = .2
        result = []
        worker = threading.Thread(target=lambda: result.append(self.call()))
        worker.start()
        self.assertTrue(self.entered.wait(1))
        self.assertTrue(self.call('/internal/vmm/status')[1]['in_flight'])
        self.assertEqual(self.call(request='request-2')[1]['code'], 'VMM_BUSY')
        self.store.configure('CLOSED')
        worker.join(2)
        self.assertEqual(result[0][0], 200)
        self.assertEqual(self.count, 1)
        self.assertFalse(self.store.snapshot()['in_flight'])
        self.assertEqual(self.call(request='request-3')[1]['code'], 'VMM_DISPATCH_CLOSED')

    def test_failed_generation_is_consumed_and_unknown_blocks_reopen(self):
        self.store.configure('ACCEPTANCE', self.permit())
        self.fail = True
        self.assertEqual(self.call()[0], 500)
        state = self.store.snapshot()
        self.assertEqual(state['attempts'], 1)
        self.assertTrue(state['uncertain'])
        with self.assertRaisesRegex(vmm.Refused, 'OUTCOME_UNCERTAIN'):
            self.store.configure('OPEN')
        self.store.acknowledge_unknown('request-1', 'operator recorded unresolved outcome; no replay')
        self.store.configure('OPEN')
        self.assertEqual(self.call()[1]['code'], 'VMM_REQUEST_ALREADY_DISPATCHED')
        self.assertEqual(self.count, 1)

    def test_not_ready_health_never_initializes_and_dispatch_not_counted(self):
        self.ready = False
        self.store.configure('OPEN')
        for _ in range(3):
            self.assertFalse(self.call('/health')[1]['model_ready'])
        self.assertEqual(self.call()[1]['code'], 'VMM_MODEL_NOT_READY')
        self.assertEqual(self.store.snapshot()['attempts'], 0)
        self.assertEqual(self.count, 0)

    def test_request_contract_duration_data_url_and_identity_rejected_before_dispatch(self):
        self.store.configure('OPEN')
        for value in (2, 31, True, 3.0, '3'):
            with self.subTest(duration=value):
                self.assertEqual(self.call(body={'image': DATA, 'duration': value})[0], 400)
        self.assertEqual(self.call(body={'image': 'https://example.test/a.png', 'duration': 3})[0], 400)
        self.assertEqual(self.call(body={'image': DATA, 'duration': 3, 'file_path': '/etc/passwd'})[0], 400)
        self.assertEqual(self.call(headers={'X-ArtLIVE-VMM-Creation-ID': ''})[0], 400)
        self.assertEqual(self.count, 0)

    def test_default_duration_is_ten_and_bounds_synthetic_wav(self):
        self.store.configure('OPEN')
        for value in (None, 3, 30):
            with self.subTest(duration=value):
                body = {'image': DATA}
                if value is not None:
                    body['duration'] = value
                status, _ = self.call(request='request-' + str(value), body=body)
                self.assertEqual(status, 200)
        self.assertEqual(self.count, 3)

    def test_invalid_backend_output_does_not_escape_or_claim_success(self):
        for name in ('../outside.wav', 'vmm_generated_bad.wav'):
            with self.subTest(filename=name):
                with self.assertRaises(vmm.Refused):
                    vmm.safe_output(self.output, {'success': True, 'fileName': name}, 3)
        target = self.output / 'vmm_generated_1.wav'
        target.symlink_to('/etc/passwd')
        with self.assertRaises(vmm.Refused):
            vmm.safe_output(self.output, {'success': True, 'fileName': target.name}, 3)

    def test_restart_process_keeps_attempt_and_closes_dispatch_without_replay(self):
        self.store.configure('OPEN')
        identity = dict(self.permit(), creation_id='creation-a', step_id='step1', retry_version='0')
        script = 'import sys; from vmm_service import GateStore; s=GateStore(sys.argv[1]); s.reserve("crashed", '+repr(identity)+'); import os; os._exit(0)'
        result = subprocess.run([sys.executable, '-c', script, str(self.state)],
                                cwd=Path(vmm.__file__).parent, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertTrue(self.store.snapshot()['in_flight'])
        recovered = vmm.GateStore(self.state)
        recovered.on_start()
        self.assertEqual(recovered.snapshot(), {'mode': 'CLOSED', 'in_flight': False, 'uncertain': True, 'attempts': 1})
        self.assertEqual(recovered.read()['requests']['crashed']['state'], 'UNKNOWN')
        self.assertEqual(self.count, 0)

    def test_fsync_persistence_failure_before_reserve_never_executes(self):
        from unittest.mock import patch
        self.store.configure('OPEN')
        with patch.object(vmm, 'atomic', side_effect=OSError('synthetic disk failure')):
            self.assertEqual(self.call()[0], 500)
        self.assertEqual(self.count, 0)
        self.assertEqual(self.store.snapshot()['attempts'], 0)

    def test_approved_gate_transition_rechecks_digest_and_closed_under_lock(self):
        before = hashlib.sha256(self.store.path.read_bytes()).hexdigest()
        self.store.configure('OPEN')
        self.store.reserve('raced', dict(self.permit()))
        self.store.finish('raced', 'SUCCEEDED')
        self.store.configure('CLOSED')
        with self.assertRaisesRegex(vmm.Refused, 'GATE_COMPARE_AND_SWAP_CONFLICT'):
            self.store.configure('ACCEPTANCE', self.permit(), expected_sha256=before)
        self.assertEqual(self.store.snapshot()['mode'], 'CLOSED')
        self.assertEqual(self.store.snapshot()['attempts'], 1)
        current = hashlib.sha256(self.store.path.read_bytes()).hexdigest()
        self.store.configure('OPEN', expected_sha256=current)
        opened = hashlib.sha256(self.store.path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(vmm.Refused, 'APPROVED_TRANSITION_REQUIRES_CLOSED'):
            self.store.configure('ACCEPTANCE', self.permit(), expected_sha256=opened)

    def test_unknown_acknowledgement_has_same_atomic_digest_guard(self):
        self.store.configure('OPEN')
        self.store.reserve('unknown', dict(self.permit()))
        self.store.finish('unknown', 'UNKNOWN')
        before = hashlib.sha256(self.store.path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(vmm.Refused, 'GATE_COMPARE_AND_SWAP_CONFLICT'):
            self.store.acknowledge_unknown('unknown', 'operator preserves uncertain outcome', expected_sha256='a' * 64)
        self.assertTrue(self.store.snapshot()['uncertain'])
        self.store.acknowledge_unknown('unknown', 'operator preserves uncertain outcome', expected_sha256=before)
        self.assertFalse(self.store.snapshot()['uncertain'])
        self.assertEqual(self.store.snapshot()['attempts'], 1)

    def test_offline_model_manifest_covers_actual_clip_cache_and_musicgen_assets(self):
        root = Path('/root/autodl-tmp/auralink')
        musicgen = root / 'VMM/models/musicgen-small'
        checkpoint = root / 'VMM/models/final_model.pth'
        environment = dict(HOME='/root', AURALINK_VMM_CLIP_MODEL='ViT-B/32', AURALINK_HF_ENDPOINT='',
                           AURALINK_VMM_MUSICGEN_PATH=str(musicgen), AURALINK_VMM_CHECKPOINT_PATH=str(checkpoint),
                           HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_DATASETS_OFFLINE='1')
        assets = [{'path': str(musicgen / name), 'sha256': 'a' * 64} for name in vmm.MUSICGEN_FILES]
        assets += [{'path': str(checkpoint), 'sha256': 'b' * 64},
                   {'path': vmm.CLIP_CACHE, 'sha256': vmm.CLIP_MODEL_SHA256}]
        self.assertEqual(len(vmm.model_manifest(root, assets, checkpoint, environment)), 8)
        for index in range(len(assets)):
            with self.subTest(missing=index), self.assertRaisesRegex(vmm.Refused, 'MODEL_ASSET_COVERAGE'):
                vmm.model_manifest(root, assets[:index] + assets[index + 1:], checkpoint, environment)
        bad = [dict(item) for item in assets]
        bad[-1]['sha256'] = 'c' * 64
        with self.assertRaisesRegex(vmm.Refused, 'CLIP_DOWNLOAD_PREVENTION_HASH'):
            vmm.model_manifest(root, bad, checkpoint, environment)
        for override in ({'HOME': '/tmp'}, {'AURALINK_VMM_CLIP_MODEL': 'ViT-B/16'},
                         {'HF_HUB_OFFLINE': '0'}, {'AURALINK_VMM_MUSICGEN_PATH': '/tmp/models'}):
            with self.subTest(override=override), self.assertRaises(vmm.Refused):
                vmm.model_manifest(root, assets, checkpoint, dict(environment, **override))
        self.assertNotIn('torch', sys.modules)
        self.assertNotIn('clip', sys.modules)

    def test_world_writable_model_source_cannot_be_production_trusted(self):
        # /tmp itself is not a production trust root; no ownership correction
        # or allow-fixture switch is hidden in the production validator.
        model = self.root / 'model.pth'
        model.write_bytes(b'intentional synthetic weights')
        model.chmod(0o666)
        with self.assertRaisesRegex(vmm.Refused, 'TRUSTED_PATH_WRITABLE_OR_OWNER'):
            vmm.trusted_path(model)

    def test_import_has_no_gpu_dependencies_and_fixture_not_production_identity(self):
        code = 'import sys,vmm_service; assert not any(x in sys.modules for x in ("torch","clip","audiocraft"))'
        result = subprocess.run([sys.executable, '-c', code], cwd=Path(vmm.__file__).parent,
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        with self.assertRaisesRegex(vmm.Refused, 'LEGACY_FIXED_ROOT'):
            vmm.verify_legacy_sources(self.root)
        with self.assertRaisesRegex(vmm.Refused, 'PRIVATE_FIXED_LISTENER'):
            vmm.serve({'schema': 'R25_VMM_SERVICE_BINDING_V1', 'host': '0.0.0.0', 'port': 5001})


if __name__ == '__main__':
    unittest.main()
