"""Bound optional VMM controller extension; never an application publication.

Production CLI uses fixed roots. Fixture Runtime is Python-only and cannot be
selected by environment/CLI. Original POLICY/CURRENT/approval records stay
byte-identical; a verified installed overlay changes only effective control
hashes, never application eligibility or historical accepted-record digests.
"""
from __future__ import annotations
import argparse
import ast
import contextlib
import copy
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import zipfile

ROOT = Path('/root/autodl-tmp/artlive-r25-native-pg16/handoff-production-20260923T062101Z')
LOCK = Path('/root/autodl-tmp/auralink-web-deploy/runtime/frontend-deployment.lock')
SCHEMA = 'R25_VMM_CONTROLLER_PLAN_V1'
MANIFEST_SCHEMA = 'R25_VMM_CONTROLLER_BUNDLE_V1'
MODULES = ('core_release_guard.py', 'vmm_controller.py', 'vmm_lifecycle.py', 'vmm_service.py')
PHASES = {'PREPARED', 'INSTALL_INTENT', 'INSTALLED', 'RESTORE_INTENT', 'RESTORED', 'RECOVERY_REQUIRED'}
BASE_LIFECYCLE = '783c10bca59e8723c2a208970efd566890776aff8813a536bbc4c8e069d1fda3'
BASE_GUARD = '8ae03f1879077d526bef83f601df4b59195c139cf654964229d11b8012f69dd1'
MAX_FILE = 4 * 1024 * 1024
SUPERVISOR_SHA = 'fc52bce2b67f84232147d9b8d52da1dac60e9816bf051b11b1132f888749b54e'
APP_CONTROL_SHA = '3916a817cb5a9094de61a14e6d05b9706a6a0711a30b15ebcb9a6c55a88ecac7'


class Refused(RuntimeError): pass

def must(condition, why):
    if not condition: raise Refused(why)

def sha(raw): return hashlib.sha256(raw).hexdigest()

def encoded(value): return json.dumps(value, sort_keys=True, separators=(',', ':')).encode() + b'\n'

def digest(value, length=64):
    must(type(value) is str and re.fullmatch('[0-9a-f]{'+str(length)+'}', value) is not None, 'digest_format')
    return value

def env_values(raw):
    values = {}
    for line in raw.decode().splitlines():
        if not line or line.startswith('#'): continue
        must('=' in line, 'handoff_syntax')
        key, value = line.split('=', 1)
        must(key not in values, 'handoff_duplicate')
        values[key] = value
    return values


def read(path, owner=(0, 0), mode=0o600, expected=None):
    path = Path(path)
    for p in (path, *path.parents): must(not p.is_symlink(), 'symlink_path')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        a = os.fstat(fd)
        must(stat.S_ISREG(a.st_mode) and a.st_nlink == 1 and (a.st_uid, a.st_gid) == tuple(owner)
             and stat.S_IMODE(a.st_mode) == mode and a.st_size <= MAX_FILE, 'file_identity')
        with os.fdopen(os.dup(fd), 'rb') as stream: raw = stream.read(MAX_FILE + 1)
        b = os.fstat(fd)
        must((a.st_ino,a.st_size,a.st_mtime_ns,a.st_ctime_ns) == (b.st_ino,b.st_size,b.st_mtime_ns,b.st_ctime_ns)
             and len(raw) == a.st_size, 'file_changed')
        must(expected is None or sha(raw) == digest(expected), 'file_digest')
        return raw
    finally: os.close(fd)


def json_read(path, owner=(0,0), expected=None):
    def pairs(items):
        result = {}
        for key, value in items:
            must(key not in result, 'json_duplicate_key'); result[key] = value
        return result
    return json.loads(read(path, owner, expected=expected), object_pairs_hook=pairs)


