"""Private VMM admission wrapper. Importing this module never imports a model.

Production accepts only the bound Round8.1 legacy source. Test backends are
injected by Python tests, never selected by environment or production CLI.
The wrapper owns dispatch accounting, not generation retries or model policy.
"""
from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import hmac
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time
import wave

CONTRACT = 'artlive-vmm-managed-v1'
LEGACY_COMMIT = 'b115f0724e16f16128563dc996c5a10082783074'
LEGACY_HASHES = {
    'VMM/app.py': 'd604e9e11943607593916f772c26bef6ba4d2014753d1d4ef9eff6a560e44f22',
    'VMM/duration_contract.py': '481f9bfb54ca2c3e94d70dc2520846112aad430d7ea6db9501cc83c3372b1e9f',
    'CLIP/clip/clip.py': '9540f200fbf8145479fa655382a56dab048d238cc698b9cbd8df3b6d86d3f1b6',
}
CLIP_CACHE = '/root/.cache/clip/ViT-B-32.pt'
CLIP_MODEL_SHA256 = '40d365715913c9da98579312b702a82c18be219cc2a73407c4526f58eba950af'
MUSICGEN_FILES = ('compression_state_dict.bin', 'state_dict.bin', 'config.json',
                  'spiece.model', 'tokenizer.json', 'tokenizer_config.json')
MAX_BODY = 16 * 1024 * 1024
TERMINAL = {'SUCCEEDED', 'FAILED', 'UNKNOWN'}


class Refused(RuntimeError):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


def must(condition, code, status=409):
    if not condition:
        raise Refused(code, status)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def regular(path, *, private=False):
    path = Path(path)
    info = path.lstat()
    must(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'UNSAFE_FILE')
    if private:
        must(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600, 'PRIVATE_FILE_IDENTITY')
    return path


def trusted_path(path):
    """Production source/assets require an explicit non-writable trust chain.

    Existing shared 0777/0666 assets are a future binding refusal, not silently
    trusted because their current hash happens to match. No chmod occurs here.
    """
    path = Path(path)
    must(path.is_absolute() and path.resolve() == path, 'TRUSTED_CANONICAL_PATH')
    for item in (path, *path.parents):
        info = item.lstat()
        must(not stat.S_ISLNK(info.st_mode) and info.st_uid in {0, os.geteuid()}
             and not info.st_mode & 0o022, 'TRUSTED_PATH_WRITABLE_OR_OWNER')
    return path


def read_token(path):
    token = regular(path, private=True).read_text().strip()
    must(re.fullmatch('[A-Za-z0-9_-]{48,128}', token) is not None, 'TOKEN_FORMAT')
    return token


