#!/usr/bin/env python3
"""Run all discovered R25 test occurrences in four deterministic partitions.

Discovery remains unittest's normal discovery, including load_tests hooks and
duplicate occurrences. No tests are executed by the report verifier.
"""
import argparse
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import traceback
import unittest


SCHEMA = "ARTLIVE_R25_TEST_SHARD_V1"
VERIFICATION_SCHEMA = "ARTLIVE_R25_TEST_SHARDS_VERIFIED_V1"
SHARD_COUNT = 4
SHARD_MODULES = {
    1: "test_managed_vmm_publication",
    2: "test_community_publication_repair",
    3: "test_community_publication_successor",
}
GOOD_OUTCOMES = {"success", "skipped", "expected_failure"}


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _commit(value):
    _require(type(value) is str and re.fullmatch(r"[0-9a-f]{40}", value), "commit must be a full lowercase SHA")
    return value


def _shard(value):
    _require(type(value) is int and 1 <= value <= SHARD_COUNT, "shard must be 1, 2, 3 or 4")
    return value


def _flatten(tests):
    for test in tests:
        if isinstance(test, unittest.TestSuite):
            yield from _flatten(test)
        else:
            yield test


def _assignment(test_id):
    module = test_id.split(".", 1)[0]
    return next((shard for shard, name in SHARD_MODULES.items() if name == module), SHARD_COUNT)


def partition_tests(tests, shard):
    """Return (full-discovery occurrence index, TestCase) pairs in original order."""
    _shard(shard)
    return [(index, test) for index, test in enumerate(_flatten(tests)) if _assignment(test.id()) == shard]


class TimedResult(unittest.TextTestResult):
    """Measure startTest through stopTest, including test fixtures and cleanups."""
    def __init__(self, stream, descriptions, verbosity, assigned, progress=None):
        super().__init__(stream, descriptions, verbosity)
        self.assigned = assigned
        self.executed = []
        self.active = None
        self.started = None
        self.progress = progress

    def startTest(self, test):
        position = len(self.executed)
        if self.active is not None or position >= len(self.assigned):
            raise RuntimeError("unexpected test occurrence")
        index, expected = self.assigned[position]
        if test is not expected or test.id() != expected.id():
            raise RuntimeError("test occurrence order changed")
        self.active = dict(occurrence_index=index, test_id=test.id(), duration_seconds=0.0, outcome="error")
        self.started = time.perf_counter()
        super().startTest(test)

    def _outcome(self, test, outcome):
        if self.active is not None and self.active["test_id"] == test.id():
            # A failing subtest or fixture cannot later become a success/skip.
            previous = self.active["outcome"]
            if previous not in ("failure", "unexpected_success") and not self.active.get("recorded_error"):
                self.active["outcome"] = outcome
            if outcome == "error":
                self.active["recorded_error"] = True

    def addSuccess(self, test):
        self._outcome(test, "success")
        super().addSuccess(test)

    def addError(self, test, err):
        self._outcome(test, "error")
        super().addError(test, err)

    def addFailure(self, test, err):
        self._outcome(test, "failure")
        super().addFailure(test, err)

    def addSkip(self, test, reason):
        self._outcome(test, "skipped")
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err):
        self._outcome(test, "expected_failure")
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):
        self._outcome(test, "unexpected_success")
        super().addUnexpectedSuccess(test)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self._outcome(test, "failure" if issubclass(err[0], test.failureException) else "error")
        super().addSubTest(test, subtest, err)

    def stopTest(self, test):
        if self.active is None:
            raise RuntimeError("test ended without matching start")
        self.active["duration_seconds"] = time.perf_counter() - self.started
        self.active.pop("recorded_error", None)
        self.executed.append(self.active)
        print("R25_TEST_TIMING " + json.dumps(self.active, sort_keys=True), flush=True)
        self.active = None
        self.started = None
        super().stopTest(test)
        if self.progress:
            self.progress(self)


def _details(items):
    return [{"test_id": test.id(), "details": details} for test, details in items]


