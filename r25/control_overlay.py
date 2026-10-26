"""Render one audited lifecycle/launcher pair; never install or execute it.

This normal-controller overlay accepts registry-backed CURRENT/ACTIVE releases.
It does not grant historical f00a eligibility. Historical writers retain their
separately backed-up controllers and their original retained-data barriers.
Every edit is a single exact byte match after complete source-SHA validation.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib

BASE_LIFECYCLE_SHA256 = "b89b0abbf127d8a2f0b959646c6eeff6ca986a8fac5e626349efae74a75b28cb"
BASE_LAUNCHER_SHA256 = "26daa88555f928079ac59b5374bd1ba2c9fd144cb6d944ca8e0e56fc8f8a462c"
V1_LIFECYCLE_SHA256 = "21f92b7bb6e2a5581bcb0f58ea4fbaf9cd0049de60b84f2e1ec59767ecb07131"
V1_LAUNCHER_SHA256 = "c5583e5d0157518385f3d54259e72ee6cd1218b2ada0b1a6fe2c846faff66b33"


class OverlayRefused(ValueError):
    pass


@dataclass(frozen=True)
class RenderedControls:
    lifecycle: bytes
    launcher: bytes
    lifecycle_sha256: str
    launcher_sha256: str


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def replace_once(data, original, replacement, label):
    if data.count(original) != 1:
        raise OverlayRefused("exact_match_required:" + label)
    return data.replace(original, replacement, 1)


CORE_MODULE_METHOD = b"""    def core_release_guard_module(self):
        path=self.root/'operations/core_release_guard.py'
        digest=self.v.get('R25_CORE_RELEASE_GUARD_SHA256','')
        must(self.v.get('R25_CORE_RELEASE_GUARD')==str(path) and
             re.fullmatch('[0-9a-f]{64}',digest) is not None,'core_release_guard_binding')
        guard(path,self.script,0o700,digest)
        module=getattr(self,'_core_release_guard_module',None)
        if module is None:
            import sys
            raw=path.read_bytes()
            must(hashlib.sha256(raw).hexdigest()==digest,'core_release_guard_source_bytes')
            specification=importlib.util.spec_from_file_location('bound_core_release_guard',path)
            module=importlib.util.module_from_spec(specification)
            sys.modules[specification.name]=module
            exec(compile(raw,str(path),'exec',dont_inherit=True),module.__dict__)
            module.__r25_source_sha256__=digest
            self._core_release_guard_module=module
        must(Path(module.__file__).absolute()==path.absolute() and sha(Path(module.__file__))==digest and
             getattr(module,'__r25_source_sha256__',None)==digest,
             'loaded_core_release_guard_identity')
        return module
"""

AI_MODULE_METHOD = b"""    def ai_release_guard_module(self):
        path=self.root/'operations/ai_release_guard.py'
        digest=self.v.get('R25_AI_RELEASE_GUARD_SHA256','')
        must(self.v.get('R25_AI_RELEASE_GUARD')==str(path) and
             re.fullmatch('[0-9a-f]{64}',digest) is not None,'ai_release_guard_binding')
        guard(path,self.script,0o700,digest)
        module=getattr(self,'_ai_release_guard_module',None)
        if module is None:
            import sys
            raw=path.read_bytes()
            must(hashlib.sha256(raw).hexdigest()==digest,'ai_release_guard_source_bytes')
            specification=importlib.util.spec_from_file_location('bound_ai_release_guard',path)
            module=importlib.util.module_from_spec(specification)
            sys.modules[specification.name]=module
            exec(compile(raw,str(path),'exec',dont_inherit=True),module.__dict__)
            module.__r25_source_sha256__=digest
            self._ai_release_guard_module=module
        must(Path(module.__file__).absolute()==path.absolute() and sha(Path(module.__file__))==digest and
             getattr(module,'__r25_source_sha256__',None)==digest,
             'loaded_ai_release_guard_identity')
        return module