class Runtime:
    """Actual file/CAS/flock adapter; non-production paths only via Python tests."""
    def __init__(self, root=ROOT, lock=LOCK, *, private=(0,0), scripts=(1000,1000), supervise=None):
        self.root, self.lock = Path(root), Path(lock)
        self.private, self.scripts = tuple(private), tuple(scripts)
        self.store = self.root/'runtime/vmm-controller'
        self.supervise = supervise or self._supervise
        self.fd = None

    def _supervise(self, mode):
        must(self.root == ROOT and self.lock == LOCK and os.geteuid() == 0, 'production_only')
        read(ROOT/'operations/r25-production-app-supervisor.sh',self.scripts,0o700,SUPERVISOR_SHA)
        read(ROOT/'operations/r25-production-app-control.sh',self.scripts,0o700,APP_CONTROL_SHA)
        env = {'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8', 'R25_ROOT':str(ROOT),
               'ARTLIVE_R25_HANDOFF_CONFIG':str(ROOT/'runtime/handoff-control.env')}
        result = subprocess.run([str(ROOT/'operations/r25-production-app-supervisor.sh'), mode],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=35)
        must(result.returncode == 0, 'watchdog_'+mode+'_failed')

    def mkdir(self, path):
        path = Path(path)
        must(path == self.store or self.store in path.parents, 'state_path')
        for p in reversed([path,*path.parents]):
            if p == self.store or self.store in p.parents:
                if not p.exists(): p.mkdir(mode=0o700); os.chown(p,*self.private)
                st = p.lstat(); must(stat.S_ISDIR(st.st_mode) and not p.is_symlink() and
                    (st.st_uid,st.st_gid,stat.S_IMODE(st.st_mode)) == (*self.private,0o700), 'state_directory')

    @contextlib.contextmanager
    def lease(self):
        read(self.lock, self.private)
        fd = os.open(self.lock, os.O_RDWR | os.O_NOFOLLOW)
        try:
            a,b = os.fstat(fd),self.lock.stat()
            must((a.st_dev,a.st_ino)==(b.st_dev,b.st_ino), 'lease_inode')
            try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise Refused('shared_publication_busy') from None
            self.fd=fd; yield
        finally: self.fd=None; os.close(fd)

    def atomic(self, path, raw, *, before=None, owner=None, mode=0o600):
        must(self.fd is not None, 'lease_required')
        path=Path(path); owner=tuple(owner or self.private)
        if path.exists() or path.is_symlink():
            must(before is not None,'target_already_exists');read(path,owner,mode,before)
        else: must(before is None,'target_missing')
        for p in (path.parent,*path.parent.parents): must(not p.is_symlink(),'symlink_path')
        tmp=path.with_name(path.name+'.vmm-new-'+os.urandom(8).hex())
        fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
        try:
            with os.fdopen(fd,'wb') as out:
                out.write(raw);out.flush();os.fchown(out.fileno(),*owner);os.fchmod(out.fileno(),mode);os.fsync(out.fileno())
            if before is None: must(not path.exists() and not path.is_symlink(),'target_appeared')
            else: read(path,owner,mode,before)
            os.replace(tmp,path)
            d=os.open(path.parent,os.O_DIRECTORY|os.O_NOFOLLOW)
            try: os.fsync(d)
            finally: os.close(d)
        finally:
            if tmp.exists(): tmp.unlink()

    def write_state(self,path,value):
        old=sha(read(path,self.private)) if path.exists() else None
        self.atomic(path,encoded(value),before=old)

    def targets(self):
        p=self.root/'operations/r25-publisher'
        return {'vmm_controller.py':(p/'vmm_controller.py',self.private,0o600),
            'vmm_lifecycle.py':(p/'vmm_lifecycle.py',self.private,0o600),
            'vmm_service.py':(p/'vmm_service.py',self.private,0o600),
            'publisher-guard':(p/'core_release_guard.py',self.private,0o600),
            'operation-guard':(self.root/'operations/core_release_guard.py',self.scripts,0o700),
            'lifecycle':(self.root/'operations/lifecycle_adapter.py',self.scripts,0o700),
            'role':(self.root/'runtime/config/vmm-role.json',self.private,0o600),
            'handoff':(self.root/'runtime/handoff-control.env',self.private,0o600)}

    def preserved(self):
        return {name:self.root/rel for name,rel in {
            'policy':'runtime/core-publications/POLICY.json',
            'core-current':'runtime/core-publications/CURRENT.json',
            'ai-current':'runtime/ai-publications/CURRENT.json',
            'ai-policy':'runtime/ai-publications/POLICY.json',
            'application':'runtime/config/application.yml',
            'ai-configuration':'runtime/config/assistant-private/assistant-runtime.json',
            'frontend':'runtime/config/frontend-selection.json',
            'frontend-reference':'runtime/config/frontend-selection.reference',
            'original-controller-active':'runtime/core-publications/CONTROL_UPGRADE_ACTIVE.json',
            'java-proof':'runtime/app-pids/java.pid', 'guide-proof':'runtime/app-pids/guide.pid',
            'critic-proof':'runtime/app-pids/critic.pid'}.items()}

    def require_watchdog_stopped(self):
        proof=self.root/'runtime/app-supervisor.pid'
        if not proof.exists():return
        values=env_values(read(proof,self.private));pid=values.get('pid','')
        must(pid.isdigit() and values.get('start_ticks','').isdigit(),'watchdog_proof_invalid')
        try:
            raw=(Path('/proc')/pid/'stat').read_text();tail=raw[raw.rfind(')')+2:].split()
        except FileNotFoundError:return
        must(tail[0]=='Z','watchdog_must_be_stopped_before_mixed_recovery')

    def require_context(self, expected=None, *, runtime=False):
        # A legitimate stop/restart may temporarily remove PID proofs. Runtime
        # policy reads validate only this extension's immutable approval base;
        # the original guard owns live application/backup transaction checks.
        observed={name:sha(read(path,self.private)) for name,path in self.preserved().items()
                  if not runtime or name in {'policy','original-controller-active'}}
        if expected is not None:
            keys={'policy','original-controller-active'} if runtime else set(observed)
            must(all(observed[k]==expected[k] for k in keys),'preserved_context_drift')
        policy=json_read(self.preserved()['policy'],self.private)
        must(policy.get('schema')=='R25_CORE_BUSINESS_POLICY_V2' and
             policy.get('upgrade',{}).get('capability')=='assistant-v1' and
             'core_followup' in policy.get('upgrade',{}),'installed_assistant_followup_required')
        active=json_read(self.preserved()['original-controller-active'],self.private)
        ident=digest(active.get('approval_sha256'))
        journal=json_read(self.root/'runtime/core-publications/controller-upgrades'/ident/'journal.json',self.private)
        prior=json_read(self.root/'runtime/core-publications/controller-upgrades'/ident/'approval.json',self.private,ident)
        must(journal.get('phase')=='INSTALLED' and journal.get('approval_sha256')==ident and
             prior.get('schema') in {'R25_ASSISTANT_CORE_FOLLOWUP_BINDING_V1','R25_MANAGED_VMM_CORE_FOLLOWUP_BINDING_V1'} and
             prior.get('operational',{}).get('policy',{}).get('after_sha256')==observed['policy'],
             'prior_controller_incomplete_or_mismatched')
        if prior['schema']=='R25_MANAGED_VMM_CORE_FOLLOWUP_BINDING_V1':
            must(policy['upgrade']['core_followup'].get('schema')=='R25_MANAGED_VMM_CORE_FOLLOWUP_V1' and
                 prior.get('files',{}).get('core_release_guard.py',{}).get('after_sha256')==policy.get('controllers',{}).get('core_release_guard.py'),
                 'managed_core_controller_binding')
        if runtime:return observed
        for kind in ('core','ai'):
            store=self.root/f'runtime/{kind}-publications'
            current=json_read(store/'CURRENT.json',self.private); tx=current.get('transaction_id')
            must(type(tx)is str and re.fullmatch('[0-9a-f]{32}',tx),'transaction_identity')
            j=json_read(store/'transactions'/tx/'journal.json',self.private)
            must(j.get('phase')=='COMMITTED','application_not_committed')
            if (store/'ACTIVE.json').exists():
                marker=json_read(store/'ACTIVE.json',self.private); active_tx=marker.get('transaction_id')
                must(type(active_tx)is str and re.fullmatch('[0-9a-f]{32}',active_tx),'transaction_identity')
                must(json_read(store/'transactions'/active_tx/'journal.json',self.private).get('phase') in {'COMMITTED','RESTORED'},'active_application_transaction')
        backup=self.root/'runtime/assistant-backups/assistant-daily-v1/ACTIVE.json'
        if backup.exists():
            day=json_read(backup,self.private).get('day');must(type(day)is str and re.fullmatch('20[0-9]{2}-[0-9]{2}-[0-9]{2}',day),'backup_identity')
            must(json_read(backup.parent/'runs'/(day+'.json'),self.private).get('phase') in
                 {'COMMITTED','FAILED_RESUMED','RECOVERED_WITHOUT_REPLAY','PURGED'},'active_backup')
        return observed