def _write_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + str(os.getpid()))
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_shard(shard, report_path, commit, start_directory="r25", pattern="test_*.py"):
    """Discover the complete suite, run one partition, always report normal errors.

    Test module/class fixtures are handled by a standard TestSuite in discovered
    occurrence order. Their time is included in run_seconds; individual test
    times cover TestCase.setUp/tearDown/cleanup, as unittest startTest specifies.
    """
    _shard(shard)
    _commit(commit)
    total_start = time.perf_counter()
    report = dict(schema=SCHEMA, commit=commit, shard=shard, shard_count=SHARD_COUNT, status="incomplete",
                  python_version=sys.version, python_executable=sys.executable,
                  discovered_ids=[], assigned_indices=[], executed=[], tests_run=0,
                  successful=False, errors=[], failures=[], skipped=[], discovery_errors=[],
                  discovery_seconds=0.0, run_seconds=0.0, total_seconds=0.0, slowest_tests=[])
    loader = unittest.TestLoader()
    tests = []
    _write_report(report_path, report)
    print(json.dumps({"commit": commit, "shard": shard, "shard_count": SHARD_COUNT,
                      "python_version": sys.version, "status": "discovering"}), flush=True)
    discovery_start = time.perf_counter()
    try:
        tests = list(_flatten(loader.discover(start_directory, pattern=pattern)))
        report["discovered_ids"] = [test.id() for test in tests]
    except (Exception, SystemExit):
        report["discovery_errors"].append(traceback.format_exc())
    report["discovery_errors"].extend(loader.errors)
    # _FailedTest is unittest's representation of import or load_tests failure.
    if any(isinstance(test, unittest.loader._FailedTest) for test in tests) and not report["discovery_errors"]:
        report["discovery_errors"].append("unittest discovery produced a failed import placeholder")
    if not tests and not report["discovery_errors"]:
        report["discovery_errors"].append("unittest discovery returned no tests")
    report["discovery_seconds"] = time.perf_counter() - discovery_start
    assigned = partition_tests(tests, shard)
    report["assigned_indices"] = [index for index, _ in assigned]
    report["total_seconds"] = time.perf_counter() - total_start
    _write_report(report_path, report)
    print(json.dumps({"commit": commit, "discovered_count": len(tests), "shard": shard,
                      "shard_count": SHARD_COUNT, "assigned_ids": [test.id() for _, test in assigned],
                      "assigned_indices": report["assigned_indices"]}), flush=True)
    result = None
    if not report["discovery_errors"]:
        run_start = time.perf_counter()
        def progress(current):
            report["executed"] = current.executed
            report["tests_run"] = current.testsRun
            report["run_seconds"] = time.perf_counter() - run_start
            report["total_seconds"] = time.perf_counter() - total_start
            report["slowest_tests"] = sorted(current.executed, key=lambda row: row["duration_seconds"], reverse=True)[:10]
            _write_report(report_path, report)
        def make_result(stream, descriptions, verbosity):
            nonlocal result
            result = TimedResult(stream, descriptions, verbosity, assigned, progress)
            return result
        try:
            runner = unittest.TextTestRunner(verbosity=2, resultclass=make_result)
            runner.run(unittest.TestSuite(test for _, test in assigned))
        except (Exception, SystemExit, KeyboardInterrupt):
            report["errors"].append({"test_id": "<runner>", "details": traceback.format_exc()})
        finally:
            report["run_seconds"] = time.perf_counter() - run_start
        if result is not None:
            report["executed"] = result.executed
            report["tests_run"] = result.testsRun
            report["errors"].extend(_details(result.errors))
            report["failures"] = _details(result.failures)
            report["failures"].extend({"test_id": test.id(), "details": "unexpected success"} for test in result.unexpectedSuccesses)
            report["skipped"] = [{"test_id": test.id(), "reason": reason} for test, reason in result.skipped]
            complete = (result.testsRun == len(assigned) == len(result.executed)
                        and [row["occurrence_index"] for row in result.executed] == report["assigned_indices"]
                        and result.active is None)
            report["successful"] = bool(complete and result.wasSuccessful() and not report["errors"]
                                        and all(row["outcome"] in GOOD_OUTCOMES for row in result.executed))
    report["total_seconds"] = time.perf_counter() - total_start
    report["status"] = "completed"
    report["slowest_tests"] = sorted(report["executed"], key=lambda row: row["duration_seconds"], reverse=True)[:10]
    _write_report(report_path, report)
    print(json.dumps({"shard": shard, "tests_run": report["tests_run"], "successful": report["successful"],
                      "discovery_seconds": report["discovery_seconds"], "run_seconds": report["run_seconds"],
                      "total_seconds": report["total_seconds"], "slowest_tests": report["slowest_tests"]}), flush=True)
    return report


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate JSON key: " + key)
        result[key] = value
    return result


