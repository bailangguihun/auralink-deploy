"""Optional VMM role driven by the installed R25 lifecycle, never a supervisor.

The production CLI is the existing lifecycle adapter after a reviewed binding.
Fixture injection is a Python API only. Model initialization belongs to the
child service, never status or the watchdog. Children never inherit the shared
deployment lock. Desired state and an execution budget survive process death.
"""
from __future__ import annotations

import hashlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import time
import urllib.request

BASE_LIFECYCLE_SHA256 = '783c10bca59e8723c2a208970efd566890776aff8813a536bbc4c8e069d1fda3'
ROLE_SCHEMA = 'R25_VMM_ROLE_V1'
ROOT = Path('/root/autodl-tmp/artlive-r25-native-pg16/handoff-production-20260923T062101Z')
INTERPRETER = '/root/autodl-tmp/auralink/micromamba/envs/auralink-ai/bin/python'


class Refused(RuntimeError):
    pass


def must(ok, reason):
    if not ok:
        raise Refused(reason)


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def private(path, directory=False):
    path = Path(path)
    for item in (path, *path.parents):
        must(not item.is_symlink(), 'vmm_symlink_path')
    item = path.lstat()
    must((stat.S_ISDIR if directory else stat.S_ISREG)(item.st_mode)
         and item.st_uid == os.geteuid() and stat.S_IMODE(item.st_mode) == (0o700 if directory else 0o600),
         'vmm_private_identity')
    if not directory:
        must(item.st_nlink == 1, 'vmm_hardlink')
    return path


def read(path):
    private(path)
    must(Path(path).stat().st_size <= 1024 * 1024, 'vmm_record_size')
    return json.loads(Path(path).read_bytes())


def write(path, value):
    path = Path(path)
    private(path.parent, True)
    if path.exists() or path.is_symlink():
        private(path)
    tmp = path.with_name(path.name + '.new-' + os.urandom(8).hex())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if tmp.exists():
            tmp.unlink()


def process(pid):
    try:
        root = Path('/proc') / str(pid)
        raw = (root / 'stat').read_text()
        fields = raw[raw.rfind(')') + 2:].split()
        if fields[0] == 'Z':
            return None
        uid = next(x for x in (root / 'status').read_text().splitlines() if x.startswith('Uid:'))
        return dict(pid=int(pid), ticks=fields[19], boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                    uid=[int(x) for x in uid.split()[1:]],
                    pgid=int(fields[2]), sid=int(fields[3]), exe=str((root / 'exe').resolve()),
                    cwd=str((root / 'cwd').resolve()),
                    argv=[x.decode() for x in (root / 'cmdline').read_bytes().split(b'\0') if x])
    except (OSError, ValueError, StopIteration):
        return None


def port_busy(port):
    # bind() failure can be TIME_WAIT, not a live listener. The child still
    # binds before any model load; a genuinely unavailable bind fails closed.
    return bool(listening_inodes(port))


def listening_inodes(port):
    inodes = set()
    for table in ('tcp', 'tcp6'):
        for line in (Path('/proc/net') / table).read_text().splitlines()[1:]:
            fields = line.split()
            if fields[3] == '0A' and int(fields[1].split(':')[1], 16) == port:
                inodes.add(fields[9])
    return inodes


def owned_listener(pid, port):
    inodes = listening_inodes(port)
    if not inodes:
        return False
    owned = set()
    try:
        for descriptor in (Path('/proc') / str(pid) / 'fd').iterdir():
            try:
                target = os.readlink(descriptor)
                if target.startswith('socket:['):
                    owned.add(target[8:-1])
            except OSError:
                continue
    except FileNotFoundError:
        return False
    must(inodes <= owned, 'vmm_foreign_listener')
    return True