def verify_bundle(rt, deploy):
    must(type(deploy)is dict and set(deploy)=={'repository','ref','commit','run_id','run_attempt','archive_sha256','manifest_sha256','tool_sha256'},'deploy_fields')
    must(deploy['repository']=='bailangguihun/auralink-deploy' and deploy['ref']=='refs/heads/release','formal_repository')
    digest(deploy['commit'],40)
    for key in ('run_id','run_attempt'): must(type(deploy[key])is str and re.fullmatch('[1-9][0-9]{0,19}',deploy[key]),'formal_run')
    folder=rt.root/'operations/r25-vmm-candidates'/deploy['commit']
    archive=read(folder/'vmm-extension.zip',rt.private,expected=deploy['archive_sha256'])
    manifest=json_read(folder/'manifest.json',rt.private,deploy['manifest_sha256'])
    must(set(manifest)=={'schema','repository','ref','commit','run_id','run_attempt','archive_sha256','files'} and
         manifest['schema']==MANIFEST_SCHEMA and all(manifest[k]==deploy[k] for k in ('repository','ref','commit','run_id','run_attempt','archive_sha256')),'manifest_provenance')
    records=manifest['files'];must(type(records)is list and [x.get('path') for x in records]==list(MODULES),'exact_extension_inventory')
    result={}
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        must(z.namelist()==list(MODULES) and len(z.infolist())==len(MODULES),'archive_inventory')
        for item in records:
            must(set(item)=={'path','bytes','sha256'} and type(item['bytes'])is int and 0<item['bytes']<=MAX_FILE,'manifest_file')
            inf=z.getinfo(item['path']);must(not stat.S_ISLNK(inf.external_attr>>16),'archive_symlink')
            raw=z.read(item['path']);must(len(raw)==item['bytes'] and sha(raw)==digest(item['sha256']),'archive_file_digest')
            must(read(folder/item['path'],rt.private,expected=item['sha256'])==raw,'delivered_source_mismatch')
            ast.parse(raw.decode()); result[item['path']]=raw
    must(sha(result['vmm_controller.py'])==deploy['tool_sha256'],'controller_tool_identity')
    must(not (folder/'__pycache__').exists() and not any(folder.glob('*.py[co]')),'bundle_bytecode')
    return folder,result


def renderer(folder, sources, before):
    # Only verified source bytes; no candidate-chosen script and no bytecode.
    name='_r25_verified_vmm_lifecycle_render';spec=importlib.util.spec_from_file_location(name,folder/'vmm_lifecycle.py')
    module=importlib.util.module_from_spec(spec)
    exec(compile(sources['vmm_lifecycle.py'],str(folder/'vmm_lifecycle.py'),'exec',dont_inherit=True),module.__dict__)
    return module.extend_lifecycle(before)


def render_handoff(before,rt,after):
    old=env_values(before)
    updates={'R25_LIFECYCLE_ADAPTER_SHA256':sha(after['lifecycle']),
             'R25_CORE_RELEASE_GUARD_SHA256':sha(after['operation-guard'])}
    additions={'R25_VMM_CONTROLLER':str(rt.targets()['vmm_controller.py'][0]),'R25_VMM_CONTROLLER_SHA256':sha(after['vmm_controller.py']),
               'R25_VMM_LIFECYCLE':str(rt.targets()['vmm_lifecycle.py'][0]),'R25_VMM_LIFECYCLE_SHA256':sha(after['vmm_lifecycle.py']),
               'R25_VMM_ROLE':str(rt.targets()['role'][0]),'R25_VMM_ROLE_SHA256':sha(after['role']),
               'R25_VMM_SERVICE_SHA256':sha(after['vmm_service.py'])}
    must(set(updates)<=old.keys() and not set(additions)&old.keys(),'handoff_existing_extension_or_missing_binding')
    lines=[line.split('=',1)[0]+'='+updates[line.split('=',1)[0]] if line.split('=',1)[0] in updates else line for line in before.decode().splitlines()]
    return ('\n'.join(lines+[key+'='+value for key,value in additions.items()])+'\n').encode()



def verify_baseline_controls(rt,old,sources):
    must(sha(old['lifecycle'])==BASE_LIFECYCLE and old['publisher-guard']==old['operation-guard'],'installed_control_baseline')
    policy=json_read(rt.preserved()['policy'],rt.private)
    if policy['upgrade']['core_followup'].get('schema')=='R25_MANAGED_VMM_CORE_FOLLOWUP_V1':
        # The bounded managed Core binding must already be installed and its
        # guard must be the identical source from this formal extension bundle.
        # An arbitrary policy hash is never an extension installation baseline.
        active=json_read(rt.preserved()['original-controller-active'],rt.private)
        prior=json_read(rt.root/'runtime/core-publications/controller-upgrades'/digest(active.get('approval_sha256'))/'approval.json',rt.private,active['approval_sha256'])
        must(prior.get('schema')=='R25_MANAGED_VMM_CORE_FOLLOWUP_BINDING_V1' and
             sha(old['operation-guard'])==sha(sources['core_release_guard.py'])==prior['files']['core_release_guard.py']['after_sha256'],
             'managed_core_formal_guard_baseline')
    else:must(sha(old['operation-guard'])==BASE_GUARD,'installed_control_baseline')

def prepare(rt, request_sha):
    request=json_read(rt.store/'REQUEST.json',rt.private,request_sha)
    must(set(request)=={'schema','deploy','review_evidence_sha256'} and request['schema']=='R25_VMM_CONTROLLER_REQUEST_V1','request_fields')
    review=json_read(rt.store/'REVIEW.json',rt.private,digest(request['review_evidence_sha256']))
    must(type(review)is dict and bool(review),'review_evidence_required')
    folder,sources=verify_bundle(rt,request['deploy'])
    with rt.lease():
        preserved=rt.require_context(); targets=rt.targets(); before={}
        for name,(path,owner,mode) in targets.items():
            before[name]=read(path,owner,mode) if path.exists() else None
            must(before[name]is not None if name in {'lifecycle','publisher-guard','operation-guard','handoff'} else before[name]is None,'installed_target_precondition')
        verify_baseline_controls(rt,before,sources)
        after={name:sources[name] for name in ('vmm_controller.py','vmm_lifecycle.py','vmm_service.py')}
        after.update({'publisher-guard':sources['core_release_guard.py'],'operation-guard':sources['core_release_guard.py'],
            'lifecycle':renderer(folder,sources,before['lifecycle']), 'role':encoded({'schema':'R25_VMM_ROLE_V1','enabled':False})})
        after['handoff']=render_handoff(before['handoff'],rt,after)
        plan={'schema':SCHEMA,'scope':'CONTROLLER_ONLY_DISABLED_ROLE','deploy':request['deploy'],
              'review_evidence_sha256':request['review_evidence_sha256'],'private_owner':list(rt.private),'controller_owner':list(rt.scripts),
              'preserved':preserved,'files':{name:{'before_sha256':sha(before[name]) if before[name]is not None else None,
                  'after_sha256':sha(after[name])} for name in targets}}
        raw=encoded(plan);p=rt.store/'plans'/digest(request_sha);rt.mkdir(p)
        for name,data in [('UNSIGNED_PLAN.json',raw),*[(name+'.after',value) for name,value in after.items()]]:
            dest=p/name
            if dest.exists():must(read(dest,rt.private)==data,'prepared_plan_conflict')
            else:rt.atomic(dest,data)
        return {'outcome':'PREPARED_UNSIGNED','plan_sha256':sha(raw),'request_sha256':request_sha,'plan_path':str(p/'UNSIGNED_PLAN.json')}


