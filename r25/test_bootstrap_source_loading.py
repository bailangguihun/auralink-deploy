"""First-install source-cache refusals with real temporary bytecode files.

The direct CLI refuses before imports or runtime construction. No production
controller, process, PostgreSQL connection or credential is used.
"""
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import install_core_controller as installer


class BootstrapSourceLoadingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='r25-bootstrap-source-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def stale_cache(self, path, marker):
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = ('from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\n').encode()
        path.write_bytes(raw)
        before = path.stat()
        cached = Path(py_compile.compile(str(path), doraise=True,
            invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP))
        path.write_bytes(b'#' + b' ' * (len(raw) - 2) + b'\n')
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        return cached

    def test_direct_cli_rejects_real_stale_sibling_cache_before_it_executes(self):
        script = self.root / 'install_core_controller.py'
        shutil.copyfile(installer.__file__, script)
        marker = self.root / 'unapproved-code-executed'
        cached = self.stale_cache(self.root / 'core_release_guard.py', marker)
        for python_options in ([], ['-B']):
            with self.subTest(python_options=python_options):
                result = subprocess.run([sys.executable, *python_options, str(script), '--help'],
                    capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 65, result.stderr)
                self.assertEqual(json.loads(result.stderr)['reason'], 'bootstrap_bundle_bytecode_cache')
                self.assertFalse(marker.exists())
                self.assertTrue(cached.exists(), 'Refusal must preserve cache evidence')

    def test_legacy_helper_cache_refuses_before_live_or_saved_engine_load(self):
        marker = self.root / 'unapproved-helper-executed'
        helper = self.root / 'operations/postgres_access.py'
        cached = self.stale_cache(helper, marker)
        with patch.object(installer.guard, 'ROOT', self.root), \
             patch.object(installer, 'load_module') as load_module:
            for source, partial in ((None, False), (self.root / 'saved-lifecycle.py', True)):
                with self.subTest(recovery=partial), self.assertRaisesRegex(
                        installer.guard.Refused, 'legacy_postgres_bytecode_cache'):
                    installer.old_engine(SimpleNamespace(fd=9), source=source, partial=partial)
            load_module.assert_not_called()
        self.assertFalse(marker.exists())
        self.assertTrue(cached.exists())

    def test_archived_extensionless_lifecycle_compiles_only_verified_bytes(self):
        path = self.root / 'before' / 'lifecycle'
        path.parent.mkdir()
        path.write_bytes(b'raise AssertionError("read unverified disk bytes")\n')
        verified = b'RECOVERY_SOURCE = "verified"\n'
        module = installer.guard.source_module(path, 'fixture_extensionless_lifecycle', verified)
        self.addCleanup(sys.modules.pop, 'fixture_extensionless_lifecycle', None)
        self.assertEqual(module.RECOVERY_SOURCE, 'verified')
        self.assertEqual(Path(module.__file__), path)

    def test_prefix_and_symlink_cache_are_refused_without_cleanup(self):
        prefix = self.root / 'prefix'
        with patch.object(sys, 'pycache_prefix', str(prefix)):
            cached = Path(importlib.util.cache_from_source(str(self.root / 'core_runtime.py')))
            cached.parent.mkdir(parents=True)
            cached.write_bytes(b'synthetic cache')
            with self.assertRaisesRegex(ValueError, 'bootstrap_bundle_bytecode_cache'):
                installer.reject_bootstrap_bytecode(self.root)
            self.assertTrue(cached.exists())
        link = self.root / '__pycache__'
        link.symlink_to(self.root / 'absent-cache', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'bootstrap_bundle_bytecode_cache'):
            installer.reject_bootstrap_bytecode(self.root)
        self.assertTrue(link.is_symlink())


if __name__ == '__main__':
    unittest.main()
