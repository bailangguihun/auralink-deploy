"""Isolated interoperability fixture. Real ManagedService, synthetic WAV backend; no model import."""
import argparse
import io
import json
from pathlib import Path
import sys
import threading
import wave
from wsgiref.simple_server import make_server

parser = argparse.ArgumentParser()
parser.add_argument('--isolated-cpu-root', required=True)
args = parser.parse_args()
root = Path(args.isolated_cpu_root)
root.mkdir(mode=0o700)  # Refuse reuse. Never points at a production root.
(root / 'output').mkdir()
(root / 'state').mkdir(mode=0o700)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'r25'))
import vmm_service as vmm

TOKEN = 'test_only_012345678901234567890123456789'
count = 0

def backend(environ, start_response):
    global count
    count += 1
    body = json.loads(environ['wsgi.input'].read(int(environ['CONTENT_LENGTH'])))
    assert not any(k.startswith('HTTP_X_ARTLIVE_VMM_') for k in environ)
    filename = 'vmm_generated_' + str(count) + '.wav'
    with wave.open(str(root / 'output' / filename), 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000)
        wav.writeframes(b'\0\0' * 8000 * body['duration'])
    (root / 'backend-count').write_text(str(count))
    start_response('200 OK', [('Content-Type', 'application/json')])
    return [json.dumps({'success': True, 'fileName': filename}).encode()]

store = vmm.GateStore(root / 'state')
app = vmm.ManagedService(backend, lambda: True, store, TOKEN, root / 'output')
store.configure('OPEN')  # Synthetic offline fixture only; not a production authorization.
server = make_server('127.0.0.1', 0, app)
thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
print(json.dumps({'port': server.server_port, 'root': str(root)}), flush=True)
try:
    sys.stdin.readline()
finally:
    server.shutdown(); server.server_close(); thread.join(3)