def validate_plan(rt, plan):
    must(type(plan)is dict and set(plan)=={'schema','scope','deploy','review_evidence_sha256','private_owner','controller_owner','preserved','files'} and
         plan['schema']==SCHEMA and plan['scope']=='CONTROLLER_ONLY_DISABLED_ROLE','plan_fields')
    must(plan['private_owner']==list(rt.private) and plan['controller_owner']==list(rt.scripts),'plan_owners')
    must(set(plan['files'])==set(rt.targets()) and set(plan['preserved'])==set(rt.preserved()),'plan_inventory')
    for entry in plan['files'].values():
        must(set(entry)=={'before_sha256','after_sha256'},'plan_file_fields')
        digest(entry['after_sha256'])
        if entry['before_sha256'] is not None:digest(entry['before_sha256'])
    for item in plan['preserved'].values():digest(item)
    digest(plan['review_evidence_sha256'])
    return plan


def load_plan(rt, ident, recover=False):
    digest(ident);folder=rt.store/'transactions'/ident
    path=folder/'approval.json' if recover and (folder/'approval.json').exists() else rt.store/'APPROVAL.json'
    plan=validate_plan(rt,json_read(path,rt.private,ident))
    review_path=folder/'review.json' if recover and (folder/'review.json').exists() else rt.store/'REVIEW.json'
    must(type(json_read(review_path,rt.private,plan['review_evidence_sha256']))is dict,'review_evidence')
    return plan,folder


def verify_files(rt,plan,mode,*,role_overrides=False,runtime=False):
    overrides=role_history(rt,plan,sha(encoded(plan))) if role_overrides else None
    if overrides:read(rt.root/'runtime/config/vmm-service.json',rt.private,expected=overrides['service'])
    for name,(path,owner,permissions) in rt.targets().items():
        e=plan['files'][name]
        if runtime and name=='handoff':
            folder=rt.store/'transactions'/sha(encoded(plan))
            expected=read(folder/'after/handoff',rt.private,expected=e['after_sha256'])
            if overrides:
                marker=json_read(rt.store/'ROLE_ACTIVE.json',rt.private)
                expected=read(role_transaction(rt,marker['approval_sha256'])/'handoff.after',rt.private,expected=overrides['handoff'])
            must(control_handoff_values(read(path,owner,permissions))==control_handoff_values(expected),'runtime_handoff_control_drift')
            continue
        current=sha(read(path,owner,permissions)) if path.exists() else None
        allowed={overrides[name] if overrides and name in overrides else e['after_sha256']} if mode=='after' else {e['before_sha256']} if mode=='before' else set(e.values())
        must(current in allowed,'target_drift:'+name)


def before_after(rt,plan,folder,sources,bundle):
    old={}
    for name,(path,owner,mode) in rt.targets().items():
        previous=folder/'before'/name
        old[name]=(read(previous,rt.private,expected=plan['files'][name]['before_sha256']) if previous.exists()
                   else read(path,owner,mode,plan['files'][name]['before_sha256']) if plan['files'][name]['before_sha256'] else None)
    verify_baseline_controls(rt,old,sources)
    after={n:sources[n] for n in ('vmm_controller.py','vmm_lifecycle.py','vmm_service.py')}
    after.update({'publisher-guard':sources['core_release_guard.py'],'operation-guard':sources['core_release_guard.py'],
        'lifecycle':renderer(bundle,sources,old['lifecycle']),'role':encoded({'schema':'R25_VMM_ROLE_V1','enabled':False})})
    after['handoff']=render_handoff(old['handoff'],rt,after)
    must(all(sha(after[n])==plan['files'][n]['after_sha256'] for n in after),'approved_render_mismatch')
    return old,after


def journal(rt,folder,ident):
    value=json_read(folder/'journal.json',rt.private)
    must(set(value)=={'schema','approval_sha256','phase'} and value['schema']==SCHEMA and value['approval_sha256']==ident and value['phase']in PHASES,'journal_identity')
    return value


def preflight(rt,ident):
    plan,folder=load_plan(rt,ident);bundle,sources=verify_bundle(rt,plan['deploy'])
    with rt.lease():
        rt.require_context(plan['preserved']);verify_files(rt,plan,'before')
        before_after(rt,plan,folder,sources,bundle)
    return {'outcome':'PREFLIGHT_PASS','approval_sha256':ident,'enabled':False}


def bind(rt,ident,*,fault=None):
    plan,folder=load_plan(rt,ident);bundle,sources=verify_bundle(rt,plan['deploy'])
    # All identity/scope checks before any operational pause.
    rt.require_context(plan['preserved'])
    if (folder/'journal.json').exists():
        phase=journal(rt,folder,ident)['phase']
        if phase=='INSTALLED':
            verify_files(rt,plan,'after');return {'outcome':'ALREADY_INSTALLED','enabled':False}
        must(phase in {'PREPARED','RESTORED'},'recover_original_transaction_required')
    verify_files(rt,plan,'before');old,after=before_after(rt,plan,folder,sources,bundle)
    safe_to_resume=False
    try:
        rt.supervise('stop')
        with rt.lease():
            rt.require_context(plan['preserved']);verify_files(rt,plan,'before')
            rt.mkdir(folder/'before');rt.mkdir(folder/'after')
            for name,raw in after.items():
                target=folder/'after'/name
                if target.exists():must(read(target,rt.private)==raw,'after_snapshot_drift')
                else:rt.atomic(target,raw)
            for name,raw in old.items():
                if raw is None:continue
                path=folder/'before'/name
                if path.exists():must(read(path,rt.private)==raw,'preimage_drift')
                else:rt.atomic(path,raw)
            for name,raw in [('approval.json',encoded(plan)),('review.json',read(rt.store/'REVIEW.json',rt.private,expected=plan['review_evidence_sha256']))]:
                path=folder/name
                if path.exists():must(read(path,rt.private)==raw,'transaction_record_drift')
                else:rt.atomic(path,raw)
            rt.write_state(folder/'journal.json',{'schema':SCHEMA,'approval_sha256':ident,'phase':'PREPARED'})
            active=rt.store/'ACTIVE.json'
            if active.exists():must(json_read(active,rt.private)=={'schema':SCHEMA,'approval_sha256':ident},'another_vmm_transaction')
            else:rt.atomic(active,encoded({'schema':SCHEMA,'approval_sha256':ident}))
            rt.write_state(folder/'journal.json',{'schema':SCHEMA,'approval_sha256':ident,'phase':'INSTALL_INTENT'})
            try:
                for name,(path,owner,mode) in rt.targets().items():
                    rt.atomic(path,after[name],before=plan['files'][name]['before_sha256'],owner=owner,mode=mode)
                    if fault:fault(name)
                rt.require_context(plan['preserved']);verify_files(rt,plan,'after')
                rt.write_state(folder/'journal.json',{'schema':SCHEMA,'approval_sha256':ident,'phase':'INSTALLED'})
                safe_to_resume=True
            except BaseException:
                rt.write_state(folder/'journal.json',{'schema':SCHEMA,'approval_sha256':ident,'phase':'RECOVERY_REQUIRED'})
                raise
    except BaseException:
        try:
            rt.require_context(plan['preserved']);verify_files(rt,plan,'before');safe_to_resume=True
        except (Refused,OSError,ValueError,KeyError):pass
        raise
    finally:
        if safe_to_resume:rt.supervise('start')
    return {'outcome':'INSTALLED','approval_sha256':ident,'enabled':False}


