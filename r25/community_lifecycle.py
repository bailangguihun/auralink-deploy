"""Reviewed caller library for a separate Community process, not an installer.

The host adapter must bind real release/config/runtime identities and supply
the installed original shared-lock verifier. No built-in approval, watchdog
registration, automatic restart, SQL, Web configuration write or VMM action.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import subprocess
import time
from urllib.parse import urlsplit
from community_bundle import manifest
from r25_storage import R25Storage, must, strict_json


def process(pid):
    root = Path('/proc') / str(pid)
    try:
        raw = (root/'stat').read_text()
    except FileNotFoundError:
        return None
    fields = raw[raw.rfind(')')+2:].split()
    if fields[0] == 'Z':
        return None
    return dict(pid=pid, ticks=fields[19],
                boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                uid=[int(v) for v in next(x for x in (root/'status').read_text().splitlines() if x.startswith('Uid:')).split()[1:]],
                exe=os.readlink(root/'exe'), cwd=os.readlink(root/'cwd'),
                argv=[x.decode() for x in (root/'cmdline').read_bytes().split(b'\0') if x])


def listeners(port):
    found = set()
    for table in ('tcp', 'tcp6'):
        for line in (Path('/proc/net')/table).read_text().splitlines()[1:]:
            fields = line.split()
            if fields[3] == '0A' and int(fields[1].split(':')[1],16) == port:
                # Community is IPv4-loopback only. A wildcard/IPv6 listener is
                # not evidence of the bound service, even on the correct port.
                must(table == 'tcp' and fields[1].split(':')[0] == '0100007F', 'community_foreign_listener')
                found.add(fields[9])
    return found


def stopping_alive(saved):
    # During exit Linux can remove exe/cwd before the task disappears. Those
    # links are readiness evidence, not a reliable stop-completion predicate.
    try:
        raw = (Path('/proc')/str(saved['pid'])/'stat').read_text()
    except FileNotFoundError:
        return False
    fields = raw[raw.rfind(')')+2:].split()
    must(fields[19] == saved['ticks'], 'community_stopping_pid_reused')
    return fields[0] != 'Z'


class Role:
    def __init__(self, root, binding, require_lease):
        self.root, self.binding, self.require_lease = Path(root), binding, require_lease
        self.storage = R25Storage([str(self.root), binding['release_root'], str(Path(binding['config_path']).parent)],
                                  owner_uid=os.geteuid(), owner_gid=os.getegid())
        self.children = {}

    def read(self, path):
        return self.storage.read_private_record(path)

    def write(self, path, value):
        old = self.storage.file_identity(path)['sha256'] if path.exists() else None
        return self.storage.atomic_write_json(path,value,replace=old is not None,expected_existing_sha256=old)

    def current(self):
        pointer = self.root/'current.json'
        if not pointer.exists(): return None, None
        value = self.read(pointer)
        must(set(value) == {'operation_id'} and re.fullmatch('[a-f0-9]{32}',value['operation_id']), 'community_pointer')
        path = self.root/('run-'+value['operation_id']+'.json')
        record = self.read(path)
        must(record['operation_id'] == value['operation_id'], 'community_run_identity')
        must(record['binding_sha256'] == hashlib.sha256(json.dumps(self.binding,sort_keys=True).encode()).hexdigest(),
             'community_binding_changed')
        return path, record

    def verify(self):
        self.require_lease()
        self.storage.validate_target(self.root, exists=True, directory=True)
        b = self.binding
        release = Path(b['release_root'])
        m = self.storage.read_private_record(release/'manifest.json',expected_sha256=b['manifest_sha256'])
        manifest(json.dumps(m).encode(), b['provenance'])
        expected = {'manifest.json'} | {f['path'] for f in m['files']}
        actual = {p.relative_to(release).as_posix() for p in release.rglob('*') if not p.is_dir()}
        must(actual == expected, 'community_installed_inventory')
        for p in release.rglob('*'):
            must(not p.is_symlink(), 'community_release_symlink')
        for f in m['files']:
            self.storage.file_identity(release/f['path'],expected_sha256=f['sha256'],expected_bytes=f['bytes'])
        node = Path(b['node'])
        must(node.is_absolute() and str(node.resolve(strict=True)) == str(node), 'community_node_path')
        info = node.stat()
        must(stat.S_ISREG(info.st_mode) and not info.st_mode & 0o022 and info.st_mode & 0o100, 'community_node_mode')
        must(hashlib.sha256(node.read_bytes()).hexdigest() == b['node_sha256'], 'community_node_digest')
        must(re.fullmatch(r'v(?:22|24)\.[0-9]+\.[0-9]+',b['node_version']), 'community_node_version')
        must(tuple(map(int,b['node_version'][1:].split('.'))) >= (22,13,0), 'community_node_minimum')
        observed = subprocess.run([str(node),'--version'],env={},capture_output=True,check=True,timeout=5).stdout.decode().strip()
        must(observed == b['node_version'], 'community_node_version_changed')
        env = self.storage.read_private_record(b['config_path'],expected_sha256=b['config_sha256'])
        must(set(env) == {'COMMUNITY_DATABASE_URL','COMMUNITY_CORE_ORIGIN','COMMUNITY_WEB_ORIGIN','COMMUNITY_PORT'}, 'community_config_keys')
        must(all(type(v) is str for v in env.values()), 'community_config_values')
        core, web, db = (urlsplit(env[k]) for k in ('COMMUNITY_CORE_ORIGIN','COMMUNITY_WEB_ORIGIN','COMMUNITY_DATABASE_URL'))
        must(core.scheme == 'http' and core.hostname == '127.0.0.1' and core.port and not core.username and
             core.path in ('','/') and not core.query and not core.fragment, 'community_core_origin')
        must(web.scheme in ('http','https') and web.hostname and not web.username and
             web.path in ('','/') and not web.query and not web.fragment, 'community_web_origin')
        must(db.scheme in ('postgres','postgresql') and db.hostname == '127.0.0.1' and db.port and
             db.username and db.password and db.path not in ('','/') and not db.query and not db.fragment, 'community_database_target')
        must(env['COMMUNITY_PORT'].isdigit() and 1024 <= int(env['COMMUNITY_PORT']) <= 65535 and
             int(env['COMMUNITY_PORT']) == b['port'] and b['port'] != core.port, 'community_port')
        return env

    def start(self, operation_id):
        env = self.verify()
        must(re.fullmatch('[a-f0-9]{32}',operation_id), 'community_operation_id')
        _, old = self.current()
        must(old is None or old['phase'] in ('STOPPED','EXITED'), 'community_existing_or_unknown_start')
        must(not listeners(self.binding['port']), 'community_port_busy')
        path = self.root/('run-'+operation_id+'.json')
        self.storage.validate_target(path, exists=False)
        record = dict(operation_id=operation_id,phase='START_INTENT',process=None,
                      binding_sha256=hashlib.sha256(json.dumps(self.binding,sort_keys=True).encode()).hexdigest())
        self.write(path,record); self.write(self.root/'current.json',{'operation_id':operation_id})
        log = self.root/('run-'+operation_id+'.log')
        fd = os.open(log,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:
            child = subprocess.Popen([self.binding['node'],str(Path(self.binding['release_root'])/'src/main.mjs')],
                                     cwd=self.binding['release_root'],env=env,stdin=subprocess.DEVNULL,
                                     stdout=fd,stderr=fd,close_fds=True,start_new_session=True)
        finally: os.close(fd)
        self.children[child.pid] = child
        # Crash here deliberately leaves START_INTENT. Reconciliation may not
        # invent ownership or start another process based on an empty pid field.
        identity = process(child.pid)
        if identity is None:
            if child.poll() is None: return 'UNKNOWN'
            record['phase']='EXITED'
        else:
            must(identity['exe'] == self.binding['node'] and identity['cwd'] == self.binding['release_root'] and
                 identity['argv'] == [self.binding['node'],str(Path(self.binding['release_root'])/'src/main.mjs')] and
                 identity['uid'] == [os.geteuid()]*4, 'community_spawn_identity_unknown')
            record.update(phase='STARTING',process=identity)
        self.write(path,record)
        return record['phase']

    def observe(self, check_health=True):
        self.require_lease()
        path, record = self.current()
        if record is None: return 'NOT_STARTED'
        if record['phase'] in ('STOPPED','EXITED'): return record['phase']
        if record['process'] is None: return 'UNKNOWN'
        saved = record['process']
        must(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == saved['boot_id'], 'community_boot_changed')
        stopping = record['phase'] in ('STOP_INTENT','STOPPING')
        if stopping and stopping_alive(saved): return record['phase']
        now = None if stopping else process(saved['pid'])
        if now is None:
            child=self.children.pop(saved['pid'],None)
            if child: child.wait(timeout=1)
            must(not listeners(self.binding['port']), 'community_listener_after_exit')
            record['phase']='STOPPED' if record['phase'] in ('STOP_INTENT','STOPPING') else 'EXITED'
            self.write(path,record); return record['phase']
        must(now == saved, 'community_process_identity_changed')
        if record['phase'] in ('STOP_INTENT','STOPPING'): return record['phase']
        inodes = listeners(self.binding['port'])
        if not inodes: return 'STARTING'
        owned = set()
        for fd in (Path('/proc')/str(saved['pid'])/'fd').iterdir():
            try: target=os.readlink(fd)
            except FileNotFoundError: continue
            if target.startswith('socket:['): owned.add(target[8:-1])
        must(inodes <= owned, 'community_listener_not_owned')
        if not check_health: return record['phase']
        connection=http.client.HTTPConnection('127.0.0.1',self.binding['port'],timeout=2)
        try:
            connection.request('GET','/health'); response=connection.getresponse()
            raw=response.read(4097)
            if response.status != 200 or len(raw)>4096: return 'STARTING'
            value=strict_json(raw)
            if value != {'status':'UP','contractVersion':1}: return 'STARTING'
        except (OSError, http.client.HTTPException):
            return 'STARTING'
        finally: connection.close()
        must(process(saved['pid']) == saved, 'community_process_changed_during_health')
        record['phase']='READY'; self.write(path,record); return 'READY'

    def stop(self, timeout=10):
        self.require_lease()
        must(type(timeout) in (int,float) and 0 <= timeout <= 30, 'community_stop_deadline')
        # An unhealthy HTTP endpoint must not prevent draining an independently
        # verified owned process. Shutdown relies on pid/port identity only.
        status = self.observe(check_health=False)
        if status in ('NOT_STARTED','STOPPED','EXITED'): return status
        must(status != 'UNKNOWN', 'community_unknown_process_no_signal')
        path, record = self.current(); saved = record['process']
        if status not in ('STOP_INTENT','STOPPING'):
            must(hasattr(os,'pidfd_open') and hasattr(signal,'pidfd_send_signal'), 'community_pidfd_required')
            fd=os.pidfd_open(saved['pid'])
            try:
                must(process(saved['pid']) == saved, 'community_process_changed_before_signal')
                record['phase']='STOP_INTENT'; self.write(path,record)
                signal.pidfd_send_signal(fd,signal.SIGTERM)
                record['phase']='STOPPING'; self.write(path,record)
                select.select([fd],[],[],timeout)
            finally: os.close(fd)
        # No second signal for an uncertain prior STOP_INTENT; no SIGKILL,
        # automatic restart or database/operation history restoration.
        return self.observe(check_health=False)
