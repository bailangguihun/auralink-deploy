#!/usr/bin/env python3
"""External, explicitly approved catalog data repair. Never installed in consumer.

One submission, existing CoreRuntime shared lease, unchanged-artifact resume.
No VMM lifecycle command, provider call, refund, database restore or retry.
An interrupted write is reconciled by read-only pre/postimage checks.
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

ROOT=Path('/root/autodl-tmp/artlive-r25-native-pg16/handoff-production-20260923T062101Z')
LOCK=Path('/root/autodl-tmp/auralink-web-deploy/runtime/frontend-deployment.lock')
SCHEMA='CATALOG_PROJECTION_TRANSACTION_V1'


def must(ok, reason):
    if not ok: raise RuntimeError(reason)


def sha(raw): return hashlib.sha256(raw).hexdigest()


def private(path, expected=None, *, mode=0o600, owner=(0,0), limit=32*1024*1024):
    path=Path(path);must(path.is_absolute() and '..' not in path.parts,'absolute_private_path')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=child
        file=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        try:
            before=os.fstat(file)
            must(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size<=limit,
                 'private_regular_single_link_size:'+str(path))
            must((before.st_uid,before.st_gid)==owner and stat.S_IMODE(before.st_mode)==mode,
                 'private_owner_mode:'+str(path))
            parts=[];size=0
            while True:
                raw=os.read(file,1024*1024)
                if not raw:break
                size+=len(raw);must(size<=limit,'private_size');parts.append(raw)
            after=os.fstat(file)
            must((before.st_size,before.st_mtime_ns,before.st_ctime_ns)==(after.st_size,after.st_mtime_ns,after.st_ctime_ns),'private_changed')
            raw=b''.join(parts);must(expected is None or sha(raw)==expected,'private_digest:'+str(path));return raw
        finally:os.close(file)
    finally:os.close(fd)


class Sources(importlib.abc.MetaPathFinder,importlib.abc.Loader):
    """Compile the exact validated bytes, never a sibling .pyc or unbound copy."""
    def __init__(self, files):self.files=files
    def find_spec(self, fullname, path=None, target=None):
        return importlib.util.spec_from_loader(fullname,self) if fullname in self.files else None
    def create_module(self,spec):return None
    def exec_module(self,module):
        x=self.files[module.__name__];raw=private(x['path'],x['sha256'])
        module.__file__=x['path'];exec(compile(raw,x['path'],'exec'),module.__dict__)


def approval_text(digest,count):
    return (f'我批准目录 Creation 投影修复计划 SHA-256：{digest}。仅补齐清单中的{count}条完全缺失投影，'
            '保留原UUID及全部既有数据；使用原共享锁，确认VMM停用、STOPPED、CLOSED并无在途工作后，'
            '受控停止原Java和watchdog，完成一致性备份，单事务最多提交一次，随后恢复原版本与健康检查。'
            '部分、冲突或漂移即拒绝；不覆盖、不退款、不重放、不恢复历史数据库快照。'
            '不授权VMM启动、OPEN、部署或任何生成；不确定结果只读核对，必要时保持维护状态并报告。')


def require_prior_repairs_terminal(parent, read):
    if not parent.exists():return
    for folder in parent.iterdir():
        must(folder.is_dir() and not folder.is_symlink(),'repair_namespace_entry')
        record=folder/'RECORD.json'
        must(record.exists(),'prior_repair_record_missing_recovery_required')
        value=read(record)
        must(value.get('schema')==SCHEMA and value.get('phase') in
             ('COMMITTED_VERIFIED_RESUMED','NO_COMMIT_VERIFIED_RESUMED'),
             'prior_repair_unknown_or_recovery_required')


def execute(backend, *, recover=False):
    """Backend methods are independently fault-tested; caller holds the lease."""
    if recover:
        backend.load_consumption()
    else:
        backend.preflight()
        backend.consume()  # durable before first service mutation; never refunded
    resuming=False
    try:
        if not recover:
            backend.quiesce()
            backend.backup()
            backend.save('SQL_SUBMISSION_INTENT')
            backend.submit_once()
        state=backend.reconcile()
        backend.save(state)
        if state=='UNKNOWN':return state  # do not resume writers around uncertain SQL
        resuming=True;backend.resume()
        backend.save(state+'_RESUMED')
        return state+'_RESUMED'
    except BaseException as error:
        backend.save('FAILURE',failure_class=type(error).__name__)
        if resuming:
            backend.save('RECOVERY_REQUIRED');return 'RECOVERY_REQUIRED'
        # No automatic SQL resubmission. Even an exception after server COMMIT
        # is reconciled using the original manifest and immutable consumption.
        state=backend.reconcile()
        backend.save(state)
        if state!='UNKNOWN':
            try:
                backend.resume();backend.save(state+'_RESUMED')
            except BaseException as resume_error:
                backend.save('RECOVERY_REQUIRED',failure_class=type(resume_error).__name__)
                return 'RECOVERY_REQUIRED'
        return state


class Production:
    def __init__(self,plan,digest,bundle,rt,guard,bulk):
        self.plan,self.digest,self.bundle,self.rt,self.guard,self.bulk=plan,digest,bundle,rt,guard,bulk
        self.manifest=json.loads(private(bundle/'AFFECTED_RECORD_MANIFEST.json',plan['manifest_sha256']))
        self.rows=self.manifest['records'];self.pre=self.manifest['preservation']
        self.sql=private(bundle/'BULK_PROJECTION_REPAIR.sql',plan['sql_sha256'])
        must(self.sql==bulk.render_bulk(self.rows,self.pre).encode(),'sql_not_current_renderer')
        must(len(self.rows)==plan['record_count'],'record_count')
        self.folder=ROOT/'runtime/catalog-projection-repairs'/digest
        self.app='catalog_projection_'+digest[:20]
        self.runtime=rt.assistant_module();self.contract=guard.policy()['upgrade']['assistant']
        self.pg=self.runtime.pg_control(self.contract)
        self.helper=rt.assistant_backup_module().ScheduledBackup(rt,self.contract,pg=self.pg)
        self.record=None;self.approval=None

    def save(self,phase,**fields):
        self.record.update(phase=phase,**fields)
        self.record['history'].append(dict(phase=phase,at=datetime.now(timezone.utc).isoformat(),**fields))
        self.rt.write(self.folder/'RECORD.json',self.record)

    def evidence(self,name,raw):
        # Private diagnostics retain phase and exact exit/timing, with known
        # password values removed. Nothing is printed or copied into the plan.
        credential=private(self.pg.env['PGPASSFILE'],mode=0o600,
                           owner=(self.contract['postgres']['credential_uid'],self.contract['postgres']['credential_gid']))
        for line in credential.splitlines():
            if line and not line.startswith(b'#') and b':' in line:
                secret=line.rsplit(b':',1)[-1]
                if secret:raw=raw.replace(secret,b'[REDACTED]')
        parent=self.folder if self.record is not None else self.bundle/'checks'
        if not parent.exists():self.rt.storage.mkdir_private(parent)
        self.rt.storage.validate_target(parent,exists=True,directory=True,
                                        expected_owner=(0,0),expected_mode=0o700)
        path=parent/name
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())

    def call(self,sql,label,*,write=False):
        self.rt.require_lease();self.pg.identity # bound binaries/credential identity from installed contract
        identity=self.plan['binaries']['psql']
        must(identity['path']==str(self.pg.binary/'psql'),'psql_fixed_path')
        private(identity['path'],self.pg.identity['psql'],mode=identity['mode'],owner=(identity['uid'],identity['gid']))
        started=time.monotonic();out=b'';err=b'';code=None;failure=None
        try:
            result=subprocess.run([str(self.pg.binary/'psql'),*self.pg.connection,'-X','-qAt','-v','ON_ERROR_STOP=1'],
                input=sql.encode()if isinstance(sql,str)else sql,capture_output=True,timeout=75,
                env=dict(self.pg.env,PGAPPNAME=self.app if write else self.app+'_inspect'),
                pass_fds=(self.rt.fd,),**self.pg.exec_identity)
            out,err,code=result.stdout,result.stderr,result.returncode
        except subprocess.TimeoutExpired as error:
            out,err=error.stdout or b'',error.stderr or b'';failure='TIMEOUT'
        suffix=label+'-'+str(time.monotonic_ns())
        self.evidence(suffix+'.stdout',out);self.evidence(suffix+'.stderr',err)
        timing=dict(label=label,exit_status=code,seconds=time.monotonic()-started,failure=failure)
        self.evidence(suffix+'.timing.json',(json.dumps(timing)+'\n').encode())
        if self.record is not None:
            self.save('SQL_RESULT',**timing)
        return code==0,out

    def identities(self):
        self.rt.require_lease()
        must(self.rt.current()==self.expected_release,'current_release_drift')
        private(ROOT/'runtime/core-publications/POLICY.json',self.plan['policy_sha256'])
        private(ROOT/'runtime/config/application.yml',self.plan['config_sha256'])
        gate=json.loads(private(ROOT/'runtime/vmm/gate.json',self.plan['gate_sha256']))
        life=json.loads(private(ROOT/'runtime/vmm/lifecycle.json',self.plan['lifecycle_sha256']))
        must(gate['mode']=='CLOSED' and life['enabled'] is False and life['desired']=='STOPPED'
             and life['state']=='DISABLED' and life['process'] is None,'vmm_not_disabled_stopped_closed')
        self.rt.require_no_active_work()

    def protected(self):
        proof=json.loads(private(self.bundle/'PROTECTED_FILES.json',self.plan['protected_files_sha256']))
        for x in proof:
            private(x['path'],x['sha256'],mode=x['mode'],owner=(x['uid'],x['gid']))

    @property
    def expected_release(self):
        from publisher_transaction import Release
        return Release(**self.plan['current_release'])

    def media(self):
        proof=json.loads(private(self.bundle/'MEDIA_PROOFS.json',self.plan['media_proofs_sha256']))
        must(len(proof['files'])==len(self.rows),'media_scope')
        proofs={x['painting_id']:x for x in proof['files']}
        for row in self.rows:
            p=proofs[row['painting_id']]
            must(p['asset_id']==row['asset_id'] and p['file_size']==row['file_size'] and p['mime_type']==row['mime_type'],'media_identity')
            raw=private(p['path'],row['sha256'],mode=int(p['mode'],8),owner=(p['uid'],p['gid']),limit=10*1024*1024)
            must(len(raw)==row['file_size'],'media_size')
            magic='image/png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if raw.startswith(b'\xff\xd8\xff') else None
            must(magic==row['mime_type'],'media_format')

    def preflight(self):
        must(not self.folder.exists(),'plan_already_consumed_or_unknown')
        require_prior_repairs_terminal(self.folder.parent,self.rt.read)
        self.rt.require_terminal_component_transactions()
        self.identities();self.rt.verify_candidate(self.rt.current());self.rt.require_compatible(self.rt.current())
        self.helper.health();self.media();self.protected()
        ok,_=self.call(self.bulk.inspection_sql(self.rows,self.pre),'preflight')
        must(ok,'read_only_preflight_refused')

    def consume(self):
        parent=self.folder.parent
        if not parent.exists():self.rt.storage.mkdir_private(parent)
        must(not self.folder.exists(),'consumption_exists')
        self.rt.storage.mkdir_private(self.folder)
        must(self.approval is not None,'approval_not_attached')
        self.record=dict(schema=SCHEMA,plan_sha256=self.digest,sql_sha256=self.plan['sql_sha256'],
                         approval_sha256=sha(self.approval),history=[])
        self.save('CONSUMED')
        self.evidence('APPROVAL.txt',self.approval)

    def load_consumption(self):
        self.record=self.rt.read(self.folder/'RECORD.json')
        must(self.record['plan_sha256']==self.digest and self.record['sql_sha256']==self.plan['sql_sha256'],'recovery_binding')
        self.identities()

    def quiesce(self):
        self.save('STOP_INTENT');e=self.rt.engine();e.watchdog_stop_locked();e.stop_role('java',19080)
        self.helper.quiet();self.identities();self.media();self.save('STOPPED')

    def backup(self):
        import assistant_release
        for name,identity in self.plan['binaries'].items():
            must(identity['path']==str(self.pg.binary/name),'pg_binary_fixed_path')
            private(identity['path'],self.pg.identity[name],mode=identity['mode'],owner=(identity['uid'],identity['gid']))
        assistant_release.backup(self.pg,self.contract['attachment_root'],self.folder/'backup',
            require_quiescent=self.helper.quiet,journal=self.save)
        self.save('BACKUP_COMPLETE')

    def submit_once(self):
        self.helper.quiet();self.identities();self.media()
        ok,_=self.call(self.sql,'single-write',write=True)
        if not ok:raise RuntimeError('sql_exit_failed_or_ambiguous_see_private_evidence')

    def reconcile(self):
        try:
            self.identities()
            # A still-running server query makes a client exit ambiguous. Never
            # classify NO_COMMIT while the original write backend still exists.
            ok,raw=self.call("BEGIN READ ONLY; SELECT count(*) FROM pg_stat_activity WHERE application_name='"+self.app+"'; COMMIT;",'backend-status')
            if not ok or raw.strip()!=b'0':return 'UNKNOWN'
            ok,_=self.call(self.bulk.inspection_sql(self.rows,self.pre,after=True),'postimage')
            if ok:return 'COMMITTED_VERIFIED'
            ok,_=self.call(self.bulk.inspection_sql(self.rows,self.pre),'preimage')
            return 'NO_COMMIT_VERIFIED' if ok else 'UNKNOWN'
        except BaseException:return 'UNKNOWN'

    def resume(self):
        self.identities();self.save('RESUME_INTENT');e=self.rt.engine()
        if e.active('java',19080) is None:e.start_java_locked(self.rt.fd)
        self.helper.health()
        if e.watchdog_identity() is None:e.watchdog_start_locked()
        must(e.watchdog_identity() is not None,'watchdog_missing')
        self.identities();self.protected();self.save('HEALTH_VERIFIED')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['check','apply','recover']);p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--sha256',required=True);p.add_argument('--approval',type=Path)
    a=p.parse_args();must(re.fullmatch('[0-9a-f]{64}',a.sha256),'plan_digest_format')
    raw=private(a.plan,a.sha256);plan=json.loads(raw);bundle=a.plan.parent
    must(plan['schema']==SCHEMA and plan['installed_root']==str(ROOT),'plan_schema_root')
    private(Path(__file__),plan['tool_sha256'])
    sources=plan['sources'];must('core_runtime' in sources and 'catalog_projection_bulk' in sources,'source_bindings')
    for x in sources.values():private(x['path'],x['sha256'])
    sys.meta_path.insert(0,Sources(sources));sys.dont_write_bytecode=True
    import core_runtime,core_release_guard,catalog_projection_bulk
    rt=core_runtime.CoreRuntime()
    with rt.lease():
        s=os.fstat(rt.fd);must((s.st_dev,s.st_ino)==(plan['lock']['device'],plan['lock']['inode']),'shared_lock_identity')
        backend=Production(plan,a.sha256,bundle,rt,core_release_guard,catalog_projection_bulk)
        if a.action=='check':backend.preflight();outcome='STATIC_READY_NOT_AUTHORIZED'
        else:
            if a.action=='apply':
                must(a.approval is not None,'fresh_explicit_approval_required')
                approval=private(a.approval)
                must(approval.decode().strip()==approval_text(a.sha256,plan['record_count']),'approval_scope_or_identity')
                backend.approval=approval
            outcome=execute(backend,recover=a.action=='recover')
    print(json.dumps(dict(outcome=outcome,plan_sha256=a.sha256)))
    if outcome in ('UNKNOWN','RECOVERY_REQUIRED'):raise SystemExit(65)


if __name__=='__main__':main()
