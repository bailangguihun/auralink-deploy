"""Real private filesystem/CAS; unit lifecycle/PG adapters are explicit fixtures.

The stage also retains a separate actual PostgreSQL dump/restore rehearsal.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import assistant_backup as backup
import assistant_release as release
from publisher_transaction import Release, Refused
from r25_storage import R25Storage, StorageRefused


class Runtime:
    def __init__(self, root):
        self.storage = R25Storage([str(root)], os.getuid(), os.getgid())
        self.root = root; self.fd = 42; self.events = []; self.busy = False
        self.java = True; self.watchdog = True; self.fail_start = False
        self.release = Release('a'*40, 'b'*64, 'c'*64)
        for folder in (root/'runtime', root/'runtime/core-publications', root/'attachments'):
            self.storage.mkdir_private(folder)
        self.storage.atomic_write_json(root/'runtime/core-publications/POLICY.json', {'fixture': True})
        self.storage.atomic_write_bytes(root/'attachments/.assistant-attachments-v1', b'ArtLIVE Assistant private attachments v1\n')
        self.storage.atomic_write_bytes(root/'attachments/11111111-1111-1111-1111-111111111111.blob', b'synthetic image')
    def require_lease(self): assert self.fd == 42
    def current(self): return self.release
    def identity(self, path, **kw): return self.storage.file_identity(path, **kw)
    def read(self, path): return self.storage.read_private_record(path)
    def write(self, path, value):
        exists = path.exists()
        return self.storage.atomic_write_json(path, value, replace=exists,
            expected_existing_sha256=self.identity(path)['sha256'] if exists else None)
    def verify_candidate(self, current): assert current == self.release
    def require_compatible(self, current): assert current == self.release
    def verify_process_contract(self, process, current): assert process and current == self.release
    def require_terminal_component_transactions(self): backup.require_terminal(self)
    def require_no_active_work(self):
        if self.busy: raise Refused('fixture_busy')
    def query(self, sql): return 'fixture'
    def engine(self):
        outer = self
        class Engine:
            backend = outer.release.artifact_sha256
            tunnel = SimpleNamespace(status=lambda: None)
            def active(self, role, port): return {'pid': 1, 'ticks': 1} if role != 'java' or outer.java else None
            def frontend(self, *args): pass
            def watchdog_identity(self): return {'pid': 2} if outer.watchdog else None
            def watchdog_stop_locked(self): outer.events.append('stop-watchdog'); outer.watchdog = False
            def stop_role(self, role, port):
                assert role == 'java'; outer.events.append('stop-java'); outer.java = False
            def start_java_locked(self, fd):
                if outer.fail_start: raise Refused('fixture_start_failed')
                outer.events.append('start-java'); outer.java = True
            def watchdog_start_locked(self): outer.events.append('start-watchdog'); outer.watchdog = True
        return Engine()


class PG:
    connection = []
    def __init__(self): self.dumps = 0; self.fail = False
    def run(self, *args): return b'synthetic nonempty db\n'
    def ledger(self): return [{'version': 'V016', 'sha256': 'a'*64}]
    def dump(self, path):
        self.dumps += 1
        if self.fail: raise OSError('fixture dump failure')
        path.write_bytes(b'synthetic dump'); path.chmod(0o600)


class ScheduledTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.root.chmod(0o700)
        self.rt = Runtime(self.root); self.pg = PG()
        for name, value in (('ROOT', self.root), ('STORE', self.root/'runtime/core-publications')):
            p = patch.object(backup.guard, name, value); p.start(); self.addCleanup(p.stop)
        for target, value in ((backup, {'verify_http': lambda *a, **k: {'fixture_http': True}}),
                              (release, {'inspect_schema': lambda *a: {'versions': list(range(16))}})):
            p = patch.multiple(target, **value); p.start(); self.addCleanup(p.stop)
        self.contract = {'backup_policy': dict(backup.POLICY), 'attachment_root': str(self.root/'attachments'), 'migrations': [{'version':f'V{i:03d}'} for i in range(1,17)]}
        self.job = self.at(0)
    def at(self, day):
        return backup.ScheduledBackup(self.rt, self.contract, pg=self.pg,
            now=datetime(2026, 9, 1, 19, 15, tzinfo=timezone.utc)+timedelta(days=day))

    def test_success_one_per_utc_day_and_no_other_role_restart(self):
        self.assertEqual(self.job.run()['outcome'], 'BACKUP_COMMITTED')
        self.assertEqual(self.job.run()['outcome'], 'BACKUP_ALREADY_VERIFIED')
        self.assertEqual(self.pg.dumps, 1)
        self.assertEqual(self.rt.events, ['stop-watchdog', 'stop-java', 'start-java', 'start-watchdog'])

    def test_policy_and_busy_refusal_are_before_mutation(self):
        with self.assertRaisesRegex(Refused, 'policy_not_bound'):
            backup.ScheduledBackup(self.rt, {}, pg=self.pg)
        self.rt.busy = True
        with self.assertRaises(Refused): self.job.run()
        self.assertEqual(self.rt.events, [])
        self.assertFalse(self.job.root.exists())

    def test_failed_dump_resumes_and_not_blindly_repeated(self):
        self.pg.fail = True
        with self.assertRaisesRegex(Refused, 'services_resumed'): self.job.run()
        self.assertTrue(self.rt.java and self.rt.watchdog)
        with self.assertRaisesRegex(Refused, 'already_attempted'): self.job.run()
        self.assertEqual(self.pg.dumps, 1)

    def test_failed_start_blocks_publication_and_recovery_does_not_dump_again(self):
        self.rt.fail_start = True
        with self.assertRaisesRegex(Refused, 'recovery_required'): self.job.run()
        with self.assertRaisesRegex(Refused, 'recovery_required'): backup.require_terminal(self.rt)
        self.rt.fail_start = False
        self.assertEqual(self.job.recover()['outcome'], 'RECOVERED_WITHOUT_REPLAY')
        self.assertEqual(self.pg.dumps, 1)
        backup.require_terminal(self.rt)
        self.assertTrue(self.rt.java and self.rt.watchdog)

    def test_recovery_refuses_changed_release_and_not_started(self):
        with self.assertRaisesRegex(Refused, 'not_started'): self.job.recover()
        self.rt.fail_start = True
        with self.assertRaises(Refused): self.job.run()
        self.rt.release = Release('d'*40, 'e'*64, 'f'*64)
        with self.assertRaisesRegex(Refused, 'current_drift'): self.job.recover()

    def test_daily_weekly_retention_owns_only_job_sets(self):
        unrelated = self.root/'runtime/historical.dump'; unrelated.write_bytes(b'preserve')
        for day in range(40): self.at(day).run()
        plan = self.job.retention()
        self.assertEqual(plan['outcome'], 'RETENTION_DRY_RUN')
        self.assertEqual(len(plan['keep']), 10)  # newest seven plus three older Saturdays
        self.assertEqual(len(list((self.job.root/'sets').iterdir())), 40)
        self.job.retention(apply=True)
        self.assertEqual(len(list((self.job.root/'sets').iterdir())), 10)
        self.assertEqual(self.job.retention(apply=True)['delete'], [])
        self.assertEqual(unrelated.read_bytes(), b'preserve')
        self.assertTrue((self.root/'attachments/11111111-1111-1111-1111-111111111111.blob').exists())

    def test_retention_rejects_foreign_file_and_symlink(self):
        for day in range(8): self.at(day).run()
        target = self.job.root/'sets/2026-09-01'
        (target/'foreign').write_bytes(b'not owned')
        with self.assertRaisesRegex(Refused, 'foreign_file'): self.job.retention(apply=True)
        self.assertTrue((target/'database.dump').exists())
        (target/'foreign').unlink()
        source = target/'files/11111111-1111-1111-1111-111111111111.blob'
        source.unlink(); source.symlink_to(self.root/'attachments/11111111-1111-1111-1111-111111111111.blob')
        with self.assertRaises((Refused, StorageRefused)): self.job.retention(apply=True)

    def test_partial_retention_is_resumable_without_foreign_deletion(self):
        for day in range(8): self.at(day).run()
        original = self.rt.storage.remove_bound_file; count = 0
        def interrupted(*a, **kw):
            nonlocal count
            count += 1
            if count == 2: raise OSError('fixture interruption')
            return original(*a, **kw)
        with patch.object(self.rt.storage, 'remove_bound_file', side_effect=interrupted):
            with self.assertRaises(OSError): self.job.retention(apply=True)
        self.assertEqual(self.job.retention(apply=True)['delete'], ['2026-09-01'])
        self.assertFalse((self.job.root/'sets/2026-09-01').exists())

    def test_marker_manual_control_private_repeat_and_backup(self):
        self.assertEqual(backup.generation_control(self.rt, self.contract, 'suspend')['outcome'], 'GENERATION_SUSPENDED')
        self.assertEqual(backup.generation_control(self.rt, self.contract, 'suspend')['outcome'], 'GENERATION_ALREADY_SUSPENDED')
        marker = self.root/'attachments'/release.GENERATION_MARKER
        self.assertEqual(marker.stat().st_mode & 0o777, 0o600)
        self.job.run()
        self.assertEqual((self.job.root/'sets/2026-09-01/files'/marker.name).read_bytes(), release.GENERATION_MARKER_BYTES)
        backup.generation_control(self.rt, self.contract, 'resume')
        self.assertFalse(marker.exists())
        self.assertTrue((self.root/'attachments/11111111-1111-1111-1111-111111111111.blob').exists())

    def test_bad_marker_is_not_overwritten_and_resume_requires_no_drift(self):
        marker = self.root/'attachments'/release.GENERATION_MARKER
        marker.write_bytes(b'foreign'); marker.chmod(0o600)
        with self.assertRaises(StorageRefused): backup.generation_control(self.rt, self.contract, 'resume')
        self.assertEqual(marker.read_bytes(), b'foreign')

    def test_dangling_marker_symlink_rejects_all_actions_before_any_journal_write(self):
        outside = self.root/'other-module'; outside.mkdir()
        sentinel = outside/'keep'; sentinel.write_bytes(b'preserve external bytes')
        missing = outside/'missing'
        marker = self.root/'attachments'/release.GENERATION_MARKER
        marker.symlink_to(missing)
        for action in ('status', 'suspend', 'resume'):
            with self.assertRaisesRegex(Refused, 'marker_symlink'):
                backup.generation_control(self.rt, self.contract, action)
            self.assertTrue(marker.is_symlink())
            self.assertFalse(missing.exists())
            self.assertFalse((self.root/'runtime/core-publications/ASSISTANT_GENERATION_CONTROL.json').exists())
            self.assertFalse((self.root/'runtime/core-publications/generation-control-history').exists())
            self.assertEqual(sentinel.read_bytes(), b'preserve external bytes')


if __name__ == '__main__': unittest.main()
