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


def reference_lease_binding(plan, plan_sha, manifest_sha, knowledge_sha, upgrade_sha, studio_sha, reference_sha):
    """One exact Reference successor; the original guard verifies its full chain."""
    def private(path, expected=None):
        item=identity(path)
        must((item['uid'],item['gid'],item['mode']) == (0,0,0o600),'community_reference_private')
        raw=Path(path).read_bytes()
        must(sha(raw) == item['sha256'] and (expected is None or sha(raw) == expected),
             'community_reference_digest')
        return strict_json(raw)
    must(re.fullmatch('[a-f0-9]{64}',str(reference_sha)) is not None,'community_reference_digest')
    marker=private(ROOT/'runtime/vmm-controller/REFERENCE_PUBLICATION_ACTIVE.json')
    must(marker == {'schema':'R25_REFERENCE_PUBLICATION_ACTIVE_V1','approval_sha256':reference_sha},
         'community_reference_pointer')
    folder=ROOT/'runtime/core-publications/controller-upgrades'/reference_sha
    approved=private(folder/'approval.json',reference_sha)
    receipt=private(folder/'journal.json')
    must(approved['schema'] == receipt['schema'] == 'R25_REFERENCE_CORE_PUBLICATION_BINDING_V1'
         and receipt['approval_sha256'] == reference_sha and receipt['phase'] in ('VERIFY_INTENT','INSTALLED'),
         'community_reference_not_installed')
    must(approved['vmm']['studio']['marker'] ==
         {'schema':'R25_STUDIO_PUBLICATION_ACTIVE_V1','approval_sha256':studio_sha},
         'community_reference_studio_parent')
    parent=private(ROOT/'runtime/core-publications/controller-upgrades'/studio_sha/'approval.json',studio_sha)
    must(parent['vmm']['supervision']['marker'] ==
         {'schema':'R25_COMMUNITY_SUPERVISION_UPGRADE_V1','approval_sha256':upgrade_sha},
         'community_reference_supervision_parent')
    recovery=private(ROOT/'runtime/vmm-controller/community-supervision'/upgrade_sha/'approval.json',upgrade_sha)
    must(recovery['knowledge_approval_sha256'] == knowledge_sha,'community_reference_knowledge_parent')
    bound=approved['vmm']['community']
    must(bound['plan_sha256'] == plan_sha == sha(encoded(plan)) and
         bound['deploy']['manifest_sha256'] == manifest_sha,'community_reference_original_plan')
    private(ROOT/'runtime/community/WATCHDOG.json',bound['registration_sha256'])
    observed=identity(ROOT/'operations/core_release_guard.py')
    must(observed['sha256'] == approved['operational']['guard']['after_sha256'] and
         (observed['uid'],observed['gid'],observed['mode']) == tuple(approved['controller_owner'])+(0o700,),
         'community_reference_guard')
    return dict(plan['lease'],guard=observed),recovery['legacy']['state_sha256']


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


def reference_core_record(host, path, expected=None):
    """Read retained Core proofs without widening Community write roots."""
    storage=R25Storage([str(ROOT/'runtime/core-publications')],*host.owner)
    return storage.read_private_record(path,expected_sha256=expected)


def reference_package_context(host, reference_sha):
    """Keep the old package's immutable context across the Core-only switch."""
    host.lease.require()
    base=ROOT/'runtime/core-publications'
    folder=base/'controller-upgrades'/reference_sha
    approved=reference_core_record(host,folder/'approval.json',reference_sha)
    must(sha(encoded(approved)) == reference_sha and
         approved['schema'] == 'R25_REFERENCE_CORE_PUBLICATION_BINDING_V1',
         'community_reference_context_approval')
    receipt=reference_core_record(host,folder/'journal.json')
    must(receipt['approval_sha256'] == reference_sha and
         receipt['phase'] in ('VERIFY_INTENT','INSTALLED'),'community_reference_context_not_installed')
    bound=approved['vmm']['community'];previous=bound['package']
    must(bound['plan_sha256'] == host.digest,'community_reference_context_plan')
    match(host.folder/'RELEASE_ACTIVE.json',dict(identity(host.folder/'RELEASE_ACTIVE.json'),
                                               sha256=previous['marker_sha256']))
    oldfolder=host.folder/'package-upgrades'/previous['plan_sha256']
    old=host.read(oldfolder/'approval.json')
    must(sha(encoded(old)) == previous['plan_sha256'],'community_reference_package_approval')
    match(oldfolder/'journal.json',dict(identity(oldfolder/'journal.json'),
                                       sha256=previous['journal_sha256']))
    must(host.read(oldfolder/'journal.json')['phase'] == 'COMMITTED',
         'community_reference_package_not_committed')
    current=reference_core_record(host,base/'CURRENT.json')
    policy=reference_core_record(host,base/'POLICY.json')
    must(sha(encoded(policy)) == approved['operational']['policy']['after_sha256'],
         'community_reference_context_policy')
    candidate=policy['upgrade']['candidate']
    must(current['release'] == approved['current_release'] or
         all(current['release'].get(k) == candidate[k] for k in ('commit_sha','artifact_sha256')),
         'community_reference_context_current')
    return {'reference_sha256':reference_sha,'core_commit':candidate['commit_sha'],
            'candidate':candidate,'predecessor':previous,'previous_context':old['context']}