def atomic(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp-' + os.urandom(8).hex())
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists() or path.is_symlink():
            regular(path, private=True)
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if temporary.exists():
            temporary.unlink()


class GateStore:
    """Durable single-execution gate. Its lock is NOT the deployment lock.

    DISPATCHED survives a crash. An operator may acknowledge uncertainty while
    CLOSED, but the request record is permanent and can never be replayed.
    """
    def __init__(self, root):
        self.root = Path(root)
        info = self.root.lstat()
        must(stat.S_ISDIR(info.st_mode) and not self.root.is_symlink()
             and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
             'STATE_DIRECTORY_IDENTITY')
        self.lock_path = self.root / 'gate.lock'
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        regular(self.lock_path, private=True)
        self.path = self.root / 'gate.json'
        with self.locked():
            if not self.path.exists():
                self.write({'schema': CONTRACT, 'mode': 'CLOSED', 'permit': None,
                            'requests': {}, 'uncertainty_acknowledgements': []})
            self.read()

    @contextlib.contextmanager
    def locked(self):
        fd = os.open(self.lock_path, os.O_RDWR | os.O_NOFOLLOW)
        try:
            regular(self.lock_path, private=True)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def read(self):
        result = json.loads(regular(self.path, private=True).read_bytes())
        must(result.get('schema') == CONTRACT and result.get('mode') in {'CLOSED', 'ACCEPTANCE', 'OPEN'}
             and isinstance(result.get('requests'), dict), 'GATE_RECORD_INVALID')
        return result

    def write(self, value):
        atomic(self.path, value)

    def on_start(self):
        # A restart is neither evidence of completion nor permission to resume.
        with self.locked():
            record = self.read()
            record['mode'], record['permit'] = 'CLOSED', None
            for request in record['requests'].values():
                if request['state'] == 'DISPATCHED':
                    request['state'] = 'UNKNOWN'
                    request['reason'] = 'PROCESS_RESTART_AFTER_DISPATCH'
            self.write(record)

    def snapshot(self):
        with self.locked():
            record = self.read()
            requests = record['requests'].values()
            return {'mode': record['mode'],
                    'in_flight': any(x['state'] == 'DISPATCHED' for x in requests),
                    'uncertain': any(x['state'] == 'UNKNOWN' and not x.get('acknowledged') for x in requests),
                    'attempts': len(record['requests'])}

    def configure(self, mode, permit=None, *, expected_sha256=None, acceptance_deadline=None):
        must(mode in {'CLOSED', 'ACCEPTANCE', 'OPEN'}, 'GATE_MODE')
        if acceptance_deadline is not None:
            must(mode == 'ACCEPTANCE' and type(acceptance_deadline) is dict
                 and set(acceptance_deadline) == {'monotonic', 'wall'}
                 and all(type(value) in (int, float) and math.isfinite(value)
                         for value in acceptance_deadline.values()), 'ACCEPTANCE_DEADLINE_INVALID')
        if mode == 'ACCEPTANCE':
            must(isinstance(permit, dict) and set(permit) >= {'owner_id', 'source_painting_id', 'image_sha256', 'duration'},
                 'ACCEPTANCE_PERMIT_FIELDS')
            must(set(permit) <= {'owner_id', 'source_painting_id', 'image_sha256', 'duration',
                                'creation_id', 'step_id', 'retry_version'}, 'ACCEPTANCE_PERMIT_FIELDS')
            for field in ('owner_id', 'source_painting_id'):
                must(isinstance(permit[field], str) and 0 < len(permit[field]) <= 128, 'ACCEPTANCE_PERMIT_ID')
            must(re.fullmatch('[0-9a-f]{64}', permit['image_sha256']) is not None, 'ACCEPTANCE_IMAGE_HASH')
            duration(permit['duration'])
        else:
            must(permit is None, 'PERMIT_ONLY_ACCEPTANCE')
        with self.locked():
            record = self.read()
            if expected_sha256 is not None:
                must(isinstance(expected_sha256, str) and re.fullmatch('[0-9a-f]{64}', expected_sha256)
                     and hashlib.sha256(regular(self.path, private=True).read_bytes()).hexdigest() == expected_sha256,
                     'GATE_COMPARE_AND_SWAP_CONFLICT')
                must(record['mode'] == 'CLOSED', 'APPROVED_TRANSITION_REQUIRES_CLOSED')
            if mode != 'CLOSED':
                must(not any(x['state'] == 'DISPATCHED' for x in record['requests'].values()), 'VMM_BUSY')
                must(not any(x['state'] == 'UNKNOWN' and not x.get('acknowledged')
                             for x in record['requests'].values()), 'VMM_OUTCOME_UNCERTAIN')
            # The controller's original READY deadlines travel as validated
            # internal arguments, never service HTTP/client fields. Waiting for
            # this independent accounting lock must not grant late admission.
            if acceptance_deadline is not None:
                must(time.monotonic() < acceptance_deadline['monotonic']
                     and time.time() < acceptance_deadline['wall'], 'ACCEPTANCE_WINDOW_EXPIRED')
            record['mode'], record['permit'] = mode, permit
            self.write(record)

    def acknowledge_unknown(self, request_id, reason, *, expected_sha256=None):
        must(isinstance(reason, str) and 8 <= len(reason) <= 200, 'RECONCILIATION_REASON_REQUIRED')
        with self.locked():
            record = self.read()
            if expected_sha256 is not None:
                must(isinstance(expected_sha256, str) and re.fullmatch('[0-9a-f]{64}', expected_sha256)
                     and hashlib.sha256(regular(self.path, private=True).read_bytes()).hexdigest() == expected_sha256,
                     'GATE_COMPARE_AND_SWAP_CONFLICT')
            must(record['mode'] == 'CLOSED', 'RECONCILIATION_REQUIRES_CLOSED')
            request = record['requests'].get(request_id)
            must(request and request['state'] == 'UNKNOWN', 'UNKNOWN_REQUEST_REQUIRED')
            request['acknowledged'] = True
            record['uncertainty_acknowledgements'].append({'request_id': request_id, 'reason': reason,
                                                         'at': time.time()})
            self.write(record)

    def reserve(self, request_id, identity):
        must(re.fullmatch('[a-zA-Z0-9_-]{1,128}', request_id or '') is not None, 'REQUEST_ID_REQUIRED', 400)
        with self.locked():
            record = self.read()
            must(request_id not in record['requests'], 'VMM_REQUEST_ALREADY_DISPATCHED')
            must(not any(x['state'] == 'DISPATCHED' for x in record['requests'].values()), 'VMM_BUSY')
            must(not any(x['state'] == 'UNKNOWN' and not x.get('acknowledged')
                         for x in record['requests'].values()), 'VMM_OUTCOME_UNCERTAIN')
            must(record['mode'] != 'CLOSED', 'VMM_DISPATCH_CLOSED', 503)
            if record['mode'] == 'ACCEPTANCE':
                permit = record['permit']
                must(permit is not None and all(identity.get(k) == v for k, v in permit.items()),
                     'VMM_ACCEPTANCE_MISMATCH', 403)
                # Consume before invoking any model code, not after success.
                record['mode'], record['permit'] = 'CLOSED', None
            record['requests'][request_id] = dict(identity, state='DISPATCHED', at=time.time())
            self.write(record)

    def finish(self, request_id, state, result=None):
        must(state in TERMINAL, 'TERMINAL_STATE')
        with self.locked():
            record = self.read()
            request = record['requests'].get(request_id)
            must(request and request['state'] == 'DISPATCHED', 'LATE_TERMINAL_WRITE')
            request.update(state=state, finished_at=time.time())
            if result is not None:
                request['result'] = result
            if state == 'UNKNOWN':
                record['mode'], record['permit'] = 'CLOSED', None
            self.write(record)


def duration(value=10):
    must(type(value) is int and 3 <= value <= 30, 'INVALID_DURATION', 400)
    return value


def request_identity(environ, body):
    must(isinstance(body, dict) and set(body) <= {'image', 'duration'}, 'INVALID_PAYLOAD', 400)
    value = body.get('image')
    must(isinstance(value, str), 'IMAGE_REQUIRED', 400)
    match = re.fullmatch(r'data:image/(png|jpeg);base64,([A-Za-z0-9+/=\r\n]+)', value)
    must(match is not None, 'IMAGE_DATA_URL_REQUIRED', 400)
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except ValueError as error:
        raise Refused('INVALID_IMAGE_BASE64', 400) from error
    must(raw and len(raw) <= 10 * 1024 * 1024, 'IMAGE_SIZE', 400)
    must((match.group(1) == 'png' and raw.startswith(b'\x89PNG\r\n\x1a\n'))
         or (match.group(1) == 'jpeg' and raw.startswith(b'\xff\xd8\xff')), 'IMAGE_MIME_MISMATCH', 400)
    result = {'image_sha256': hashlib.sha256(raw).hexdigest(), 'duration': duration(body.get('duration', 10))}
    for name in ('owner_id', 'source_painting_id', 'creation_id', 'step_id', 'retry_version'):
        value = environ.get('HTTP_X_ARTLIVE_VMM_' + name.upper(), '')
        must(isinstance(value, str) and 0 < len(value) <= 128 and '\n' not in value, 'CONTEXT_REQUIRED', 400)
        result[name] = value
    return result


def safe_output(output_root, value, seconds):
    must(isinstance(value, dict) and value.get('success') is True, 'VMM_BACKEND_FAILED', 502)
    filename = value.get('fileName')
    must(isinstance(filename, str) and re.fullmatch(r'vmm_generated_[0-9]+\.wav', filename),
         'VMM_OUTPUT_FILENAME', 502)
    root = Path(output_root)
    must(root.is_dir() and not root.is_symlink(), 'OUTPUT_ROOT', 502)
    path = regular(root / filename)
    must(path.resolve().parent == root.resolve(), 'VMM_OUTPUT_ESCAPE', 502)
    must(path.stat().st_size <= 32 * 1024 * 1024, 'VMM_OUTPUT_SIZE', 502)
    with wave.open(str(path), 'rb') as sound:
        must(sound.getcomptype() == 'NONE' and sound.getnchannels() in (1, 2)
             and sound.getframerate() > 0 and abs(sound.getnframes() / sound.getframerate() - seconds) <= 0.05,
             'VMM_OUTPUT_DURATION', 502)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {'success': True, 'fileName': filename}, {'file_name': filename, 'sha256': digest, 'duration': seconds}


class ManagedService:
    """WSGI boundary usable with the installed Flask app, without changing it."""
    def __init__(self, backend, ready, store, token, output_root, *, reset_on_start=True):
        self.backend, self.ready, self.store = backend, ready, store
        self.token, self.output_root = token, Path(output_root)
        self.execution = threading.Lock()
        if reset_on_start:
            self.store.on_start()

    def status(self):
        state = self.store.snapshot()
        ready = self.ready() is True
        return dict(state, contract=CONTRACT, process_alive=True, model_ready=ready,
                    dispatch_permitted=ready and state['mode'] != 'CLOSED' and not state['in_flight'] and not state['uncertain'])

    def __call__(self, environ, start_response):
        try:
            value, status = self.route(environ)
        except Refused as error:
            value, status = {'success': False, 'code': error.code}, error.status
        except Exception:
            # Do not disclose model exceptions, input bodies, paths or tokens.
            value, status = {'success': False, 'code': 'VMM_INTERNAL_ERROR'}, 500
        raw = encoded(value)
        start_response(str(status) + ' ' + {200: 'OK', 400: 'Bad Request', 401: 'Unauthorized',
                                           403: 'Forbidden', 404: 'Not Found', 409: 'Conflict',
                                           500: 'Internal Server Error', 502: 'Bad Gateway',
                                           503: 'Service Unavailable'}.get(status, 'Error'),
                       [('Content-Type', 'application/json'), ('Content-Length', str(len(raw))),
                        ('Cache-Control', 'no-store')])
        return [raw]

    def route(self, environ):
        method, path = environ['REQUEST_METHOD'], environ['PATH_INFO']
        if method == 'GET' and path == '/health':
            return {'contract': CONTRACT, 'process_alive': True, 'model_ready': self.ready() is True}, 200
        must(hmac.compare_digest(environ.get('HTTP_X_ARTLIVE_VMM_TOKEN', ''), self.token), 'UNAUTHORIZED', 401)
        if method == 'GET' and path == '/internal/vmm/status':
            return self.status(), 200
        must(method == 'POST' and path == '/api/generate_with_image', 'NOT_FOUND', 404)
        must(environ.get('CONTENT_TYPE', '').split(';')[0] == 'application/json', 'CONTENT_TYPE', 400)
        try:
            length = int(environ.get('CONTENT_LENGTH', '0'))
        except ValueError as error:
            raise Refused('BODY_LENGTH', 400) from error
        must(0 < length <= MAX_BODY, 'BODY_LENGTH', 400)
        raw = environ['wsgi.input'].read(length)
        must(len(raw) == length, 'INCOMPLETE_BODY', 400)
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError) as error:
            raise Refused('INVALID_JSON', 400) from error
        identity = request_identity(environ, body)
        must(self.ready() is True, 'VMM_MODEL_NOT_READY', 503)
        must(self.execution.acquire(blocking=False), 'VMM_BUSY')
        request_id = environ.get('HTTP_X_ARTLIVE_VMM_REQUEST_ID')
        reserved = False
        try:
            self.store.reserve(request_id, identity)
            reserved = True
            # No client URL, file path or credential reaches the legacy app.
            backend_env = {key: value for key, value in environ.items()
                           if not key.startswith('HTTP_X_ARTLIVE_VMM_')}
            backend_env['wsgi.input'] = io.BytesIO(raw)
            received = []
            def capture(status, headers, exc_info=None):
                received.append((status, headers))
            response = self.backend(backend_env, capture)
            try:
                chunks, count = [], 0
                for chunk in response:
                    count += len(chunk)
                    must(count <= 65536, 'VMM_BACKEND_RESPONSE_SIZE', 502)
                    chunks.append(chunk)
                must(received and received[0][0].startswith('200 '), 'VMM_BACKEND_FAILED', 502)
                value = json.loads(b''.join(chunks))
                public, proof = safe_output(self.output_root, value, identity['duration'])
                public['request_id'] = request_id
                self.store.finish(request_id, 'SUCCEEDED', proof)
                reserved = False
                return public, 200
            finally:
                if hasattr(response, 'close'):
                    response.close()
        except BaseException:
            if reserved:
                # Failure after admission can mean generation happened. Retain
                # the count and block until an operator records reconciliation.
                self.store.finish(request_id, 'UNKNOWN')
            raise
        finally:
            self.execution.release()