"""

OLD_CRITIC_LOOP = b"""            for name,digest in {**PYTHON[self.kind],'llm.py':LLM}.items():
                guard(self.root/'release/critic-service/app'/name,self.script,0o600,digest)
"""
NEW_CRITIC_LOOP = b"""            if 'R25_AI_RELEASE_GUARD' in self.v:
                module=self.ai_release_guard_module()
                for name in ('main.py','config.py','llm.py'):
                    digest=module.validate_critic_identity(self.root,name)
                    must(type(digest)is str and re.fullmatch('[0-9a-f]{64}',digest) is not None,
                         'ai_critic_source_digest')
                    guard(self.root/'release/critic-service/app'/name,self.script,0o600,digest)
            else:
                for name,digest in {**PYTHON[self.kind],'llm.py':LLM}.items():
                    guard(self.root/'release/critic-service/app'/name,self.script,0o600,digest)
"""

OLD_BACKEND_ELSE = b"        else:must(self.backend in allowed,'backend_manifest_binding')\n"
NEW_BACKEND_ELSE = b"""        else:
            module=self.core_release_guard_module()
            module.validate_backend_identity(self.root,self.backend)
"""

ELIGIBLE_MARKER = b"    def require_backend_eligible(self,fd):\n"
NORMAL_ELIGIBILITY = b"""        normal_module=self.core_release_guard_module()
        normal=normal_module.production()
        if normal.is_registered_backend(self.backend):
            try:return normal.require_backend_eligible(self.backend,fd)
            except normal_module.Refused as error:raise Refused(str(error)) from None
"""

LAUNCHER_EDITS = (
    (b"helper=root/'operations/huaniao_recovery_guard.py'",
     b"helper=root/'operations/core_release_guard.py'", "launcher_helper"),
    (b"entries.get('R25_HUANIAO_RECOVERY_GUARD_SHA256','')",
     b"entries.get('R25_CORE_RELEASE_GUARD_SHA256','')", "launcher_guard_hash"),
    (b"entries.get('R25_HUANIAO_RECOVERY_GUARD')",
     b"entries.get('R25_CORE_RELEASE_GUARD')", "launcher_guard_path"),
    (b"'java_huaniao_recovery_guard'", b"'java_core_release_guard'", "launcher_module_name"),
    (b"    module=importlib.util.module_from_spec(specification);specification.loader.exec_module(module)\n",
     b"    raw=helper.read_bytes()\n"
     b"    if hashlib.sha256(raw).hexdigest()!=expected:fail('core_release_guard_source_bytes')\n"
     b"    module=importlib.util.module_from_spec(specification)\n"
     b"    sys.modules[specification.name]=module\n"
     b"    exec(compile(raw,str(helper),'exec',dont_inherit=True),module.__dict__)\n"
     b"    module.__r25_source_sha256__=expected\n", "launcher_source_bytes"),
)


def render_controls(lifecycle, launcher):
    if type(lifecycle) is not bytes or type(launcher) is not bytes:
        raise OverlayRefused("source_bytes_required")
    if sha256(lifecycle) != BASE_LIFECYCLE_SHA256 or sha256(launcher) != BASE_LAUNCHER_SHA256:
        raise OverlayRefused("audited_source_sha256_mismatch")
    rendered_launcher = launcher
    for original, replacement, label in LAUNCHER_EDITS:
        rendered_launcher = replace_once(rendered_launcher, original, replacement, label)
    launcher_hash = sha256(rendered_launcher)
    lifecycle_marker = b"    def load_backend_identity(self):\n"
    rendered_lifecycle = replace_once(lifecycle, lifecycle_marker,
        CORE_MODULE_METHOD + AI_MODULE_METHOD + lifecycle_marker, "lifecycle_module_method")
    rendered_lifecycle = replace_once(rendered_lifecycle, OLD_BACKEND_ELSE,
        NEW_BACKEND_ELSE, "lifecycle_backend_identity")
    rendered_lifecycle = replace_once(rendered_lifecycle, ELIGIBLE_MARKER,
        ELIGIBLE_MARKER + NORMAL_ELIGIBILITY, "lifecycle_normal_eligibility")
    rendered_lifecycle = replace_once(rendered_lifecycle, OLD_CRITIC_LOOP,
        NEW_CRITIC_LOOP, "lifecycle_registered_ai_identity")
    old_launcher_constant = ("JAVA_LAUNCH = '" + BASE_LAUNCHER_SHA256 + "'\n").encode("ascii")
    new_launcher_constant = ("JAVA_LAUNCH = '" + launcher_hash + "'\n").encode("ascii")
    rendered_lifecycle = replace_once(rendered_lifecycle, old_launcher_constant,
        new_launcher_constant, "lifecycle_launcher_digest")
    try:
        # Parse only. The official launcher has top-level execve and must never
        # be imported/executed by this renderer or its validation tests.
        ast.parse(rendered_launcher.decode("utf-8"))
        ast.parse(rendered_lifecycle.decode("utf-8"))
    except (UnicodeError, SyntaxError) as error:
        raise OverlayRefused("rendered_python_syntax") from error
    return RenderedControls(rendered_lifecycle, rendered_launcher,
                            sha256(rendered_lifecycle), launcher_hash)


BUSINESS_LAUNCH_MARKER = b"        recovery.require_backend_eligible(backend_hash,descriptor)\n"
BUSINESS_LAUNCH_HOOK = BUSINESS_LAUNCH_MARKER + b"""        if hasattr(recovery,'prepare_launch'):
            recovery.prepare_launch(backend_hash,descriptor)
