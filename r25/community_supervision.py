"""One bounded Community registration in the existing R25 watchdog.

No daemon, new global lock, PID adoption, DDL, model call or ledger restore.
Every entry is called under the original installed guard's verified lease.
"""
import os
from pathlib import Path
import re
import time
import uuid
from community_host import ROOT, SUCCESSOR_SCHEMA, encoded, identity, sha
from r25_storage import must

SCHEMA='ARTLIVE_COMMUNITY_WATCHDOG_V1'
TERMINAL=('NOT_STARTED','STOPPED','EXITED')


class Supervisor:
    def __init__(self, host):
        self.host=host
        self.marker=host.root/'WATCHDOG.json'
        self.state=host.folder/'watchdog-state.json'

    def installed(self):
        h=self.host
        h.validate(installing=False)
        must(h.plan['parent']['schema']==SUCCESSOR_SCHEMA and h.plan['service']['restart_budget']==2,
             'community_persistent_scope_required')
        must(h.read(h.record)['phase']=='INSTALLED' and h.read(h.root/'ACTIVE.json')['plan_sha256']==h.digest,
             'community_not_installed')

    def register(self, bundle):
        self.installed();h=self.host
        from community_host_cli import verify_controller
        verify_controller(bundle,h.plan['controller_manifest_sha256'],h.owner)
        if h.root==ROOT/'runtime/community':
            must(Path(bundle)==ROOT/'operations/community-controller'/h.plan['controller_manifest_sha256'],
                 'community_fixed_controller_path')
        marker=dict(schema=SCHEMA,plan_sha256=h.digest,controller_manifest_sha256=h.plan['controller_manifest_sha256'],
                    cli_sha256=identity(Path(bundle)/'community_host_cli.py')['sha256'],
                    python_sha256=h.plan['runtime']['python']['identity']['sha256'])
        if self.marker.exists():
            must(h.read(self.marker)==marker,'community_registration_conflict')
            self.load();return 'REGISTERED_VERIFIED_NOOP'
        if self.state.exists():
            # Registration receipt loss preserves all attempt counters.
            self.load()
        else:
            must(h.role().current()[1] is None,'community_registration_existing_process')
            h.write(self.state,dict(schema=SCHEMA,plan_sha256=h.digest,desired='RUNNING',attempts=[],
                                   unhealthy=None,reason='REGISTERED'))
        h.storage.atomic_write_bytes(self.marker,encoded(marker))
        return 'REGISTERED_NOT_STARTED'

    def load(self):
        h=self.host;s=h.read(self.state)
        must(set(s)=={'schema','plan_sha256','desired','attempts','unhealthy','reason'} and
             s['schema']==SCHEMA and s['plan_sha256']==h.digest and s['desired'] in ('RUNNING','STOPPED'),
             'community_supervision_state')
        must(type(s['attempts']) is list and len(s['attempts'])<=3 and
             all(type(x) is str and re.fullmatch('[a-f0-9]{32}',x) for x in s['attempts']) and
             len(set(s['attempts']))==len(s['attempts']), 'community_restart_history')
        return s

    def registered(self):
        self.installed();h=self.host;m=h.read(self.marker)
        must(m['schema']==SCHEMA and m['plan_sha256']==h.digest and
             m['controller_manifest_sha256']==h.plan['controller_manifest_sha256'] and
             m['python_sha256']==h.plan['runtime']['python']['identity']['sha256'], 'community_registration_drift')
        return self.load()

    def enable(self):
        s=self.registered();h=self.host
        h.role().reconcile_reboot()
        status=h.control('observe')
        must(status in TERMINAL,'community_enable_requires_drained')
        must(len(s['attempts'])<3,'community_restart_budget_exhausted')
        s.update(desired='RUNNING',unhealthy=None,reason='OPERATOR_RESUME')
        h.write(self.state,s)
        return 'ENABLED_NOT_STARTED'

    def tick(self, *, drain=False):
        s=self.registered();h=self.host;role=h.role()
        # Validate release/config/runtime before any start, signal or receipt.
        role.verify()
        role.reconcile_reboot()
        _,current=role.current()
        if s['attempts']:
            if current is None or current['operation_id']!=s['attempts'][-1]:
                # A durable start decision without a matching process receipt is
                # ambiguous. Never replay it, even if no listener is visible.
                return 'UNKNOWN_START_RECEIPT'
        else:must(current is None,'community_unregistered_process')
        if drain:
            s.update(desired='STOPPED',unhealthy=None,reason='OPERATOR_OR_MAINTENANCE_DRAIN')
            h.write(self.state,s)
        if s['desired']=='STOPPED':return h.control('stop')
        if current and s['reason']!='OPERATOR_RESUME':
            log=role.root/('run-'+current['operation_id']+'.log')
            if log.exists() and log.stat().st_size>=h.plan['service']['log_limit_bytes']:
                s.update(desired='STOPPED',unhealthy=None,reason='LOG_LIMIT');h.write(self.state,s)
                state=h.control('stop')
                return state if state in TERMINAL else 'LOG_LIMIT_DRAINING'
        status=h.control('observe')
        if status=='UNKNOWN':return status
        if status=='LOG_LIMIT_DRAINING':
            s.update(desired='STOPPED',unhealthy=None,reason='LOG_LIMIT')
            h.write(self.state,s);return status
        if status in ('STOP_INTENT','STOPPING'):return status
        if status in TERMINAL:
            if len(s['attempts'])>=3:
                s.update(desired='STOPPED',unhealthy=None,reason='RESTART_BUDGET_EXHAUSTED')
                h.write(self.state,s);return 'RESTART_BUDGET_EXHAUSTED'
            operation=uuid.uuid4().hex
            s['attempts'].append(operation)
            s.update(unhealthy=None,reason='START_DECISION')
            h.write(self.state,s)  # Consumption precedes the only start call.
            return h.control('start',operation)
        if status=='READY':
            if s['unhealthy'] is not None:
                s['unhealthy']=None;h.write(self.state,s)
            return status
        must(status=='STARTING','community_unexpected_runtime_state')
        now=time.monotonic();boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if s['unhealthy'] is None:
            s['unhealthy']=dict(boot_id=boot,since=now);h.write(self.state,s)
        else:
            deadline=s['unhealthy']
            must(set(deadline)=={'boot_id','since'} and deadline['boot_id']==boot and
                 type(deadline['since']) in (float,int) and deadline['since']<=now,'community_health_clock')
            if now-deadline['since']>=60:
                # Graceful one-signal drain; next tick may restart only after
                # verified exit, using the same lifetime budget.
                s['reason']='HEALTH_TIMEOUT';h.write(self.state,s)
                return h.control('stop')
        return status