class Role:
    """All methods require the caller's original deployment lease.

    start returns STARTING without waiting under that lock. The existing
    watchdog advances readiness and bounded recovery; stop closes admission
    first and refuses to interrupt an in-flight request.
    """
    def __init__(self, state_dir, command, environment, cwd, port, gate, *,
                 startup_seconds=900, stop_seconds=30, restart_limit=1):
        self.root, self.command, self.environment = Path(state_dir), command, environment
        self.cwd, self.port, self.gate = str(Path(cwd).resolve()), port, gate
        self.startup_seconds, self.stop_seconds, self.restart_limit = startup_seconds, stop_seconds, restart_limit
        private(self.root, True)
        self.record = self.root / 'lifecycle.json'
        self.instance_lock = self.root / 'instance.lock'
        descriptor = os.open(self.instance_lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
        private(self.instance_lock)
        self.children = []
        if not self.record.exists():
            write(self.record, dict(schema=ROLE_SCHEMA, enabled=False, desired='STOPPED',
                                    attempts=0, process=None, state='DISABLED'))
        self.state()

    def state(self):
        value = read(self.record)
        must(value.get('schema') == ROLE_SCHEMA and type(value.get('enabled')) is bool
             and value.get('desired') in {'RUNNING', 'STOPPED'}
             and type(value.get('attempts')) is int and 0 <= value['attempts'], 'vmm_lifecycle_record')
        return value

    def owned(self, value):
        saved = value.get('process')
        if not saved:
            return None
        current = process(saved['pid'])
        if current is None:
            return None
        # A reboot makes the old identity stale, never adopt a coincident PID.
        if current['boot_id'] != saved.get('boot_id'):
            must(not port_busy(self.port), 'vmm_stale_boot_foreign_listener')
            return None
        must(current == saved, 'vmm_pid_reused_or_identity_changed')
        must(current['uid'] == [os.geteuid()] * 4 and current['pgid'] == current['pid'] == current['sid']
             and current['cwd'] == self.cwd and current['argv'] == self.command,
             'vmm_process_identity')
        return current

    def health(self):
        # Never inherit HTTP proxy settings or call a generation route.
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}/internal/vmm/status',
                                         headers={'X-ArtLIVE-VMM-Token': self.environment['R25_VMM_HEALTH_TOKEN']})
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=1) as response:
                data = response.read(8193)
                must(len(data) <= 8192, 'vmm_health_size')
                value = json.loads(data)
                must(value.get('contract') == 'artlive-vmm-managed-v1'
                     and type(value.get('model_ready')) is bool
                     and type(value.get('dispatch_permitted')) is bool, 'vmm_health_contract')
                return value
        except (OSError, ValueError):
            return {'model_ready': False, 'dispatch_permitted': False}

    def status(self):
        state = self.state()
        active = self.owned(state)
        if not active and port_busy(self.port):
            raise Refused('vmm_foreign_listener')
        if not active:
            descriptor = self.instance_lease()
            os.close(descriptor)
        health = self.health() if active and owned_listener(active['pid'], self.port) else {}
        return dict(result=state['state'], enabled=state['enabled'], desired=state['desired'],
                    alive=bool(active), ready=bool(health.get('model_ready')),
                    dispatch_permitted=bool(health.get('dispatch_permitted')),
                    start_attempts=state['attempts'], **self.gate.snapshot())

    def instance_lease(self):
        """Process-lifetime lock, distinct from the deployment lock.

        A crash between fork/exec and PID-record persistence cannot allow a
        second model process. No adoption or port-based kill is attempted.
        """
        private(self.instance_lock)
        descriptor = os.open(self.instance_lock, os.O_RDWR | os.O_NOFOLLOW)
        try:
            a, b = os.fstat(descriptor), self.instance_lock.stat()
            must((a.st_dev, a.st_ino) == (b.st_dev, b.st_ino), 'vmm_instance_inode')
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return descriptor
        except BlockingIOError:
            os.close(descriptor)
            raise Refused('vmm_unrecorded_process_lease') from None
        except BaseException:
            os.close(descriptor)
            raise

    def enable(self):
        value = self.state()
        value['enabled'] = True
        if value['desired'] == 'STOPPED':
            value['state'] = 'STOPPED'
        write(self.record, value)
        return self.status()

    def start(self, explicit=False):
        value = self.state()
        must(value['enabled'], 'vmm_role_disabled')
        active = self.owned(value)
        if active:
            return self.status()
        must(not port_busy(self.port), 'vmm_foreign_listener')
        instance_fd = self.instance_lease()
        # Keep no process-lifetime lease over preflight exceptions.
        os.close(instance_fd)
        # An explicit start after a failed attempt is a reviewed reset of the
        # PROCESS budget only. Gate records/request identities are never reset.
        if explicit:
            value['attempts'] = 0
        must(value['attempts'] <= self.restart_limit, 'vmm_restart_budget_exhausted')
        snapshot = self.gate.snapshot()
        if snapshot['in_flight']:
            self.gate.on_start()  # process is verified absent; retain UNKNOWN
        must(not self.gate.snapshot()['uncertain'], 'vmm_outcome_uncertain')
        self.gate.configure('CLOSED')
        value.update(desired='RUNNING', state='START_INTENT', attempts=value['attempts'] + 1,
                     started_at=time.time(), started_monotonic=time.monotonic(), process=None)
        write(self.record, value)
        log = self.root / 'service.log'
        descriptor = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        private(log)
        environment = {k: v for k, v in self.environment.items() if k != 'R25_VMM_HEALTH_TOKEN'}
        try:
            instance_fd = self.instance_lease()
            try:
                environment['R25_VMM_INSTANCE_FD'] = str(instance_fd)
                with os.fdopen(descriptor, 'ab') as stream:
                    child = subprocess.Popen(self.command, cwd=self.cwd, env=environment, stdin=subprocess.DEVNULL,
                                             stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
                                             close_fds=True, pass_fds=(instance_fd,))
            finally:
                os.close(instance_fd)
            self.children.append(child)
            deadline = time.monotonic() + 1
            current = process(child.pid)
            while time.monotonic() < deadline and (not current or current['argv'] != self.command):
                time.sleep(.01)
                current = process(child.pid)
            must(current is not None and current['argv'] == self.command and current['pgid'] == child.pid,
                 'vmm_spawn_identity')
            value.update(process=current, state='STARTING')
            write(self.record, value)
        except BaseException:
            # Do not signal an unproven identity. Persist intent for recovery;
            # foreign/unrecorded listeners will refuse further launches.
            value['state'] = 'START_FAILED'
            write(self.record, value)
            raise
        return self.status()

    def stop(self, disable=False):
        value = self.state()
        self.gate.configure('CLOSED')
        # Intentional stop survives busy refusal, reboot and watchdog loops.
        value.update(desired='STOPPED', state='STOP_PENDING')
        if disable:
            value['enabled'] = False
        write(self.record, value)
        active = self.owned(value)
        if not active and self.gate.snapshot()['in_flight']:
            self.gate.on_start()
        must(not self.gate.snapshot()['in_flight'], 'vmm_in_flight_drain_required')
        if active:
            owned_listener(active['pid'], self.port)
            self.owned(value)
            os.kill(active['pid'], signal.SIGTERM)
            until = time.monotonic() + self.stop_seconds
            current = process(active['pid'])
            while time.monotonic() < until and current is not None:
                must(current['ticks'] == active['ticks'] and current['boot_id'] == active['boot_id'], 'vmm_stop_pid_reused')
                time.sleep(.025)
                current = process(active['pid'])
            must(current is None, 'vmm_stop_timeout')
        must(not port_busy(self.port), 'vmm_foreign_listener_after_stop')
        descriptor = self.instance_lease()
        os.close(descriptor)
        value.update(process=None, state='DISABLED' if not value['enabled'] else 'STOPPED')
        write(self.record, value)
        for child in self.children:
            child.poll()
        return self.status()

    def recover(self):
        value = self.state()
        if not value['enabled'] or value['desired'] == 'STOPPED':
            return self.status()
        active = self.owned(value)
        if active:
            if owned_listener(active['pid'], self.port) and self.health().get('model_ready') is True:
                value['state'] = 'READY'
                write(self.record, value)
                return self.status()
            if time.monotonic() - value['started_monotonic'] < self.startup_seconds:
                return self.status()
            self.stop()
            value = self.state()
            value.update(desired='RUNNING', state='STARTUP_TIMEOUT')
            write(self.record, value)
        try:
            return self.start()
        except Refused:
            value = self.state()
            value['state'] = 'RECOVERY_BLOCKED'
            write(self.record, value)
            raise