"""


def render_business_controls(lifecycle, launcher):
    """Add the V2 sandbox hook only to the exact already-reviewed V1 pair.

    The hook executes in the Java launcher child immediately before exec, after
    eligibility validation. V1 guards have no prepare_launch and retain their
    behavior. UID, owned process checks, all non-Java and c5ea code stay intact.
    """
    if type(lifecycle) is not bytes or type(launcher) is not bytes:
        raise OverlayRefused('source_bytes_required')
    if sha256(lifecycle) != V1_LIFECYCLE_SHA256 or sha256(launcher) != V1_LAUNCHER_SHA256:
        raise OverlayRefused('audited_v1_source_sha256_mismatch')
    rendered_launcher = replace_once(launcher, BUSINESS_LAUNCH_MARKER,
                                     BUSINESS_LAUNCH_HOOK, 'business_launch_sandbox')
    launcher_sha = sha256(rendered_launcher)
    rendered_lifecycle = replace_once(lifecycle,
        ("JAVA_LAUNCH = '" + V1_LAUNCHER_SHA256 + "'\n").encode(),
        ("JAVA_LAUNCH = '" + launcher_sha + "'\n").encode(), 'business_launcher_digest')
    try:
        ast.parse(rendered_launcher.decode('utf-8'))
        ast.parse(rendered_lifecycle.decode('utf-8'))
    except (SyntaxError, UnicodeError) as error:
        raise OverlayRefused('rendered_python_syntax') from error
    return RenderedControls(rendered_lifecycle, rendered_launcher,
                            sha256(rendered_lifecycle), launcher_sha)


# Exact currently installed managed lifecycle, reproduced by the unchanged
# vmm_lifecycle.extend_lifecycle(fixtures/lifecycle_assistant_v2.py.snapshot).
COMMUNITY_PREDECESSOR = '0bfd456125b9a3f09b8398aade49615f9db1a1127617ea99ac748613a40be919'
COMMUNITY_METHOD = b'''    def community_action(self, mode, lease_fd=None):
        # Optional registration, not another daemon or a GPU/VMM dependency.
        marker=self.root/'runtime/community/WATCHDOG.json'
        if not marker.exists() and not marker.is_symlink():return 'DISABLED'
        guard(marker,self.private)
        record=json.loads(marker.read_bytes())
        keys={'schema','plan_sha256','controller_manifest_sha256','cli_sha256','python_sha256'}
        must(set(record)==keys and record['schema']=='ARTLIVE_COMMUNITY_WATCHDOG_V1','community_registration')
        for key in keys-{'schema'}:must(re.fullmatch('[a-f0-9]{64}',record[key]),'community_registration_digest')
        bundle=self.root/'operations/community-controller'/record['controller_manifest_sha256']
        guard(bundle,self.private,0o700,directory=True)
        entry=bundle/'community_host_cli.py'
        guard(entry,self.private,0o600,record['cli_sha256'])
        guard(bundle/'manifest.json',self.private,0o600,record['controller_manifest_sha256'])
        python=Path('/usr/bin/python3.10')
        guard(python,self.private,0o755,record['python_sha256'])
        for path in (marker,entry,bundle/'manifest.json',python):
            must(path.stat().st_nlink==1,'community_single_link')
        must(mode in ('tick','drain'),'community_watchdog_mode')
        plan=self.root/'runtime/community/installations'/record['plan_sha256']/'approval.json'
        guard(plan,self.private,0o600,record['plan_sha256'])
        def invoke(fd):
            # The child verifies this SAME descriptor with the original guard.
            result=subprocess.run([str(python),'-I','-B',str(entry),'watchdog-'+mode,
                '--plan',str(plan),'--plan-sha256',record['plan_sha256'],
                '--bundle-manifest-sha256',record['controller_manifest_sha256'],'--lease-fd',str(fd)],
                env={},stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                pass_fds=(fd,),timeout=20)
            if result.returncode:
                self.diagnostics.emit('COMMUNITY_WATCHDOG_REFUSED',phase=mode,exit_status=result.returncode,
                    diagnostic=result.stderr.decode(errors='replace')[-4096:])
                raise Refused('community_watchdog_refused')
            value=json.loads(result.stdout)['community']
            self.diagnostics.emit('COMMUNITY_WATCHDOG',phase=mode,state=value)
            if mode=='drain':must(value in ('DISABLED','NOT_STARTED','STOPPED','EXITED'),'community_drain_pending')
            return value
        if lease_fd is not None:return invoke(lease_fd)
        with self.lease() as fd:return invoke(fd)

'''
COMMUNITY_WATCHDOG = b'''            try:self.community_action('tick')
            except (Refused,Busy,OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
                self.diagnostics.emit('COMMUNITY_WATCHDOG_FAILURE',error_type=type(error).__name__,reason=str(error))
'''


def render_community_lifecycle(before):
    """One exact retained lifecycle -> optional original-watchdog hook.

    No VMM code, lock, wrapper or launcher is replaced by this renderer.
    The finite publication successor must independently bind its output.
    """
    if sha256(before) != COMMUNITY_PREDECESSOR:
        raise OverlayRefused('community_lifecycle_predecessor')
    raw=replace_once(before,b'    def watchdog_once(self):\n',
        COMMUNITY_METHOD+b'    def watchdog_once(self):\n','community_method')
    raw=replace_once(raw,b'            return result\n        except Busy:',
        COMMUNITY_WATCHDOG+b'            return result\n        except Busy:','community_watchdog_tick')
    raw=replace_once(raw,b"        with self.lease(partial=True):self.stop_role('java',19080)\n",
        b"        with self.lease(partial=True) as fd:\n            self.community_action('drain',fd)\n            self.stop_role('java',19080)\n",'community_java_drain')
    raw=replace_once(raw,b'            # All currently running identities are established before signals.\n',
        b"            self.community_action('drain',fd)\n            # All currently running identities are established before signals.\n",'community_application_drain')
    ast.parse(raw)
    return raw

