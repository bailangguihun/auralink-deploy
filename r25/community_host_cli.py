#!/usr/bin/env python3
"""Explicit operator entry, run with the verified system Python -I -B.

Persistent operation is registered with the existing R25 watchdog. The older
foreground supervise command is explicit zero-restart operation only.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import sys


def verify_controller(root, digest, owner=(0,0)):
    root=Path(root).absolute()
    manifest=root/'manifest.json'
    s=manifest.lstat();d=root.lstat()
    if (not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or (s.st_uid,s.st_gid,stat.S_IMODE(s.st_mode))!=(*owner,0o600) or
        (d.st_uid,d.st_gid,stat.S_IMODE(d.st_mode))!=(*owner,0o700) or any(p.is_symlink() for p in (manifest,*manifest.parents))):
        raise ValueError('community_controller_private_identity')
    raw=manifest.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('community_controller_manifest_digest')
    value=json.loads(raw)
    expected={'community_bundle.py','community_lifecycle.py','community_host.py',
              'community_migration.py','community_supervision.py','community_host_cli.py','r25_storage.py'}
    if (value['schema']!='ARTLIVE_COMMUNITY_CONTROLLER_BUNDLE_V1' or
        value['repository']!='bailangguihun/auralink-deploy' or value['ref']!='refs/heads/release' or
        len(value['files'])!=len(expected) or {x['path'] for x in value['files']}!=expected):raise ValueError('community_controller_inventory')
    if root.is_symlink() or (root/'__pycache__').exists():raise ValueError('community_controller_cache')
    for item in value['files']:
        path=root/item['path'];s=path.lstat()
        if (not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or (s.st_uid,s.st_gid,stat.S_IMODE(s.st_mode))!=(*owner,0o600) or
            s.st_size!=item['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']):
            raise ValueError('community_controller_identity:'+item['path'])
    if {p.name for p in root.iterdir()}!=expected|{'manifest.json'}:raise ValueError('community_controller_extra_files')


def bootstrap(digest):
    path=Path(__file__).absolute()
    if path.is_symlink():raise ValueError('community_controller_entry_symlink')
    root=path.parent
    verify_controller(root,digest)
    sys.dont_write_bytecode=True
    sys.path.insert(0,str(root))


def supervision_command(host, command, bundle):
    """Shared production CLI dispatch, also exercised across fixture processes."""
    from community_supervision import Supervisor
    supervisor=Supervisor(host)
    with host.lease.held():
        if command=='register':return supervisor.register(bundle)
        if command=='enable':return supervisor.enable()
        if command in {'disable','watchdog-tick','watchdog-drain'}:
            return supervisor.tick(drain=command in {'disable','watchdog-drain'})
        raise ValueError('community_supervision_command')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['check','install','recover','observe','stop','supervise','bind-web','register','enable','disable','watchdog-tick','watchdog-drain'])
    for name in ['plan','plan-sha256','bundle-manifest-sha256']:p.add_argument('--'+name,required=True)
    for name in ['archive','manifest','settings','node','approval','operation-id']:p.add_argument('--'+name)
    p.add_argument('--lease-fd',type=int)
    a=p.parse_args()
    if (a.lease_fd is not None) != a.command.startswith('watchdog-'):
        raise ValueError('community_watchdog_inherited_lease_required')
    if os.geteuid()!=0:raise ValueError('community_host_requires_current_root_operator')
    bootstrap(a.bundle_manifest_sha256)
    from community_host import ROOT,Host,encoded,identity,production_lease,sha
    raw=Path(a.plan).read_bytes();plan=json.loads(raw)
    if raw!=encoded(plan) or sha(raw)!=a.plan_sha256:raise ValueError('community_exact_plan_bytes')
    if plan['controller_manifest_sha256']!=a.bundle_manifest_sha256:raise ValueError('community_unapproved_controller')
    i=identity(a.plan)
    if (i['uid'],i['gid'],i['mode'])!=(0,0,0o600):raise ValueError('community_private_plan')
    lease=production_lease(plan['lease'],a.lease_fd);host=Host(ROOT/'runtime/community',plan,lease)
    if a.command in {'check','install','recover'}:
        if not all([a.archive,a.manifest,a.settings,a.node]):raise ValueError('community_material_required')
        with lease.held():
            if a.command=='check':host.material(a.archive,a.manifest,a.settings,a.node);result='STATIC_VERIFIED_NOT_AUTHORIZED'
            else:
                if not a.approval:raise ValueError('community_explicit_approval_required')
                result=host.install(a.archive,a.manifest,a.settings,a.node,a.approval,recover=a.command=='recover')
    elif a.command=='supervise':
        if plan['service']['restart_budget']!=0 or (host.root/'WATCHDOG.json').exists():
            raise ValueError('community_foreground_requires_unregistered_zero_restart_plan')
        if not a.operation_id:raise ValueError('community_fresh_operation_required')
        # A second invocation cannot adopt or automatically restart a prior run.
        closing=False
        def stop(*_):
            nonlocal closing
            closing=True
        for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(sig,stop)
        if closing:raise ValueError('community_start_cancelled_before_intent')
        with lease.held():host.control('start',a.operation_id)
        from community_host import supervise
        result=supervise(host,lambda:closing)
    elif a.command in {'register','enable','disable','watchdog-tick','watchdog-drain'}:
        result=supervision_command(host,a.command,Path(__file__).parent)
    elif a.command=='bind-web':
        with lease.held():result=host.bind_web()
    elif a.command=='stop' and (host.root/'WATCHDOG.json').exists():
        result=supervision_command(host,'disable',Path(__file__).parent)
    else:
        with lease.held():result=host.control(a.command)
    print(json.dumps(dict(community=result)))


if __name__=='__main__':
    try:main()
    except Exception as error:
        # No config/SQL/credential payload in public errors.
        print(json.dumps(dict(community='REFUSED',error_type=type(error).__name__,reason=str(error))),file=sys.stderr)
        sys.exit(65)