def load_service_module(engine):
    values = engine.v
    path = engine.root / 'operations/r25-publisher/vmm_service.py'
    digest = values.get('R25_VMM_SERVICE_SHA256')
    private(path)
    must(sha(path) == digest, 'vmm_service_identity')
    spec = importlib.util.spec_from_file_location('verified_vmm_service', path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(path.read_bytes(), str(path), 'exec', dont_inherit=True), module.__dict__)
    return module


def managed_role(engine):
    path = engine.root / 'runtime/config/vmm-role.json'
    must(engine.v.get('R25_VMM_ROLE') == str(path) and sha(path) == engine.v.get('R25_VMM_ROLE_SHA256'),
         'vmm_role_binding')
    policy = read(path)
    must(policy.get('schema') == ROLE_SCHEMA and policy.get('enabled') is False, 'vmm_disabled_installation')
    # A disabled installation need not possess weights, a token or directories.
    if set(policy) == {'schema', 'enabled'}:
        return None
    binding_path = engine.root / 'runtime/config/vmm-service.json'
    must(policy['service_binding'] == str(binding_path) and sha(binding_path) == policy['service_binding_sha256'],
         'vmm_service_binding')
    binding = read(binding_path)
    must(binding['state_dir'] == str(engine.root / 'runtime/vmm') and binding['port'] == 5001
         and binding['host'] == '127.0.0.1' and binding['token_file'] == str(engine.root / 'runtime/config/vmm-token')
         and binding['output_root'] == '/root/autodl-tmp/auralink/backend/temp_uploads', 'vmm_fixed_paths')
    must(policy['interpreter'] == INTERPRETER and sha(Path(INTERPRETER).resolve()) == policy['interpreter_sha256'],
         'vmm_interpreter_identity')
    must(type(policy['restart_limit']) is int and 0 <= policy['restart_limit'] <= 3
         and type(policy['startup_seconds']) is int and 1 <= policy['startup_seconds'] <= 900
         and type(policy['stop_seconds']) is int and 1 <= policy['stop_seconds'] <= 30, 'vmm_finite_limits')
    service = load_service_module(engine)
    script = engine.root / 'operations/r25-publisher/vmm_service.py'
    must(sha(script) == policy['service_script_sha256'], 'vmm_script_policy')
    state = Path(binding['state_dir'])
    if not state.exists():
        state.mkdir(mode=0o700)
    private(state, True)
    source = Path(binding['source_root'])
    environment = dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', HOME='/root',
        PYTHONUNBUFFERED='1', LD_LIBRARY_PATH=str(source / 'micromamba/envs/auralink-ai/lib'),
        NO_PROXY='127.0.0.1,localhost,::1', no_proxy='127.0.0.1,localhost,::1',
        PYTHONPATH=str(source / 'VMM/audiocraft') + ':' + str(source / 'CLIP'),
        HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_DATASETS_OFFLINE='1', AURALINK_HF_ENDPOINT='',
        AURALINK_VMM_SERVICE_HOST='127.0.0.1', AURALINK_VMM_SERVICE_PORT='5001',
        AURALINK_VMM_OUTPUT_DIR=binding['output_root'], AURALINK_VMM_UPLOAD_DIR=binding['output_root'],
        AURALINK_VMM_CHECKPOINT_PATH=binding['checkpoint'], AURALINK_VMM_MUSICGEN_PATH=binding['musicgen_path'],
        AURALINK_VMM_CLIP_MODEL='ViT-B/32',
        R25_VMM_HEALTH_TOKEN=service.read_token(binding['token_file']))
    if 'cuda_visible_devices' in binding:
        import re
        must(re.fullmatch(r'[0-9]+(?:,[0-9]+)*', binding['cuda_visible_devices']) is not None,
             'vmm_device_binding')
        environment['CUDA_VISIBLE_DEVICES'] = binding['cuda_visible_devices']
    return Role(state, [INTERPRETER, '-B', str(script), '--binding', str(binding_path)], environment,
                source / 'VMM', 5001, service.GateStore(state), startup_seconds=policy['startup_seconds'],
                stop_seconds=policy['stop_seconds'], restart_limit=policy['restart_limit'])


