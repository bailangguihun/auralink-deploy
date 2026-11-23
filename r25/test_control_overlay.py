"""Exact-hash rendering and byte-preservation checks; never execute launcher."""
import ast
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

import control_overlay as overlay


def fixtures():
    launcher = b"""import importlib.util
from pathlib import Path
import os
def guard_backend_start(root, backend_hash):
    entries={}
    helper=root/'operations/huaniao_recovery_guard.py'
    expected=entries.get('R25_HUANIAO_RECOVERY_GUARD_SHA256','')
    if entries.get('R25_HUANIAO_RECOVERY_GUARD')!=str(helper):raise ValueError('binding')
    specification=importlib.util.spec_from_file_location('java_huaniao_recovery_guard',helper)
    module=importlib.util.module_from_spec(specification);specification.loader.exec_module(module)
    lease=os.environ.get('R25_DEPLOYMENT_LEASE_FD')
    # Original launcher lease/identity/exec boundaries remain here.
os.execve('/fixed/java', [], {})
"""
    lifecycle = (b"JAVA_LAUNCH = '" + overlay.sha256(launcher).encode() + b"'\n" +
b"""class Lifecycle:
    def load_backend_identity(self):
        self.backend=self.v.get('R25_BACKEND_SHA256')
        allowed={'historic-fixed-digest'}
        if self.reconciliation_contract is not None:
            pass
        else:must(self.backend in allowed,'backend_manifest_binding')
        manifest=self.root/'release/MANIFEST.json'
        return self.backend
    def require_backend_eligible(self,fd):
        return self.historical_guard(fd)
    def load(self,partial=False):
        if not partial:
            for name,digest in {**PYTHON[self.kind],'llm.py':LLM}.items():
                guard(self.root/'release/critic-service/app'/name,self.script,0o600,digest)
    def unchanged_control(self):
        return 'fixed_owned_lease_and_metadata_checks'
""")
    return lifecycle, launcher


def approved_fixture(lifecycle, launcher):
    return patch.multiple(overlay, BASE_LIFECYCLE_SHA256=overlay.sha256(lifecycle),
                          BASE_LAUNCHER_SHA256=overlay.sha256(launcher))


def undo(result, original_launcher_hash):
    launcher = result.launcher
    for original, replacement, label in reversed(overlay.LAUNCHER_EDITS):
        launcher = overlay.replace_once(launcher, replacement, original, "undo_" + label)
    lifecycle = overlay.replace_once(result.lifecycle, overlay.CORE_MODULE_METHOD, b"", "undo_method")
    lifecycle = overlay.replace_once(lifecycle, overlay.AI_MODULE_METHOD, b"", "undo_ai_method")
    lifecycle = overlay.replace_once(lifecycle, overlay.NEW_BACKEND_ELSE,
                                    overlay.OLD_BACKEND_ELSE, "undo_identity")
    lifecycle = overlay.replace_once(lifecycle, overlay.NORMAL_ELIGIBILITY, b"", "undo_eligibility")
    lifecycle = overlay.replace_once(lifecycle, overlay.NEW_CRITIC_LOOP,
                                    overlay.OLD_CRITIC_LOOP, "undo_ai_identity")
    current = ("JAVA_LAUNCH = '" + result.launcher_sha256 + "'\n").encode()
    previous = ("JAVA_LAUNCH = '" + original_launcher_hash + "'\n").encode()
    lifecycle = overlay.replace_once(lifecycle, current, previous, "undo_launcher_hash")
    return lifecycle, launcher