def verify_legacy_sources(source_root):
    """No imports or model allocation. Fixed source, never the AI fixed-30 copy."""
    import subprocess
    root = Path(source_root)
    must(str(root) == '/root/autodl-tmp/auralink' and not root.is_symlink(), 'LEGACY_FIXED_ROOT')
    trusted_path(root)
    result = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                            env={'PATH': '/usr/bin:/bin', 'GIT_OPTIONAL_LOCKS': '0'},
                            capture_output=True, text=True, timeout=10, check=True)
    must(result.stdout.strip() == LEGACY_COMMIT, 'LEGACY_COMMIT_MISMATCH')
    result = subprocess.run(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=no'],
                            env={'PATH': '/usr/bin:/bin', 'GIT_OPTIONAL_LOCKS': '0'},
                            capture_output=True, text=True, timeout=10, check=True)
    must(not result.stdout, 'LEGACY_TRACKED_SOURCE_DIRTY')
    for name, expected in LEGACY_HASHES.items():
        path = regular(trusted_path(root / name))
        must(path.resolve() == path and hashlib.sha256(path.read_bytes()).hexdigest() == expected,
             'LEGACY_SOURCE_HASH_MISMATCH')
    return root


def model_manifest(source_root, model_assets, checkpoint, environment):
    """Validate exact offline asset coverage without importing model libraries.

    CLIP ignores HF offline flags: its bound source calls _download even for a
    named model. Require its actual default cache and the source table's digest,
    so _download takes only its verified-local-file branch.
    """
    root = Path(source_root)
    must(str(root) == '/root/autodl-tmp/auralink', 'LEGACY_FIXED_ROOT')
    musicgen = root / 'VMM/models/musicgen-small'
    expected_checkpoint = root / 'VMM/models/final_model.pth'
    must(str(checkpoint) == str(expected_checkpoint), 'CHECKPOINT_FIXED_BINDING')
    must(environment.get('AURALINK_VMM_MUSICGEN_PATH') == str(musicgen)
         and environment.get('AURALINK_VMM_CHECKPOINT_PATH') == str(checkpoint), 'MODEL_ENV_BINDING')
    must(environment.get('HOME') == '/root' and environment.get('AURALINK_VMM_CLIP_MODEL') == 'ViT-B/32',
         'CLIP_EFFECTIVE_CACHE_BINDING')
    must(environment.get('AURALINK_HF_ENDPOINT') == '' and not environment.get('HF_ENDPOINT'),
         'REMOTE_MODEL_ENDPOINT_DISABLED')
    for name in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_DATASETS_OFFLINE'):
        must(environment.get(name) == '1', 'OFFLINE_ENV_REQUIRED')
    must(isinstance(model_assets, list) and model_assets, 'MODEL_ASSETS_REQUIRED')
    expected = {}
    for item in model_assets:
        must(isinstance(item, dict) and set(item) == {'path', 'sha256'}, 'MODEL_ASSET_BINDING')
        path = Path(item['path'])
        digest = item['sha256']
        must(path.is_absolute() and isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest), 'MODEL_ASSET_IDENTITY')
        must(str(path) not in expected, 'DUPLICATE_MODEL_ASSET')
        expected[str(path)] = digest
    required = {str(musicgen / name) for name in MUSICGEN_FILES} | {str(checkpoint), CLIP_CACHE}
    must(set(expected) == required, 'MODEL_ASSET_COVERAGE')
    must(expected[CLIP_CACHE] == CLIP_MODEL_SHA256, 'CLIP_DOWNLOAD_PREVENTION_HASH')
    return expected


