"""Installation bundle integrity, using the actual runtime source allowlist."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('release_bundle', ROOT / 'scripts/package-r25-release.py')
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)
IDENTITY = (bundle.REPOSITORY, bundle.REF, 'a' * 40, '42', '1')


class ReleaseBundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / 'out'

    def build(self, output=None):
        return bundle.create(ROOT / 'r25', output or self.output, *IDENTITY)

    def verify(self, expected=IDENTITY):
        return bundle.verify(self.output / bundle.ARCHIVE_NAME, self.output / bundle.MANIFEST_NAME, *expected)

    def test_actual_runtime_roundtrip_deterministic_allowlist(self):
        report = self.build()
        other = self.root / 'other'
        self.build(other)
        self.assertEqual(report, self.verify())
        for name in (bundle.ARCHIVE_NAME, bundle.MANIFEST_NAME):
            self.assertEqual((self.output / name).read_bytes(), (other / name).read_bytes())
        self.assertEqual(report['status'], 'NOT_DEPLOYED')
        self.assertIs(report['installation_performed'], False)
        self.assertEqual(len(report['files']), 28)
        self.assertEqual(sum(name.endswith('.py') for name in bundle.FILES), 27)
        self.assertEqual(set(bundle.FILES) - set(bundle.FILES_V1), {
            'assistant_rollout_binding.py', 'assistant_rollout_controller.py', 'assistant_rollout_prepare.py',
        })
        with zipfile.ZipFile(self.output / bundle.ARCHIVE_NAME) as archive:
            self.assertEqual(archive.namelist(), list(bundle.FILES))
            for name in bundle.FILES:
                self.assertEqual(archive.read(name), (ROOT / 'r25' / name).read_bytes())
        self.assertFalse(any(name.startswith('test_') or '/' in name or name.endswith('.env') for name in bundle.FILES))

    def test_historical_inventory_is_still_verifiable(self):
        self.output.mkdir()
        files = {name: (ROOT / 'r25' / name).read_bytes() for name in bundle.FILES_V1}
        payload = bundle.archive_bytes(files)
        manifest = bundle.manifest_for(files, payload, bundle.identity(*IDENTITY))
        (self.output / bundle.ARCHIVE_NAME).write_bytes(payload)
        (self.output / bundle.MANIFEST_NAME).write_text(json.dumps(manifest))
        self.assertEqual(self.verify(), manifest)

    def test_wrong_ref_repository_and_independent_event_identity_refused(self):
        self.build()
        for position, wrong in ((0, 'fork/auralink-deploy'), (1, 'refs/heads/main'),
                                (2, 'b' * 40), (3, '43'), (4, '2')):
            values = list(IDENTITY)
            values[position] = wrong
            with self.subTest(position=position), self.assertRaises(ValueError):
                self.verify(tuple(values))

    def test_transferred_repository_is_not_an_alias_for_historical_artifacts(self):
        self.build()
        self.assertEqual(self.verify()['repository'], 'bailangguihun/auralink-deploy')
        historical = (bundle.HISTORICAL_REPOSITORY, *IDENTITY[1:])
        with self.assertRaisesRegex(ValueError, 'manifest_or_expected_identity_mismatch'):
            self.verify(historical)
        # Synthetic old manifest: the payload is identical, but its recorded
        # repository remains part of the independent expected build identity.
        path = self.output / bundle.MANIFEST_NAME
        document = json.loads(path.read_text())
        document['repository'] = bundle.HISTORICAL_REPOSITORY
        path.write_text(json.dumps(document))
        self.assertEqual(self.verify(historical)['repository'], bundle.HISTORICAL_REPOSITORY)
        with self.assertRaisesRegex(ValueError, 'manifest_or_expected_identity_mismatch'):
            self.verify()
        for position, wrong in ((1, 'refs/heads/main'), (2, 'b' * 40), (3, '43'), (4, '2')):
            values = list(historical)
            values[position] = wrong
            with self.subTest(position=position), self.assertRaises(ValueError):
                self.verify(tuple(values))

    def test_new_builds_refuse_historical_or_foreign_repository_before_output(self):
        for repository in (bundle.HISTORICAL_REPOSITORY, 'fork/auralink-deploy',
                           'bailangguihun/another-repository', 'Bailangguihun/auralink-deploy'):
            with self.subTest(repository=repository), self.assertRaisesRegex(ValueError, 'new_release_repository'):
                bundle.create(ROOT / 'r25', self.output, repository, *IDENTITY[1:])
            self.assertFalse(self.output.exists())

    def test_changed_archive_and_manifest_rejected(self):
        self.build()
        path = self.output / bundle.ARCHIVE_NAME
        original = path.read_bytes()
        path.write_bytes(original + b'untracked')
        with self.assertRaisesRegex(ValueError, 'not_canonical'):
            self.verify()
        path.write_bytes(original)
        manifest = self.output / bundle.MANIFEST_NAME
        document = json.loads(manifest.read_text())
        document['installation_performed'] = True
        manifest.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, 'manifest_or_expected'):
            self.verify()
        document['installation_performed'] = 0
        manifest.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, 'manifest_or_expected'):
            self.verify()

    def test_missing_file_refused_before_output_creation(self):
        with self.assertRaises(FileNotFoundError):
            bundle.create(self.root / 'missing', self.output, *IDENTITY)
        self.assertFalse(self.output.exists())

    def test_existing_output_is_not_overwritten(self):
        self.build()
        original = (self.output / bundle.MANIFEST_NAME).read_bytes()
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertEqual((self.output / bundle.MANIFEST_NAME).read_bytes(), original)

    def test_extra_or_traversal_zip_entry_refused(self):
        for extra in ('test_runtime.py', '../outside.py', '.env'):
            with self.subTest(extra=extra):
                output = self.root / str(len(extra))
                self.build(output)
                with zipfile.ZipFile(output / bundle.ARCHIVE_NAME, 'a') as archive:
                    archive.writestr(extra, b'not included')
                with self.assertRaisesRegex(ValueError, 'archive_allowlist'):
                    bundle.verify(output / bundle.ARCHIVE_NAME, output / bundle.MANIFEST_NAME, *IDENTITY)

    def test_duplicate_manifest_and_unknown_fields_refused(self):
        self.build()
        path = self.output / bundle.MANIFEST_NAME
        original = path.read_text()
        path.write_text(original.replace('{', '{"schema": 1,', 1))
        with self.assertRaisesRegex(ValueError, 'duplicate_manifest_key'):
            self.verify()
        document = json.loads(original)
        document['extra'] = 'unreviewed'
        path.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, 'manifest_or_expected'):
            self.verify()

    def test_symlink_source_refused_if_platform_supports_it(self):
        link = self.root / 'source-link'
        try:
            link.symlink_to(ROOT / 'r25', target_is_directory=True)
        except OSError:
            self.skipTest('Symlink creation unavailable for this account; Linux CI exercises this case')
        with self.assertRaisesRegex(ValueError, 'symlink_path'):
            bundle.create(link, self.output, *IDENTITY)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