class ReferenceDatabase:
    """The existing native backup principal, confined to reviewed forward SQL.

    A private plan binds the same native profile, account/groups, credential and
    binaries that the original Community installer used. No credential bytes
    enter the plan or journal. Business processes inherit no deployment FD.
    """
    def __init__(self, host, plan):
        self.host,self.plan=host,plan
        must(set(plan) == {'schema','versions','operation_id','postgres','snapshot'} and
             plan['schema'] == 'ARTLIVE_COMMUNITY_REFERENCE_DATABASE_V1',
             'community_reference_database_plan')
        must(type(plan['versions']) is list and all(type(v) is int for v in plan['versions']) and
             plan['versions'] in ([1],[1,2]) and
             re.fullmatch('[a-f0-9]{32}',str(plan['operation_id'])) is not None,
             'community_reference_database_predecessor')
        p=plan['postgres'];self.pg=p
        must(set(p) == {'binary_dir','binaries','host','port','user','passfile','credential',
                        'library_dir','library_identity'},
             'community_reference_postgres_fields')
        import pwd
        principal=p['credential'];account=pwd.getpwnam(principal['account'])
        must(set(principal) == {'account','uid','gid','groups'} and
             (account.pw_uid,account.pw_gid) == (principal['uid'],principal['gid']) and
             sorted(set(os.getgrouplist(account.pw_name,account.pw_gid))) == principal['groups'],
             'community_reference_database_account')
        self.exec_identity={}
        if os.geteuid() != account.pw_uid:
            must(os.geteuid() == 0,'community_reference_database_identity')
            self.exec_identity={'user':account.pw_uid,'group':account.pw_gid,
                                'extra_groups':principal['groups']}
        must(set(p['passfile']) == {'path','identity'},'community_reference_passfile_binding')
        match(p['passfile']['path'],p['passfile']['identity'])
        must((p['passfile']['identity']['uid'],p['passfile']['identity']['gid'],
              p['passfile']['identity']['mode']) == (account.pw_uid,account.pw_gid,0o600),
             'community_reference_passfile_identity')
        must(set(p['binaries']) == {'psql','pg_dump','pg_restore'},'community_reference_pg_tools')
        for name,want in p['binaries'].items():match(Path(p['binary_dir'])/name,want)
        must(p['host'] == '127.0.0.1' or (p['host'].startswith('/') and
             str(Path(p['host']).resolve(strict=True)) == p['host']),'community_reference_pg_loopback')
        must(type(p['port']) is int and 1024 <= p['port'] <= 65535 and
             re.fullmatch('[a-z_][a-z0-9_]{0,62}',p['user']), 'community_reference_pg_target')
        if host.root == ROOT/'runtime/community':
            def envfile(path):
                return dict(l.split('=',1) for l in path.read_text().splitlines()
                            if '=' in l and not l.startswith('#'))
            native=envfile(ROOT/'runtime/native-control.env')
            profile=envfile(ROOT/'runtime/postgres/ops.env')
            must(p['binary_dir'] == native['R25_NATIVE_PG_BIN'] and
                 p['library_dir'] == native.get('R25_NATIVE_PG_LIB','') and
                 p['host'] == profile['ARTLIVE_PG_SOCKET_DIR'] and p['port'] == 55640 and
                 p['user'] == profile['ARTLIVE_PG_BACKUP_USER'] and
                 p['passfile']['path'] == profile['ARTLIVE_PG_BACKUP_PASSFILE'] and
                 principal['account'] == native['R25_NATIVE_SERVICE_USER'],
                 'community_reference_original_database_principal')
        self.env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
                  'PGPASSFILE':p['passfile']['path'],'PGCONNECT_TIMEOUT':'5',
                  'PGOPTIONS':'-c statement_timeout=60000 -c lock_timeout=5000',
                  'PGAPPNAME':'artlive-reference-'+plan['operation_id']}
        self.verify_libraries()
        if p['library_dir']:self.env['LD_LIBRARY_PATH']=p['library_dir']
        self.connection=['-h',p['host'],'-p',str(p['port']),'-U',p['user'],'-d','artlive_community']

    def verify_libraries(self):
        p=self.pg;directory=p['library_dir'];files=p['library_identity']
        must(type(directory) is str and type(files) is dict,'community_reference_library_profile')
        if not directory:
            must(files == {},'community_reference_library_profile');return
        path=Path(directory)
        must(path.is_absolute() and path.is_dir() and not path.is_symlink() and
             str(path.resolve(strict=True)) == directory and files,
             'community_reference_library_directory')
        actual={}
        for item in path.iterdir():
            target=item.resolve(strict=True)
            must(target.parent == path and target.is_file(),
                 'community_reference_library_target')
            actual[item.name]={'target':target.name,'identity':identity(target)}
        must(actual == files,'community_reference_library_drift')

    def run(self, name, args, *, raw=None, timeout=80, stdout=None):
        self.host.lease.require()
        self.verify_libraries()
        match(Path(self.pg['binary_dir'])/name,self.pg['binaries'][name])
        match(self.pg['passfile']['path'],self.pg['passfile']['identity'])
        result=subprocess.run([str(Path(self.pg['binary_dir'])/name),*args],input=raw,
            stdout=stdout if stdout is not None else subprocess.PIPE,stderr=subprocess.PIPE,
            env=self.env,timeout=timeout,close_fds=True,**self.exec_identity)
        must(result.returncode == 0,'community_reference_database_'+name+'_failed')
        if stdout is None:
            must(len(result.stdout) <= 16*1024*1024,'community_reference_database_result_limit')
            return result.stdout

    def query(self, sql):
        return self.run('psql',[*self.connection,'-X','-w','-qAt','-v','ON_ERROR_STOP=1'],
                        raw=sql.encode()).decode().strip()

    def read(self, sql):
        result=self.query('BEGIN READ ONLY;\n'+sql+'\nCOMMIT;\n')
        return strict_json(('{"value":'+result+'}').encode())['value']

    def snapshot(self):
        metadata=self.read("SELECT json_build_object('versions',"
            "(SELECT json_agg(version ORDER BY version) FROM community.schema_version),"
            "'can_migrate',pg_has_role(current_user,'artlive_community_migrator','MEMBER'),"
            "'database',current_database());")
        must(metadata['database'] == 'artlive_community' and metadata['can_migrate'] is True,
             'community_reference_database_authority')
        data=self.read("""SELECT jsonb_build_object(
          'profiles',(SELECT coalesce(jsonb_agg(jsonb_build_array(user_id,display_name) ORDER BY user_id),'[]'::jsonb) FROM community.profiles WHERE user_id>0),
          'posts',(SELECT coalesce(jsonb_agg(jsonb_build_array(id,author_id,content,gallery_artwork_id,status,created_at) ORDER BY id),'[]'::jsonb) FROM community.posts),
          'comments',(SELECT coalesce(jsonb_agg(jsonb_build_array(id,post_id,author_id,content,created_at) ORDER BY id),'[]'::jsonb) FROM community.comments),
          'reactions',(SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY post_id,user_id,kind),'[]'::jsonb) FROM community.reactions t),
          'operations',(SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY user_id,request_id),'[]'::jsonb) FROM community.operations t));""")
        preserved={k:{'rows':len(v),'sha256':sha(encoded(v))} for k,v in data.items()}
        receipts={}
        for name in ('learning','reference'):
            table=name+'_publication_receipt'
            found=self.read("SELECT to_json(to_regclass('community."+table+"') IS NOT NULL);")
            receipts[name]=self.read('SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY operation_id),'+
                "'[]'::jsonb) FROM community."+table+' t;') if found else []
        safety=self.read("""SELECT jsonb_build_object(
          'superuser',rolsuper,'createdb',rolcreatedb,'createrole',rolcreaterole,
          'inherit',rolinherit,'replication',rolreplication,'bypassrls',rolbypassrls,
          'schema_create',has_schema_privilege('artlive_community_runtime','community','CREATE'),
          'delete_operations',has_table_privilege('artlive_community_runtime','community.operations','DELETE'))
          FROM pg_roles WHERE rolname='artlive_community_runtime';""")
        must(type(safety) is dict and all(v is False for v in safety.values()),
             'community_reference_runtime_privilege_drift')
        reference=None
        if metadata['versions'] == [1,2,3]:
            reference=self.read("""SELECT jsonb_build_object(
              'delete_receipts',has_table_privilege('artlive_community_runtime','community.v2_operations','DELETE'),
              'update_ledger',has_table_privilege('artlive_community_runtime','community.v2_coin_ledger','UPDATE'),
              'delete_ledger',has_table_privilege('artlive_community_runtime','community.v2_coin_ledger','DELETE'),
              'missing_legacy_posts',(SELECT count(*) FROM community.posts p
                WHERE NOT EXISTS(SELECT 1 FROM community.v2_posts v WHERE v.legacy_id=p.id)),
              'missing_legacy_comments',(SELECT count(*) FROM community.comments c
                WHERE NOT EXISTS(SELECT 1 FROM community.v2_comments v WHERE v.legacy_id=c.id)));""")
        return {'versions':metadata['versions'],'preserved':preserved,'receipts':receipts,
                'runtime_safety':safety,'reference':reference}

    def backup(self, target):
        must(not Path(target).exists(),'community_reference_backup_exists')
        fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as out:
            self.run('pg_dump',[*self.connection,'--format=custom','--no-owner','--no-acl'],
                     stdout=out,timeout=120)
            out.flush();os.fsync(out.fileno())
        # The controller reads the private dump; no account/permission changes.
        restore=Path(self.pg['binary_dir'])/'pg_restore'
        match(restore,self.pg['binaries']['pg_restore'])
        p=subprocess.run([str(restore),'--list',str(target)],capture_output=True,env=self.env,timeout=20)
        must(p.returncode == 0,'community_reference_backup_invalid')
        return identity(target)

    def status(self, *, preserved=True):
        current=self.snapshot();before=self.plan['snapshot']
        if preserved:
            must(current['preserved'] == before['preserved'],'community_reference_legacy_data_drift')
        if current == before or (not preserved and current['versions'] == before['versions'] and
                                current['receipts'] == before['receipts']):
            return 'NOT_APPLIED'
        if current['versions'] != [1,2,3]:
            return 'UNKNOWN'
        if current['reference'] != {'delete_receipts':False,'update_ledger':False,'delete_ledger':False,
                                    'missing_legacy_posts':0,'missing_legacy_comments':0}:
            return 'UNKNOWN'
        from community_migration import LEARNING_SHA,REFERENCE_SHA
        operation=self.plan['operation_id']
        expected={'operation_id':operation,'migration_sha256':REFERENCE_SHA,'schema_version':3}
        if current['receipts']['reference'] != [expected]:return 'UNKNOWN'
        learning=({'operation_id':operation,'migration_sha256':LEARNING_SHA,'schema_version':2}
                  if self.plan['versions'] == [1] else None)
        if (current['receipts']['learning'] != [learning] if learning else
            current['receipts']['learning'] != before['receipts']['learning']):return 'UNKNOWN'
        return 'COMMITTED'

    def migrate(self, learning, reference):
        must(self.snapshot() == self.plan['snapshot'],'community_reference_database_preimage_drift')
        from community_migration import reference_transition_sql
        raw=reference_transition_sql(learning,reference,self.plan['operation_id'],self.plan['versions'])
        self.query(raw.decode())
        must(self.status() == 'COMMITTED','community_reference_migration_receipt')


