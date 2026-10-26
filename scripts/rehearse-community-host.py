#!/usr/bin/env python3
"""Disposable native PG16 + real Community package + retained-guard host test.

Core authentication is an explicit HTTP fixture here; reuse the separately
accepted real-JAR integration run. No production path, token or provider call.
"""
import argparse
import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'r25'))
from community_host import Host, identity, encoded
import community_migration as migration
from test_community_host import HostTests


def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]


def scram(password):
    salt=os.urandom(16);key=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,4096)
    client=hmac.new(key,b'Client Key',hashlib.sha256).digest()
    b=lambda raw:base64.b64encode(raw).decode()
    return 'SCRAM-SHA-256$4096:'+b(salt)+'$'+b(hashlib.sha256(client).digest())+':'+b(hmac.new(key,b'Server Key',hashlib.sha256).digest())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixture',type=Path,required=True)
    p.add_argument('--pg-bin',type=Path,required=True)
    p.add_argument('--core-source',type=Path,required=True)
    a=p.parse_args();a.fixture=a.fixture.resolve();a.core_source=a.core_source.resolve()
    a.fixture.mkdir(mode=0o700,parents=True,exist_ok=False)
    data=a.fixture/'data';pgport=port();results=[];started=False;f=None;server=None
    env=dict(os.environ,PGHOST='127.0.0.1',PGPORT=str(pgport),PGUSER='community_fixture_admin',PGDATABASE='postgres')
    def run(label,cmd,raw=None,success=True,extra=None):
        r=subprocess.run([str(x) for x in cmd],input=raw,capture_output=True,env=env|dict(extra or {}),timeout=45)
        (a.fixture/(label+'.stdout')).write_bytes(r.stdout);(a.fixture/(label+'.stderr')).write_bytes(r.stderr)
        if success and r.returncode:raise RuntimeError(label+' exit '+str(r.returncode))
        return r
    def sql(label,raw,database='postgres',success=True,extra=None):
        return run(label,[a.pg_bin/'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-d',database],
                   raw.encode() if isinstance(raw,str) else raw,success,extra)
    def ok(label):results.append(dict(name=label,result='PASS'))
    def req(path,body=None):
        data=None if body is None else json.dumps(body).encode()
        request=urllib.request.Request('http://127.0.0.1:'+str(f.f.port)+'/api/v1/community'+path,data=data,
            headers={'authorization':'Bearer isolated-user','content-type':'application/json'})
        with urllib.request.urlopen(request,timeout=8) as response:return json.load(response)
    try:
        run('initdb',[a.pg_bin/'initdb','-D',data,'--username=community_fixture_admin','--auth=trust','--encoding=UTF8','--locale=C'])
        run('start',[a.pg_bin/'pg_ctl','-D',data,'-l',a.fixture/'postgres.log','-o',f"-h 127.0.0.1 -p {pgport} -k ''",'-w','start']);started=True
        sql('sentinel',"CREATE TABLE preserved(id integer PRIMARY KEY,value text); INSERT INTO preserved VALUES(1,'historical');")
        password='disposable-community-runtime-only'
        roles=migration.provision_sql(scram(password));sql('roles',roles)
        assert sql('roles-replay',roles,success=False).returncode!=0;ok('role-replay-refused')
        # Match the observed production SCRAM boundary for this separate login.
        # The fixture administrator remains local-only and fixture-only trust.
        hba=data/'pg_hba.conf'
        hba.write_text(f'host {migration.DATABASE} {migration.RUNTIME} 127.0.0.1/32 scram-sha-256\n'+hba.read_text())
        run('reload-fixture-auth',[a.pg_bin/'pg_ctl','-D',data,'reload'])
        sql('database',migration.database_sql())
        assert sql('database-replay',migration.database_sql(),success=False).returncode!=0;ok('database-replay-refused')
        source=a.core_source/'community-service';foundation=(source/'db/001_foundation.sql').read_bytes();op=uuid.uuid4().hex
        schema=migration.schema_sql(foundation,op)
        failed=schema.replace(b'COMMIT;',b'SELECT * FROM deliberately_missing_transaction_fixture;\nCOMMIT;')
        assert sql('schema-transaction-failure',failed,migration.DATABASE,False).returncode!=0
        assert sql('no-partial-schema',"SELECT count(*) FROM pg_namespace WHERE nspname='community';",migration.DATABASE).stdout.strip()==b'0'
        ok('schema-failure-atomic-no-historical-restore')
        sql('schema-commit',schema,migration.DATABASE)
        assert sql('schema-replay',schema,migration.DATABASE,False).returncode!=0
        receipt=sql('receipt-reconcile','SELECT operation_id FROM community.publication_receipt;',migration.DATABASE).stdout.strip().decode()
        assert receipt==op;ok('schema-receipt-replay-reconciliation')
        sql('verify-grants',migration.VERIFY_SQL,migration.DATABASE)
        runtime={'PGUSER':migration.RUNTIME,'PGPASSWORD':password}
        sql('runtime-scram-login','SELECT current_user;',migration.DATABASE,extra=runtime)
        assert sql('runtime-wrong-password','SELECT current_user;',migration.DATABASE,False,
                   dict(runtime,PGPASSWORD='wrong-fixture-secret')).returncode!=0
        ok('separate-runtime-scram-secret-enforced')
        for label,query in [('ddl','CREATE TABLE community.forbidden(id int);'),('temp','CREATE TEMP TABLE forbidden(id int);'),
                            ('public-ddl','CREATE TABLE public.forbidden(id int);'),('journal-delete','DELETE FROM community.operations;'),
                            ('receipt-write',"UPDATE community.publication_receipt SET operation_id=repeat('a',32);")]:
            assert sql('deny-'+label,query,migration.DATABASE,False,runtime).returncode!=0
        ok('non-ddl-minimum-grants')
        dump=a.fixture/'before-requests.dump'
        run('backup',[a.pg_bin/'pg_dump','-Fc','-d',migration.DATABASE,'-f',dump])
        assert run('backup-list',[a.pg_bin/'pg_restore','--list',dump]).stdout
        ok('native-consistent-backup-inventory')
        # Restore validation is confined to a new disposable fixture database;
        # production recovery never restores an earlier Community/database state.
        sql('backup-fixture-database','CREATE DATABASE community_backup_fixture;')
        run('backup-fixture-restore',[a.pg_bin/'pg_restore','--exit-on-error','-d','community_backup_fixture',dump])
        assert sql('backup-fixture-receipt','SELECT operation_id FROM community.publication_receipt;','community_backup_fixture').stdout.strip().decode()==op
        ok('backup-restore-verified-in-disposable-database-only')
        class CoreFixture(BaseHTTPRequestHandler):
            def do_GET(self):
                raw=encoded(dict(success=True,data=dict(id=101,username='isolated-user')))
                self.send_response(200);self.send_header('content-type','application/json');self.end_headers();self.wfile.write(raw)
            def log_message(self,*_):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),CoreFixture)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        f=HostTests('runTest');f.setUp()
        spec=importlib.util.spec_from_file_location('community_packager',a.core_source/'scripts/community-release-bundle.py')
        packager=importlib.util.module_from_spec(spec);spec.loader.exec_module(packager)
        raw,manifest=packager.materialize(source,dict(repository='bailangguihun/auralink-core-api',ref='refs/heads/release',**f.f.provenance),f.f.binding['node_version'])
        f.archive.write_bytes(raw);f.manifest.write_bytes(manifest)
        f.settings.write_bytes(encoded(dict(COMMUNITY_DATABASE_URL=f'postgres://{migration.RUNTIME}:{password}@127.0.0.1:{pgport}/{migration.DATABASE}',
            COMMUNITY_CORE_ORIGIN=f'http://127.0.0.1:{server.server_port}',COMMUNITY_WEB_ORIGIN='https://fixture.invalid',COMMUNITY_PORT=str(f.f.port))))
        f.plan['archive'].update(identity=identity(f.archive),manifest_identity=identity(f.manifest));f.plan['settings']=identity(f.settings)
        f.host=Host(f.f.base/'host',f.plan,f.lease);f.approval.write_bytes(encoded(f.plan))
        assert f.install()=='INSTALLED'
        with f.lease.held():f.host.control('start',uuid.uuid4().hex)
        f.wait('READY');ok('actual-node-package-host-ready')
        with f.lease.held():
            capabilities=req('/capabilities');assert not any(capabilities[k] for k in ('uploads','rooms','voice','coins','ai'))
            command=dict(requestId=str(uuid.uuid4()),content='isolated host publication test')
            made=req('/posts',command);assert made['state']=='COMMITTED'
            again=req('/posts',command);assert made==again
        ok('http-independent-of-held-original-lease-and-idempotent')
        sql('drain-trigger',"CREATE FUNCTION community.fixture_delay() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(1); RETURN NEW; END $$; CREATE TRIGGER delay_insert BEFORE INSERT ON community.posts FOR EACH ROW EXECUTE FUNCTION community.fixture_delay();",migration.DATABASE)
        command=dict(requestId=str(uuid.uuid4()),content='disconnected drain fixture');body=json.dumps(command).encode()
        connection=socket.create_connection(('127.0.0.1',f.f.port));connection.sendall(
            b'POST /api/v1/community/posts HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer isolated-user\r\nContent-Type: application/json\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            waiting=sql('drain-sync',"SELECT count(*) FROM pg_stat_activity WHERE application_name='artlive-community' AND wait_event='PgSleep';",migration.DATABASE).stdout.strip()
            if waiting!=b'0':break
            time.sleep(.02)
        else:raise AssertionError('controlled write never reached pg_sleep')
        connection.close()
        with f.lease.held():assert f.host.control('stop')=='STOPPING'
        f.wait('STOPPED')
        assert sql('drain-count','SELECT count(*) FROM community.operations;',migration.DATABASE).stdout.strip()==b'2'
        ok('disconnected-handler-drains-before-exit-no-lost-write')
        with f.lease.held():f.host.control('start',uuid.uuid4().hex)
        f.wait('READY');assert req('/operations/'+command['requestId'])['state']=='COMMITTED'
        with f.lease.held():f.host.control('stop')
        f.wait('STOPPED');ok('explicit-restart-retains-journal-and-results')
        assert sql('preserved','SELECT value FROM preserved;').stdout.strip()==b'historical'
        assert f.preserved.read_bytes()==encoded(dict(consumed=['a'],UNKNOWN=['b']));ok('other-database-and-historical-evidence-preserved')
        result='PASS'
    except BaseException:
        result='FAIL';raise
    finally:
        if f:f.doCleanups()
        if server:server.shutdown();server.server_close()
        if started:run('stop',[a.pg_bin/'pg_ctl','-D',data,'-m','fast','-w','stop'])
        (a.fixture/'RESULT.json').write_text(json.dumps(dict(result=locals().get('result','FAIL'),checks=results,production=False,
            core_authentication='HTTP_FIXTURE; actual JAR run reused separately',paid_calls=0),indent=2)+'\n')
    print(json.dumps(dict(result=result,checks=len(results),fixture=str(a.fixture))))


if __name__=='__main__':main()
