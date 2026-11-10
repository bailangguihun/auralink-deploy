#!/usr/bin/env python3
"""Actual PG16 fixed V017 migration, lost-reply reconciliation and data retention.

Creates a fresh loopback-only PostgreSQL cluster. No production connections,
provider calls, application startup or changes to any existing database.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'r25'))
import assistant_release as ar


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('fixture','pg-bin','core-source'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--pg-lib',type=Path)
    a=p.parse_args();a.fixture=a.fixture.resolve();a.core_source=a.core_source.resolve()
    os.umask(0o077);a.fixture.mkdir(parents=True,mode=0o700,exist_ok=False)
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    env={k:v for k,v in os.environ.items() if not k.startswith('PG')}
    if a.pg_lib:env['LD_LIBRARY_PATH']=str(a.pg_lib)
    env.update(PGHOST='127.0.0.1',PGPORT=str(port),PGUSER='studio_fixture',PGDATABASE='postgres')
    data=a.fixture/'data';started=False;checks=[];events=[]
    def run(label,args,raw=None):
        r=subprocess.run([str(x) for x in args],input=raw,capture_output=True,env=env,timeout=60)
        (a.fixture/(label+'.stdout')).write_bytes(r.stdout);(a.fixture/(label+'.stderr')).write_bytes(r.stderr)
        if r.returncode:raise RuntimeError(label+' failed; inspect fixture evidence')
        return r.stdout
    def journal(phase,**kw):
        events.append(dict(phase=phase,**kw));(a.fixture/'journal.json').write_text(json.dumps(events,indent=2)+'\n')
    try:
        entries=[]
        for name,digest in [*ar.MIGRATION_SHA256.items(),ar.STUDIO_MIGRATION]:
            directory='tests/fixtures/full_startup' if int(name[1:4])<=13 else 'db/postgres/migrations'
            entries.append(dict(version=name[:4],name=name,sha256=digest,path=str(a.core_source/directory/name)))
        ar.migration_inventory(entries)
        run('initdb',[a.pg_bin/'initdb','-D',data,'--username=studio_fixture','--auth=trust','--encoding=UTF8','--locale=C'])
        run('start',[a.pg_bin/'pg_ctl','-D',data,'-l',a.fixture/'postgres.log','-o',f"-h 127.0.0.1 -p {port} -k ''",'-w','start']);started=True
        pg=ar.PostgresControl(binary_dir=a.pg_bin,host='127.0.0.1',port=port,database='postgres',user='studio_fixture',environment=env)
        sql=['BEGIN; CREATE TABLE prototype_schema_migrations(version text PRIMARY KEY,checksum text NOT NULL);']
        for e in entries[:16]:
            sql += [ar.regular(e['path'],e['sha256']).decode(),"INSERT INTO prototype_schema_migrations VALUES ('"+e['version']+"','"+e['sha256']+"');"]
        sql += ["INSERT INTO catalog_users(username,password_hash,full_name,email,created_at,updated_at) VALUES('fixture','not-a-login','fixture','fixture@example.invalid',now(),now());",
                "INSERT INTO creations(public_id,user_id,workflow_snapshot,source_modality,status,created_at,updated_at) SELECT 'preserved-history',id,'{}','TEXT','SUCCEEDED',now(),now() FROM catalog_users;",'COMMIT;']
        pg.query('\n'.join(sql));assert len(ar.inspect_schema(pg,entries)['versions'])==16
        original=pg.query("SELECT row_to_json(c)::text FROM creations c;")
        backup=a.fixture/'before.dump';pg.dump(backup);checks.append('V016 and native backup verified')
        query=pg.query
        def fail_transaction(sql):
            if sql.startswith('BEGIN; SET LOCAL'):sql=sql.replace('COMMIT;', 'SELECT 1/0; COMMIT;')
            return query(sql)
        pg.query=fail_transaction
        try:ar.migrate(pg,entries,require_quiescent=lambda:None,journal=journal)
        except ar.Refused as error:assert str(error)=='assistant_pg_psql_failed'
        else:raise AssertionError('transaction failure was not exercised')
        pg.query=query
        assert ar.reconcile_migration(pg,entries)['outcome']=='MIGRATION_NOT_COMMITTED'
        assert query("SELECT row_to_json(c)::text FROM creations c;")==original
        checks.append('failed migration rolls back column, index and ledger atomically')
        # Inject a lost client response after the real database committed.
        def lose_reply(sql):
            result=query(sql)
            if sql.startswith('BEGIN; SET LOCAL'):raise InterruptedError('isolated lost commit response')
            return result
        pg.query=lose_reply
        try:ar.migrate(pg,entries,require_quiescent=lambda:None,journal=journal)
        except InterruptedError:pass
        else:raise AssertionError('lost reply boundary was not exercised')
        assert events[-1]['phase']=='MIGRATION_RESULT_UNKNOWN'
        pg.query=query
        result=ar.reconcile_migration(pg,entries)
        assert result['outcome']=='FORWARD_SCHEMA_PRESERVED' and result['automatic_replay'] is False and result['database_restore'] is False
        checks.append('lost commit response reconciled without replay or restore')
        assert json.loads(query("SELECT (to_jsonb(c)-'history_deleted_at')::text FROM creations c;"))==json.loads(original)
        assert query('SELECT count(*) FROM creations WHERE history_deleted_at IS NULL;')=='1'
        checks.append('historical creations retained and initially visible')
        query("UPDATE creations SET history_deleted_at=timestamp '2026-11-10 12:00:00';")
        count=len(events)
        assert ar.migrate(pg,entries,require_quiescent=lambda:None,journal=journal)['outcome']=='SCHEMA_CURRENT_VERIFIED'
        assert len(events)==count and query("SELECT count(*) FROM creations WHERE history_deleted_at=timestamp '2026-11-10 12:00:00';")=='1'
        checks.append('idempotent verify preserves deletion timestamps')
        # Old application column projection still works; no downgrade SQL exists.
        assert query('SELECT public_id FROM creations;')=='preserved-history'
        assert ar.reconcile_migration(pg,entries)['database_restore'] is False
        checks.append('old application read shape remains compatible; forward schema retained')
        query('DROP INDEX idx_creations_visible_history;')
        try:ar.inspect_schema(pg,entries)
        except ar.Refused as error:assert str(error)=='studio_physical_schema_conflict'
        else:raise AssertionError('ledger-only verification accepted a missing index')
        checks.append('physical schema drift refuses despite matching ledger')
        result='PASS'
    finally:
        if started:run('stop',[a.pg_bin/'pg_ctl','-D',data,'-m','fast','-w','stop'])
        (a.fixture/'RESULT.json').write_text(json.dumps(dict(result=locals().get('result','FAIL'),checks=checks,production=False,provider_calls=0),indent=2)+'\n')
    print(json.dumps(dict(result=result,checks=len(checks),fixture=str(a.fixture))))


if __name__=='__main__':main()