def recover(rt,ident,*,rollback=False):
    digest(ident);folder=rt.store/'transactions'/ident
    if not (folder/'journal.json').exists():return {'outcome':'NOT_STARTED'}
    plan,folder=load_plan(rt,ident,True);verify_bundle(rt,plan['deploy']);phase=journal(rt,folder,ident)['phase']
    rt.require_context(plan['preserved'],runtime=phase=='INSTALLED' and not rollback)
    if phase=='INSTALLED' and not rollback:
        verify_files(rt,plan,'after',role_overrides=True);rt.supervise('start');return {'outcome':'ALREADY_INSTALLED_SUPERVISION_RESTORED'}
    if rollback:
        must(phase=='INSTALLED','rollback_requires_installed');require_role_stopped(rt)
        must(not (rt.store/'ROLE_ACTIVE.json').exists(),'restore_role_binding_first')
        rt.supervise('stop')
    if phase=='RESTORED':verify_files(rt,plan,'before');rt.supervise('start');return {'outcome':'ALREADY_RESTORED'}
    verify_files(rt,plan,'mixed')
    # Original supported stop entry can validate mixed lifecycle metadata only
    # after restoring handoff/lifecycle as a consistent pair under this lease;
    # binding already stopped supervision. Refuse a live watchdog, do not kill it.
    with rt.lease():
        rt.require_context(plan['preserved']);verify_files(rt,plan,'mixed');rt.require_watchdog_stopped()
        rt.write_state(folder/'journal.json',{'schema':SCHEMA,'approval_sha256':ident,'phase':'RESTORE_INTENT'})
        for name,(path,owner,mode) in reversed(list(rt.targets().items())):
            e=plan['files'][name];current=sha(read(path,owner,mode)) if path.exists() else None
            if current==e['before_sha256']:continue
            if e['before_sha256'] is None:
                read(path,owner,mode,e['after_sha256']);path.unlink()
                fd=os.open(path.parent,os.O_DIRECTORY|os.O_NOFOLLOW)
                try:os.fsync(fd)
                finally:os.close(fd)
            else:rt.atomic(path,read(folder/'before'/name,rt.private,expected=e['before_sha256']),before=e['after_sha256'],owner=owner,mode=mode)
        verify_files(rt,plan,'before');rt.require_context(plan['preserved'])
        rt.write_state(folder/'journal.json',{'schema':SCHEMA,'approval_sha256':ident,'phase':'RESTORED'})
    rt.supervise('start')
    return {'outcome':'RESTORED','approval_sha256':ident}


def apply_policy_overlay(value, handoff, root=ROOT, *, fixture_runtime=None):
    """Verified effective control hashes; never rewrite original policy/records."""
    if 'R25_VMM_CONTROLLER' not in handoff:return value
    rt=fixture_runtime or Runtime(root,LOCK)
    must(handoff['R25_VMM_CONTROLLER']==str(rt.targets()['vmm_controller.py'][0]),'overlay_controller_path')
    controller=read(rt.targets()['vmm_controller.py'][0],rt.private,expected=digest(handoff.get('R25_VMM_CONTROLLER_SHA256')))
    active=json_read(rt.store/'ACTIVE.json',rt.private);ident=digest(active.get('approval_sha256'))
    must(active=={'schema':SCHEMA,'approval_sha256':ident},'overlay_active')
    folder=rt.store/'transactions'/ident
    plan=validate_plan(rt,json_read(folder/'approval.json',rt.private,expected=ident));verify_bundle(rt,plan['deploy'])
    json_read(folder/'review.json',rt.private,expected=plan['review_evidence_sha256'])
    must(journal(rt,folder,ident)['phase']=='INSTALLED','overlay_not_installed')
    rt.require_context(plan['preserved'],runtime=True);verify_files(rt,plan,'after',role_overrides=True,runtime=True)
    must(sha(controller)==plan['files']['vmm_controller.py']['after_sha256'],'overlay_self_identity')
    must(sha(read(rt.preserved()['policy'],rt.private))==plan['preserved']['policy'],'overlay_policy_drift')
    result=copy.deepcopy(value)
    must(result['controllers']['lifecycle_adapter.py']==plan['files']['lifecycle']['before_sha256'] and
         result['controllers']['core_release_guard.py']==plan['files']['operation-guard']['before_sha256'] and
         result['runtime_modules']['core_release_guard.py']==plan['files']['publisher-guard']['before_sha256'],'overlay_base_control_bindings')
    result['controllers']['lifecycle_adapter.py']=plan['files']['lifecycle']['after_sha256']
    result['controllers']['core_release_guard.py']=plan['files']['operation-guard']['after_sha256']
    result['runtime_modules']['core_release_guard.py']=plan['files']['publisher-guard']['after_sha256']
    return result


def control_handoff_values(raw):
    values=env_values(raw)
    return {k:v for k,v in values.items() if k.startswith('R25_VMM_') or
            k in {'R25_LIFECYCLE_ADAPTER_SHA256','R25_CORE_RELEASE_GUARD_SHA256'}}


ROLE_BINDING_SCHEMA = 'R25_VMM_ROLE_BINDING_PLAN_V1'