def manage(engine, mode):
    with engine.lease():
        role = managed_role(engine)
        if role is None:
            must(mode in {'status', 'recover', 'stop', 'disable'}, 'vmm_activation_binding_required')
            return dict(result='DISABLED', alive=False, ready=False, dispatch_permitted=False)
        if mode == 'start':
            return role.start(explicit=True)
        if mode == 'disable':
            return role.stop(disable=True)
        if mode == 'close':
            role.gate.configure('CLOSED')
            return role.status()
        if mode in {'acceptance', 'open', 'reconcile'}:
            # Approval is an external reviewed digest, never generated by this
            # command. Consuming it first makes an interrupted control action
            # fail closed instead of reopening a consumed acceptance permit.
            command_path = engine.root / 'runtime/config/vmm-gate-command.json'
            expected = getattr(engine, 'vmm_gate_sha256', None)
            must(isinstance(expected, str) and len(expected) == 64 and sha(command_path) == expected,
                 'vmm_gate_approval_identity')
            command = read(command_path)
            must(command.get('schema') == 'R25_VMM_GATE_COMMAND_V1' and command.get('mode') == mode
                 and command.get('role_sha256') == engine.v['R25_VMM_ROLE_SHA256']
                 and command.get('before_gate_sha256') == sha(role.gate.path), 'vmm_gate_command_binding')
            must(role.gate.snapshot()['mode'] == 'CLOSED', 'vmm_gate_close_before_approval')
            value = role.state()
            used = value.setdefault('gate_commands', [])
            must(expected not in used, 'vmm_gate_command_consumed')
            if mode != 'reconcile':
                must(role.status()['ready'], 'vmm_model_not_ready')
            used.append(expected)
            write(role.record, value)
            if mode == 'reconcile':
                role.gate.acknowledge_unknown(command['request_id'], command['reason'],
                                             expected_sha256=command['before_gate_sha256'])
            else:
                role.gate.configure(mode.upper(), command.get('permit'), expected_sha256=command['before_gate_sha256'])
            return role.status()
        must(mode in {'enable', 'status', 'stop', 'recover'}, 'vmm_mode')
        return getattr(role, mode)()