class ReferenceHost(ReleaseHost):
    def __init__(self, original, release, package_plan, settings):
        super().__init__(original,release)
        self.package_plan,self.settings=package_plan,settings

    def binding(self):
        return dict(super().binding(),config_path=str(self.settings['path']),
                    config_sha256=self.settings['sha256'],release_root=str(self.source_release))


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
        self.require_terminal_predecessors()
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

    def require_terminal_predecessors(self):
        for path in self.root.glob('*/journal.json'):
            record=self.host.read(path)
            must(record.get('schema')==PACKAGE_SCHEMA and record.get('plan_sha256')==path.parent.name and
                 record.get('phase')=='RESTORED','community_package_unfinished_predecessor')

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
        # A later approved attempt uses a new history even for the same archive.
        release=self.host.folder/'package-releases'/sha(encoded(plan))
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
            self.require_terminal_predecessors()
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
            must(record['phase']=='ACTIVATE_INTENT','community_package_activation_phase')
            from community_supervision import PackageSupervisor
            supervisor=PackageSupervisor(candidate,ident,record['drained_state_sha256'],self.supervisor.state)
            state=supervisor.registered()
            _,current=candidate.role().current()
            must(current is not None and current['operation_id']==record['operation_id'] and
                 state['desired']=='RUNNING' and state['attempts']==[record['operation_id']],
                 'community_package_activation_receipt')
            status=candidate.control('observe')
            if status=='READY':candidate.preserve_running(supervisor)
            else:
                # Health was verified before ACTIVATE_INTENT. A later recorded
                # exit belongs to normal V2 supervision, not an uncertain spawn.
                must(status=='EXITED','community_package_activation_not_reconciled')
                from community_lifecycle import listeners
                must(not listeners(h.plan['service']['port']),'community_package_foreign_listener')
            phase('COMMITTED')
            return 'COMMITTED_RECONCILED' if status=='READY' else 'COMMITTED_RECONCILED_RESTART_PENDING'
        phase_before=record['phase']
        if phase_before=='RESTORE_INTENT':
            return self.resume_original(record,phase)
        if phase_before=='PREPARED':
            must(h.preserve_running(self.supervisor)==plan['before'],'community_package_preimage_drift')
            phase('RESTORED');return 'RESTORED_NOT_STARTED'
        if phase_before in ('DRAIN_INTENT','DRAINED'):
            # DRAIN_INTENT might have signalled already: observe owned exit only.
            self.supervisor.tick(drain=True)
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