def initialize_legacy(source_root, model_assets, checkpoint):
    """Called only by explicit future lifecycle start; never at module import.

    Binding/lifecycle must first verify the interpreter, dependency inventory,
    offline environment and resources. Checkpoint omission cannot yield READY.
    """
    import importlib.util
    import sys
    root = verify_legacy_sources(source_root)
    expected = model_manifest(root, model_assets, checkpoint, os.environ)
    def verify_assets():
        for name, digest in expected.items():
            path = regular(trusted_path(name))
            must(path.resolve() == path, 'MODEL_ASSET_SYMLINK')
            hasher = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    hasher.update(block)
            must(hasher.hexdigest() == digest, 'MODEL_ASSET_HASH_MISMATCH')
    verify_assets()
    sys.path.insert(0, str(root / 'VMM'))
    spec = importlib.util.spec_from_file_location('artlive_bound_vmm', root / 'VMM/app.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.initialize_model()
    verify_assets()
    must(module.model_initialized is True and module.model is not None, 'MODEL_INITIALIZATION_FAILED')
    return module


def serve(binding):
    """Explicit lifecycle entry point. No fixture/backend selector is accepted.

    Loading happens once in a startup thread, health/status never load models.
    The caller owns PID, listener, deadline, stop and restart supervision.
    """
    from socketserver import ThreadingMixIn
    from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server
    must(binding.get('schema') == 'R25_VMM_SERVICE_BINDING_V1', 'SERVICE_BINDING_SCHEMA')
    must(binding.get('host') == '127.0.0.1' and binding.get('port') == 5001, 'PRIVATE_FIXED_LISTENER')
    must(binding.get('legacy_commit') == LEGACY_COMMIT and binding.get('legacy_hashes') == LEGACY_HASHES,
         'LEGACY_IDENTITY_BINDING')
    root = verify_legacy_sources(binding['source_root'])
    token = read_token(binding['token_file'])
    store = GateStore(binding['state_dir'])
    output = Path(binding['output_root'])
    must(output.is_absolute() and output.resolve() == output and output.is_dir(), 'OUTPUT_ROOT_IDENTITY')
    must(os.environ.get('AURALINK_VMM_OUTPUT_DIR') == str(output)
         and os.environ.get('AURALINK_VMM_UPLOAD_DIR') == str(output), 'OUTPUT_ENV_BINDING')
    loaded = {}
    ready = threading.Event()
    def backend(environ, start_response):
        must(ready.is_set(), 'VMM_MODEL_NOT_READY', 503)
        return loaded['module'].app.wsgi_app(environ, start_response)
    def initialize():
        try:
            loaded['module'] = initialize_legacy(root, binding['model_assets'], binding['checkpoint'])
            ready.set()
        except BaseException:
            # No exception text: model libraries may include paths/private data.
            loaded['error'] = 'MODEL_INITIALIZATION_FAILED'
    wrapper = ManagedService(backend, ready.is_set, store, token, output)
    class ThreadedServer(ThreadingMixIn, WSGIServer):
        daemon_threads = True
    class QuietHandler(WSGIRequestHandler):
        def log_message(self, format, *args):
            pass
    # Bind before loading. A foreign listener must prevent all model loading.
    with make_server('127.0.0.1', 5001, wrapper, ThreadedServer, QuietHandler) as server:
        threading.Thread(target=initialize, daemon=True, name='vmm-initialize').start()
        server.serve_forever(poll_interval=0.2)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--binding', required=True)
    args = parser.parse_args()
    binding = json.loads(regular(args.binding, private=True).read_bytes())
    serve(binding)