HOOK = '''    def vmm_action(self, mode):
        # Optional role. Missing binding preserves legacy behavior without
        # requiring a token, model, directory or additional listener.
        guard(self.config,self.private); values=envfile(self.config)
        if 'R25_VMM_LIFECYCLE' not in values:
            return dict(result='DISABLED')
        path=self.root/'operations/r25-publisher/vmm_lifecycle.py'
        digest=values.get('R25_VMM_LIFECYCLE_SHA256','')
        must(values['R25_VMM_LIFECYCLE']==str(path) and re.fullmatch('[a-f0-9]{64}',digest),'vmm_module_binding')
        guard(path,self.private,0o600,digest)
        raw=path.read_bytes(); must(hashlib.sha256(raw).hexdigest()==digest,'vmm_module_bytes')
        module=type(sys)('verified_vmm_lifecycle'); module.__file__=str(path)
        exec(compile(raw,str(path),'exec',dont_inherit=True),module.__dict__)
        try:return module.manage(self,mode)
        except (module.Refused,RuntimeError) as error:raise Refused('vmm_'+str(error)) from None

'''


def extend_lifecycle(raw):
    """Exact installed baseline transform, not a replacement of an old fixture."""
    must(hashlib.sha256(raw).hexdigest() == BASE_LIFECYCLE_SHA256, 'vmm_lifecycle_baseline')
    source = raw.decode()
    source = source.replace('    def watchdog_once(self):\n', HOOK + '    def watchdog_once(self):\n', 1)
    source = source.replace('        try:return self.status()\n        except Busy:',
        "        try:\n            result=self.status()\n            try:self.vmm_action('recover')\n            except (Refused,Busy,OSError,ValueError,KeyError):pass  # VMM cannot restart healthy Core/AI\n            return result\n        except Busy:", 1)
    source = source.replace("'java-start'])", "'java-start','vmm-enable','vmm-start','vmm-status','vmm-stop','vmm-disable','vmm-recover','vmm-close','vmm-acceptance','vmm-open','vmm-reconcile'])\n    parser.add_argument('--vmm-command-sha256')", 1)
    marker = "        result=getattr(engine,{'start-normal'"
    must(marker in source, 'vmm_cli_anchor')
    source = source.replace(marker, "        if args.mode.startswith('vmm-'):\n            engine.vmm_gate_sha256=args.vmm_command_sha256\n            result=engine.vmm_action(args.mode[4:]); print('R25_VMM='+json.dumps(result,sort_keys=True)); return 0\n" + marker, 1)
    compile(source, '<bound lifecycle>', 'exec')
    return source.encode()
