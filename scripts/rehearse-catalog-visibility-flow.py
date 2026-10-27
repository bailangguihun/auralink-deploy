#!/usr/bin/env python3
"""Native PG + captured guard/proof + original detached-watchdog CPU rehearsal.

No production connection. Catalog selections/UUIDs are exact captured inputs;
other tables/media/HTTP services are isolated fixtures. The full production
JAR/media/asset check is a separate read-only projected-guard gate, not claimed
by this fixture. No paid calls, production approval or process adoption.
"""
import argparse,contextlib,copy,fcntl,json,os,runpy,subprocess,sys,tempfile,time,types
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
helpers=runpy.run_path(str(HERE/'rehearse-catalog-visibility.py'))
v=helpers['v'];Capture=helpers['Capture'];material=helpers['material']


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('capture','manifest','heads','pg-bin','output'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--interrupt',choices=['none','sql-receipt','restore-controls','watchdog-receipt'],default='none')
    a=p.parse_args();c=Capture(a.capture);D=Path(tempfile.mkdtemp(prefix='catalog-visibility-flow-'));B=a.pg_bin
    socket=D/'socket';socket.mkdir();env=dict(os.environ)
    lib=B.parents[3]/'lib/x86_64-linux-gnu'
    if lib.exists():env['LD_LIBRARY_PATH']=str(lib)
    def run(argv,**kw):return subprocess.run([str(x) for x in argv],capture_output=True,env=env,**kw)
    def sql(raw):
        q=run([B/'psql','-h',socket,'-p','55479','-U',os.environ.get('USER','runner'),'-d','postgres','-X','-qAt','-v','ON_ERROR_STOP=1'],input=raw.encode() if isinstance(raw,str) else raw)
        if q.returncode:raise RuntimeError(q.stderr.decode())
        return q.stdout.decode().strip()
    def jsonsql(raw):return json.loads(sql('SET TIME ZONE \'Asia/Shanghai\'; '+raw))
    def fp(t):return "(SELECT jsonb_build_object('rows',count(*),'md5',md5(coalesce(string_agg(to_jsonb(t)::text,E'\\n' ORDER BY to_jsonb(t)::text),''))) FROM "+t+' t)'
    m=json.loads(a.manifest.read_bytes());heads=json.loads(a.heads.read_bytes())['heads']
    events=[];calls=[];engine=None
    try:
        q=run([B/'initdb','-D',D/'data','-A','trust','--no-locale','-E','UTF8']);assert q.returncode==0,q.stderr
        q=run([B/'pg_ctl','-D',D/'data','-l',D/'pg.log','-o',f'-k {socket} -p 55479 -c listen_addresses=', '-w','start']);assert q.returncode==0,q.stderr
        tables=list(json.loads(next(z for k,z in c.queries.items() if 'catalog_batches' in k))['tables'])+['preserved_user_history']
        sql('CREATE TABLE catalog_batch_current_revisions(batch_id uuid primary key,revision int,selected_at timestamptz NOT NULL); CREATE TABLE catalog_paintings(public_id uuid primary key,current_batch_id uuid); CREATE TABLE catalog_source_records(painting_id uuid,batch_id uuid);')
        for table in tables:
            if table not in ('catalog_batch_current_revisions','catalog_paintings','catalog_source_records'):
                sql('CREATE TABLE '+table+"(id int primary key,value text); INSERT INTO "+table+" VALUES(1,'retained CPU fixture');")
        sql("INSERT INTO catalog_batch_current_revisions SELECT * FROM jsonb_populate_recordset(NULL::catalog_batch_current_revisions,'"+json.dumps(heads)+"'::jsonb);")
        sql('INSERT INTO catalog_paintings VALUES'+','.join("('"+r['painting_id']+"','"+r['batch_id']+"')" for r in m['rows'])+'; INSERT INTO catalog_source_records SELECT public_id,current_batch_id FROM catalog_paintings;')
        def snapshot():return {t:jsonsql('SELECT '+fp(t)) for t in tables}
        pre=snapshot();assert pre['catalog_batch_current_revisions']==v.VISIBILITY_HEADS['before']
        with contextlib.ExitStack() as stack:
            c.install_patches(stack);rt,plan,folder,ident=material(c,a.manifest.read_bytes(),v.encoded(pre))
            lock=D/'lock';lock.write_bytes(b'');lock.chmod(0o600);rt.lock=lock
            def atomic(path,raw,before=None,owner=None,mode=0o600):
                assert rt.fd is not None
                if path in c.files:assert before==v.sha(c.read(path,owner or rt.private,mode))
                else:assert before is None
                c.put(path,raw,owner or rt.private,mode)
            rt.atomic=atomic
            @contextlib.contextmanager
            def lease():
                fd=os.open(lock,os.O_RDWR)
                try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);rt.fd=fd;yield
                finally:rt.fd=None;os.close(fd)
            rt.lease=lease
            guardpath=c.base/'operations/core_release_guard.py'
            g=c.load(guardpath,'core_release_guard',c.read(guardpath,(1000,1000),0o700))
            raw_query=next(z for k,z in c.queries.items() if 'catalog_batches' in k)
            retention=json.loads(c.read(plan['retention_before']['path']))
            media={x['path']:x for x in retention['media']}
            def guard(mode,fd):
                # Original lease verifier against a real CPU fixture flock.
                verifier=g.BusinessGuard();verifier.lock=lock
                old=g.read_file
                def file_read(path,**kw):
                    if Path(path)==lock:
                        st=lock.stat();assert st.st_nlink==1 and st.st_mode&0o777==0o600;return b''
                    return old(path,**kw)
                g.read_file=file_read
                try:verifier.verify_lease(fd)
                finally:g.read_file=old
                if mode=='lease':return
                effective=g.policy();g.validate_backend_identity(c.base,plan['current']['release']['artifact_sha256'])
                b=g.business_module(effective);original=b.read_bound
                # External media I/O is represented explicitly here. The same
                # installed validator's real media/asset/JAR checks pass in the
                # separate full read-only projection, not fabricated here.
                def read_bound(path,*args,**kw):
                    if str(path) in media:
                        item=media[str(path)];assert kw['expected']==item['sha256'];return bytes(item['bytes'])
                    return original(path,*args,**kw)
                b.read_bound=read_bound
                def query(text):
                    if text==b.RETENTION_SQL:
                        now=json.loads(raw_query);now['tables']['catalog_batch_current_revisions']=jsonsql('SELECT '+fp('catalog_batch_current_revisions'));return json.dumps(now)
                    return c.queries[text]
                b.verify_compatibility(effective,query)
                events.append('installed_guard_accepted')
            lifecycle_path=c.base/'operations/lifecycle_adapter.py'
            life=c.load(lifecycle_path,'captured_lifecycle',c.read(lifecycle_path,(1000,1000),0o700))
            runtime=D/'app';(runtime/'runtime').mkdir(parents=True)
            owner=(os.getuid(),os.getgid());heartbeat=D/'heartbeat'
            class Diagnostics:
                def emit(self,*a,**k):pass
                @contextlib.contextmanager
                def step(self,*a,**k):yield
            class FixtureEngine(life.Engine):
                def __init__(self):
                    super().__init__(runtime,D,private_owner=owner,cwd=D)
                    self.v={'R25_PYTHON':sys.executable};self._diagnostics_recorder=Diagnostics()
                    self._diagnostics_module=types.SimpleNamespace(process=life.proc);self._diagnostics_validated=True
                @contextlib.contextmanager
                def lease(self,*a,**kw):
                    fd=os.open(lock,os.O_RDWR)
                    try:
                        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);guard('eligible',fd);yield fd
                    finally:os.close(fd)
                def watchdog_stop_metadata(self):pass
                def watchdog_stop_lease(self):return self.lease()
                def status(self):
                    with self.lease():
                        with heartbeat.open('a') as stream:stream.write('guard accepted\n')
                        return {'result':'CPU_SERVICES_HEALTHY'}
                def vmm_action(self,*a):return 'DISABLED'
                def community_action(self,*a):return 'CPU_READY_NO_RESTART'
                def recover(self):raise AssertionError('fixture must never restart services')
            engine=FixtureEngine()
            def supervise(mode,fd):
                # Parent owns the same descriptor. Original watchdog starts and
                # stops through its real code, and its child opens the lock anew.
                caller=os.getpid()
                @contextlib.contextmanager
                def inherited():
                    if os.getpid()!=caller:
                        with FixtureEngine.lease(engine) as fresh:yield fresh
                    else:
                        guard('eligible',fd);yield fd
                if mode in ('start','status'):
                    with patch.object(engine,'lease',inherited):result=getattr(engine,'watchdog_'+mode)()
                else:
                    # Stop must work with a partial controller install.
                    with patch.object(engine,'watchdog_stop_lease',lambda:contextlib.nullcontext(fd)):
                        result=engine.watchdog_stop()
                    try:os.waitpid(-1,os.WNOHANG)
                    except ChildProcessError:pass
                events.append('original_watchdog_'+mode+':'+result['result'])
            rt.require_watchdog_stopped=lambda:None if engine.watchdog_identity() is None else (_ for _ in ()).throw(v.Refused('watchdog_must_be_stopped_before_mixed_recovery'))
            def database(mode):
                if mode=='observe':
                    value=jsonsql('SELECT '+fp('catalog_batch_current_revisions'))
                    for side in ('before','after'):
                        if value==v.VISIBILITY_HEADS[side]:return side
                    raise AssertionError('partial/foreign SQL state')
                calls.append(mode);sql(c.read(folder/('delist.sql' if mode=='delist' else 'restore.sql')))
            # Actual native SQL, actual retained proof and original watchdog.
            with patch.object(v,'revision_stopped',lambda _:None):
                def fault(where):
                    if (a.interrupt,where) in (('sql-receipt','after:sql'),('watchdog-receipt','after:watchdog')):raise InterruptedError('isolated lost receipt')
                try:result=v.visibility_execute(rt,ident,database=database,guard=guard,supervise=supervise,fault=fault)
                except InterruptedError:
                    assert a.interrupt in ('sql-receipt','watchdog-receipt')
                    result=v.visibility_execute(rt,ident,database=database,guard=guard,supervise=supervise,recover=True)
                assert result=='COMMITTED' and calls==['delist']
                deadline=time.monotonic()+12
                while time.monotonic()<deadline:
                    if heartbeat.exists() and len(heartbeat.read_text().splitlines())>=2:break
                    time.sleep(.1)
                assert heartbeat.exists() and len(heartbeat.read_text().splitlines())>=2, 'original watchdog did not tick after release of lease'
                assert engine.watchdog_identity() is not None
                # Preserve a new forward user record through exact restoration.
                sql("INSERT INTO preserved_user_history VALUES(2,'new forward result UNKNOWN preserved')")
                def restore_fault(where):
                    if a.interrupt=='restore-controls' and where=='restore:handoff':raise InterruptedError('isolated restoration interruption')
                try:result=v.visibility_execute(rt,ident,database=database,guard=guard,supervise=supervise,restore=True,fault=restore_fault)
                except InterruptedError:
                    assert a.interrupt=='restore-controls'
                    result=v.visibility_execute(rt,ident,database=database,guard=guard,supervise=supervise,recover=True)
                assert result=='RESTORED' and calls==['delist','restore']
                assert jsonsql('SELECT '+fp('catalog_batch_current_revisions'))==pre['catalog_batch_current_revisions']
                assert sql('SELECT count(*) FROM preserved_user_history')=='2'
                for n,(path,_,_) in v.visibility_paths(rt).items():assert c.read(path)==c.read(folder/'before'/n)
                for path,raw in c.original.items():assert c.files[path]['bytes']==raw,str(path)
                engine.watchdog_stop()
                try:os.waitpid(-1,os.WNOHANG)
                except ChildProcessError:pass
            result=dict(result='PASS',interruption=a.interrupt,targets=486,selections=12,sql_calls=calls,watchdog_heartbeats=len(heartbeat.read_text().splitlines()),
                actual_guard_source=v.sha(c.original[guardpath]),actual_watchdog_source=v.sha(c.original[lifecycle_path]),
                controls_restored=True,old_proofs_preserved=True,forward_history_preserved=True,events=events,
                production_access=False,limits=['native CPU fixture data outside exact selected heads/UUIDs','media and service I/O are fixtures; real complete guard verified separately read-only'])
            a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
    finally:
        if engine is not None:
            try:
                owned=engine.watchdog_identity()
                if owned:os.kill(owned['pid'],15);os.waitpid(owned['pid'],0)
            except (OSError,Exception):pass
        q=run([B/'pg_ctl','-D',D/'data','-m','fast','-w','stop']);print('ISOLATED_PG_STOP_EXIT='+str(q.returncode))

if __name__=='__main__':main()
