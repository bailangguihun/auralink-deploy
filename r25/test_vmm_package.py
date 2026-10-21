import importlib.util
import io
from pathlib import Path
import unittest
import zipfile

import vmm_controller


class PackageTest(unittest.TestCase):
    def test_package_inventory_determinism_and_provenance(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('vmm_package', root / 'scripts/package-vmm-extension.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        evidence = dict(repository='bailangguihun/auralink-deploy', ref='refs/heads/release',
                        commit='a' * 40, run_id='1', run_attempt='1')
        first = module.materialize(root, evidence)
        self.assertEqual(first, module.materialize(root, evidence))
        with zipfile.ZipFile(io.BytesIO(first[0])) as archive:
            self.assertEqual(list(vmm_controller.MODULES), archive.namelist())
            self.assertTrue(all(x.external_attr >> 16 == 0o100600 for x in archive.infolist()))
        with self.assertRaises(AssertionError):
            module.materialize(root, dict(evidence, ref='refs/heads/main'))