def validate_role_documents(rt, role, service, service_sha, script_sha):
    keys={'schema','enabled','service_binding','service_binding_sha256','service_script_sha256',
          'interpreter','interpreter_sha256','restart_limit','startup_seconds','stop_seconds'}
    must(type(role)is dict and set(role)==keys and role['schema']=='R25_VMM_ROLE_V1' and role['enabled']is False,'role_binding_fields')
    must(role['service_binding']==str(rt.root/'runtime/config/vmm-service.json') and
         role['service_binding_sha256']==service_sha and role['service_script_sha256']==script_sha,'role_service_identity')
    must(role['interpreter']=='/root/autodl-tmp/auralink/micromamba/envs/auralink-ai/bin/python','role_interpreter')
    digest(role['interpreter_sha256'])
    for key,minimum,maximum in [('restart_limit',0,3),('startup_seconds',1,900),('stop_seconds',1,30)]:
        must(type(role[key])is int and minimum<=role[key]<=maximum,'role_finite_limits')
    required={'schema','host','port','legacy_commit','legacy_hashes','source_root','token_file','state_dir',
              'output_root','checkpoint','musicgen_path','model_assets'}
    must(type(service)is dict and required<=set(service)<=required|{'cuda_visible_devices'},'service_binding_fields')
    must(service['schema']=='R25_VMM_SERVICE_BINDING_V1' and service['host']=='127.0.0.1' and service['port']==5001 and
         service['legacy_commit']=='b115f0724e16f16128563dc996c5a10082783074' and
         service['legacy_hashes']=={'VMM/app.py':'d604e9e11943607593916f772c26bef6ba4d2014753d1d4ef9eff6a560e44f22',
             'VMM/duration_contract.py':'481f9bfb54ca2c3e94d70dc2520846112aad430d7ea6db9501cc83c3372b1e9f',
             'CLIP/clip/clip.py':'9540f200fbf8145479fa655382a56dab048d238cc698b9cbd8df3b6d86d3f1b6'},'service_legacy_identity')
    must(service['source_root']=='/root/autodl-tmp/auralink' and service['state_dir']==str(rt.root/'runtime/vmm') and
         service['token_file']==str(rt.root/'runtime/config/vmm-token') and
         service['output_root']=='/root/autodl-tmp/auralink/backend/temp_uploads' and
         service['checkpoint']=='/root/autodl-tmp/auralink/VMM/models/final_model.pth' and
         service['musicgen_path']=='/root/autodl-tmp/auralink/VMM/models/musicgen-small','service_fixed_paths')
    if 'cuda_visible_devices' in service:must(type(service['cuda_visible_devices'])is str and re.fullmatch('[0-9]+(?:,[0-9]+)*',service['cuda_visible_devices']),'device_binding')
    assets=service['model_assets'];must(type(assets)is list and len(assets)==8,'model_asset_inventory')
    names=set()
    for item in assets:
        must(type(item)is dict and set(item)=={'path','sha256'},'model_asset_fields');digest(item['sha256']);names.add(item['path'])
    expected={service['musicgen_path']+'/'+x for x in ('compression_state_dict.bin','state_dict.bin','config.json','spiece.model','tokenizer.json','tokenizer_config.json')}|{service['checkpoint'],'/root/.cache/clip/ViT-B-32.pt'}
    must(names==expected,'model_asset_paths')
    must(next(x['sha256'] for x in assets if x['path']=='/root/.cache/clip/ViT-B-32.pt')=='40d365715913c9da98579312b702a82c18be219cc2a73407c4526f58eba950af','clip_offline_binding')


def role_paths(rt):
    return {'role':rt.root/'runtime/config/vmm-role.json',
            'service':rt.root/'runtime/config/vmm-service.json','handoff':rt.root/'runtime/handoff-control.env'}


def role_transaction(rt,ident):
    return rt.store/'role-bindings'/digest(ident)


def validate_role_plan(rt,plan):
    fields={'schema','controller_approval_sha256','previous_role_approval_sha256','review_evidence_sha256','files'}
    must(type(plan)is dict and set(plan)==fields and plan['schema']==ROLE_BINDING_SCHEMA and set(plan['files'])==set(role_paths(rt)),'role_plan_fields')
    digest(plan['controller_approval_sha256']);digest(plan['review_evidence_sha256'])
    if plan['previous_role_approval_sha256']is not None:digest(plan['previous_role_approval_sha256'])
    for name,e in plan['files'].items():
        must(set(e)=={'before_sha256','after_sha256'},'role_plan_file')
        digest(e['after_sha256'])
        if e['before_sha256']is not None:digest(e['before_sha256'])
        else:must(name=='service','role_missing_preimage')
    return plan


def role_history(rt,baseplan,baseid,ident=None):
    active=rt.store/'ROLE_ACTIVE.json'
    if ident is None:
        if not active.exists():return None
        marker=json_read(active,rt.private);must(set(marker)=={'schema','approval_sha256'} and marker['schema']==ROLE_BINDING_SCHEMA,'role_active_fields');ident=digest(marker['approval_sha256'])
    seen=set();chain=[]
    while ident is not None:
        must(ident not in seen and len(seen)<32,'role_chain_cycle_or_limit');seen.add(ident)
        folder=role_transaction(rt,ident);plan=validate_role_plan(rt,json_read(folder/'approval.json',rt.private,ident))
        must(plan['controller_approval_sha256']==baseid,'role_controller_binding')
        j=json_read(folder/'journal.json',rt.private)
        must(j=={'schema':ROLE_BINDING_SCHEMA,'approval_sha256':ident,'phase':'INSTALLED'},'role_not_installed')
        json_read(folder/'review.json',rt.private,plan['review_evidence_sha256'])
        role=json_read(folder/'role.after',rt.private,plan['files']['role']['after_sha256']);service=json_read(folder/'service.after',rt.private,plan['files']['service']['after_sha256'])
        validate_role_documents(rt,role,service,plan['files']['service']['after_sha256'],baseplan['files']['vmm_service.py']['after_sha256'])
        old=read(folder/'handoff.before',rt.private,expected=plan['files']['handoff']['before_sha256'])
        value=env_values(old);must('R25_VMM_ROLE_SHA256'in value,'role_handoff_binding')
        updated=old.replace(('R25_VMM_ROLE_SHA256='+value['R25_VMM_ROLE_SHA256']+'\n').encode(),('R25_VMM_ROLE_SHA256='+plan['files']['role']['after_sha256']+'\n').encode())
        must(sha(updated)==plan['files']['handoff']['after_sha256'] and read(folder/'handoff.after',rt.private,expected=plan['files']['handoff']['after_sha256'])==updated,'role_handoff_other_changes')
        chain.append(plan);ident=plan['previous_role_approval_sha256']
    previous={'role':baseplan['files']['role']['after_sha256'],'handoff':baseplan['files']['handoff']['after_sha256'],'service':None}
    previous_hand=read(rt.store/'transactions'/baseid/'after/handoff',rt.private,expected=previous['handoff'])
    for plan in reversed(chain):
        must(all(plan['files'][k]['before_sha256']==previous[k] for k in ('role','service')),'role_chain_preimage')
        currentfolder=role_transaction(rt,sha(encoded(plan)))
        current_hand=read(currentfolder/'handoff.before',rt.private,expected=plan['files']['handoff']['before_sha256'])
        must(control_handoff_values(current_hand)==control_handoff_values(previous_hand),'role_chain_handoff_controls')
        previous_hand=read(currentfolder/'handoff.after',rt.private,expected=plan['files']['handoff']['after_sha256'])
        previous={k:plan['files'][k]['after_sha256'] for k in previous}
    return previous