def _duration(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def verify_reports(directory, commit):
    """Require four successful reports covering every ordered occurrence once."""
    _commit(commit)
    directory = Path(directory)
    _require(directory.is_dir(), "report directory missing")
    paths = sorted(directory.rglob("*.json"))
    _require(len(paths) == SHARD_COUNT, "exactly four shard reports are required")
    reports = []
    for path in paths:
        _require(path.stat().st_size <= 32 * 1024 * 1024, "report too large")
        report = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        _require(type(report) is dict and report.get("schema") == SCHEMA, "invalid report schema")
        _require(report.get("commit") == commit, "report commit differs")
        _shard(report.get("shard"))
        _require(type(report.get("shard_count")) is int and report["shard_count"] == SHARD_COUNT, "shard_count differs")
        _require(report.get("successful") is True, "unsuccessful shard report")
        _require(report.get("status") == "completed", "incomplete shard report")
        _require(report.get("errors") == [] and report.get("failures") == [] and report.get("discovery_errors") == [], "test or discovery errors present")
        _require(type(report.get("skipped")) is list, "missing skipped results")
        for field in ("discovery_seconds", "run_seconds", "total_seconds"):
            _require(_duration(report.get(field)), "invalid timing " + field)
        reports.append(report)
    _require(sorted(report["shard"] for report in reports) == [1, 2, 3, 4], "duplicate or missing shard")
    discovered = reports[0].get("discovered_ids")
    _require(type(discovered) is list and discovered and all(type(item) is str and item for item in discovered), "invalid or empty discovery list")
    covered = []
    for report in reports:
        _require(report.get("discovered_ids") == discovered, "discovery lists differ between shards")
        expected = [index for index, test_id in enumerate(discovered) if _assignment(test_id) == report["shard"]]
        assigned = report.get("assigned_indices")
        _require(type(assigned) is list and all(type(index) is int for index in assigned) and assigned == expected, "wrong partition or assignment order")
        executed = report.get("executed")
        _require(type(executed) is list and len(executed) == len(expected), "assigned tests not fully executed")
        _require(type(report.get("tests_run")) is int and report["tests_run"] == len(expected), "tests_run differs from assignment")
        for index, row in zip(expected, executed):
            _require(type(row) is dict and type(row.get("occurrence_index")) is int and row["occurrence_index"] == index, "executed occurrence differs")
            _require(row.get("test_id") == discovered[index], "executed test identity differs")
            _require(row.get("outcome") in GOOD_OUTCOMES, "non-successful test outcome")
            _require(_duration(row.get("duration_seconds")), "invalid test timing")
            covered.append(index)
    _require(sorted(covered) == list(range(len(discovered))), "incomplete or duplicate occurrence coverage")
    return dict(schema=VERIFICATION_SCHEMA, commit=commit, shard_count=SHARD_COUNT,
                discovered_count=len(discovered), tests_run=len(covered), successful=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--shard", type=int, choices=range(1, SHARD_COUNT + 1), required=True)
    run.add_argument("--report", type=Path, required=True)
    run.add_argument("--commit", required=True)
    run.add_argument("--start-directory", default="r25")
    run.add_argument("--pattern", default="test_*.py")
    verify = commands.add_parser("verify-reports")
    verify.add_argument("--directory", type=Path, required=True)
    verify.add_argument("--commit", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            report = run_shard(args.shard, args.report, args.commit, args.start_directory, args.pattern)
            return 0 if report["successful"] else 1
        print(json.dumps(verify_reports(args.directory, args.commit), sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print("R25 test shards refused: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
