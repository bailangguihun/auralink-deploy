"""Execute the exact rendered original watchdog and drain hooks offline."""
from contextlib import contextmanager
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch, PropertyMock
import control_overlay as overlay
import vmm_lifecycle
import core_runtime

HERE=Path(__file__).parent


class WatchdogHook(unittest.TestCase):
    def setUp(self):
        self.before=vmm_lifecycle.extend_lifecycle((HERE/'fixtures/lifecycle_assistant_v2.py.snapshot').read_bytes())
        self.after=overlay.render_community_lifecycle(self.before)
        module=types.ModuleType('community_original_watchdog_fixture')
        module.__file__=str(HERE/'fixtures/lifecycle_assistant_v2.py.snapshot')
        exec(compile(self.after,module.__file__,'exec'),module.__dict__)
        self.m=module;self.engine=module.Engine.__new__(module.Engine)

    def test_exact_predecessor_and_vmm_method_bytes_unchanged(self):
        self.assertEqual(overlay.sha256(self.before),overlay.COMMUNITY_PREDECESSOR)
        with self.assertRaisesRegex(overlay.OverlayRefused,'predecessor'):
            overlay.render_community_lifecycle(self.before+b'\n')
        import ast
        def methods(raw):
            tree=ast.parse(raw);cls=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='Engine')
            return {x.name:ast.dump(x) for x in cls.body if isinstance(x,ast.FunctionDef)}
        before,after=methods(self.before),methods(self.after)
        self.assertEqual(set(after)-set(before),{'community_action'})
        changed={k for k in before if before[k]!=after[k]}
        self.assertEqual(changed,{'watchdog_once','java_stop','_stop_services'})

    def test_actual_watchdog_tick_failure_does_not_restart_core(self):
        diagnostic=Mock()
        patched=patch.object(self.m.Engine,'diagnostics',new_callable=PropertyMock,return_value=diagnostic);patched.start();self.addCleanup(patched.stop)
        e=self.engine;e.status=Mock(return_value={'healthy':True})
        e.vmm_action=Mock();e.community_action=Mock(side_effect=self.m.Refused('fixture unavailable'))
        e.recover=Mock();e.root=HERE/'fixture-absent-root'
        # Only the existing intentional-stop file lookup is redirected.
        with patch.object(self.m.Path,'exists',return_value=False):
            self.assertEqual(e.watchdog_once(),{'healthy':True})
        e.community_action.assert_called_once_with('tick');e.recover.assert_not_called()
        self.assertTrue(e.diagnostics.emit.called)

    def test_maintenance_drain_failure_precedes_watchdog_and_java_stop(self):
        e=Mock();e.community_action.side_effect=ValueError('community_drain_pending')
        rt=core_runtime.CoreRuntime.__new__(core_runtime.CoreRuntime);rt.fd=37
        rt.engine=Mock(return_value=e);rt.verify_current=Mock()
        with self.assertRaisesRegex(ValueError,'drain_pending'):rt.stop_owned_java(None)
        e.community_action.assert_called_once_with('drain',37)
        e.watchdog_stop_locked.assert_not_called();e.stop_role.assert_not_called()

    def test_java_stop_forwards_original_descriptor_and_refuses_pending_drain(self):
        e=self.engine;e.community_action=Mock(side_effect=self.m.Refused('community_drain_pending'));e.stop_role=Mock()
        @contextmanager
        def lease(**kw):yield 41
        e.lease=lease
        with self.assertRaisesRegex(self.m.Refused,'drain_pending'):e.java_stop()
        e.community_action.assert_called_once_with('drain',41);e.stop_role.assert_not_called()


if __name__=='__main__':unittest.main()
