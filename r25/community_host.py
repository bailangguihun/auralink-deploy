"""Finite Community-only host installation and retained-lease lifecycle.

No SQL, Core publication, VMM action, old control-file edit or automatic retry.
An approved plan supplies observed preimages; the original installed guard
verifies every lease. Normal Node HTTP handlers inherit no deployment FD.
"""
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
import types
import zipfile
from urllib.parse import urlsplit

from community_bundle import verify_archive
from community_lifecycle import Role, process
from r25_storage import R25Storage, must, strict_json

ROOT = Path('/root/autodl-tmp/artlive-r25-native-pg16/handoff-production-20260923T062101Z')
LOCK = Path('/root/autodl-tmp/auralink-web-deploy/runtime/frontend-deployment.lock')
WEB_ORIGIN = LOCK.parent/'community-origin.json'
SCHEMA = 'ARTLIVE_COMMUNITY_HOST_V1'
PARENT_SCHEMA = 'R25_MANAGED_VMM_CORE_PUBLICATION_BINDING_V1'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'))+'\n').encode()


def identity(path):
    """Reject links and concurrent replacement, including executable inputs."""
    path = Path(path)
    must(path.is_absolute() and str(path) == str(path.resolve(strict=True)), 'community_canonical_path')
    must(not any(p.is_symlink() for p in (path, *path.parents)), 'community_symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        must(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'community_regular_single_link')
        h = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1024*1024)
            if not chunk: break
            h.update(chunk)
        after = os.fstat(fd)
        keys=('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
        stable=lambda s:tuple(getattr(s,k) for k in keys)
        must(stable(before)==stable(after)==stable(path.stat()), 'community_file_changed')
        return dict(sha256=h.hexdigest(), uid=before.st_uid, gid=before.st_gid,
                    mode=stat.S_IMODE(before.st_mode), bytes=before.st_size)
    finally:
        os.close(fd)


def match(path, expected):
    must(identity(path) == expected, 'community_identity_drift:'+str(path))


class InstalledLease:
    """Calls the retained guard implementation; never implements another lock.

    The installer supplies the exact observed guard and inode in the approved
    plan. The production factory also exercises production() and its proof
    chain. Tests relocate only the original guard's paths/owners into fixtures.
    """
    def __init__(self, guard, lock, expected, descriptor=None):
        self.guard, self.lock, self.expected = guard, Path(lock), expected
        self.fd = descriptor

    def require(self):
        must(type(self.fd) is int and self.fd >= 3, 'community_lease_required')
        s = self.lock.lstat()
        must((s.st_dev, s.st_ino) == (self.expected['device'], self.expected['inode']), 'community_lock_replaced')
        self.guard.verify_lease(self.fd)

    @contextmanager
    def held(self):
        must(self.fd is None, 'community_nested_lease')
        self.fd = os.open(self.lock, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            self.require()
            yield self
        finally:
            os.close(self.fd)
            self.fd = None


def production_lease(binding):
    path = ROOT/'operations/core_release_guard.py'
    match(path, binding['guard'])
    must(binding['lock']['path'] == str(LOCK), 'community_original_lock_only')
    # The retained policy validator imports this exact module name again.
    # Do not satisfy it from ambient sys.path or an unverified module cache.
    must('core_release_guard' not in sys.modules, 'community_ambient_guard_module')
    module = types.ModuleType('core_release_guard')
    module.__file__ = str(path)
    sys.modules['core_release_guard'] = module
    try:
        exec(compile(path.read_bytes(), str(path), 'exec', dont_inherit=True), module.__dict__)
        guard = module.production()  # verifies the actual installed successors
    except BaseException:
        del sys.modules['core_release_guard']
        raise
    must(hasattr(guard, 'verify_lease') and guard.lock == LOCK, 'community_installed_lease_contract')
    return InstalledLease(guard, LOCK, binding['lock'])


class Host:
    """All writes are confined to a new private Community subtree.

    Recovery finishes only this interrupted file transaction. It never
    reinstates a prior ACTIVE, removes records, reruns SQL or refunds approval.
    """
    def __init__(self, root, plan, lease):
        self.root, self.plan, self.lease = Path(root), plan, lease
        self.owner = (os.geteuid(), os.getegid())
        self.storage = R25Storage([str(self.root)], *self.owner)
        self.digest = sha(encoded(plan))
        self.folder = self.root/'installations'/self.digest
        self.record = self.folder/'journal.json'
        self._role = None

    def write(self, path, value):
        old = self.storage.file_identity(path)['sha256'] if path.exists() else None
        return self.storage.atomic_write_json(path, value, replace=old is not None, expected_existing_sha256=old)

    def read(self, path):
        return self.storage.read_private_record(path)

    def validate(self, *, installing=True):
        p = self.plan
        must(set(p) == {'schema','parent','preserved','lease','owner','runtime','service','archive',
                        'settings','core_commit','expected_active_sha256','review_sha256','controller_manifest_sha256','web_origin'}, 'community_plan_fields')
        must(p['schema'] == SCHEMA and p['owner'] == list(self.owner), 'community_host_identity')
        must(re.fullmatch('[a-f0-9]{40}', p['core_commit']) is not None, 'community_compatible_core')
        must(re.fullmatch('[a-f0-9]{64}', p['review_sha256']) is not None, 'community_review')
        must(re.fullmatch('[a-f0-9]{64}', p['controller_manifest_sha256']) is not None, 'community_controller_binding')
        must(p['parent']['schema'] == PARENT_SCHEMA, 'community_unsupported_predecessor')
        must(p['runtime']['python']['path'] == str(Path(os.sys.executable).resolve()), 'community_python_path')
        match(p['runtime']['python']['path'], p['runtime']['python']['identity'])
        must(hasattr(os,'pidfd_open') and hasattr(signal,'pidfd_send_signal'), 'community_pidfd_required')
        self.lease.require()
        if installing:
            for path, expected in p['preserved'].items(): match(path, expected)
        if installing and self.root == ROOT/'runtime/community':
            must(self.owner == (0,0), 'community_current_host_owner')
            required = [ROOT/n for n in ('runtime/core-publications/CURRENT.json',
                'runtime/core-publications/POLICY.json','runtime/core-publications/CONTROL_UPGRADE_ACTIVE.json',
                'runtime/handoff-control.env','runtime/vmm-controller/CORE_PUBLICATION_ACTIVE.json',
                'runtime/vmm/lifecycle.json','runtime/vmm/gate.json')]
            must(all(str(path) in p['preserved'] for path in required), 'community_preservation_inventory')
            current = strict_json((ROOT/'runtime/core-publications/CURRENT.json').read_bytes())
            must(current['release']['commit_sha'] == p['core_commit'], 'community_compatible_core_not_installed')
            life = strict_json((ROOT/'runtime/vmm/lifecycle.json').read_bytes())
            gate = strict_json((ROOT/'runtime/vmm/gate.json').read_bytes())
            must(life['enabled'] is False and life['state']=='DISABLED' and life['desired']=='STOPPED' and
                 life['process'] is None and gate['mode']=='CLOSED' and gate['permit'] is None,
                 'community_install_requires_vmm_stopped')
        parent = p['parent']
        match(parent['approval_path'], parent['approval_identity'])
        approval = strict_json(Path(parent['approval_path']).read_bytes())
        must(approval['schema'] == PARENT_SCHEMA, 'community_parent_schema')
        match(parent['journal_path'], parent['journal_identity'])
        must(strict_json(Path(parent['journal_path']).read_bytes())['phase'] == 'INSTALLED', 'community_parent_incomplete')
        must(p['service']['port'] == 19086 or self.root != ROOT/'runtime/community', 'community_production_port')
        must(p['service']['node_heap_mib'] == 256 and p['service']['log_limit_bytes'] == 8*1024*1024,
             'community_resource_policy')
        must(p['service']['restart_budget'] == 0, 'community_no_automatic_restart')
        must(p['web_origin']['before_sha256'] is None, 'community_web_origin_initial_install_only')
        if self.root == ROOT/'runtime/community':must(p['web_origin']['path']==str(WEB_ORIGIN),'community_web_fixed_setting_path')

    def material(self, archive, manifest, settings, node):
        self.validate()
        p = self.plan
        match(archive, p['archive']['identity']); match(manifest, p['archive']['manifest_identity'])
        raw, mr = Path(archive).read_bytes(), Path(manifest).read_bytes()
        m = verify_archive(raw, mr, p['archive']['provenance'])
        match(settings, p['settings']); match(node, p['runtime']['node']['identity'])
        ni=p['runtime']['node']['identity']
        must(ni['mode']==0o700 and (ni['uid'],ni['gid'])==self.owner,'community_private_staged_node')
        must((p['settings']['uid'],p['settings']['gid'],p['settings']['mode'])==(*self.owner,0o600),'community_private_settings')
        version=p['runtime']['node']['version']
        must(re.fullmatch(r'v(?:22|24)\.[0-9]+\.[0-9]+',version) and
             tuple(map(int,version[1:].split('.'))) >= (22,13,0),'community_node_version')
        observed=subprocess.run([str(node),'--version'],env={},capture_output=True,check=True,timeout=5).stdout.decode().strip()
        must(observed==version,'community_node_version_changed')
        env=strict_json(Path(settings).read_bytes())
        must(set(env)=={'COMMUNITY_DATABASE_URL','COMMUNITY_CORE_ORIGIN','COMMUNITY_WEB_ORIGIN','COMMUNITY_PORT'} and
             all(type(v) is str for v in env.values()),'community_settings_scope')
        db,core,web=(urlsplit(env[k]) for k in ('COMMUNITY_DATABASE_URL','COMMUNITY_CORE_ORIGIN','COMMUNITY_WEB_ORIGIN'))
        must(db.scheme in ('postgres','postgresql') and db.hostname=='127.0.0.1' and db.port and db.username and db.password and
             db.path not in ('','/') and not db.query and not db.fragment,'community_database_target')
        must(core.scheme=='http' and core.hostname=='127.0.0.1' and core.port and not core.username and
             core.path in ('','/') and not core.query and not core.fragment,'community_core_origin')
        must(web.scheme in ('http','https') and web.hostname and not web.username and web.path in ('','/') and
             not web.query and not web.fragment,'community_web_origin')
        must(env['COMMUNITY_PORT']==str(p['service']['port']) and 1024<=p['service']['port']<=65535 and
             p['service']['port']!=core.port,'community_port')
        if self.root==ROOT/'runtime/community':
            must(db.username=='artlive_community_runtime' and db.path=='/artlive_community' and db.port==55640 and
                 env['COMMUNITY_CORE_ORIGIN']=='http://127.0.0.1:19080' and env['COMMUNITY_WEB_ORIGIN']=='https://artlive.top',
                 'community_production_targets')
        return raw, mr, m

    def binding(self):
        p = self.plan
        return dict(release_root=str(self.folder/'release'), manifest_sha256=p['archive']['manifest_identity']['sha256'],
                    provenance=p['archive']['provenance'], node=str(self.folder/'node'),
                    node_sha256=p['runtime']['node']['identity']['sha256'], node_version=p['runtime']['node']['version'],
                    config_path=str(self.folder/'settings.json'), config_sha256=p['settings']['sha256'],
                    port=p['service']['port'], node_heap_mib=p['service']['node_heap_mib'])

    def role(self):
        if self._role is None:
            self._role = Role(self.folder/'process', self.binding(), self.lease.require)
        return self._role

    def install(self, archive, manifest, settings, node, approval, *, recover=False):
        raw, mr, m = self.material(archive, manifest, settings, node)
        must(Path(approval).read_bytes() == encoded(self.plan), 'community_exact_plan_approval')
        a = identity(approval)
        must((a['uid'], a['gid'], a['mode']) == (*self.owner, 0o600), 'community_approval_private')
        self.storage.mkdir_private(self.root)
        self.storage.mkdir_private(self.root/'installations')
        active = self.root/'ACTIVE.json'
        expected = self.plan['expected_active_sha256']
        observed = self.storage.file_identity(active)['sha256'] if active.exists() else None
        new = dict(schema=SCHEMA, plan_sha256=self.digest)
        newsha = sha(encoded(new))
        if self.record.exists():
            record = self.read(self.record)
            must(record['plan_sha256'] == self.digest, 'community_transaction_identity')
            if record['phase'] == 'INSTALLED':
                must(observed == newsha, 'community_active_drift')
                self.role().verify()
                return 'INSTALLED_VERIFIED_NOOP'
            must(recover and record['phase'] == 'INSTALL_INTENT', 'community_recovery_required')
        else:
            must(not recover and observed == expected, 'community_active_cas')
            # Refuse orphaned transactions, including an interrupted journal creation.
            must(not any((self.root/'installations').iterdir()), 'community_existing_installation_requires_reconciliation')
            self.storage.mkdir_private(self.folder)
            self.storage.atomic_write_bytes(self.folder/'approval.json', encoded(self.plan))
            self.write(self.record, dict(schema=SCHEMA, phase='INSTALL_INTENT', plan_sha256=self.digest))
        must(observed in (expected, newsha), 'community_active_cas')
        # This finite installer admits the first Community installation only.
        must(expected is None, 'community_upgrade_requires_reviewed_successor')
        def put(path, data, mode=0o600):
            for parent in reversed(path.parent.parents):
                if parent == self.folder or self.folder in parent.parents: self.storage.mkdir_private(parent)
            self.storage.mkdir_private(path.parent)
            if path.exists():
                self.storage.file_identity(path, expected_sha256=sha(data), expected_mode=mode)
            else:
                # Archive files have a separate 32 MiB bound; JSON records keep
                # the retained storage library's original 1 MiB record limit.
                must(len(data)<=32*1024*1024,'community_release_file_limit')
                if len(data)<=1024*1024:self.storage.atomic_write_bytes(path,data,mode=mode)
                else:
                    def write_blob(fd):
                        self.storage._write(fd,data)
                        return dict(sha256=sha(data),bytes=len(data))
                    self.storage._atomic(path,write_blob,mode=mode)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for item in m['files']: put(self.folder/'release'/item['path'], z.read(item['path']))
        put(self.folder/'release/manifest.json', mr)
        put(self.folder/'settings.json', Path(settings).read_bytes())
        node_record=self.plan['runtime']['node']['identity']
        must(node_record['mode']==0o700 and (node_record['uid'],node_record['gid'])==self.owner,'community_private_staged_node')
        target=self.folder/'node'
        if target.exists():self.storage.file_identity(target,expected_sha256=node_record['sha256'],expected_mode=0o700)
        else:
            copy_storage=R25Storage([str(self.root),str(Path(node).parent)],*self.owner,private_roots=[str(self.root)])
            copy_storage.atomic_copy_file(node,target,node_record['sha256'],expected_bytes=node_record['bytes'],source_mode=0o700,mode=0o700)
        self.storage.mkdir_private(self.folder/'process')
        self.role().verify()
        if observed != newsha:
            self.storage.atomic_write_bytes(active, encoded(new), replace=expected is not None, expected_existing_sha256=expected)
        self.write(self.record, dict(schema=SCHEMA, phase='INSTALLED', plan_sha256=self.digest))
        return 'INSTALLED'

    def control(self, action, operation_id=None):
        self.validate(installing=False)
        must(self.read(self.record)['phase'] == 'INSTALLED' and
             self.read(self.root/'ACTIVE.json')['plan_sha256'] == self.digest, 'community_not_installed')
        role = self.role()
        if action == 'start':
            # No startup DDL and no package-time start. Explicit separate caller.
            return role.start(operation_id)
        if action == 'stop': return role.stop(0)
        if action == 'observe':
            state = role.observe()
            _, record = role.current()
            if record and state in ('READY','STARTING'):
                log = role.root/('run-'+record['operation_id']+'.log')
                if log.stat().st_size >= self.plan['service']['log_limit_bytes']:
                    # Rotate by ending this operation gracefully; never truncate
                    # an open FD, delete history or auto-start another process.
                    role.stop(0)
                    return 'LOG_LIMIT_DRAINING'
            return state
        raise ValueError('community_control_action')

    def bind_web(self):
        """Only one server setting after the actual owned backend is READY.

        A lost write receipt reconciles bytes; it never reruns service start or
        Web deployment. All legacy launcher/selection/policy files stay intact.
        """
        self.validate()
        must(self.control('observe')=='READY','community_backend_not_ready')
        path=Path(self.plan['web_origin']['path'])
        storage=R25Storage([str(path.parent)],*self.owner)
        raw=encoded({'COMMUNITY_INTERNAL_ORIGIN':'http://127.0.0.1:'+str(self.plan['service']['port'])})
        if path.exists():
            storage.file_identity(path,expected_sha256=sha(raw))
        else:storage.atomic_write_bytes(path,raw)
        self.write(self.folder/'web-origin-receipt.json',dict(path=str(path),sha256=sha(raw),plan_sha256=self.digest))
        return 'WEB_ORIGIN_BOUND_NOT_DEPLOYED'


def supervise(host, stop_requested, *, interval=2):
    """One foreground host owner, invoked by the approved operator session.

    No new detached watchdog and no automatic service restart. Disconnect or
    SIGTERM requests drain. A caller must keep this runner alive until STOPPED;
    an unknown process is reported for reconciliation, never signalled blindly.
    """
    while True:
        try:
            with host.lease.held():
                state = host.control('stop' if stop_requested() else 'observe')
        except RuntimeError as error:
            if str(error) != 'shared_publication_busy': raise
            # Contention is waiting for the original lease, not resubmission.
            time.sleep(interval)
            continue
        if state in ('NOT_STARTED','STOPPED','EXITED','UNKNOWN'): return state
        time.sleep(interval)