REFERENCE_PACKAGE_SCHEMA='ARTLIVE_COMMUNITY_REFERENCE_UPGRADE_V1'


class ReferencePackageUpgrade(PackageUpgrade):
    """One successor of the committed Studio package, with a compatible bridge.

    The new immutable package first proves that it serves the original schema.
    Its independent final process then starts after the exact forward SQL. The
    old package, approval, activation and every restart attempt remain intact.
    Uncertain spawns are observed, never repeated. A committed schema is never
    undone and recovery never starts the incompatible original package.
    """
    def __init__(self,host,supervisor,context,*,database_factory=None):
        super().__init__(host,supervisor,context)
        must(set(context) == {'reference_sha256','core_commit','candidate','predecessor','previous_context'},
             'community_reference_context_fields')
        must(database_factory is None or host.root != ROOT/'runtime/community',
             'community_reference_fixture_database_forbidden')
        self.database_factory=database_factory or ReferenceDatabase
        self.previous=PackageUpgrade(host,supervisor,context['previous_context'])
        self.root=host.folder/'reference-upgrades'
        self.marker=host.folder/'REFERENCE_ACTIVE.json'

    def database(self,plan):
        return self.database_factory(self.host,plan['database'])

    def require_core(self):
        if self.host.root != ROOT/'runtime/community':return
        current=reference_core_record(self.host,ROOT/'runtime/core-publications/CURRENT.json')
        candidate=self.context['candidate']
        must(all(current['release'].get(k) == candidate[k] for k in ('commit_sha','artifact_sha256')),
             'community_reference_new_core_required')
        tx=current['transaction_id']
        must(re.fullmatch('[a-f0-9]{32}',str(tx)),'community_reference_core_transaction')
        receipt=reference_core_record(self.host,ROOT/'runtime/core-publications/transactions'/tx/'journal.json')
        must(receipt['phase'] == 'COMMITTED' and receipt['transaction_id'] == tx and
             receipt['candidate'] == current['release'],'community_reference_core_not_committed')

    def prepare(self,archive,manifest,provenance,review_sha256,database_plan):
        h=self.host;h.lease.require();h.validate(installing=False);self.require_core()
        must(not self.marker.is_symlink(),'community_reference_active_symlink')
        if self.marker.exists():
            marker=h.read(self.marker)
            must(marker['schema'] == REFERENCE_PACKAGE_SCHEMA and marker['channel'] == 'previous' and
                 h.read(self.root/marker['plan_sha256']/'journal.json')['phase'] == 'RESTORED',
                 'community_reference_already_active')
        must(re.fullmatch('[a-f0-9]{64}',str(review_sha256)),'community_reference_review')
        must(provenance.get('commit') == self.context['core_commit'],'community_reference_core_release')
        for path in (archive,manifest,database_plan):
            item=identity(path)
            must((item['uid'],item['gid'],item['mode']) == (*h.owner,0o600),
                 'community_reference_material_private')
        raw=Path(archive).read_bytes();mr=Path(manifest).read_bytes()
        m=verify_archive(raw,mr,provenance)
        must(m.get('reference_schema_version') == 3 and m.get('learning_schema_version') == 2,
             'community_reference_schema3_bundle')
        dbplan=strict_json(Path(database_plan).read_bytes())
        db=self.database_factory(h,dbplan)
        must(dbplan['versions'] == dbplan['snapshot']['versions'] and
             db.snapshot() == dbplan['snapshot'],'community_reference_database_preimage')
        previous,supervisor=self.previous.active()
        before=previous.preserve_running(supervisor)
        old_settings=Path(h.binding()['config_path'])
        match(old_settings,h.plan['settings'])
        settings=strict_json(old_settings.read_bytes())
        required={'COMMUNITY_DATABASE_URL','COMMUNITY_CORE_ORIGIN','COMMUNITY_WEB_ORIGIN','COMMUNITY_PORT'}
        must(set(settings) == required,'community_reference_original_settings')
        operation=dbplan['operation_id']
        material=h.folder/'reference-material'/operation
        h.storage.mkdir_private(material.parent);h.storage.mkdir_private(material)
        media=material/'media';h.storage.mkdir_private(media);h.storage.mkdir_private(media/'reference')
        settings=dict(settings,COMMUNITY_MEDIA_ROOT=str(media))
        target=material/'settings.json';configuration=encoded(settings)
        if target.exists():match(target,dict(identity(target),sha256=sha(configuration)))
        else:h.storage.atomic_write_bytes(target,configuration)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            from community_migration import reference_transition_sql
            reference_transition_sql(z.read('db/002_learning_community.sql'),
                z.read('db/003_reference_community.sql'),operation,dbplan['versions'])
        plan={'schema':REFERENCE_PACKAGE_SCHEMA,'context':self.context,'original_plan_sha256':h.digest,
              'archive':identity(archive),'manifest':identity(manifest),'provenance':provenance,
              'review_sha256':review_sha256,'before':before,'binding_sha256':sha(encoded(h.binding())),
              'database':dbplan,'settings':dict(path=str(target),**identity(target))}
        ident=sha(encoded(plan));folder=self.root/ident
        h.storage.mkdir_private(self.root);h.storage.mkdir_private(folder)
        for path in self.root.glob('*/journal.json'):
            must(h.read(path)['phase'] == 'RESTORED','community_reference_unfinished_attempt')
        unsigned=folder/'UNSIGNED_PLAN.json'
        if unsigned.exists():must(unsigned.read_bytes() == encoded(plan),'community_reference_plan_conflict')
        else:h.storage.atomic_write_bytes(unsigned,encoded(plan))
        return plan,unsigned

    def verify(self,plan):
        h=self.host;h.lease.require();h.validate(installing=False);self.require_core()
        must(set(plan) == {'schema','context','original_plan_sha256','archive','manifest','provenance',
             'review_sha256','before','binding_sha256','database','settings'} and
             plan['schema'] == REFERENCE_PACKAGE_SCHEMA,'community_reference_plan')
        must(plan['context'] == self.context and plan['original_plan_sha256'] == h.digest and
             plan['binding_sha256'] == sha(encoded(h.binding())) and
             plan['provenance']['commit'] == self.context['core_commit'],'community_reference_context')
        ident=sha(encoded(plan));folder=self.root/ident
        approval=folder/'approval.json'
        match(approval,dict(identity(approval),sha256=ident))
        must((identity(approval)['uid'],identity(approval)['gid'],identity(approval)['mode']) ==
             (*h.owner,0o600) and approval.read_bytes() == encoded(plan),'community_reference_exact_approval')
        settings=plan['settings'];wanted=dict(settings);wanted.pop('path')
        expected=h.folder/'reference-material'/plan['database']['operation_id']/'settings.json'
        must(settings['path'] == str(expected),'community_reference_settings_path')
        match(expected,wanted)
        old=Path(h.binding()['config_path']);match(old,h.plan['settings'])
        original=strict_json(old.read_bytes())
        must(set(original) == {'COMMUNITY_DATABASE_URL','COMMUNITY_CORE_ORIGIN',
             'COMMUNITY_WEB_ORIGIN','COMMUNITY_PORT'},'community_reference_original_settings')
        media=expected.parent/'media'
        must(strict_json(expected.read_bytes()) == dict(original,COMMUNITY_MEDIA_ROOT=str(media)),
             'community_reference_settings_values')
        for path in (expected.parent,media,media/'reference'):
            h.storage.validate_target(path,exists=True,directory=True,
                                      expected_owner=h.owner,expected_mode=0o700)
        return ident,folder

    def candidate(self,plan,channel='bridge'):
        must(channel in ('bridge','final'),'community_reference_channel')
        ident=sha(encoded(plan));base=self.host.folder/'reference-releases'/ident
        key=(ident,channel)
        if key not in self.candidates:
            candidate=ReferenceHost(self.host,base/channel,plan,plan['settings'])
            candidate.source_release=base/'release'
            self.candidates[key]=candidate
        return self.candidates[key]

    def materialize(self,plan,archive,manifest):
        h=self.host;match(archive,plan['archive']);match(manifest,plan['manifest'])
        raw=Path(archive).read_bytes();mr=Path(manifest).read_bytes()
        m=verify_archive(raw,mr,plan['provenance'])
        must(m.get('reference_schema_version') == 3,'community_reference_manifest')
        release=self.candidate(plan).source_release
        def put(path,data):
            for parent in reversed([path.parent,*path.parent.parents]):
                if parent == h.folder or h.folder in parent.parents:h.storage.mkdir_private(parent)
            if path.exists():h.storage.file_identity(path,expected_sha256=sha(data),expected_bytes=len(data))
            else:
                must(len(data) <= 32*1024*1024,'community_reference_file_limit')
                def write(fd):h.storage._write(fd,data);return {'sha256':sha(data),'bytes':len(data)}
                h.storage._atomic(path,write)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for item in m['files']:put(release/item['path'],z.read(item['path']))
        put(release/'manifest.json',mr)
        for channel in ('bridge','final'):
            candidate=self.candidate(plan,channel)
            h.storage.mkdir_private(candidate.release);h.storage.mkdir_private(candidate.release/'process')
            candidate.role().verify()

    def supervisor_for(self,plan,record,channel):
        from community_supervision import PackageSupervisor
        if channel == 'bridge':
            _,prior=self.previous.active()
            return PackageSupervisor(self.candidate(plan,channel),sha(encoded(plan)),
                                     record['predecessor_state_sha256'],prior.state)
        prior=self.supervisor_for(plan,record,'bridge')
        return PackageSupervisor(self.candidate(plan,channel),sha(encoded(plan)),
                                 record['bridge_state_sha256'],prior.state)

    def start(self,plan,record,channel):
        h=self.host;supervisor=self.supervisor_for(plan,record,channel)
        operation=record[channel+'_operation_id']
        must(not supervisor.state.exists(),'community_reference_supervision_already_exists')
        state={'schema':'ARTLIVE_COMMUNITY_SUPERVISION_V2','plan_sha256':h.digest,
               'upgrade_sha256':sha(encoded(plan)),'legacy_state_sha256':supervisor.legacy_sha256,
               'desired':'RUNNING','attempts':[operation],'budget_start':0,'unhealthy':None,
               'stable':None,'reason':'REFERENCE_'+channel.upper()+'_START_DECISION','resumptions':[]}
        h.write(supervisor.state,state)
        self.candidate(plan,channel).role().start(operation)

    def ready(self,plan,record,channel):
        candidate=self.candidate(plan,channel);supervisor=self.supervisor_for(plan,record,channel)
        deadline=time.monotonic()+15
        while True:
            status=candidate.control('observe')
            if status == 'READY':break
            must(status == 'STARTING' and time.monotonic() < deadline,
                 'community_reference_'+channel+'_not_ready')
            time.sleep(.05)
        candidate.preserve_running(supervisor)

    def activate(self,plan,channel):
        must(channel in ('bridge','final','previous'),'community_reference_activation_channel')
        ident=sha(encoded(plan));value={'schema':REFERENCE_PACKAGE_SCHEMA,'plan_sha256':ident,'channel':channel}
        before=identity(self.marker)['sha256'] if self.marker.exists() else None
        if before is not None:
            current=self.host.read(self.marker)
            restored=(current['channel'] == 'previous' and
                      self.host.read(self.root/current['plan_sha256']/'journal.json')['phase'] == 'RESTORED')
            must(current['schema'] == REFERENCE_PACKAGE_SCHEMA and
                 ((current['plan_sha256'] == ident and current['channel'] in ('bridge','final','previous'))
                  or restored),'community_reference_foreign_active')
        self.host.storage.atomic_write_json(self.marker,value,replace=before is not None,
                                           expected_existing_sha256=before)

    def install(self,plan,archive,manifest,*,recover=False,fault=None):
        h=self.host;ident,folder=self.verify(plan);journal=folder/'journal.json'
        record=h.read(journal) if journal.exists() else None
        def phase(value):
            record['phase']=value;record['history'].append(value);h.write(journal,record)
            if fault:fault(value)
        if record is not None:
            must(record['schema'] == REFERENCE_PACKAGE_SCHEMA and record['plan_sha256'] == ident,
                 'community_reference_journal')
            if record['phase'] == 'COMMITTED':
                active,supervisor=self.active();active.preserve_running(supervisor)
                return 'COMMITTED_VERIFIED_NOOP'
            must(recover,'community_reference_explicit_recovery_required')
            return self.recover(plan,record,phase)
        must(not recover,'community_reference_not_started')
        previous,supervisor=self.previous.active()
        must(previous.preserve_running(supervisor) == plan['before'],
             'community_reference_process_preimage_drift')
        db=self.database(plan)
        must(db.snapshot() == plan['database']['snapshot'],'community_reference_database_preimage_drift')
        self.materialize(plan,archive,manifest)
        import uuid
        record={'schema':REFERENCE_PACKAGE_SCHEMA,'plan_sha256':ident,'phase':'PREPARED',
                'history':['PREPARED'],**{key:uuid.uuid4().hex for key in
                ('bridge_operation_id','final_operation_id','resume_previous_operation_id','resume_bridge_operation_id')}}
        h.write(journal,record)
        phase('DRAIN_PREVIOUS_INTENT');supervisor.tick(drain=True);self.drain(previous.role())
        from community_lifecycle import listeners
        must(not listeners(h.plan['service']['port']),'community_reference_foreign_listener')
        record['predecessor_state_sha256']=identity(supervisor.state)['sha256'];phase('PREVIOUS_DRAINED')
        phase('BRIDGE_START_INTENT');self.start(plan,record,'bridge');phase('BRIDGE_STARTED')
        self.ready(plan,record,'bridge');phase('BRIDGE_ACTIVATE_INTENT');self.activate(plan,'bridge')
        phase('BRIDGE_READY')
        phase('DRAIN_BRIDGE_INTENT')
        bridge=self.candidate(plan,'bridge');bs=self.supervisor_for(plan,record,'bridge')
        bs.tick(drain=True);self.drain(bridge.role())
        must(not listeners(h.plan['service']['port']),'community_reference_foreign_listener')
        record['bridge_state_sha256']=identity(bs.state)['sha256'];phase('BRIDGE_DRAINED')
        must(db.snapshot() == plan['database']['snapshot'],'community_reference_database_changed')
        record['backup']=db.backup(folder/'before-reference.dump');phase('BACKUP_VERIFIED')
        phase('MIGRATION_COMMIT_INTENT')
        source=bridge.source_release
        db.migrate((source/'db/002_learning_community.sql').read_bytes(),
                   (source/'db/003_reference_community.sql').read_bytes())
        phase('MIGRATED')
        return self.finish_final(plan,record,phase)

    def finish_final(self,plan,record,phase):
        must(self.database(plan).status() == 'COMMITTED','community_reference_commit_required')
        phase('FINAL_START_INTENT');self.start(plan,record,'final');phase('FINAL_STARTED')
        self.ready(plan,record,'final');phase('FINAL_ACTIVATE_INTENT');self.activate(plan,'final')
        phase('COMMITTED');return 'COMMITTED'

    def owned_bridge(self,plan,record):
        bridge=self.candidate(plan,'bridge');supervisor=self.supervisor_for(plan,record,'bridge')
        must(supervisor.state.exists() and not supervisor.state.is_symlink(),
             'community_reference_unknown_bridge_start')
        ledger=supervisor.registered()
        _,saved=bridge.role().current()
        attempts=ledger['attempts']
        must(saved is not None and attempts and attempts[0] == record['bridge_operation_id'] and
             saved['operation_id'] == attempts[-1],'community_reference_unknown_bridge_start')
        budget=record.get('bridge_resume_budget_start');resumptions=ledger['resumptions']
        if resumptions:
            must(type(budget) is int and budget >= 1 and resumptions ==
                 [{'operation_id':record['resume_bridge_operation_id'],'budget_start':budget}] and
                 ledger['budget_start'] >= budget,'community_reference_bridge_resume_ledger')
        elif budget is not None:
            must(type(budget) is int and budget == len(attempts) and ledger['desired'] == 'STOPPED',
                 'community_reference_bridge_resume_ledger')
        return bridge,supervisor

    def resume_bridge(self,plan,record,phase):
        bridge,supervisor=self.owned_bridge(plan,record)
        must(self.database(plan).status(preserved=False) in ('NOT_APPLIED','COMMITTED'),
             'community_reference_unknown_migration')
        ledger=supervisor.registered()
        if not ledger['resumptions'] and ledger['desired'] == 'STOPPED' and 'bridge_resume_budget_start' not in record:
            record['bridge_resume_budget_start']=len(ledger['attempts']);phase('BRIDGE_RESUME_INTENT')
        self.resume_owned(bridge,supervisor,record['resume_bridge_operation_id'])
        bridge.preserve_running(supervisor)
        self.activate(plan,'bridge');phase('FORWARD_READY')
        return 'COMPATIBLE_BRIDGE_READY_FORWARD_SCHEMA_RETAINED'

    def resume_owned(self,host,supervisor,operation):
        """Reconcile an authorized resume; a STARTING receipt is not a retry."""
        ledger=supervisor.registered();_,saved=host.role().current()
        must(saved is not None and ledger['attempts'] and
             saved['operation_id'] == ledger['attempts'][-1],
             'community_reference_unknown_resume_start')
        status=host.control('observe')
        resumed=any(r['operation_id'] == operation for r in ledger['resumptions'])
        if status in ('READY','STARTING') and ledger['desired'] == 'STOPPED':
            must(not resumed,'community_reference_resume_already_drained')
            supervisor.tick(drain=True);self.drain(host.role());status=host.control('observe')
        if status in ('STOP_INTENT','STOPPING'):
            self.drain(host.role());status=host.control('observe')
        if status in ('STOPPED','EXITED'):
            if not resumed:supervisor.enable(operation)
            else:must(ledger['desired'] == 'RUNNING','community_reference_resume_already_drained')
            supervisor.tick()
        else:
            must(status in ('READY','STARTING') and ledger['desired'] == 'RUNNING',
                 'community_reference_resume_not_running')
            must(status != 'STARTING' or resumed,'community_reference_unknown_resume_start')
        deadline=time.monotonic()+15
        while True:
            status=host.control('observe')
            if status == 'READY':break
            must(status == 'STARTING' and time.monotonic() < deadline,
                 'community_reference_resume_not_ready')
            time.sleep(.05)
        host.preserve_running(supervisor)

    def restore_previous(self,plan,record,phase):
        must(self.database(plan).status(preserved=False) == 'NOT_APPLIED',
             'community_reference_forward_schema_no_old_restore')
        bridge=self.candidate(plan,'bridge');bridge_state=bridge.release/'supervision-v2.json'
        if record['phase'] != 'RESTORE_PREVIOUS_INTENT':
            record['restore_bridge']=None
            if 'predecessor_state_sha256' in record and bridge_state.exists():
                bridge,bs=self.owned_bridge(plan,record)
                bs.tick(drain=True);self.drain(bridge.role())
                receipt,saved=bridge.role().current()
                must(saved['phase'] in ('STOPPED','EXITED'),'community_reference_bridge_not_drained')
                record['restore_bridge']={'state_sha256':identity(bridge_state)['sha256'],
                    'receipt_path':str(receipt),'receipt_sha256':identity(receipt)['sha256']}
            phase('RESTORE_PREVIOUS_INTENT')
        else:
            # The old package's state may now have advanced. Recheck the frozen
            # stopped bridge directly, without reusing its old legacy hash.
            frozen=record['restore_bridge']
            if frozen is None:
                must(not bridge_state.exists(),'community_reference_unexpected_bridge_ledger')
            else:
                receipt,saved=bridge.role().current()
                must(str(receipt) == frozen['receipt_path'] and saved['phase'] in ('STOPPED','EXITED') and
                     identity(receipt)['sha256'] == frozen['receipt_sha256'] and
                     identity(bridge_state)['sha256'] == frozen['state_sha256'],
                     'community_reference_restored_bridge_drift')
        previous,supervisor=self.previous.active()
        self.resume_owned(previous,supervisor,record['resume_previous_operation_id'])
        self.activate(plan,'previous');phase('RESTORED');return 'RESTORED'

    def recover(self,plan,record,phase):
        """Continue known boundaries; never execute SQL or replay a spawn."""
        h=self.host;state=record['phase']
        if state == 'PREPARED':
            previous,supervisor=self.previous.active()
            must(previous.preserve_running(supervisor) == plan['before'],'community_reference_preimage_drift')
            phase('RESTORED');return 'RESTORED_NOT_STARTED'
        if state == 'RESTORED':
            must(self.database(plan).status(preserved=False) == 'NOT_APPLIED',
                 'community_reference_forward_schema_no_old_restore')
            previous,supervisor=self.previous.active();previous.preserve_running(supervisor)
            return 'RESTORED_VERIFIED_NOOP'
        if state in ('DRAIN_PREVIOUS_INTENT','PREVIOUS_DRAINED','RESTORE_PREVIOUS_INTENT'):
            return self.restore_previous(plan,record,phase)
        if state == 'MIGRATION_COMMIT_INTENT':
            outcome=self.database(plan).status()
            must(outcome in ('NOT_APPLIED','COMMITTED'),'community_reference_unknown_migration')
            if outcome == 'COMMITTED':phase('MIGRATED');return self.finish_final(plan,record,phase)
            return self.restore_previous(plan,record,phase)
        if state == 'MIGRATED':return self.finish_final(plan,record,phase)
        if state in ('FINAL_START_INTENT','FINAL_STARTED','FINAL_ACTIVATE_INTENT'):
            final=self.candidate(plan,'final');_,saved=final.role().current()
            must(saved is not None and saved['operation_id'] == record['final_operation_id'],
                 'community_reference_unknown_final_start')
            status=final.control('observe')
            if status == 'READY':
                self.ready(plan,record,'final');self.activate(plan,'final');phase('COMMITTED')
                return 'COMMITTED_RECONCILED'
            self.drain(final.role());return self.resume_bridge(plan,record,phase)
        if state in ('BRIDGE_START_INTENT','BRIDGE_STARTED','BRIDGE_ACTIVATE_INTENT','BRIDGE_READY',
                     'DRAIN_BRIDGE_INTENT','BRIDGE_DRAINED','BACKUP_VERIFIED','BRIDGE_RESUME_INTENT','FORWARD_READY'):
            bridge,_=self.owned_bridge(plan,record)
            outcome=self.database(plan).status(preserved=False)
            must(outcome in ('NOT_APPLIED','COMMITTED'),'community_reference_unknown_migration')
            if outcome == 'NOT_APPLIED':return self.restore_previous(plan,record,phase)
            return self.resume_bridge(plan,record,phase)
        raise ValueError('community_reference_recovery_phase')

    def active(self):
        h=self.host;h.lease.require()
        if not self.marker.exists():return self.previous.active()
        marker=h.read(self.marker);ident=marker.get('plan_sha256');channel=marker.get('channel')
        must(marker == {'schema':REFERENCE_PACKAGE_SCHEMA,'plan_sha256':ident,'channel':channel} and
             re.fullmatch('[a-f0-9]{64}',str(ident)) and channel in ('bridge','final','previous'),
             'community_reference_active_pointer')
        folder=self.root/ident;plan=h.read(folder/'approval.json');self.verify(plan)
        record=h.read(folder/'journal.json')
        must(record['schema'] == REFERENCE_PACKAGE_SCHEMA and record['plan_sha256'] == ident,
             'community_reference_active_journal')
        if channel == 'previous':
            must(record['phase'] in ('RESTORE_PREVIOUS_INTENT','RESTORED'),
                 'community_reference_previous_not_restored')
            return self.previous.active()
        if channel == 'final':
            must(record['phase'] in ('FINAL_ACTIVATE_INTENT','COMMITTED'),
                 'community_reference_final_not_committed')
        return self.candidate(plan,channel),self.supervisor_for(plan,record,channel)