class OverlayTests(unittest.TestCase):
    def test_assistant_rollout_routes_only_from_existing_community_code_overlay(self):
        anchor = b"        bundle=self.root/'operations/community-controller'/record['controller_manifest_sha256']\n"
        legacy = b"class Engine:\n    def choose(self):\n" + anchor
        with self.assertRaisesRegex(overlay.OverlayRefused, 'assistant_rollout_requires_community_code'):
            overlay.render_assistant_rollout_lifecycle(legacy)
        code = legacy.replace(anchor, b"        extra+=['--code-publication-sha256',code_id]\n" + anchor)
        rendered = overlay.render_assistant_rollout_lifecycle(code)
        ast.parse(rendered)
        self.assertEqual(rendered.count(anchor), 1)
        self.assertIn(b"extra+=['--assistant-rollout-sha256',rollout_id]", rendered)
        self.assertIn(b"must(extra[-2:]==['--code-publication-sha256',community['code_controller_sha256']]", rendered)
        with self.assertRaises(overlay.OverlayRefused):
            overlay.render_assistant_rollout_lifecycle(rendered)

    def test_unapproved_hash_and_nonbytes_input_are_refused(self):
        lifecycle, launcher = fixtures()
        for inputs in ((lifecycle, launcher), (bytearray(lifecycle), launcher),
                       (lifecycle, bytearray(launcher)), (b"", b"")):
            with self.subTest(types=tuple(map(type, inputs))), self.assertRaises(overlay.OverlayRefused):
                overlay.render_controls(*inputs)

    def test_fixture_render_parses_and_preserves_every_other_byte(self):
        lifecycle, launcher = fixtures()
        with approved_fixture(lifecycle, launcher):
            result = overlay.render_controls(lifecycle, launcher)
        ast.parse(result.lifecycle.decode())
        ast.parse(result.launcher.decode())
        self.assertEqual(undo(result, overlay.sha256(launcher)), (lifecycle, launcher))
        self.assertEqual(result.lifecycle_sha256, overlay.sha256(result.lifecycle))
        self.assertEqual(result.launcher_sha256, overlay.sha256(result.launcher))
        self.assertIn(("JAVA_LAUNCH = '" + result.launcher_sha256 + "'").encode(), result.lifecycle)

    def test_exact_edit_match_zero_and_duplicate_are_refused(self):
        for source in (b"nothing", b"expected expected"):
            with self.assertRaisesRegex(overlay.OverlayRefused, "exact_match_required"):
                overlay.replace_once(source, b"expected", b"replacement", "fixture")

    def test_approved_fixture_with_missing_or_duplicate_marker_is_refused(self):
        lifecycle, launcher = fixtures()
        for changed in (lifecycle.replace(overlay.OLD_BACKEND_ELSE, b"        else:pass\n"),
                        lifecycle + overlay.ELIGIBLE_MARKER):
            with approved_fixture(changed, launcher), self.assertRaises(overlay.OverlayRefused):
                overlay.render_controls(changed, launcher)

    def test_normal_identity_never_falls_back_to_finite_allowlist(self):
        lifecycle, launcher = fixtures()
        with approved_fixture(lifecycle, launcher):
            result = overlay.render_controls(lifecycle, launcher)
        self.assertNotIn(overlay.OLD_BACKEND_ELSE, result.lifecycle)
        self.assertIn(b"module.validate_backend_identity(self.root,self.backend)", result.lifecycle)
        self.assertIn(b"R25_CORE_RELEASE_GUARD_SHA256", result.lifecycle)
        self.assertIn(b"guard(path,self.script,0o700,digest)", result.lifecycle)

    def test_registered_backend_uses_normal_guard_and_history_is_preserved(self):
        lifecycle, launcher = fixtures()
        with approved_fixture(lifecycle, launcher):
            result = overlay.render_controls(lifecycle, launcher)
        tree = ast.parse(result.lifecycle.decode())
        method = next(node for node in tree.body[1].body
                      if isinstance(node, ast.FunctionDef) and node.name == "require_backend_eligible")
        isolated = ast.Module(body=[method], type_ignores=[])
        namespace = {"Refused": RuntimeError}
        exec(compile(ast.fix_missing_locations(isolated), "<synthetic-eligibility-fixture>", "exec"),
             namespace)
        calls = []
        class Normal:
            registered = True
            def is_registered_backend(self, backend): return self.registered
            def require_backend_eligible(self, backend, fd):
                calls.append(("normal", backend, fd))
                return "normal"
        normal = Normal()
        class Module:
            Refused = RuntimeError
            @staticmethod
            def production(): return normal
        class Context:
            backend = "registry-backend"
            def core_release_guard_module(self): return Module
            def historical_guard(self, fd):
                calls.append(("historic", fd))
                return "historic"
        method = namespace["require_backend_eligible"]
        self.assertEqual(method(Context(), 17), "normal")
        self.assertEqual(calls, [("normal", "registry-backend", 17)])
        normal.registered = False
        self.assertEqual(method(Context(), 17), "historic")
        self.assertEqual(calls[-1], ("historic", 17))

    def test_reapplying_overlay_is_refused(self):
        lifecycle, launcher = fixtures()
        with approved_fixture(lifecycle, launcher):
            result = overlay.render_controls(lifecycle, launcher)
            with self.assertRaisesRegex(overlay.OverlayRefused, "audited_source_sha256_mismatch"):
                overlay.render_controls(result.lifecycle, result.launcher)

    def test_ai_hook_retains_exact_legacy_loop_ast_when_key_is_absent(self):
        lifecycle, launcher = fixtures()
        original_class = ast.parse(lifecycle.decode()).body[1]
        original_load = next(node for node in original_class.body if node.name == "load")
        original_loop = original_load.body[0].body[0]
        with approved_fixture(lifecycle, launcher):
            result = overlay.render_controls(lifecycle, launcher)
        rendered_class = ast.parse(result.lifecycle.decode()).body[1]
        rendered_load = next(node for node in rendered_class.body if node.name == "load")
        optional_hook = rendered_load.body[0].body[0]
        self.assertEqual(ast.dump(optional_hook.orelse[0]), ast.dump(original_loop))

    def test_ai_guard_accepts_only_three_fixed_names_and_strict_digests(self):
        import re
        lifecycle, launcher = fixtures()
        with approved_fixture(lifecycle, launcher):
            result = overlay.render_controls(lifecycle, launcher)
        rendered_class = ast.parse(result.lifecycle.decode()).body[1]
        method = next(node for node in rendered_class.body if node.name == "load")
        calls = []
        expected = {"main.py": "a" * 64, "config.py": "b" * 64, "llm.py": "c" * 64}
        def must(ok, reason):
            if not ok: raise overlay.OverlayRefused(reason)
        namespace = {"re": re, "must": must, "PYTHON": {"candidate-isolated": {
            "main.py": expected["main.py"], "config.py": expected["config.py"]}},
            "LLM": expected["llm.py"], "guard": lambda *args: calls.append(args)}
        isolated = ast.Module(body=[method], type_ignores=[])
        exec(compile(ast.fix_missing_locations(isolated), "<synthetic-ai-identity-fixture>", "exec"), namespace)
        class Module:
            @staticmethod
            def validate_critic_identity(root, name): return expected[name]
        class Context:
            root = Path("/root/fixed")
            script = (1000, 1000)
            kind = "candidate-isolated"
            v = {}
            def ai_release_guard_module(self): return Module
        load = namespace["load"]
        context = Context()
        load(context)
        self.assertEqual([call[0].name for call in calls], ["main.py", "config.py", "llm.py"])
        calls.clear()
        context.v = {"R25_AI_RELEASE_GUARD": "/root/fixed/operations/ai_release_guard.py"}
        load(context)
        self.assertEqual([call[0].name for call in calls], ["main.py", "config.py", "llm.py"])
        self.assertTrue(all(call[1:3] == ((1000, 1000), 0o600) for call in calls))
        expected["main.py"] = "not-an-approved-digest"
        calls.clear()
        with self.assertRaisesRegex(overlay.OverlayRefused, "ai_critic_source_digest"):
            load(context)
        self.assertEqual(calls, [])

    def test_actual_audited_pair_is_hash_bound_and_preserved(self):
        root = Path(__file__).resolve().parent / "fixtures"
        lifecycle_path = root / "lifecycle_adapter.py.snapshot"
        launcher_path = root / "r25-production-java-launch.py.snapshot"
        lifecycle, launcher = lifecycle_path.read_bytes(), launcher_path.read_bytes()
        result = overlay.render_controls(lifecycle, launcher)
        self.assertEqual(undo(result, overlay.BASE_LAUNCHER_SHA256), (lifecycle, launcher))
        self.assertIn(b"os.execve(args[0], args, environment)", result.launcher)
        self.assertIn(b"if descriptor is not None:os.close(descriptor)", result.launcher)
        with self.assertRaises(overlay.OverlayRefused):
            overlay.render_controls(lifecycle + b"\n", launcher)

    def test_business_overlay_preserves_every_byte_except_hook_and_bound_launcher_hash(self):
        root = Path(__file__).resolve().parent / 'fixtures'
        old = overlay.render_controls((root / 'lifecycle_adapter.py.snapshot').read_bytes(),
                                      (root / 'r25-production-java-launch.py.snapshot').read_bytes())
        result = overlay.render_business_controls(old.lifecycle, old.launcher)
        recovered_launcher = overlay.replace_once(result.launcher, overlay.BUSINESS_LAUNCH_HOOK,
                                                    overlay.BUSINESS_LAUNCH_MARKER, 'undo_business_hook')
        recovered_lifecycle = overlay.replace_once(result.lifecycle,
            ("JAVA_LAUNCH = '" + result.launcher_sha256 + "'\n").encode(),
            ("JAVA_LAUNCH = '" + old.launcher_sha256 + "'\n").encode(), 'undo_business_digest')
        self.assertEqual((recovered_lifecycle, recovered_launcher), (old.lifecycle, old.launcher))
        self.assertIn(b'os.execve(args[0], args, environment)', result.launcher)
        with self.assertRaisesRegex(overlay.OverlayRefused, 'audited_v1_source_sha256_mismatch'):
            overlay.render_business_controls(result.lifecycle, result.launcher)
        with self.assertRaisesRegex(overlay.OverlayRefused, 'audited_v1_source_sha256_mismatch'):
            overlay.render_business_controls(old.lifecycle, old.launcher + b'\n')

    def test_business_launch_hook_runs_after_eligibility_and_is_absent_for_v1(self):
        source = b''.join(line[8:] for line in overlay.BUSINESS_LAUNCH_HOOK.splitlines(keepends=True))
        calls = []
        class V1:
            def require_backend_eligible(self, backend, descriptor):
                calls.append(('eligible', backend, descriptor))
        class V2(V1):
            def prepare_launch(self, backend, descriptor):
                calls.append(('sandbox', backend, descriptor))
        scope = {'backend_hash': 'fixture-sha', 'descriptor': 42}
        exec(compile(source, '<isolated-launcher-hook>', 'exec'), dict(scope, recovery=V1()))
        self.assertEqual(calls, [('eligible', 'fixture-sha', 42)])
        calls.clear()
        exec(compile(source, '<isolated-launcher-hook>', 'exec'), dict(scope, recovery=V2()))
        self.assertEqual(calls, [('eligible', 'fixture-sha', 42), ('sandbox', 'fixture-sha', 42)])


if __name__ == "__main__":
    unittest.main()

