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


ASSISTANT_COMMANDS={'observe','stop','enable','watchdog-tick','watchdog-drain','watchdog-preserve'}


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


def supervision_command(host, command, bundle, *, supervisor=None, operation_id=None):
    """Shared production CLI dispatch, also exercised across fixture processes."""
    with host.lease.held():
        return _supervision_action(host,command,bundle,supervisor,operation_id)


def _supervision_action(host, command, bundle, supervisor, operation_id):
    from community_supervision import Supervisor
    supervisor=supervisor or Supervisor(host)
    if command=='register':return supervisor.register(bundle)
    if command=='enable':
        return supervisor.enable() if type(supervisor) is Supervisor else supervisor.enable(operation_id)
    if command in {'disable','watchdog-tick','watchdog-drain'}:
        return supervisor.tick(drain=command in {'disable','watchdog-drain'})
    raise ValueError('community_supervision_command')


def assistant_community_command(code, command, expected_plan, core_authority, *, operation_id=None):
    if command not in ASSISTANT_COMMANDS:raise ValueError('community_assistant_scope')
    preserve=command=='watchdog-preserve'
    host,supervisor=code.active_for_assistant_rollout(expected_plan,core_authority,preserve_only=preserve)
    if preserve:return host.preserve_running(supervisor)
    if command=='observe':return host.control('observe')
    if command=='stop':command='disable'
    return _supervision_action(host,command,None,supervisor,operation_id)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['check','install','recover','observe','stop','supervise','bind-web','register','enable','disable','watchdog-tick','watchdog-drain','watchdog-preserve','package-prepare','package-install','package-recover','reference-prepare','reference-install','reference-recover','code-prepare','code-install','code-recover'])
    for name in ['plan','plan-sha256','bundle-manifest-sha256']:p.add_argument('--'+name,required=True)
    for name in ['archive','manifest','settings','node','approval','operation-id','package-plan','package-plan-sha256','review-sha256']:p.add_argument('--'+name)
    p.add_argument('--lease-fd',type=int)
    p.add_argument('--knowledge-publication-sha256')
    p.add_argument('--supervision-upgrade-sha256')
    p.add_argument('--studio-publication-sha256')
    p.add_argument('--reference-publication-sha256')
    p.add_argument('--code-publication-sha256')
    p.add_argument('--assistant-rollout-sha256')
    p.add_argument('--reference-plan')
    p.add_argument('--reference-plan-sha256')
    p.add_argument('--database-plan')
    a=p.parse_args()
    if (a.lease_fd is not None) != a.command.startswith('watchdog-'):
        raise ValueError('community_watchdog_inherited_lease_required')
    if os.geteuid()!=0:raise ValueError('community_host_requires_current_root_operator')
    bootstrap(a.bundle_manifest_sha256)
    from community_host import ROOT,Host,encoded,identity,production_lease,sha
    raw=Path(a.plan).read_bytes();plan=json.loads(raw)
    if raw!=encoded(plan) or sha(raw)!=a.plan_sha256:raise ValueError('community_exact_plan_bytes')
    binding=plan['lease']
    legacy_sha=None
    code_context=None
    rollout=None
    if a.assistant_rollout_sha256 is not None:
        if not a.code_publication_sha256 or a.command not in ASSISTANT_COMMANDS:
            raise ValueError('community_assistant_scope')
        from community_host import assistant_community_approval
        rollout=assistant_community_approval(a.assistant_rollout_sha256,a.code_publication_sha256,
            a.bundle_manifest_sha256,preserve_only=a.command=='watchdog-preserve')
        entry=ROOT/'operations/community-controller'/a.bundle_manifest_sha256/'community_host_cli.py'
        if Path(__file__).absolute()!=entry or identity(entry)['sha256']!=rollout['assistant_rollout']['community']['cli_sha256']:
            raise ValueError('community_assistant_controller_entry')
    if a.command.startswith('code-') and not a.code_publication_sha256:
        raise ValueError('community_code_controller_required')
    if a.command.startswith('reference-') and not a.reference_publication_sha256:
        raise ValueError('community_reference_binding_required')
    if a.command.startswith('package-') and not a.studio_publication_sha256:
        raise ValueError('community_package_requires_studio')
    if a.code_publication_sha256:
        if not all([a.reference_publication_sha256,a.knowledge_publication_sha256,
                    a.supervision_upgrade_sha256,a.studio_publication_sha256]) or a.command not in (
                    'observe','stop','enable','watchdog-tick','watchdog-drain','watchdog-preserve',
                    'code-prepare','code-install','code-recover'):
            raise ValueError('community_code_scope')
        from community_host import code_publication_binding
        binding,legacy_sha,code_context=code_publication_binding(plan,a.plan_sha256,a.bundle_manifest_sha256,
            a.knowledge_publication_sha256,a.supervision_upgrade_sha256,a.studio_publication_sha256,
            a.reference_publication_sha256,a.code_publication_sha256,rollout=rollout)
    elif a.reference_publication_sha256:
        if not all([a.knowledge_publication_sha256,a.supervision_upgrade_sha256,a.studio_publication_sha256]) or a.command not in ('observe','stop','enable','watchdog-tick','watchdog-drain','watchdog-preserve','reference-prepare','reference-install','reference-recover'):
            raise ValueError('community_reference_scope')
        from community_host import reference_lease_binding
        binding,legacy_sha=reference_lease_binding(plan,a.plan_sha256,a.bundle_manifest_sha256,
            a.knowledge_publication_sha256,a.supervision_upgrade_sha256,a.studio_publication_sha256,
            a.reference_publication_sha256)
    elif a.studio_publication_sha256:
        if not a.knowledge_publication_sha256 or not a.supervision_upgrade_sha256 or a.command not in ('observe','stop','enable','watchdog-tick','watchdog-drain','watchdog-preserve','package-prepare','package-install','package-recover'):
            raise ValueError('community_studio_scope')
        from community_host import studio_lease_binding
        binding,legacy_sha=studio_lease_binding(plan,a.plan_sha256,a.bundle_manifest_sha256,
            a.knowledge_publication_sha256,a.supervision_upgrade_sha256,a.studio_publication_sha256)
    elif a.supervision_upgrade_sha256:
        if not a.knowledge_publication_sha256 or a.command not in ('observe','stop','enable','watchdog-tick','watchdog-drain','watchdog-preserve'):
            raise ValueError('community_recovery_original_installation_only')
        from community_host import recovery_lease_binding
        binding,legacy_sha=recovery_lease_binding(plan,a.plan_sha256,a.bundle_manifest_sha256,
            a.knowledge_publication_sha256,a.supervision_upgrade_sha256)
    elif a.knowledge_publication_sha256:
        if a.command not in ('observe','stop','watchdog-tick','watchdog-drain','watchdog-preserve'):
            raise ValueError('community_knowledge_original_installation_only')
        from community_host import knowledge_lease_binding
        binding=knowledge_lease_binding(plan,a.plan_sha256,a.bundle_manifest_sha256,a.knowledge_publication_sha256)
    elif plan['controller_manifest_sha256']!=a.bundle_manifest_sha256:
        raise ValueError('community_unapproved_controller')
    if a.command=='watchdog-preserve' and not a.knowledge_publication_sha256:
        raise ValueError('community_preserve_requires_knowledge_binding')
    i=identity(a.plan)
    if (i['uid'],i['gid'],i['mode'])!=(0,0,0o600):raise ValueError('community_private_plan')
    lease=production_lease(binding,a.lease_fd);host=Host(ROOT/'runtime/community',plan,lease)
    supervisor=None
    if a.supervision_upgrade_sha256:
        from community_supervision import RecoverySupervisor
        supervisor=RecoverySupervisor(host,a.supervision_upgrade_sha256,legacy_sha)
    if rollout is not None:
        from community_host import CodePackageUpgrade,ReferencePackageUpgrade,assistant_reference_package_context
        from core_release_guard import require_community_rollout
        with lease.held():
            context=assistant_reference_package_context(host,a.reference_publication_sha256,
                a.assistant_rollout_sha256,rollout,code_context)
            code=CodePackageUpgrade(ReferencePackageUpgrade(host,supervisor,context),code_context)
            def core_authority(*,preserve_only):
                return require_community_rollout(a.assistant_rollout_sha256,lease.fd,preserve_only=preserve_only)
            result=assistant_community_command(code,a.command,rollout['assistant_rollout']['community']['code_plan_sha256'],
                                               core_authority,operation_id=a.operation_id)
        print(json.dumps(dict(community=result)))
        return
    if a.reference_publication_sha256:
        from community_host import ReferencePackageUpgrade,reference_package_context
        with lease.held():
            service=ReferencePackageUpgrade(host,supervisor,reference_package_context(host,a.reference_publication_sha256))
            if code_context is not None:
                from community_host import CodePackageUpgrade
                code=CodePackageUpgrade(service,code_context)
                if a.command=='code-prepare':
                    if not all([a.archive,a.manifest,a.review_sha256]):raise ValueError('community_code_material_required')
                    value,path=code.prepare(a.archive,a.manifest,a.review_sha256)
                    print(json.dumps(dict(community='UNSIGNED_CODE_PLAN',plan_sha256=sha(encoded(value)),path=str(path))))
                    return
                if a.command in ('code-install','code-recover'):
                    if not all([a.package_plan,a.package_plan_sha256,a.archive,a.manifest]):raise ValueError('community_code_exact_plan_required')
                    raw=Path(a.package_plan).read_bytes();value=json.loads(raw)
                    if raw!=encoded(value) or sha(raw)!=a.package_plan_sha256:raise ValueError('community_code_exact_plan')
                    result=code.install(value,a.archive,a.manifest,recover=a.command=='code-recover')
                    print(json.dumps(dict(community=result)));return
                host,supervisor=code.active() if code.marker.exists() else service.active()
            if a.command=='reference-prepare':
                if not all([a.archive,a.manifest,a.review_sha256,a.database_plan]):
                    raise ValueError('community_reference_material_required')
                manifest=json.loads(Path(a.manifest).read_bytes())
                provenance={k:manifest[k] for k in ('commit','run_id','run_attempt')}
                value,path=service.prepare(a.archive,a.manifest,provenance,a.review_sha256,a.database_plan)
                print(json.dumps(dict(community='UNSIGNED_REFERENCE_PLAN',plan_sha256=sha(encoded(value)),path=str(path))))
                return
            if a.command in ('reference-install','reference-recover'):
                if not all([a.reference_plan,a.reference_plan_sha256,a.archive,a.manifest]):
                    raise ValueError('community_reference_exact_plan_required')
                raw=Path(a.reference_plan).read_bytes();value=json.loads(raw)
                if raw!=encoded(value) or sha(raw)!=a.reference_plan_sha256:
                    raise ValueError('community_reference_exact_plan')
                result=service.install(value,a.archive,a.manifest,recover=a.command=='reference-recover')
                print(json.dumps(dict(community=result)));return
            if code_context is None:host,supervisor=service.active()
    elif a.command.startswith('package-') or (a.studio_publication_sha256 and (host.folder/'RELEASE_ACTIVE.json').exists()):
        from community_host import PackageUpgrade,package_context
        with lease.held():
            service=PackageUpgrade(host,supervisor,package_context(host,a.studio_publication_sha256))
            if a.command=='package-prepare':
                if not all([a.archive,a.manifest,a.review_sha256]):raise ValueError('community_package_material_required')
                # The archive verifier checks these fields against the whole bundle.
                manifest=json.loads(Path(a.manifest).read_bytes())
                provenance={k:manifest[k] for k in ('commit','run_id','run_attempt')}
                value,path=service.prepare(a.archive,a.manifest,provenance,a.review_sha256)
                print(json.dumps(dict(community='UNSIGNED_PACKAGE_PLAN',plan_sha256=sha(encoded(value)),path=str(path))))
                return
            if a.command in ('package-install','package-recover'):
                if not all([a.package_plan,a.package_plan_sha256,a.archive,a.manifest]):raise ValueError('community_package_plan_required')
                raw=Path(a.package_plan).read_bytes();value=json.loads(raw)
                if raw!=encoded(value) or sha(raw)!=a.package_plan_sha256:raise ValueError('community_package_exact_plan')
                result=service.install(value,a.archive,a.manifest,recover=a.command=='package-recover')
                print(json.dumps(dict(community=result)));return
            host,supervisor=service.active()
    if a.command in {'check','install','recover'}:
        if not all([a.archive,a.manifest,a.settings,a.node]):raise ValueError('community_material_required')
        with lease.held():
            if a.command=='check':host.material(a.archive,a.manifest,a.settings,a.node);result='STATIC_VERIFIED_NOT_AUTHORIZED'
            else:
                if not a.approval:raise ValueError('community_explicit_approval_required')
                result=host.install(a.archive,a.manifest,a.settings,a.node,a.approval,recover=a.command=='recover')
    elif a.command=='watchdog-preserve':
        with lease.held():result=host.preserve_running(supervisor)
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
        result=supervision_command(host,a.command,Path(__file__).parent,supervisor=supervisor,operation_id=a.operation_id)
    elif a.command=='bind-web':
        with lease.held():result=host.bind_web()
    elif a.command=='stop' and (host.root/'WATCHDOG.json').exists():
        result=supervision_command(host,'disable',Path(__file__).parent,supervisor=supervisor)
    else:
        with lease.held():result=host.control(a.command)
    print(json.dumps(dict(community=result)))


if __name__=='__main__':
    try:main()
    except Exception as error:
        # No config/SQL/credential payload in public errors.
        print(json.dumps(dict(community='REFUSED',error_type=type(error).__name__,reason=str(error))),file=sys.stderr)
        sys.exit(65)
