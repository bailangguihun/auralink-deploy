"""Fast synthetic contracts for CI sharding; never discovers the real R25 suite."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/r25_test_shards.py'
COMMIT = '1234567890abcdef1234567890abcdef12345678'
MODULES = {
    1: 'test_managed_vmm_publication',
    2: 'test_community_publication_repair',
    3: 'test_community_publication_successor',
}


def load_runner():
    spec = importlib.util.spec_from_file_location('synthetic_r25_test_shards', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_module(directory, name, source):
    path = directory / (name + '.py')
    path.write_text(textwrap.dedent(source), encoding='utf-8')
    return path


def fixture_tree(directory):
    directory.mkdir()
    for name in MODULES.values():
        write_module(directory, name, '''
            import unittest
            class Dedicated(unittest.TestCase):
                def test_ok(self):
                    self.assertTrue(True)
        ''')
    write_module(directory, 'test_remainder', '''
        import unittest
        class Repeated(unittest.TestCase):
            def test_same_id(self):
                self.assertEqual(2 + 2, 4)
        def load_tests(loader, tests, pattern):
            return unittest.TestSuite([
                Repeated('test_same_id'), Repeated('test_same_id')])
    ''')
    return directory


def cli(*args):
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    return subprocess.run(
        [sys.executable, '-B', str(SCRIPT), *map(str, args)],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=20)


def run_report(start, directory, shard):
    path = directory / ('shard-' + str(shard) + '.json')
    result = cli('run', '--shard', shard, '--report', path, '--commit', COMMIT,
                 '--start-directory', start, '--pattern', 'test_*.py')
    if not path.is_file():
        raise AssertionError('CLI did not retain report: ' + result.stderr + result.stdout)
    return result, json.loads(path.read_text()), path


class PartitionContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def test_every_occurrence_once_with_duplicate_ids_and_discovery_order(self):
        class Case:
            def __init__(self, identity):
                self.identity = identity
            def id(self):
                return self.identity
        ids = [
            'test_misc.C.test_z',
            MODULES[1] + '.C.test_a',
            MODULES[2] + '.C.test_b',
            MODULES[1] + '.C.test_a',
            'test_newly_discovered.C.test_new',
            MODULES[3] + '.C.test_c',
            'test_misc.C.test_z',
        ]
        tests = [Case(identity) for identity in ids]
        expected = {1: [1, 3], 2: [2], 3: [5], 4: [0, 4, 6]}
        observed = []
        for shard in range(1, 5):
            partition = self.runner.partition_tests(tests, shard)
            self.assertEqual([index for index, _ in partition], expected[shard])
            for index, test in partition:
                self.assertIs(test, tests[index])
            observed.extend(index for index, _ in partition)
        self.assertEqual(sorted(observed), list(range(len(tests))))
        self.assertEqual(len(observed), len(set(observed)))


class RunContracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='r25-shards-synthetic-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.start = fixture_tree(self.root / 'tests')
        self.reports = self.root / 'reports'
        self.reports.mkdir()

    def test_four_cli_shards_cover_discovery_and_new_tests_join_remainder(self):
        write_module(self.start, 'test_brand_new', '''
            import unittest
            class New(unittest.TestCase):
                def test_added_without_runner_edit(self):
                    pass
        ''')
        reports = []
        for shard in range(1, 5):
            result, report, _ = run_report(self.start, self.reports, shard)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(report['successful'])
            self.assertEqual(report['shard'], shard)
            self.assertEqual(report['shard_count'], 4)
            self.assertEqual(report['commit'], COMMIT)
            self.assertEqual(report['tests_run'], len(report['executed']))
            self.assertEqual(report['assigned_indices'],
                             [item['occurrence_index'] for item in report['executed']])
            reports.append(report)
        discovered = reports[0]['discovered_ids']
        self.assertEqual(len(discovered), 6)
        self.assertLess(len(set(discovered)), len(discovered))
        self.assertTrue(all(report['discovered_ids'] == discovered for report in reports))
        executed = [item for report in reports for item in report['executed']]
        self.assertEqual(sorted(item['occurrence_index'] for item in executed),
                         list(range(len(discovered))))
        self.assertTrue(any(item['test_id'].startswith('test_brand_new.')
                            for item in reports[3]['executed']))
        for item in executed:
            self.assertEqual(item['test_id'], discovered[item['occurrence_index']])
        result = cli('verify-reports', '--directory', self.reports, '--commit', COMMIT)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_discovery_import_error_fails_every_shard_and_retains_report(self):
        write_module(self.start, 'test_unrelated_broken_import', '''
            raise ImportError('synthetic discovery import failure')
        ''')
        for shard in range(1, 5):
            with self.subTest(shard=shard):
                result, report, _ = run_report(self.start, self.reports, shard)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(report['successful'])
                self.assertTrue(report['discovery_errors'])
                self.assertIn('synthetic discovery import failure',
                              json.dumps(report['discovery_errors']))

    def test_test_failure_and_error_return_nonzero_and_retain_details(self):
        write_module(self.start, 'test_outcomes', '''
            import unittest
            class Outcomes(unittest.TestCase):
                def test_failure(self):
                    self.fail('synthetic assertion failure')
                def test_error(self):
                    raise RuntimeError('synthetic test exception')
        ''')
        result, report, _ = run_report(self.start, self.reports, 4)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(report['successful'])
        self.assertEqual(len(report['failures']), 1)
        self.assertEqual(len(report['errors']), 1)
        self.assertIn('synthetic assertion failure', json.dumps(report['failures']))
        self.assertIn('synthetic test exception', json.dumps(report['errors']))
        outcomes = {item['test_id']: item['outcome'] for item in report['executed']}
        self.assertEqual(outcomes['test_outcomes.Outcomes.test_failure'], 'failure')
        self.assertEqual(outcomes['test_outcomes.Outcomes.test_error'], 'error')

    def test_interrupted_process_leaves_unsuccessful_incomplete_report(self):
        write_module(self.start, 'test_cancelled_process', '''
            import os
            import unittest
            class Interrupted(unittest.TestCase):
                def test_interrupt_this_synthetic_child(self):
                    os._exit(23)
        ''')
        result, report, _ = run_report(self.start, self.reports, 4)
        self.assertEqual(result.returncode, 23)
        self.assertFalse(report['successful'])
        self.assertEqual(report['status'], 'incomplete')
        self.assertLess(len(report['executed']), len(report['assigned_indices']))

    def test_timing_includes_setup_teardown_cleanup_and_preserves_class_fixture(self):
        write_module(self.start, 'test_timing', '''
            import time
            import unittest
            class_calls = 0
            module_calls = 0
            def setUpModule():
                global module_calls
                module_calls += 1
                time.sleep(.02)
            def tearDownModule():
                time.sleep(.02)
            class Timed(unittest.TestCase):
                @classmethod
                def setUpClass(cls):
                    global class_calls
                    class_calls += 1
                    time.sleep(.02)
                @classmethod
                def tearDownClass(cls):
                    time.sleep(.02)
                def setUp(self):
                    time.sleep(.02)
                    self.addCleanup(time.sleep, .02)
                def tearDown(self):
                    time.sleep(.02)
                def test_a(self):
                    self.assertEqual(class_calls, 1)
                    self.assertEqual(module_calls, 1)
                def test_b(self):
                    self.assertEqual(class_calls, 1)
                    self.assertEqual(module_calls, 1)
        ''')
        result, report, _ = run_report(self.start, self.reports, 4)
        self.assertEqual(result.returncode, 0, result.stderr)
        timed = [item for item in report['executed'] if item['test_id'].startswith('test_timing.')]
        self.assertEqual(len(timed), 2)
        self.assertTrue(all(item['duration_seconds'] >= .055 for item in timed), timed)
        self.assertGreaterEqual(report['run_seconds'], .19)
        self.assertGreaterEqual(report['total_seconds'], report['run_seconds'])
        self.assertGreaterEqual(report['total_seconds'], report['discovery_seconds'])
        self.assertEqual(report['slowest_tests'][:2],
                         sorted(timed, key=lambda item: item['duration_seconds'], reverse=True))

    def test_skip_and_expected_failure_remain_successful(self):
        write_module(self.start, 'test_expected', '''
            import unittest
            class Expected(unittest.TestCase):
                @unittest.skip('synthetic skip')
                def test_skip(self):
                    self.fail('must not execute')
                @unittest.expectedFailure
                def test_expected_failure(self):
                    self.fail('expected')
        ''')
        result, report, _ = run_report(self.start, self.reports, 4)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(report['successful'])
        self.assertEqual(len(report['skipped']), 1)
        self.assertIn('synthetic skip', json.dumps(report['skipped']))
        self.assertEqual({item['outcome'] for item in report['executed']},
                         {'success', 'skipped', 'expected_failure'})


class AggregateContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()
        cls.fixture = tempfile.TemporaryDirectory(prefix='r25-shards-aggregate-')
        cls.addClassCleanup(cls.fixture.cleanup)
        root = Path(cls.fixture.name)
        start = fixture_tree(root / 'tests')
        reports = root / 'reports'
        reports.mkdir()
        cls.good = []
        for shard in range(1, 5):
            result, report, _ = run_report(start, reports, shard)
            if result.returncode:
                raise AssertionError(result.stderr + result.stdout)
            cls.good.append(report)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='r25-shards-reports-')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.reports = copy.deepcopy(self.good)

    def save(self):
        for report in self.reports:
            path = self.directory / ('shard-' + str(report['shard']) + '.json')
            path.write_text(json.dumps(report), encoding='utf-8')

    def refused(self):
        self.save()
        with self.assertRaises(ValueError):
            self.runner.verify_reports(self.directory, COMMIT)

    def test_complete_reports_verify_with_duplicate_test_ids(self):
        self.save()
        result = self.runner.verify_reports(self.directory, COMMIT)
        self.assertTrue(result['successful'])
        self.assertEqual(result['shard_count'], 4)
        self.assertEqual(result['discovered_count'], 5)
        self.assertEqual(result['tests_run'], 5)
        self.assertEqual(result['commit'], COMMIT)

    def test_missing_or_cancelled_report_fails_cli(self):
        self.reports.pop()
        self.refused()
        result = cli('verify-reports', '--directory', self.directory, '--commit', COMMIT)
        self.assertNotEqual(result.returncode, 0)

    def test_cancelled_incomplete_execution_fails(self):
        self.reports[3]['executed'].pop()
        self.reports[3]['tests_run'] -= 1
        self.refused()

    def test_failed_shard_report_fails_even_if_coverage_is_complete(self):
        self.reports[1]['successful'] = False
        self.refused()

    def test_incomplete_status_fails_even_if_coverage_claims_success(self):
        self.reports[1]['status'] = 'incomplete'
        self.refused()

    def test_wrong_commit_fails(self):
        self.reports[0]['commit'] = 'f' * 40
        self.refused()

    def test_mismatched_discovery_fails(self):
        self.reports[2]['discovered_ids'].append('test_extra.C.test_unreported')
        self.refused()

    def test_duplicate_occurrence_fails_even_when_ids_may_repeat(self):
        self.reports[3]['executed'][1] = copy.deepcopy(self.reports[3]['executed'][0])
        self.refused()

    def test_missing_occurrence_fails_when_execution_claims_success(self):
        self.reports[0]['executed'] = []
        self.reports[0]['tests_run'] = 0
        self.refused()

    def test_occurrence_assigned_to_wrong_shard_fails(self):
        first = self.reports[0]['executed'][0]
        second = self.reports[1]['executed'][0]
        self.reports[0]['executed'] = [second]
        self.reports[0]['assigned_indices'] = [second['occurrence_index']]
        self.reports[1]['executed'] = [first]
        self.reports[1]['assigned_indices'] = [first['occurrence_index']]
        self.refused()


if __name__ == '__main__':
    unittest.main()
