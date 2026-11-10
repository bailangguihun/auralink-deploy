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
SUCCESSOR_SCHEMA = 'R25_MANAGED_VMM_CORE_PUBLICATION_SUCCESSOR_BINDING_V1'
REPAIR_SCHEMA = 'R25_MANAGED_VMM_CORE_PUBLICATION_REPAIR_BINDING_V1'


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
        self.inherited = descriptor is not None

    def require(self):
        must(type(self.fd) is int and self.fd >= 3, 'community_lease_required')
        s = self.lock.lstat()
        must((s.st_dev, s.st_ino) == (self.expected['device'], self.expected['inode']), 'community_lock_replaced')
        self.guard.verify_lease(self.fd)

    @contextmanager
    def held(self):
        if self.inherited:
            # Inherited original-watchdog descriptor, verified by the retained
            # guard; never open a second descriptor or unlock the parent's lease.
            self.require()
            yield self
            return
        must(self.fd is None, 'community_nested_lease')
        self.fd = os.open(self.lock, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            self.require()
            yield self
        finally:
            os.close(self.fd)
            self.fd = None


def knowledge_lease_binding(plan, plan_sha, manifest_sha, approval_sha):
    """One installed Core binding can inherit this original Community lease.

    The immutable Host approval, registration, service and restart state are
    not replaced. Full Core proof verification still runs in production_lease.
    """
    must(re.fullmatch('[a-f0-9]{64}', str(approval_sha)) is not None,'community_knowledge_digest')
    def private(path, expected=None):
        item=identity(path)
        must((item['uid'],item['gid'],item['mode'])==(0,0,0o600),'community_knowledge_private')
        if expected:must(item['sha256']==expected,'community_knowledge_file_digest')
        raw=Path(path).read_bytes();must(sha(raw)==item['sha256'],'community_knowledge_file_changed')
        return strict_json(raw)
    marker=private(ROOT/'runtime/vmm-controller/KNOWLEDGE_PUBLICATION_ACTIVE.json')
    must(marker=={'schema':'R25_KNOWLEDGE_PUBLICATION_ACTIVE_V1','approval_sha256':approval_sha},
         'community_knowledge_pointer')
    folder=ROOT/'runtime/core-publications/controller-upgrades'/approval_sha
    approval=private(folder/'approval.json',approval_sha);journal=private(folder/'journal.json')
    must(approval['schema']=='R25_KNOWLEDGE_CORE_PUBLICATION_BINDING_V1' and
         journal['approval_sha256']==approval_sha and journal['phase'] in ('VERIFY_INTENT','INSTALLED'),
         'community_knowledge_not_installed')
    bound=approval['vmm']['community']
    must(sha(encoded(plan))==plan_sha==bound['plan_sha256'] and plan['lease']==bound['lease'] and
         manifest_sha==bound['deploy']['manifest_sha256'],'community_knowledge_original_plan')
    private(ROOT/'runtime/community/WATCHDOG.json',bound['registration_sha256'])
    private(ROOT/'runtime/community/ACTIVE.json',bound['active_sha256'])
    before=approval['operational']['guard']['before_sha256'];after=approval['operational']['guard']['after_sha256']
    must(plan['lease']['guard']['sha256']==before,'community_knowledge_guard_predecessor')
    match(folder/'before/guard',dict(plan['lease']['guard'],uid=0,gid=0,mode=0o600))
    path=ROOT/'operations/core_release_guard.py';observed=identity(path)
    must(observed['sha256']==after and
         (observed['uid'],observed['gid'],observed['mode'])==tuple(approval['controller_owner'])+(0o700,),
         'community_knowledge_guard_successor')
    return dict(plan['lease'],guard=observed)


def recovery_lease_binding(plan, plan_sha, manifest_sha, knowledge_sha, upgrade_sha):
    """Retain the old Host approval and lease through one exact installed successor."""
    def private(path, expected=None):
        item=identity(path)
        must((item['uid'],item['gid'],item['mode'])==(0,0,0o600),'community_recovery_private')
        if expected:must(item['sha256']==expected,'community_recovery_digest')
        raw=Path(path).read_bytes();must(sha(raw)==item['sha256'],'community_recovery_changed')
        return strict_json(raw)
    must(re.fullmatch('[a-f0-9]{64}',str(upgrade_sha)) is not None,'community_recovery_upgrade_digest')
    marker=private(ROOT/'runtime/vmm-controller/COMMUNITY_SUPERVISION_ACTIVE.json')
    must(marker=={'schema':'R25_COMMUNITY_SUPERVISION_UPGRADE_V1','approval_sha256':upgrade_sha},
         'community_recovery_pointer')
    folder=ROOT/'runtime/vmm-controller/community-supervision'/upgrade_sha
    approved=private(folder/'approval.json',upgrade_sha)
    must(approved['schema']=='R25_COMMUNITY_SUPERVISION_UPGRADE_V1'
         and private(folder/'journal.json')==dict(marker,phase='INSTALLED')
         and approved['knowledge_approval_sha256']==knowledge_sha
         and approved['legacy']['plan_sha256']==plan_sha==sha(encoded(plan))
         and approved['community_deploy']['manifest_sha256']==manifest_sha,
         'community_recovery_binding')
    private(ROOT/'runtime/community/installations'/plan_sha/'watchdog-state.json',approved['legacy']['state_sha256'])
    parent=private(ROOT/'runtime/core-publications/controller-upgrades'/knowledge_sha/'approval.json',knowledge_sha)
    binding=knowledge_lease_binding(plan,plan_sha,parent['vmm']['community']['deploy']['manifest_sha256'],knowledge_sha)
    return binding,approved['legacy']['state_sha256']



def studio_lease_binding(plan, plan_sha, manifest_sha, knowledge_sha, upgrade_sha, studio_sha):
    """An exact installed finite successor selects the new guard and CLI only."""
    def private(path, expected=None):
        item=identity(path)
        must((item['uid'],item['gid'],item['mode'])==(0,0,0o600),'community_studio_private')
        if expected:must(item['sha256']==expected,'community_studio_digest')
        raw=Path(path).read_bytes();must(sha(raw)==item['sha256'],'community_studio_changed')
        return strict_json(raw)
    must(re.fullmatch('[a-f0-9]{64}',str(studio_sha)) is not None,'community_studio_digest')
    marker=private(ROOT/'runtime/vmm-controller/STUDIO_PUBLICATION_ACTIVE.json')
    must(marker=={'schema':'R25_STUDIO_PUBLICATION_ACTIVE_V1','approval_sha256':studio_sha},'community_studio_pointer')
    folder=ROOT/'runtime/core-publications/controller-upgrades'/studio_sha
    approved=private(folder/'approval.json',studio_sha);receipt=private(folder/'journal.json')
    bound=approved['vmm']['community']
    must(approved['schema']=='R25_STUDIO_CORE_PUBLICATION_BINDING_V1' and receipt['approval_sha256']==studio_sha
         and receipt['phase'] in ('VERIFY_INTENT','INSTALLED') and
         approved['vmm']['supervision']['marker']=={'schema':'R25_COMMUNITY_SUPERVISION_UPGRADE_V1','approval_sha256':upgrade_sha}
         and manifest_sha==bound['deploy']['manifest_sha256'] and plan_sha==bound['plan_sha256']==sha(encoded(plan)),
         'community_studio_binding')
    private(ROOT/'runtime/community/WATCHDOG.json',bound['registration_sha256'])
    parent_folder=ROOT/'runtime/vmm-controller/community-supervision'/upgrade_sha
    parent=private(parent_folder/'approval.json',upgrade_sha)
    must(parent['knowledge_approval_sha256']==knowledge_sha and parent['legacy']['plan_sha256']==plan_sha,
         'community_studio_predecessor')
    private(parent_folder/'journal.json',approved['vmm']['supervision']['journal_sha256'])
    private(ROOT/'runtime/community/installations'/plan_sha/'watchdog-state.json',parent['legacy']['state_sha256'])
    path=ROOT/'operations/core_release_guard.py';observed=identity(path)
    must(observed['sha256']==approved['operational']['guard']['after_sha256'] and
         (observed['uid'],observed['gid'],observed['mode'])==tuple(approved['controller_owner'])+(0o700,),
         'community_studio_guard')
    # production_lease next verifies the full historical chain and actual CURRENT.
    return dict(plan['lease'],guard=observed),parent['legacy']['state_sha256']


def production_lease(binding, descriptor=None):
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
        raw=path.read_bytes();must(sha(raw)==binding['guard']['sha256'],'community_guard_changed')
        exec(compile(raw, str(path), 'exec', dont_inherit=True), module.__dict__)
        guard = module.production()  # verifies the actual installed successors
    except BaseException:
        del sys.modules['core_release_guard']
        raise
    must(hasattr(guard, 'verify_lease') and guard.lock == LOCK, 'community_installed_lease_contract')
    return InstalledLease(guard, LOCK, binding['lock'], descriptor)


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
        must(p['parent']['schema'] in (PARENT_SCHEMA,SUCCESSOR_SCHEMA,REPAIR_SCHEMA), 'community_unsupported_predecessor')
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
            parent_id=p['parent']['approval_identity']['sha256']
            parent_root=ROOT/'runtime/core-publications/controller-upgrades'/parent_id
            marker=ROOT/'runtime/vmm-controller'/('CORE_PUBLICATION_REPAIR_ACTIVE.json' if p['parent']['schema']==REPAIR_SCHEMA else 'CORE_PUBLICATION_SUCCESSOR_ACTIVE.json' if p['parent']['schema']==SUCCESSOR_SCHEMA else 'CORE_PUBLICATION_ACTIVE.json')
            must(p['parent']['approval_path']==str(parent_root/'approval.json') and
                 p['parent']['journal_path']==str(parent_root/'journal.json') and
                 strict_json(marker.read_bytes())['approval_sha256']==parent_id and
                 strict_json((ROOT/'runtime/core-publications/CONTROL_UPGRADE_ACTIVE.json').read_bytes())['approval_sha256']==parent_id,
                 'community_current_publication_parent')
            required.append(marker)
            if p['parent']['schema']==REPAIR_SCHEMA:
                required.append(ROOT/'runtime/vmm-controller/CORE_PUBLICATION_SUCCESSOR_ACTIVE.json')
            must(all(str(path) in p['preserved'] for path in required), 'community_preservation_inventory')
            current = strict_json((ROOT/'runtime/core-publications/CURRENT.json').read_bytes())
            must(current['release']['commit_sha'] == p['core_commit'], 'community_compatible_core_not_installed')
            self.require_committed_core(current)
            life = strict_json((ROOT/'runtime/vmm/lifecycle.json').read_bytes())
            gate = strict_json((ROOT/'runtime/vmm/gate.json').read_bytes())
            must(life['enabled'] is False and life['state']=='DISABLED' and life['desired']=='STOPPED' and
                 life['process'] is None and gate['mode']=='CLOSED' and gate['permit'] is None,
                 'community_install_requires_vmm_stopped')
        parent = p['parent']
        match(parent['approval_path'], parent['approval_identity'])
        approval = strict_json(Path(parent['approval_path']).read_bytes())
        must(approval['schema'] == parent['schema'], 'community_parent_schema')
        match(parent['journal_path'], parent['journal_identity'])
        must(strict_json(Path(parent['journal_path']).read_bytes())['phase'] == 'INSTALLED', 'community_parent_incomplete')
        must(p['service']['port'] == 19086 or self.root != ROOT/'runtime/community', 'community_production_port')
        must(p['service']['node_heap_mib'] == 256 and p['service']['log_limit_bytes'] == 8*1024*1024,
             'community_resource_policy')
        must(type(p['service']['restart_budget']) is int and p['service']['restart_budget'] in (0,2), 'community_restart_budget')
        if p['service']['restart_budget']:
            must(parent['schema'] in (SUCCESSOR_SCHEMA,REPAIR_SCHEMA),'community_watchdog_requires_successor')
        must(p['web_origin']['before_sha256'] is None, 'community_web_origin_initial_install_only')
        if self.root == ROOT/'runtime/community':must(p['web_origin']['path']==str(WEB_ORIGIN),'community_web_fixed_setting_path')

    def require_committed_core(self,current):
        p=self.plan
        current_tx=current.get('transaction_id');must(re.fullmatch('[a-f0-9]{32}',str(current_tx)) is not None,'community_core_transaction_identity')
        current_journal=ROOT/'runtime/core-publications/transactions'/current_tx/'journal.json'
        must(str(current_journal) in p['preserved'],'community_core_journal_binding')
        committed=strict_json(current_journal.read_bytes())
        must(committed.get('phase')=='COMMITTED' and committed.get('candidate')==current['release'],'community_core_not_committed')

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

    def preserve_running(self, supervisor=None):
        """Read-only maintenance proof; never drain, restart, or spend a retry."""
        from community_supervision import Supervisor
        supervisor=supervisor or Supervisor(self);state=supervisor.registered();role=self.role();role.verify()
        state_identity=identity(supervisor.state);state_raw=supervisor.state.read_bytes()
        must(sha(state_raw)==state_identity['sha256'] and strict_json(state_raw)==state,'community_preserve_state_changed')
        must(state['desired']=='RUNNING','community_preserve_not_running')
        path,record=role.current()
        must(record is not None and record['phase']=='READY' and
             state['attempts'][-1]==record['operation_id'],'community_preserve_not_ready')
        saved=record['process']
        must(process(saved['pid'])==saved,'community_preserve_process_drift')
        from community_lifecycle import listeners
        sockets=listeners(self.plan['service']['port']);owned=set()
        for descriptor in (Path('/proc')/str(saved['pid'])/'fd').iterdir():
            try:target=os.readlink(descriptor)
            except FileNotFoundError:continue
            if target.startswith('socket:['):owned.add(target[8:-1])
        must(bool(sockets) and sockets<=owned,'community_preserve_listener_not_owned')
        log=role.root/('run-'+record['operation_id']+'.log')
        logfile=identity(log)
        must((logfile['uid'],logfile['gid'],logfile['mode'])==(*self.owner,0o600) and
             logfile['bytes']<self.plan['service']['log_limit_bytes'],
             'community_preserve_log_limit')
        import http.client
        connection=http.client.HTTPConnection('127.0.0.1',self.plan['service']['port'],timeout=2)
        try:
            connection.request('GET','/health');response=connection.getresponse();raw=response.read(4097)
            must(response.status==200 and len(raw)<=4096 and
                 strict_json(raw)=={'status':'UP','contractVersion':1},'community_preserve_health')
        finally:connection.close()
        must(process(saved['pid'])==saved,'community_preserve_process_changed')
        must(identity(supervisor.state)==state_identity,'community_preserve_state_changed')
        return {'state':'READY_PRESERVED','process':saved,'watchdog_state_sha256':state_identity['sha256'],
                'process_record_sha256':identity(path)['sha256']}

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


PACKAGE_SCHEMA = 'ARTLIVE_COMMUNITY_PACKAGE_UPGRADE_V1'


def package_context(host, studio_sha):
    """Actual committed Core identity, checked under the retained production lease."""
    host.lease.require()
    must(re.fullmatch('[a-f0-9]{64}',str(studio_sha)) is not None,'community_package_studio_digest')
    def read(path, expected=None):
        i=identity(path)
        must((i['uid'],i['gid'],i['mode'])==(*host.owner,0o600),'community_package_context_private')
        raw=path.read_bytes()
        must(sha(raw)==i['sha256'] and (expected is None or sha(raw)==expected),'community_package_context_drift')
        return strict_json(raw),i['sha256']
    base=ROOT/'runtime/core-publications';folder=base/'controller-upgrades'/studio_sha
    approved,_=read(folder/'approval.json',studio_sha);installed,_=read(folder/'journal.json')
    must(approved['schema']==installed['schema']=='R25_STUDIO_CORE_PUBLICATION_BINDING_V1' and
         installed['approval_sha256']==studio_sha and installed['phase']=='INSTALLED',
         'community_package_studio_not_installed')
    policy,_=read(base/'POLICY.json',approved['operational']['policy']['after_sha256'])
    current,current_sha=read(base/'CURRENT.json');candidate=policy['upgrade']['candidate'];release=current['release']
    must(all(release[k]==candidate[k] for k in ('commit_sha','artifact_sha256')),'community_package_new_core_required')
    tx=current['transaction_id'];must(re.fullmatch('[a-f0-9]{32}',str(tx)) is not None,'community_package_core_transaction')
    receipt,receipt_sha=read(base/'transactions'/tx/'journal.json')
    must(receipt['phase']=='COMMITTED' and receipt['candidate']==release and receipt['transaction_id']==tx,
         'community_package_core_not_committed')
    return dict(studio_approval_sha256=studio_sha,core_commit=release['commit_sha'],
                core_current_sha256=current_sha,core_transaction_sha256=receipt_sha)


class ReleaseHost(Host):
    """Use a separately verified immutable package; retain the original Host plan."""
    def __init__(self, original, release):
        super().__init__(original.root, original.plan, original.lease)
        self.release = release

    def binding(self):
        return dict(super().binding(),release_root=str(self.release/'release'),
                    manifest_sha256=self.package_plan['manifest']['sha256'],
                    provenance=self.package_plan['provenance'])

    def role(self):
        if self._role is None:
            self._role=Role(self.release/'process',self.binding(),self.lease.require)
        return self._role


class PackageUpgrade:
    """One package replacement after Studio Core, under the original lease.

    No SQL, config edits, process adoption, missing-receipt retry, or data restore.
    The original watchdog shares the lease. Durable STOPPED/activation records
    prevent it replaying an interrupted package operation after lease release.
    """
    def __init__(self, host, supervisor, context):
        self.host,self.supervisor,self.context=host,supervisor,context
        self.root=host.folder/'package-upgrades'
        self.marker=host.folder/'RELEASE_ACTIVE.json'
        self.candidates={}

    def prepare(self, archive, manifest, provenance, review_sha256):
        h=self.host;h.lease.require();h.validate(installing=False)
        must(not self.marker.exists() and not self.marker.is_symlink(),'community_package_already_upgraded')
        must(provenance.get('commit')==self.context['core_commit'],'community_package_core_release')
        must(re.fullmatch('[a-f0-9]{64}',review_sha256) is not None,'community_package_review')
        for p in (archive,manifest):
            i=identity(p);must((i['uid'],i['gid'],i['mode'])==(*h.owner,0o600),'community_package_private')
        raw=Path(archive).read_bytes();mr=Path(manifest).read_bytes()
        verify_archive(raw,mr,provenance)
        before=h.preserve_running(self.supervisor)
        plan=dict(schema=PACKAGE_SCHEMA,context=self.context,original_plan_sha256=h.digest,
                  archive=identity(archive),manifest=identity(manifest),provenance=provenance,
                  review_sha256=review_sha256,before=before,
                  binding_sha256=sha(encoded(h.binding())))
        ident=sha(encoded(plan));folder=self.root/ident
        h.storage.mkdir_private(self.root);h.storage.mkdir_private(folder)
        path=folder/'UNSIGNED_PLAN.json'
        if path.exists():must(path.read_bytes()==encoded(plan),'community_package_plan_conflict')
        else:h.storage.atomic_write_bytes(path,encoded(plan))
        return plan,path

    def verify(self, plan):
        h=self.host;h.lease.require();h.validate(installing=False)
        must(set(plan)=={'schema','context','original_plan_sha256','archive','manifest','provenance',
                         'review_sha256','before','binding_sha256'} and plan['schema']==PACKAGE_SCHEMA,
             'community_package_plan')
        must(plan['context']==self.context and plan['original_plan_sha256']==h.digest and
             plan['binding_sha256']==sha(encoded(h.binding())),'community_package_context')
        must(plan['provenance']['commit']==self.context['core_commit'],'community_package_core_release')
        ident=sha(encoded(plan));folder=self.root/ident
        approval=folder/'approval.json';match(approval,dict(identity(approval),sha256=ident))
        must((identity(approval)['uid'],identity(approval)['gid'],identity(approval)['mode'])==(*h.owner,0o600),
             'community_package_approval_private')
        must(approval.read_bytes()==encoded(plan),'community_package_exact_approval')
        return ident,folder

    def candidate(self,plan):
        release=self.host.folder/'package-releases'/plan['manifest']['sha256']
        if release not in self.candidates:
            host=ReleaseHost(self.host,release);host.package_plan=plan;self.candidates[release]=host
        return self.candidates[release]

    def materialize(self,plan,archive,manifest):
        h=self.host;match(archive,plan['archive']);match(manifest,plan['manifest'])
        raw=Path(archive).read_bytes();mr=Path(manifest).read_bytes()
        must(sha(raw)==plan['archive']['sha256'] and sha(mr)==plan['manifest']['sha256'],'community_package_material_changed')
        m=verify_archive(raw,mr,plan['provenance']);candidate=self.candidate(plan)
        def put(path,data):
            for parent in reversed([path.parent,*path.parent.parents]):
                if parent==h.folder or h.folder in parent.parents:h.storage.mkdir_private(parent)
            if path.exists():h.storage.file_identity(path,expected_sha256=sha(data),expected_bytes=len(data))
            else:
                must(len(data)<=32*1024*1024,'community_package_file_limit')
                def write(fd):h.storage._write(fd,data);return dict(sha256=sha(data),bytes=len(data))
                h.storage._atomic(path,write)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for item in m['files']:put(candidate.release/'release'/item['path'],z.read(item['path']))
        put(candidate.release/'release/manifest.json',mr)
        h.storage.mkdir_private(candidate.release/'process');candidate.role().verify()
        return candidate

    def install(self,plan,archive,manifest,*,recover=False,fault=None):
        h=self.host;ident,folder=self.verify(plan);journal=folder/'journal.json'
        record=h.read(journal) if journal.exists() else None
        if record:
            must(record.get('schema')==PACKAGE_SCHEMA and record.get('plan_sha256')==ident,'community_package_journal')
            if record['phase']=='COMMITTED':
                active=self.active();active[0].preserve_running(active[1]);return 'COMMITTED_VERIFIED_NOOP'
            must(recover and record['phase'] not in ('RESTORED',),'community_package_explicit_recovery_required')
        else:
            must(not recover,'community_package_not_started')
            must(h.preserve_running(self.supervisor)==plan['before'],'community_package_preimage_drift')
            candidate=self.materialize(plan,archive,manifest)
            import uuid
            record=dict(schema=PACKAGE_SCHEMA,plan_sha256=ident,phase='PREPARED',operation_id=uuid.uuid4().hex,
                        resume_operation_id=uuid.uuid4().hex,history=['PREPARED'])
            h.write(journal,record)
        def phase(value):
            record['phase']=value;record['history'].append(value);h.write(journal,record)
            if fault:fault(value)
        candidate=self.candidate(plan)
        if recover:
            return self.recover(plan,record,phase,candidate)
        # The only normal start decision is persisted before Role.start().
        phase('DRAIN_INTENT')
        self.supervisor.tick(drain=True)
        self.drain(h.role())
        from community_lifecycle import listeners
        must(not listeners(h.plan['service']['port']),'community_package_foreign_listener')
        phase('DRAINED')
        legacy=identity(self.supervisor.state)['sha256']
        record['drained_state_sha256']=legacy
        from community_supervision import PackageSupervisor
        supervisor=PackageSupervisor(candidate,ident,legacy,self.supervisor.state)
        state=dict(schema='ARTLIVE_COMMUNITY_SUPERVISION_V2',plan_sha256=h.digest,upgrade_sha256=ident,
                   legacy_state_sha256=legacy,desired='RUNNING',attempts=[record['operation_id']],budget_start=0,
                   unhealthy=None,stable=None,reason='PACKAGE_START_DECISION',resumptions=[])
        h.write(supervisor.state,state);phase('START_INTENT')
        candidate.role().start(record['operation_id'])
        phase('STARTED')
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            status=candidate.control('observe')
            if status=='READY':break
            must(status=='STARTING','community_package_start_failed')
            time.sleep(.05)
        must(status=='READY','community_package_health_timeout')
        candidate.preserve_running(supervisor)
        phase('ACTIVATE_INTENT')
        h.storage.atomic_write_json(self.marker,dict(schema=PACKAGE_SCHEMA,plan_sha256=ident))
        phase('COMMITTED')
        return 'COMMITTED'

    def recover(self,plan,record,phase,candidate):
        """Reconcile the original attempt; never replay an uncertain spawn."""
        h=self.host;ident=sha(encoded(plan))
        if self.marker.exists():
            must(h.read(self.marker)==dict(schema=PACKAGE_SCHEMA,plan_sha256=ident),'community_package_foreign_active')
            from community_supervision import PackageSupervisor
            supervisor=PackageSupervisor(candidate,ident,record['drained_state_sha256'],self.supervisor.state)
            candidate.preserve_running(supervisor)
            phase('COMMITTED');return 'COMMITTED_RECONCILED'
        phase_before=record['phase']
        if phase_before=='RESTORE_INTENT':
            return self.resume_original(record,phase)
        if phase_before=='PREPARED':
            must(h.preserve_running(self.supervisor)==plan['before'],'community_package_preimage_drift')
            phase('RESTORED');return 'RESTORED_NOT_STARTED'
        if phase_before in ('DRAIN_INTENT','DRAINED'):
            # DRAIN_INTENT might have signalled already: observe owned exit only.
            self.drain(h.role())
        else:
            path,current=candidate.role().current()
            must(current is not None,'community_package_unknown_start_receipt')
            must(current['operation_id']==record['operation_id'],'community_package_operation_drift')
            self.drain(candidate.role())
        from community_lifecycle import listeners
        must(not listeners(h.plan['service']['port']),'community_package_foreign_listener')
        phase('RESTORE_INTENT')
        return self.resume_original(record,phase)

    def drain(self,role):
        status=role.stop(0);deadline=time.monotonic()+15
        while status not in ('STOPPED','EXITED') and time.monotonic()<deadline:
            must(status in ('STOP_INTENT','STOPPING'),'community_package_drain_unknown')
            time.sleep(.05);status=role.observe(check_health=False)
        must(status in ('STOPPED','EXITED'),'community_package_drain_pending')

    def resume_original(self,record,phase):
        h=self.host
        self.supervisor.enable(record['resume_operation_id'])
        self.supervisor.tick()
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            status=self.supervisor.tick()
            if status=='READY':break
            must(status=='STARTING','community_package_restore_failed')
            time.sleep(.05)
        must(status=='READY','community_package_restore_timeout')
        h.preserve_running(self.supervisor);phase('RESTORED')
        return 'RESTORED'

    def active(self):
        h=self.host;h.lease.require()
        marker=h.read(self.marker);ident=marker.get('plan_sha256')
        must(marker==dict(schema=PACKAGE_SCHEMA,plan_sha256=ident) and
             re.fullmatch('[a-f0-9]{64}',str(ident)) is not None,'community_package_active')
        folder=self.root/ident;plan=h.read(folder/'approval.json')
        self.verify(plan);record=h.read(folder/'journal.json')
        must(record['plan_sha256']==ident and record['phase']=='COMMITTED','community_package_not_committed')
        from community_supervision import PackageSupervisor
        candidate=self.candidate(plan)
        return candidate,PackageSupervisor(candidate,ident,record['drained_state_sha256'],self.supervisor.state)
