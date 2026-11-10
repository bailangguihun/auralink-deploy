"""Assistant hooks invoked only by the existing CoreRuntime/Publisher lease.

No standalone publish command. Candidate input cannot select this code. The
installed V2 successor policy binds this module and the fixed SQL validator.
"""
from __future__ import annotations
from pathlib import Path
import json
import os

import assistant_release as release
from publisher_transaction import Refused


def _runtime(rt):
    import core_release_guard as guard
    policy=guard.policy()
    contract=policy.get('upgrade',{}).get('assistant')
    if contract is None:
        return None
    release.validate_contract(contract)
    release.must(contract.get('policy_status') == 'PRODUCTION_REVIEWED',
                 'assistant_isolated_policy_not_installable')
    rt.require_lease()
    return policy,contract


def pg_control(contract):
    pg=contract['postgres']
    required={'binary_dir','binary_sha256','host','port','database','user','pgpass_path','pgpass_sha256','credential_uid','credential_gid','credential_account','credential_groups','library_path'}
    release.must(set(pg)==required,'assistant_postgres_contract')
    release.must(pg['host']=='127.0.0.1','assistant_postgres_production_loopback')
    credential=Path(pg['pgpass_path']); release.regular(credential,pg['pgpass_sha256'])
    info=credential.stat()
    release.must((info.st_uid,info.st_gid)==(pg['credential_uid'],pg['credential_gid']) and info.st_mode&0o777==0o600,'assistant_postgres_credential_owner')
    result=release.PostgresControl(binary_dir=pg['binary_dir'],host=pg['host'],port=pg['port'],database=pg['database'],user=pg['user'],
        environment={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LD_LIBRARY_PATH':pg['library_path'],'PGPASSFILE':str(credential)},credential_identity={'account':pg['credential_account'],'uid':pg['credential_uid'],'gid':pg['credential_gid'],'groups':pg['credential_groups']})
    release.must(result.identity==pg['binary_sha256'],'assistant_postgres_binary_binding')
    return result


def before_install(rt,current,candidate,snapshot_id):
    bound=_runtime(rt)
    if bound is None:
        return
    _,contract=bound
    release.must(candidate.artifact_sha256==contract['artifacts']['core']['sha256'],'assistant_selected_jar')
    release.must(current.artifact_sha256==contract['compatible_recovery_jar_sha256'],'assistant_recovery_jar')
    pg=pg_control(contract)
    folder=rt.transaction_folder(snapshot_id)
    logpath=folder/'assistant-journal.json'
    if logpath.exists():
        old=rt.read(logpath)
        # Deliberate recover/reconcile only, no blind migration or provider replay.
        release.must(old.get('phase')=='SCHEMA_READY','assistant_existing_schema_transaction_requires_recovery')
        release.must(len(release.inspect_schema(pg,contract['migrations'])['versions'])==len(contract['migrations']),'assistant_schema_ready_drift')
        return
    def quiescent():
        rt.require_lease(); rt.require_no_active_work()
        engine=rt.engine(partial_recovery=True)
        release.must(engine.active('java',19080) is None,'assistant_backup_requires_java_stopped')
        release.must(engine.watchdog_identity() is None,'assistant_backup_requires_watchdog_stopped')
    def journal(phase,**evidence):
        old=rt.read(logpath) if logpath.exists() else {'schema':'R25_ASSISTANT_SCHEMA_TRANSACTION_V1','history':[]}
        old.update(phase=phase); old['history'].append({'phase':phase,**evidence}); rt.write(logpath,old)
    quiescent()
    # Root retained assets are separate from the catalog media and poem memfds.
    release.backup(pg,contract['attachment_root'],folder/'assistant-backup',require_quiescent=quiescent,journal=journal)
    release.migrate(pg,contract['migrations'],require_quiescent=quiescent,journal=journal)
    journal('SCHEMA_READY')


def recovery(rt,plan):
    bound=_runtime(rt)
    if bound is None:
        return
    _,contract=bound
    release.must(plan.current.artifact_sha256==contract['compatible_recovery_jar_sha256'],'assistant_rollback_jar_not_reviewed')
    pg=pg_control(contract)
    result=release.reconcile_migration(pg,contract['migrations'])
    path=rt.transaction_folder(plan.transaction_id)/'assistant-recovery.json'
    rt.write(path,result)
    # Never restore database/file backups during application recovery: they may
    # predate a user's deletion or subsequent valid writes. Preserve schema/data.
