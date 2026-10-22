"""Real CPU child processes, loopback status and original lock semantics."""
import contextlib
import ctypes
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

import vmm_lifecycle as life
import vmm_service


CHILD = '''import json,sys,time
from http.server import HTTPServer,BaseHTTPRequestHandler
started=time.monotonic()
class H(BaseHTTPRequestHandler):
 def do_GET(self):
  if self.path!='/internal/vmm/status':self.send_error(404);return
  value={'contract':'artlive-vmm-managed-v1','model_ready':time.monotonic()-started>float(sys.argv[2]),'dispatch_permitted':False}
  raw=json.dumps(value).encode();self.send_response(200);self.end_headers();self.wfile.write(raw)
 def log_message(self,*a):pass
HTTPServer(('127.0.0.1',int(sys.argv[1])),H).serve_forever()
'''


class FixtureEngine:
    """Inject only the existing role boundary; coordination uses real flock."""
    def __init__(self, root):
        self.root = root
        self.v = {'R25_VMM_ROLE_SHA256': 'a' * 64}
        self.lock = root / 'original-shared.lock'
        self.lock.write_bytes(b'')
        self.lock.chmod(0o600)
        self.held = False
        self.vmm_gate_sha256 = None

    @contextlib.contextmanager
    def lease(self):
        with self.lock.open('rb') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise life.Refused('fixture_shared_deployment_busy') from None
            self.held = True
            try:
                yield
            finally:
                self.held = False

    def command(self, gate, mode, **fields):
        directory = self.root / 'runtime/config'
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / 'vmm-gate-command.json'
        life.write(path, dict(schema='R25_VMM_GATE_COMMAND_V1', mode=mode,
                              role_sha256=self.v['R25_VMM_ROLE_SHA256'],
                              before_gate_sha256=life.sha(gate.path), **fields))
        self.vmm_gate_sha256 = life.sha(path)
        return self.vmm_gate_sha256


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='vmm-role-cpu-')
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.state.mkdir(mode=0o700)
        self.script = self.root / 'fixture.py'
        self.script.write_text(CHILD)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.store = vmm_service.GateStore(self.state)
        self.role = life.Role(self.state, [sys.executable, '-B', str(self.script), str(self.port), '.05'],
                             {'PATH': '/usr/bin:/bin', 'R25_VMM_HEALTH_TOKEN': 'fixture'}, self.root,
                             self.port, self.store, startup_seconds=.3, stop_seconds=1, restart_limit=0)
        self.processes = []

    def tearDown(self):
        # Tests clean only their captured child handles; no port/name signaling.
        for child in self.role.children + self.processes:
            if child.poll() is None:
                child.terminate()
                child.wait(2)
            else:
                child.wait()
        self.temp.cleanup()

    def ready(self):
        until = time.monotonic() + 2
        while time.monotonic() < until:
            value = self.role.recover()
            if value['ready']:
                return value
            time.sleep(.02)
        self.fail('fixture readiness')

    def start(self):
        self.role.enable()
        self.role.start(explicit=True)
        return self.ready()

    def test_disabled_no_start_and_repeated_start_stop(self):
        self.assertFalse(self.role.recover()['alive'])
        with self.assertRaisesRegex(life.Refused, 'disabled'):
            self.role.start()
        self.assertTrue(self.start()['alive'])
        first = self.role.state()['process']
        self.role.start(explicit=True)
        self.assertEqual(first, self.role.state()['process'])
        self.role.stop()
        self.assertFalse(self.role.recover()['alive'])
        self.assertEqual('STOPPED', self.role.stop()['result'])
        self.assertTrue(self.start()['ready'])

    def test_delayed_ready_does_not_hold_parent_lock(self):
        lock = self.root / 'original.lock'
        lock.write_bytes(b'')
        inode = lock.stat().st_ino
        with lock.open('rb') as fd:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.role.enable()
            self.role.start(explicit=True)
        with lock.open('rb') as check:
            fcntl.flock(check, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertEqual(inode, lock.stat().st_ino)
        self.assertTrue(self.ready()['ready'])

    def test_readiness_timeout_ends_with_bounded_budget(self):
        self.role.command[-1] = '99'
        self.role.enable()
        self.role.start(explicit=True)
        time.sleep(.35)
        with self.assertRaisesRegex(life.Refused, 'budget'):
            self.role.recover()
        self.assertFalse(self.role.status()['alive'])
        self.assertEqual(1, self.role.state()['attempts'])
        with self.assertRaisesRegex(life.Refused, 'budget'):
            self.role.recover()

    def test_foreign_port_not_adopted_or_signalled(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', self.port)); sock.listen()
            with self.assertRaisesRegex(life.Refused, 'foreign_listener'):
                self.role.enable()
            self.assertEqual(0, self.role.state()['attempts'])
            self.assertTrue(life.port_busy(self.port))

    def test_time_wait_is_not_an_active_foreign_listener(self):
        # Real kernel TIME_WAIT: a bind probe fails although no LISTEN socket
        # remains. This regression fails with the original bind-based probe.
        with socket.socket() as server, socket.socket() as client:
            server.bind(('127.0.0.1', 0)); port = server.getsockname()[1]
            server.listen(); client.connect(('127.0.0.1', port))
            accepted, _ = server.accept()
            accepted.shutdown(socket.SHUT_WR)
            self.assertEqual(b'', client.recv(1))
            client.close(); accepted.close(); server.close()
            until = time.monotonic() + 1
            while time.monotonic() < until:
                states = [line.split()[3] for line in Path('/proc/net/tcp').read_text().splitlines()[1:]
                          if int(line.split()[1].split(':')[1], 16) == port]
                if '06' in states: break
                time.sleep(.01)
            self.assertIn('06', states)
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                with self.assertRaises(OSError):probe.bind(('127.0.0.1', port))
            self.assertFalse(life.port_busy(port))

    def test_reused_pid_rejected_without_signal(self):
        self.start()
        value = self.role.state()
        value['process']['ticks'] = str(int(value['process']['ticks']) - 1)
        life.write(self.role.record, value)
        with self.assertRaisesRegex(life.Refused, 'reused'):
            self.role.stop()
        self.assertIsNone(self.role.children[0].poll())

    def test_stop_uses_stat_when_full_identity_probe_disappears(self):
        self.script.write_text(CHILD.replace('started=time.monotonic()',
            'import signal\nsignal.signal(signal.SIGTERM, lambda *_: (time.sleep(.15), sys.exit(0)))\nstarted=time.monotonic()'))
        self.start()
        real_process, real_signal = life.process, signal.pidfd_send_signal
        sent = []
        def send(*args):
            result = real_signal(*args); sent.append(True); return result
        def observed(pid):
            # Reproduce an exe/cwd lookup disappearing after SIGTERM while
            # the actual HTTP child still performs its bounded exit cleanup.
            return None if sent else real_process(pid)
        with patch.object(signal, 'pidfd_send_signal', send), patch.object(life, 'process', observed):
            self.assertEqual('STOPPED', self.role.stop()['result'])
        self.assertTrue(sent)
        self.assertFalse(life.port_busy(self.port))

    def test_stop_pins_actual_child_without_python_pidfd_open_wrapper(self):
        self.start()
        with patch.object(os, 'pidfd_open', None, create=True):
            self.assertEqual('STOPPED', self.role.stop()['result'])
        self.assertFalse(self.role.status()['alive'])

    def test_pidfd_unavailable_never_falls_back_to_numeric_signal(self):
        self.start()
        libc = Mock()
        def unavailable(*args):
            ctypes.set_errno(errno.ENOSYS)
            return -1
        libc.syscall.side_effect = unavailable
        with patch.object(os, 'pidfd_open', None, create=True), patch.object(ctypes, 'CDLL', return_value=libc):
            with self.assertRaisesRegex(OSError, 'not implemented'):
                self.role.stop()
        self.assertIsNone(self.role.children[0].poll())
        self.assertEqual('CLOSED', self.store.snapshot()['mode'])
        self.assertEqual('STOPPED', self.role.state()['desired'])

    def test_stop_waits_only_for_previously_owned_socket_observation(self):
        self.start()
        real_inodes = life.listening_inodes
        old = real_inodes(self.port)
        self.assertTrue(old)
        lag = []
        def observed(port):
            current = real_inodes(port)
            if port == self.port and not current and len(lag) < 3:
                lag.append(True)
                return old  # kernel observation lag, not a new listener
            return current
        with patch.object(life, 'listening_inodes', observed):
            self.assertEqual('STOPPED', self.role.stop()['result'])
        self.assertEqual(3, len(lag))

    def test_new_listener_during_stop_is_refused_without_signal(self):
        self.start()
        real_inodes = life.listening_inodes
        replacement = socket.socket()
        replacement.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        bound = []
        def observed(port):
            current = real_inodes(port)
            if port == self.port and not current and not bound:
                replacement.bind(('127.0.0.1', port)); replacement.listen()
                bound.append(True)
                return real_inodes(port)
            return current
        try:
            with patch.object(life, 'listening_inodes', observed):
                with self.assertRaisesRegex(life.Refused, 'foreign_listener_after_stop'):
                    self.role.stop()
            self.assertTrue(bound)
            with socket.create_connection(('127.0.0.1', self.port), timeout=1):
                accepted, _ = replacement.accept(); accepted.close()
        finally:
            replacement.close()

    def test_stale_record_recovery_budget_is_durable(self):
        self.role.restart_limit = 1
        self.start()
        child = self.role.children[0]
        child.terminate(); child.wait()
        self.role.recover()
        self.assertTrue(self.ready()['alive'])
        self.assertEqual(2, self.role.state()['attempts'])
        self.role.children[-1].terminate(); self.role.children[-1].wait()
        with self.assertRaisesRegex(life.Refused, 'budget'):
            self.role.recover()

    def test_in_flight_stop_closes_and_does_not_kill(self):
        self.start()
        self.store.configure('OPEN')
        self.store.reserve('one', {'synthetic': True})
        with self.assertRaisesRegex(life.Refused, 'drain'):
            self.role.stop()
        self.assertIsNone(self.role.children[0].poll())
        self.assertEqual('CLOSED', self.store.snapshot()['mode'])
        self.assertEqual('STOPPED', self.role.state()['desired'])
        self.role.recover()
        self.assertEqual(1, self.role.state()['attempts'])
        self.store.finish('one', 'SUCCEEDED')
        self.role.stop()
        self.assertFalse(self.role.status()['alive'])

    def test_ambiguous_crash_not_restarted_or_replayed(self):
        self.start()
        self.store.configure('OPEN'); self.store.reserve('uncertain', {'synthetic': True})
        self.role.children[0].terminate(); self.role.children[0].wait()
        self.role.restart_limit = 1
        with self.assertRaisesRegex(life.Refused, 'uncertain'):
            self.role.recover()
        self.assertEqual('UNKNOWN', self.store.read()['requests']['uncertain']['state'])
        self.assertEqual(1, self.store.snapshot()['attempts'])

    def test_owned_process_without_listener_cannot_adopt_foreign_health(self):
        self.role.command = [sys.executable, '-B', '-c', 'import time; time.sleep(20)']
        self.role.enable(); self.role.start(explicit=True)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', self.port)); sock.listen()
            with self.assertRaisesRegex(life.Refused, 'foreign_listener'):
                self.role.recover()
            with self.assertRaisesRegex(life.Refused, 'foreign_listener'):
                self.role.stop()
            self.assertIsNone(self.role.children[0].poll())

    def test_malformed_readiness_is_not_true(self):
        self.script.write_text(CHILD.replace("time.monotonic()-started>float(sys.argv[2])", "'false'"))
        self.role.enable(); self.role.start(explicit=True)
        time.sleep(.1)
        with self.assertRaisesRegex(life.Refused, 'health_contract'):
            self.role.recover()

    def test_boot_identity_prevents_pid_adoption(self):
        self.start()
        value = self.role.state(); value['process']['boot_id'] = 'previous-boot'
        life.write(self.role.record, value)
        with self.assertRaisesRegex(life.Refused, 'stale_boot'):
            self.role.stop()
        self.assertIsNone(self.role.children[0].poll())

    def test_unrecorded_child_cannot_be_duplicated_even_before_listen(self):
        self.role.command = [sys.executable, '-B', '-c', 'import time; time.sleep(20)']
        self.role.enable(); self.role.start(explicit=True)
        value = self.role.state(); value['process'] = None
        life.write(self.role.record, value)
        with self.assertRaisesRegex(life.Refused, 'unrecorded_process_lease'):
            self.role.start(explicit=True)
        self.assertEqual(1, len(self.role.children))
        self.assertIsNone(self.role.children[0].poll())

    def test_manage_approval_hash_closed_gate_and_consumed_command(self):
        self.start()
        engine = FixtureEngine(self.root)
        permit = {'owner_id': '7', 'source_painting_id': '33', 'image_sha256': 'b' * 64, 'duration': 3}
        def injected_role(current):
            self.assertTrue(current.held)
            return self.role
        with patch.object(life, 'managed_role', side_effect=injected_role):
            approved = engine.command(self.store, 'acceptance', permit=permit)
            engine.vmm_gate_sha256 = 'c' * 64
            with self.assertRaisesRegex(life.Refused, 'approval_identity'):
                life.manage(engine, 'acceptance')
            self.assertEqual([], self.role.state().get('gate_commands', []))
            self.store.configure('OPEN')
            engine.command(self.store, 'acceptance', permit=permit)
            with self.assertRaisesRegex(life.Refused, 'close_before_approval'):
                life.manage(engine, 'acceptance')
            self.assertEqual([], self.role.state().get('gate_commands', []))
            self.store.configure('CLOSED')
            from test_vmm_window_identity import write_window
            write_window(engine, self.role)
            approved = engine.vmm_gate_sha256
            self.assertEqual('ACCEPTANCE', life.manage(engine, 'acceptance')['mode'])
            self.assertEqual([approved], self.role.state()['gate_commands'])
            # Closing without dispatch restores the same gate bytes, but the
            # used approval is still not authority to open a second window.
            self.store.configure('CLOSED')
            with self.assertRaisesRegex(life.Refused, 'command_consumed'):
                life.manage(engine, 'acceptance')
            self.assertEqual('CLOSED', self.store.snapshot()['mode'])
            self.assertEqual(0, self.store.snapshot()['attempts'])

    def test_manage_busy_shared_lock_never_spawns_or_consumes_approval(self):
        engine = FixtureEngine(self.root)
        engine.command(self.store, 'open')
        inode = engine.lock.stat().st_ino
        with engine.lock.open('rb') as holder, patch.object(life, 'managed_role', return_value=self.role) as injected:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for mode in ('start', 'open'):
                with self.subTest(mode=mode), self.assertRaisesRegex(life.Refused, 'shared_deployment_busy'):
                    life.manage(engine, mode)
            injected.assert_not_called()
        self.assertEqual(inode, engine.lock.stat().st_ino)
        self.assertEqual([], self.role.children)
        self.assertEqual(0, self.role.state()['attempts'])
        self.assertEqual([], self.role.state().get('gate_commands', []))
        self.assertEqual('CLOSED', self.store.snapshot()['mode'])

    def test_manage_http_race_changes_ledger_after_outer_hash_but_inner_cas_refuses(self):
        marker = self.root / 'insert-ledger-race'
        # The actual fake HTTP child mutates the actual gate only when its
        # status is fetched after approval validation. No gate or CAS result
        # is stubbed: this crosses a real process/file/flock boundary.
        before_value = "  value={'contract'"
        race = (
            "  if Path(" + repr(str(marker)) + ").exists():\n"
            "   gate=GateStore(" + repr(str(self.state)) + ");gate.configure('OPEN')\n"
            "   gate.reserve('racing-request',{'synthetic':True});gate.finish('racing-request','SUCCEEDED');gate.configure('CLOSED')\n"
            "   Path(" + repr(str(marker)) + ").unlink()\n"
        )
        child = CHILD.replace('started=time.monotonic()',
            "from pathlib import Path\nsys.path.insert(0," + repr(str(Path(vmm_service.__file__).parent)) + ")\nfrom vmm_service import GateStore\nstarted=time.monotonic()")
        child = child.replace(before_value, race + before_value)
        self.script.write_text(child)
        self.start()
        engine = FixtureEngine(self.root)
        approved = engine.command(self.store, 'open')
        marker.write_text('synthetic race boundary')
        with patch.object(life, 'managed_role', return_value=self.role):
            with self.assertRaisesRegex(vmm_service.Refused, 'COMPARE_AND_SWAP_CONFLICT'):
                life.manage(engine, 'open')
            self.assertEqual([approved], self.role.state()['gate_commands'])
            self.assertEqual('CLOSED', self.store.snapshot()['mode'])
            self.assertEqual(1, self.store.snapshot()['attempts'])
            with self.assertRaisesRegex(life.Refused, 'command_binding'):
                life.manage(engine, 'open')
            self.assertEqual(1, self.store.snapshot()['attempts'])

    def test_disabled_adapter_hook_and_identity_transform(self):
        base = Path(__file__).parent / 'fixtures/lifecycle_assistant_v2.py.snapshot'
        self.assertEqual(life.BASE_LIFECYCLE_SHA256, hashlib.sha256(base.read_bytes()).hexdigest())
        compiled = life.extend_lifecycle(base.read_bytes())
        namespace = {'__name__': 'fixture_module'}
        exec(compile(compiled, str(base), 'exec'), namespace)
        engine = namespace['Engine'](self.root, self.root)
        config = self.root / 'runtime/handoff-control.env'
        config.parent.mkdir()
        config.write_bytes(b'UNRELATED=preserved\n'); config.chmod(0o600)
        engine.private = (os.geteuid(), os.getegid())
        self.assertEqual({'result': 'DISABLED'}, engine.vmm_action('recover'))
        # VMM recovery failure cannot invoke Core recover on a healthy stack.
        engine.status = lambda: {'result': 'RUNNING'}
        def refused(mode): raise namespace['Refused']('fixture VMM not ready')
        engine.vmm_action = refused
        engine.recover = lambda: self.fail('healthy Core recovery invoked')
        self.assertEqual({'result': 'RUNNING'}, engine.watchdog_once())
        with self.assertRaisesRegex(life.Refused, 'baseline'):
            life.extend_lifecycle(base.read_bytes() + b'\n')


if __name__ == '__main__':
    unittest.main()
