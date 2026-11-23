"""Exercise actual CoreRuntime methods with a portable private-file fixture.

Only Linux process/lease/PG/HTTP and ownership syscalls are replaced. These
tests do not certify onsite file permissions, fsync, processes or live data.
No production constructor or CLI path override is introduced.
"""
from dataclasses import asdict
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import core_runtime as runtime_module
from publisher_transaction import Publisher, Refused, Release
from r25_storage import StorageRefused, strict_json


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(document):
    return (json.dumps(document, sort_keys=True, indent=2) + "\n").encode()


class FileFixture:
    """Ordinary temp files, simulated root metadata, exact digest replacement."""
    def __init__(self, root):
        self.root = root
        self.events = []
        self.copy_count = 0
        self.fail_copy = None
        self.after_read = None

    def identity(self, path, expected_sha256=None, expected_bytes=None, **_):
        path = Path(path)
        data = path.read_bytes()
        result = {"path": str(path), "sha256": digest(data), "bytes": len(data),
                  "uid": 0, "gid": 0, "mode": "0o600"}
        if expected_sha256 is not None and result["sha256"] != expected_sha256:
            raise StorageRefused("fixture_sha256_mismatch")
        if expected_bytes is not None and len(data) != expected_bytes:
            raise StorageRefused("fixture_bytes_mismatch")
        return result

    file_identity = identity

    def validate_target(self, path, exists=None, directory=False, **_):
        path = Path(path)
        if exists is True and not path.exists():
            raise StorageRefused("fixture_target_missing")
        if exists is False and path.exists():
            raise StorageRefused("fixture_target_exists")
        if directory and path.exists() and not path.is_dir():
            raise StorageRefused("fixture_directory_required")
        self.events.append(("validate_target", path))
        return path

    def read_private_record(self, path, expected_sha256=None, **_):
        path = Path(path)
        data = path.read_bytes()
        if expected_sha256 is not None and digest(data) != expected_sha256:
            raise StorageRefused("fixture_record_digest")
        document = strict_json(data)
        if self.after_read is not None:
            hook, self.after_read = self.after_read, None
            hook(path)
        return document

    def mkdir_private(self, path, **_):
        path = Path(path)
        path.mkdir()
        self.events.append(("mkdir", path))
        return path

    def _write(self, path, data, replace=False, expected_existing_sha256=None, **_):
        path = Path(path)
        if path.exists():
            if not replace or expected_existing_sha256 != digest(path.read_bytes()):
                raise StorageRefused("fixture_existing_digest")
        elif expected_existing_sha256 is not None:
            raise StorageRefused("fixture_expected_missing")
        path.write_bytes(data)
        self.events.append(("write", path))
        return self.identity(path)

    def atomic_write_json(self, path, document, **kwargs):
        return self._write(path, encoded(document), **kwargs)

    def atomic_write_bytes(self, path, data, **kwargs):
        return self._write(path, data, **kwargs)

    def atomic_copy_file(self, source, target, expected_sha256, expected_bytes=None, **kwargs):
        source, target = Path(source), Path(target)
        self.identity(source, expected_sha256, expected_bytes)
        self.copy_count += 1
        if self.fail_copy == self.copy_count:
            raise OSError("synthetic copy boundary failure")
        result = self._write(target, source.read_bytes(), **kwargs)
        self.events.append(("copy", source, target))
        return result


class RuntimeMethodTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="core-runtime-fixture-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.root, self.preview = root / "production", root / "preview"
        self.store = self.root / "runtime/core-publications"
        for path in (self.root / "release/artifacts", self.root / "runtime/config",
                     self.preview / "release", self.store / "transactions", self.store / "releases"):
            path.mkdir(parents=True, exist_ok=True)
        self.live = {"release-jar": self.root / "release/artifacts/backend.jar",
                     "preview-jar": self.preview / "release/backend.jar",
                     "manifest": self.root / "release/MANIFEST.json",
                     "handoff": self.root / "runtime/handoff-control.env",
                     "application": self.root / "runtime/config/application.yml"}
        self.fs = FileFixture(root)
        old_bytes, new_bytes = b"baseline fixture jar bytes", b"candidate fixture jar bytes"
        self.old = Release("a" * 40, digest(old_bytes), "c" * 64)
        self.new = Release("d" * 40, digest(new_bytes), "f" * 64)
        self.records = {
            self.old.artifact_sha256: {"commit_sha": self.old.commit_sha,
                "artifact_bytes": len(old_bytes), "payload_fingerprint": "fixture-equivalent-payload"},
            self.new.artifact_sha256: {"commit_sha": self.new.commit_sha,
                "artifact_bytes": len(new_bytes), "payload_fingerprint": "fixture-equivalent-payload"}}
        for release, data in ((self.old, old_bytes), (self.new, new_bytes)):
            folder = self.store / "releases" / (release.artifact_sha256 + "-" + release.commit_sha)
            folder.mkdir()
            (folder / "backend.jar").write_bytes(data)
        for key in ("release-jar", "preview-jar"):
            self.live[key].write_bytes(old_bytes)
        self.live["manifest"].write_bytes(encoded({"schema": "FIXTURE_MANIFEST",
            "entries": [{"path": "artifacts/backend.jar", "sha256": self.old.artifact_sha256,
                         "size": len(old_bytes)}, {"path": "keep/fixed.json", "sha256": "7" * 64}]}))
        self.live["handoff"].write_bytes(("R25_BACKEND_SHA256=" + self.old.artifact_sha256 +
                                         "\nPRESERVED_FIXTURE_KEY=opaque-fixture-value\n").encode())
        self.live["application"].write_bytes(b"fixed: application fixture\n")
        self.before_current = {"schema": "R25_CORE_CURRENT_V1", "release": asdict(self.old),
                               "paired_fixture_identity": "must-be-restored"}
        (self.store / "CURRENT.json").write_bytes(encoded(self.before_current))
        self.original = {name: path.read_bytes() for name, path in self.live.items()}

        for name, value in (("ROOT", self.root), ("PREVIEW", self.preview), ("STORE", self.store),
                            ("LIVE_FILES", self.live)):
            started = patch.object(runtime_module, name, value)
            started.start()
            self.addCleanup(started.stop)
        patches = [
            patch.object(runtime_module.guard, "policy", return_value={"schema": runtime_module.guard.POLICY_SCHEMA}),
            patch.object(runtime_module.secrets, "token_hex", return_value="1" * 32),
            patch.object(runtime_module.shutil, "disk_usage", return_value=SimpleNamespace(free=10 * 1024**3)),
            patch.object(runtime_module.guard, "read_file", side_effect=lambda path, **_: Path(path).read_bytes()),
            patch.object(runtime_module.guard, "release_record", side_effect=self.release_record),
            patch.object(runtime_module.guard, "payload_fingerprint", return_value="fixture-equivalent-payload"),
            patch.object(runtime_module, "verify_http", side_effect=lambda *_, **__: {"gallery_total": 123}),
            patch.object(runtime_module.guard, "production", return_value=SimpleNamespace(
                legacy=SimpleNamespace(require_backend_eligible=lambda *args: None),
                require_backend_eligible=lambda *args: None)),
        ]
        for started in patches:
            started.start()
            self.addCleanup(started.stop)
        self.events = []
        self.runtime = object.__new__(runtime_module.CoreRuntime)
        self.runtime.storage = self.fs
        self.runtime.fd, self.runtime.last_http, self.runtime.tx = 17, None, None
        self.runtime.require_lease = self.require_lease
        self.runtime.engine = self.engine
        self.runtime.query = lambda _: "0"

    def release_record(self, artifact, record_hash=None, commit=None):
        result = self.records[artifact]
        if commit != result["commit_sha"]:
            raise Refused("fixture_commit_binding")
        return dict(result)

    def require_lease(self):
        if self.runtime.fd is None:
            raise Refused("fixture_lease_required")
        self.events.append("lease_checked")

    def engine(self, **kwargs):
        testcase = self
        class Engine:
            backend = runtime_module.env_values(testcase.live["handoff"].read_bytes())["R25_BACKEND_SHA256"]
            tunnel = SimpleNamespace(status=lambda: None)
            def active(self, name, port): return {"pid": 100, "ticks": 200}
            def frontend(self, *args): return None
            def watchdog_stop_locked(self): testcase.events.append("watchdog_stopped")
            def stop_role(self, *args): testcase.events.append("java_stopped")
            def start_java_locked(self, fd): testcase.events.append("java_started")
            def watchdog_start_locked(self): testcase.events.append("watchdog_started")
            def watchdog_identity(self): return {"pid": 101}
        if kwargs.get("partial_recovery"):
            self.events.append("partial_recovery_engine")
        return Engine()

    def prepared(self):
        snapshot_id = self.runtime.snapshot(self.old, self.new)
        self.runtime.begin(self.old, self.new, snapshot_id)
        return snapshot_id, self.runtime.load_recovery(snapshot_id)

    def test_snapshot_has_complete_fixed_backups_and_bound_after_images(self):
        snapshot_id = self.runtime.snapshot(self.old, self.new)
        folder = self.runtime.transaction_folder(snapshot_id)
        snapshot = self.runtime.read(folder / "snapshot.json")
        self.assertEqual(set(snapshot["files"]), set(self.live))
        self.assertEqual(snapshot["before_current"], self.before_current)
        self.assertEqual(snapshot["minimum_total"], 123)
        for name, original in self.original.items():
            self.assertEqual((folder / "before" / name).read_bytes(), original)
            self.assertEqual(snapshot["files"][name]["path"], str(self.live[name]))
            self.assertEqual(self.live[name].read_bytes(), original)
        manifest = self.runtime.read(folder / "after/manifest")
        self.assertEqual(manifest["entries"][0]["sha256"], self.new.artifact_sha256)
        self.assertEqual(manifest["entries"][1]["sha256"], "7" * 64)
        self.assertIn(b"PRESERVED_FIXTURE_KEY=opaque-fixture-value",
                      (folder / "after/handoff").read_bytes())

    def test_begin_and_record_bind_active_snapshot_and_reject_extra_fields(self):
        snapshot_id, _ = self.prepared()
        active = self.runtime.read(self.store / "ACTIVE.json")
        self.assertEqual(active["transaction_id"], snapshot_id)
        self.runtime.record(snapshot_id, "INSTALL_INTENT", failure_class="RuntimeError")
        journal = self.runtime.read(self.runtime.transaction_folder(snapshot_id) / "journal.json")
        self.assertEqual(journal["phase"], "INSTALL_INTENT")
        self.assertEqual(journal["history"][-1]["failure_class"], "RuntimeError")
        with self.assertRaises(Refused):
            self.runtime.record(snapshot_id, "UNKNOWN_PHASE")
        with self.assertRaises(Refused):
            self.runtime.record(snapshot_id, "STOPPED", command="must not be interpreted")

    def test_illustration_core_publication_keeps_the_community_process_checkpoint(self):
        snapshot_id, _ = self.prepared()
        observed = {"state": "READY_PRESERVED", "pid": 12, "start_ticks": 345}
        actions = []

        def community_action(action, fd):
            actions.append((action, fd))
            return dict(observed)

        engine = SimpleNamespace(community_action=community_action)
        runtime_module.guard.policy.return_value = {"upgrade": {"core_followup": {
            "schema": "R25_ASSISTANT_ILLUSTRATION_CORE_PUBLICATION_V1",
        }}}
        self.runtime.community_maintenance(engine)
        self.runtime.community_maintenance(engine, resuming=True)
        self.assertEqual(actions, [("preserve", 17), ("preserve", 17)])
        checkpoint = self.runtime.transaction_folder(snapshot_id) / "community-maintenance.json"
        self.assertEqual(self.runtime.read(checkpoint), observed)
        observed["start_ticks"] = 346
        with self.assertRaisesRegex(Refused, "community_maintenance_drift"):
            self.runtime.community_maintenance(engine, resuming=True)

    def test_illustration_rollback_refuses_new_critic_before_changing_journal(self):
        snapshot_id, _ = self.prepared()
        journal = self.runtime.transaction_folder(snapshot_id) / "journal.json"
        before = journal.read_bytes()
        guard = runtime_module.guard
        guard.policy.return_value = {"schema": guard.BUSINESS_POLICY_SCHEMA, "upgrade": {"core_followup": {
            "schema": guard.ASSISTANT_ILLUSTRATION_CORE_SCHEMA, "ai_release": {"commit_sha": "old"},
        }}}
        guard.release_record.side_effect = lambda *args: {"schema": guard.BUSINESS_RECORD_SCHEMA}
        with patch.object(guard, 'verify_assistant_rollout_ai', return_value={"commit_sha": "new"}), \
                self.assertRaisesRegex(Refused, "illustration_core_restore_ai_before_core"):
            self.runtime.verify_rollback_source(self.new)
        self.assertEqual(journal.read_bytes(), before)
        self.assertNotIn("java_stopped", self.events)

    def test_begin_refuses_pending_recovery_without_replacing_active_pointer(self):
        first = self.runtime.snapshot(self.old, self.new)
        with patch.object(runtime_module.secrets, "token_hex", return_value="2" * 32):
            second = self.runtime.snapshot(self.old, self.new)
        self.runtime.begin(self.old, self.new, first)
        self.runtime.record(first, "RECOVERY_REQUIRED")
        active_before = (self.store / "ACTIVE.json").read_bytes()
        with self.assertRaisesRegex(Refused, "recovery_required"):
            self.runtime.begin(self.old, self.new, second)
        self.assertEqual((self.store / "ACTIVE.json").read_bytes(), active_before)
        journal = self.runtime.read(self.runtime.transaction_folder(first) / "journal.json")
        self.assertEqual(journal["phase"], "RECOVERY_REQUIRED")

    def test_crash_after_begin_can_abort_unchanged_prepared_transaction(self):
        transaction_id, _ = self.prepared()
        current_before = (self.store / "CURRENT.json").read_bytes()
        self.runtime.lease = lambda: nullcontext()
        result = Publisher(self.runtime).recover(transaction_id)
        self.assertEqual(result.outcome, "PREPARED_ABORTED_UNCHANGED")
        journal = self.runtime.read(self.runtime.transaction_folder(transaction_id) / "journal.json")
        self.assertEqual(journal["phase"], "RESTORED")
        self.assertEqual((self.store / "CURRENT.json").read_bytes(), current_before)
        for name, original in self.original.items():
            self.assertEqual(self.live[name].read_bytes(), original)
        self.assertNotIn("java_stopped", self.events)
        self.runtime.require_terminal_component_transactions()

    def test_unfinished_ai_transaction_blocks_core_before_snapshot(self):
        ai_root = self.root / "runtime/ai-publications"
        transaction_id = "3" * 32
        (ai_root / "transactions" / transaction_id).mkdir(parents=True)
        (ai_root / "ACTIVE.json").write_bytes(encoded({"transaction_id": transaction_id}))
        (ai_root / "transactions" / transaction_id / "journal.json").write_bytes(encoded({
            "transaction_id": transaction_id, "phase": "RECOVERY_REQUIRED"}))
        with self.assertRaisesRegex(Refused, "ai_recovery_required_before_core_publication"):
            self.runtime.snapshot(self.old, self.new)
        self.assertEqual(list((self.store / "transactions").iterdir()), [])

    def test_install_replaces_only_fixed_live_files_and_does_not_commit_current(self):
        snapshot_id, _ = self.prepared()
        outsider = self.root / "never-replace.json"
        outsider.write_bytes(b"unrelated fixture")
        before_current = (self.store / "CURRENT.json").read_bytes()
        self.runtime.install_candidate(self.new, snapshot_id)
        for name in ("release-jar", "preview-jar"):
            self.assertEqual(digest(self.live[name].read_bytes()), self.new.artifact_sha256)
        self.assertEqual(self.live["application"].read_bytes(), self.original["application"])
        self.assertEqual(outsider.read_bytes(), b"unrelated fixture")
        self.assertEqual((self.store / "CURRENT.json").read_bytes(), before_current)
        targets = {event[2] for event in self.fs.events if event[0] == "copy" and event[2] in self.live.values()}
        self.assertEqual(targets, set(self.live.values()))

    def test_partial_install_is_valid_recovery_state_and_restores_exact_snapshot(self):
        snapshot_id, _ = self.prepared()
        self.fs.fail_copy = self.fs.copy_count + 2
        with self.assertRaises(OSError):
            self.runtime.install_candidate(self.new, snapshot_id)
        self.assertEqual(digest(self.live["release-jar"].read_bytes()), self.new.artifact_sha256)
        self.assertEqual(digest(self.live["preview-jar"].read_bytes()), self.old.artifact_sha256)
        self.runtime.record(snapshot_id, "RECOVERY_REQUIRED")
        plan = self.runtime.load_recovery(snapshot_id)
        self.runtime.validate_recovery(plan)
        self.runtime.stop_for_restore(plan)
        self.runtime.record(snapshot_id, "RESTORE_INTENT")
        self.runtime.restore_snapshot(plan)
        self.runtime.verify_restored(plan)
        for name, original in self.original.items():
            self.assertEqual(self.live[name].read_bytes(), original)
        self.assertEqual(self.runtime.read(self.store / "CURRENT.json"), self.before_current)
        self.assertIn("partial_recovery_engine", self.events)
        self.assertIn("java_started", self.events)

    def test_recovery_rejects_foreign_file_pointer_active_and_backup_drift(self):
        snapshot_id, plan = self.prepared()
        self.live["application"].write_bytes(b"foreign content")
        with self.assertRaisesRegex(Refused, "foreign_live_file_drift"):
            self.runtime.validate_recovery(plan)
        self.live["application"].write_bytes(self.original["application"])
        current = self.runtime.read(self.store / "CURRENT.json")
        changed = dict(current, release=asdict(Release("9" * 40, "8" * 64, "7" * 64)))
        (self.store / "CURRENT.json").write_bytes(encoded(changed))
        with self.assertRaisesRegex(Refused, "recovery_current_drift"):
            self.runtime.validate_recovery(plan)
        (self.store / "CURRENT.json").write_bytes(encoded(current))
        (self.store / "ACTIVE.json").write_bytes(encoded({"transaction_id": "2" * 32}))
        with self.assertRaisesRegex(Refused, "recovery_not_active_transaction"):
            self.runtime.validate_recovery(plan)
        (self.store / "ACTIVE.json").write_bytes(encoded({"transaction_id": snapshot_id}))
        (self.runtime.transaction_folder(snapshot_id) / "before/release-jar").write_bytes(b"bad backup")
        with self.assertRaises(StorageRefused):
            self.runtime.validate_recovery(plan)

    def test_snapshot_cannot_redirect_a_live_target_even_with_updated_journal_hash(self):
        snapshot_id, plan = self.prepared()
        folder = self.runtime.transaction_folder(snapshot_id)
        snapshot = self.runtime.read(folder / "snapshot.json")
        snapshot["files"]["handoff"]["path"] = str(self.root / "unapproved.env")
        (folder / "snapshot.json").write_bytes(encoded(snapshot))
        journal = self.runtime.read(folder / "journal.json")
        journal["snapshot_sha256"] = digest((folder / "snapshot.json").read_bytes())
        (folder / "journal.json").write_bytes(encoded(journal))
        with self.assertRaisesRegex(Refused, "snapshot_fixed_path"):
            self.runtime.snapshot_document(plan)
        self.assertFalse((self.root / "unapproved.env").exists())

    def test_current_cas_refuses_change_after_checked_record_read(self):
        foreign = {"schema": "R25_CORE_CURRENT_V1",
                   "release": asdict(Release("9" * 40, "8" * 64, "7" * 64))}
        current_path = self.store / "CURRENT.json"
        self.fs.after_read = lambda path: path.write_bytes(encoded(foreign))
        with self.assertRaises((Refused, StorageRefused)):
            self.runtime.commit_current(self.new, self.old)
        self.assertEqual(self.runtime.read(current_path), foreign)

    def test_restore_current_happens_after_old_version_health_verification(self):
        snapshot_id, plan = self.prepared()
        self.runtime.install_candidate(self.new, snapshot_id)
        self.runtime.commit_current(self.new, self.old)
        self.runtime.record(snapshot_id, "RESTORE_INTENT")
        original_start = self.runtime.start_and_verify
        observed = []
        def check_before_pointer(release):
            observed.append(self.runtime.current())
            original_start(release)
        self.runtime.start_and_verify = check_before_pointer
        self.runtime.restore_snapshot(plan)
        self.assertEqual(observed, [self.new])
        self.assertEqual(self.runtime.current(), self.old)

    def test_invalid_transaction_ids_and_missing_lease_refused(self):
        for identity in ("../outside", "1" * 31, "G" * 32, None):
            with self.assertRaises(Refused):
                self.runtime.transaction_folder(identity)
        self.runtime.fd = None
        with self.assertRaises(Refused):
            self.runtime.snapshot(self.old, self.new)

    def test_mutating_adapter_methods_require_the_lease_themselves(self):
        snapshot_id, _ = self.prepared()
        self.runtime.fd = None
        for action in (lambda: self.runtime.begin(self.old, self.new, snapshot_id),
                       lambda: self.runtime.install_candidate(self.new, snapshot_id),
                       lambda: self.runtime.commit_current(self.new, self.old),
                       lambda: self.runtime.record(snapshot_id, "STOPPED")):
            with self.assertRaisesRegex(Refused, "fixture_lease_required"):
                action()

    def test_insufficient_snapshot_space_never_creates_transaction(self):
        with patch.object(runtime_module.shutil, "disk_usage", return_value=SimpleNamespace(free=1)):
            with self.assertRaisesRegex(Refused, "snapshot_disk_space"):
                self.runtime.snapshot(self.old, self.new)
        self.assertEqual(list((self.store / "transactions").iterdir()), [])


if __name__ == "__main__":
    unittest.main()