def require_role_stopped(rt):
    state=rt.root/'runtime/vmm/lifecycle.json'
    if state.exists():
        value=json_read(state,rt.private)
        must(value.get('enabled')is False and value.get('desired')=='STOPPED','role_must_be_disabled_stopped')
        saved=value.get('process')
        if saved:
            pid=saved.get('pid');must(type(pid)is int and pid>1,'role_process_record')
            try:raw=(Path('/proc')/str(pid)/'stat').read_text();tail=raw[raw.rfind(')')+2:].split()
            except FileNotFoundError:tail=None
            must(tail is None or tail[0]=='Z','role_process_still_alive')
    gate=rt.root/'runtime/vmm/gate.json'
    if gate.exists():
        value=json_read(gate,rt.private)
        must(value.get('mode')=='CLOSED' and type(value.get('requests'))is dict and
             not any(x.get('state')=='DISPATCHED' or (x.get('state')=='UNKNOWN' and not x.get('acknowledged')) for x in value['requests'].values()),'role_dispatch_or_uncertainty_active')


def prepare_role(rt,request_sha):
    request=json_read(rt.store/'ROLE_REQUEST.json',rt.private,request_sha)
    must(set(request)=={'schema','controller_approval_sha256','review_evidence_sha256','role_sha256','service_sha256'} and
         request['schema']=='R25_VMM_ROLE_BINDING_REQUEST_V1','role_request')
    baseid=digest(request['controller_approval_sha256']);baseplan,_=load_plan(rt,baseid,True)
    must(journal(rt,rt.store/'transactions'/baseid,baseid)['phase']=='INSTALLED','controller_not_installed')
    verify_bundle(rt,baseplan['deploy']);json_read(rt.store/'REVIEW.json',rt.private,request['review_evidence_sha256'])
    role_raw=read(rt.store/'ROLE.next.json',rt.private,expected=request['role_sha256']);service_raw=read(rt.store/'SERVICE.next.json',rt.private,expected=request['service_sha256'])
    validate_role_documents(rt,json.loads(role_raw),json.loads(service_raw),sha(service_raw),baseplan['files']['vmm_service.py']['after_sha256'])
    with rt.lease():
        verify_files(rt,baseplan,'after',role_overrides=True,runtime=True);require_role_stopped(rt)
        before={key:read(path,rt.private) if path.exists() else None for key,path in role_paths(rt).items()}
        prior=json_read(rt.store/'ROLE_ACTIVE.json',rt.private)['approval_sha256'] if (rt.store/'ROLE_ACTIVE.json').exists() else None
        values=env_values(before['handoff']);oldline=('R25_VMM_ROLE_SHA256='+values['R25_VMM_ROLE_SHA256']+'\n').encode();must(before['handoff'].count(oldline)==1,'role_handoff_binding')
        after={'role':role_raw,'service':service_raw,'handoff':before['handoff'].replace(oldline,('R25_VMM_ROLE_SHA256='+sha(role_raw)+'\n').encode())}
        plan={'schema':ROLE_BINDING_SCHEMA,'controller_approval_sha256':baseid,'previous_role_approval_sha256':prior,
              'review_evidence_sha256':request['review_evidence_sha256'],'files':{key:{'before_sha256':sha(before[key]) if before[key]is not None else None,'after_sha256':sha(after[key])} for key in before}}
        folder=rt.store/'role-plans'/request_sha;rt.mkdir(folder)
        records={'UNSIGNED_PLAN.json':encoded(plan),**{key+'.after':raw for key,raw in after.items()}}
        for key,raw in before.items():
            if raw is not None:records[key+'.before']=raw
        for key,raw in records.items():
            path=folder/key
            if path.exists():must(read(path,rt.private)==raw,'role_prepared_conflict')
            else:rt.atomic(path,raw)
    return {'outcome':'ROLE_PREPARED_UNSIGNED','plan_sha256':sha(encoded(plan)),'plan_path':str(folder/'UNSIGNED_PLAN.json'),'request_sha256':request_sha}


def _bind_role(rt,ident,*,fault=None):
    plan=validate_role_plan(rt,json_read(rt.store/'ROLE_APPROVAL.json',rt.private,ident));folder=role_transaction(rt,ident)
    baseplan,_=load_plan(rt,plan['controller_approval_sha256'],True);verify_bundle(rt,baseplan['deploy'])
    paths=role_paths(rt);after={'role':read(rt.store/'ROLE.next.json',rt.private,expected=plan['files']['role']['after_sha256']),
        'service':read(rt.store/'SERVICE.next.json',rt.private,expected=plan['files']['service']['after_sha256'])}
    validate_role_documents(rt,json.loads(after['role']),json.loads(after['service']),sha(after['service']),baseplan['files']['vmm_service.py']['after_sha256'])
    review=read(rt.store/'REVIEW.json',rt.private,expected=plan['review_evidence_sha256'])
    with rt.lease():
        require_role_stopped(rt);verify_files(rt,baseplan,'after',role_overrides=True,runtime=True)
        if (folder/'journal.json').exists():
            j=json_read(folder/'journal.json',rt.private)
            must(j['phase'] in {'RESTORED','INSTALLED'},'recover_original_role_transaction')
            if j['phase']=='INSTALLED':return {'outcome':'ROLE_ALREADY_INSTALLED','enabled':False}
        current=json_read(rt.store/'ROLE_ACTIVE.json',rt.private)['approval_sha256'] if (rt.store/'ROLE_ACTIVE.json').exists() else None
        must(current==plan['previous_role_approval_sha256'],'role_active_drift')
        before={key:read(path,rt.private) if path.exists() else None for key,path in paths.items()}
        must(all((sha(raw)if raw is not None else None)==plan['files'][key]['before_sha256'] for key,raw in before.items()),'role_before_drift')
        old=env_values(before['handoff'])['R25_VMM_ROLE_SHA256'];after['handoff']=before['handoff'].replace(('R25_VMM_ROLE_SHA256='+old+'\n').encode(),('R25_VMM_ROLE_SHA256='+sha(after['role'])+'\n').encode())
        must(sha(after['handoff'])==plan['files']['handoff']['after_sha256'],'role_handoff_drift')
        rt.mkdir(folder)
        for key,raw in {'approval.json':encoded(plan),'review.json':review,**{k+'.after':x for k,x in after.items()},**{k+'.before':x for k,x in before.items() if x is not None}}.items():
            target=folder/key
            if target.exists():must(read(target,rt.private)==raw,'role_transaction_drift')
            else:rt.atomic(target,raw)
        rt.write_state(folder/'journal.json',{'schema':ROLE_BINDING_SCHEMA,'approval_sha256':ident,'phase':'INSTALL_INTENT'})
        try:
            for key,path in paths.items():
                rt.atomic(path,after[key],before=plan['files'][key]['before_sha256'])
                if fault:fault(key)
            rt.write_state(folder/'journal.json',{'schema':ROLE_BINDING_SCHEMA,'approval_sha256':ident,'phase':'INSTALLED'})
            rt.write_state(rt.store/'ROLE_ACTIVE.json',{'schema':ROLE_BINDING_SCHEMA,'approval_sha256':ident})
        except BaseException:
            rt.write_state(folder/'journal.json',{'schema':ROLE_BINDING_SCHEMA,'approval_sha256':ident,'phase':'RECOVERY_REQUIRED'});raise
    return {'outcome':'ROLE_INSTALLED_DISABLED','approval_sha256':ident,'enabled':False}


def _recover_role(rt,ident):
    folder=role_transaction(rt,ident)
    if not (folder/'journal.json').exists():return {'outcome':'ROLE_NOT_STARTED'}
    plan=validate_role_plan(rt,json_read(folder/'approval.json',rt.private,ident));baseplan,_=load_plan(rt,plan['controller_approval_sha256'],True)
    verify_bundle(rt,baseplan['deploy']);json_read(folder/'review.json',rt.private,plan['review_evidence_sha256'])
    with rt.lease():
        require_role_stopped(rt);rt.require_watchdog_stopped()
        j=json_read(folder/'journal.json',rt.private);must(j.get('schema')==ROLE_BINDING_SCHEMA and j.get('approval_sha256')==ident and j.get('phase')in PHASES,'role_journal')
        if j['phase']=='RESTORED':return {'outcome':'ROLE_ALREADY_RESTORED'}
        active=rt.store/'ROLE_ACTIVE.json';current=json_read(active,rt.private)['approval_sha256'] if active.exists() else None
        must(current in {ident,plan['previous_role_approval_sha256']},'role_recovery_active_drift')
        for key,path in role_paths(rt).items():
            observed=sha(read(path,rt.private)) if path.exists() else None;must(observed in set(plan['files'][key].values()),'role_foreign_drift')
        rt.write_state(folder/'journal.json',{'schema':ROLE_BINDING_SCHEMA,'approval_sha256':ident,'phase':'RESTORE_INTENT'})
        for key,path in role_paths(rt).items():
            e=plan['files'][key];observed=sha(read(path,rt.private)) if path.exists() else None
            if observed==e['before_sha256']:continue
            if e['before_sha256']is None:
                read(path,rt.private,expected=e['after_sha256']);path.unlink()
                fd=os.open(path.parent,os.O_DIRECTORY|os.O_NOFOLLOW)
                try:os.fsync(fd)
                finally:os.close(fd)
            else:rt.atomic(path,read(folder/(key+'.before'),rt.private,expected=e['before_sha256']),before=e['after_sha256'])
        if current==ident:
            if plan['previous_role_approval_sha256']is None:active.unlink()
            else:rt.write_state(active,{'schema':ROLE_BINDING_SCHEMA,'approval_sha256':plan['previous_role_approval_sha256']})
        rt.write_state(folder/'journal.json',{'schema':ROLE_BINDING_SCHEMA,'approval_sha256':ident,'phase':'RESTORED'})
    return {'outcome':'ROLE_RESTORED','approval_sha256':ident}



def bind_role(rt,ident,*,fault=None):
    plan=validate_role_plan(rt,json_read(rt.store/'ROLE_APPROVAL.json',rt.private,ident))
    baseplan,_=load_plan(rt,plan['controller_approval_sha256'],True)
    verify_bundle(rt,baseplan['deploy']);require_role_stopped(rt)
    try:
        rt.supervise('stop')
        result=_bind_role(rt,ident,fault=fault)
    except BaseException:
        folder=role_transaction(rt,ident)
        if (folder/'journal.json').exists():_recover_role(rt,ident)
        else:
            must(all((sha(read(path,rt.private)) if path.exists() else None)==plan['files'][key]['before_sha256']
                     for key,path in role_paths(rt).items()),'role_failed_stop_context_drift')
        rt.supervise('start');raise
    rt.supervise('start');return result


def recover_role(rt,ident):
    folder=role_transaction(rt,ident)
    if not (folder/'journal.json').exists():return {'outcome':'ROLE_NOT_STARTED'}
    require_role_stopped(rt)
    try:
        rt.supervise('stop')
        result=_recover_role(rt,ident)
    except BaseException:
        plan=validate_role_plan(rt,json_read(folder/'approval.json',rt.private,ident))
        if all((sha(read(path,rt.private)) if path.exists() else None)==plan['files'][key]['before_sha256']
               for key,path in role_paths(rt).items()):rt.supervise('start')
        raise
    rt.supervise('start');return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['prepare','preflight','bind','recover','rollback','prepare-role','bind-role','recover-role'])
    parser.add_argument('--request-sha256');parser.add_argument('--approval-sha256');args=parser.parse_args()
    try:
        must(os.geteuid()==0 and sys.platform.startswith('linux'),'root_linux_required')
        rt=Runtime();ident=args.request_sha256 if args.command in {'prepare','prepare-role'} else args.approval_sha256;digest(ident)
        if args.command.endswith('-role'):
            if args.command=='recover-role' and not (role_transaction(rt,ident)/'journal.json').exists():
                print(json.dumps({'outcome':'ROLE_NOT_STARTED'}));return 0
            rolep=(json_read(role_transaction(rt,ident)/'approval.json',expected=ident) if args.command=='recover-role' else
                   json_read(rt.store/('ROLE_REQUEST.json' if args.command=='prepare-role' else 'ROLE_APPROVAL.json')))
            p=load_plan(rt,digest(rolep['controller_approval_sha256']),True)[0]
            folder,_=verify_bundle(rt,p['deploy']);must(Path(__file__).absolute()==folder/'vmm_controller.py','formal_execution_path')
        elif args.command!='recover' or (rt.store/'transactions'/ident/'journal.json').exists():
            p=(load_plan(rt,ident,True)[0] if args.command in {'recover','rollback'} else
               json_read(rt.store/('REQUEST.json' if args.command=='prepare' else 'APPROVAL.json')))
            folder,_=verify_bundle(rt,p['deploy']);must(Path(__file__).absolute()==folder/'vmm_controller.py','formal_execution_path')
        result={'prepare':prepare,'preflight':preflight,'bind':bind,'recover':recover,'rollback':lambda r,i:recover(r,i,rollback=True),
                'prepare-role':prepare_role,'bind-role':bind_role,'recover-role':recover_role}[args.command](rt,ident)
        print(json.dumps(result));return 0
    except (Refused,OSError,ValueError,KeyError,zipfile.BadZipFile) as e:
        print(json.dumps({'outcome':'VMM_CONTROLLER_REFUSED','reason':str(e) if isinstance(e,Refused) else type(e).__name__}));return 65

if __name__=='__main__':sys.dont_write_bytecode=True;raise SystemExit(main())
