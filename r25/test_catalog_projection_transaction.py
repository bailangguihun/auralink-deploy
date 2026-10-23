import os
import json
from pathlib import Path
import tempfile
import unittest

import catalog_projection_transaction as tx


class Backend:
    def __init__(self,state='COMMITTED_VERIFIED',fail=None):
        self.calls=[];self.state=state;self.fail=fail
    def call(self,name):
        self.calls.append(name)
        if self.fail==name:raise RuntimeError('ISOLATED_'+name)
    def preflight(self):self.call('preflight')
    def consume(self):self.call('consume')
    def load_consumption(self):self.call('load_consumption')
    def quiesce(self):self.call('quiesce')
    def backup(self):self.call('backup')
    def submit_once(self):self.call('submit_once')
    def reconcile(self):self.call('reconcile');return self.state
    def resume(self):self.call('resume')
    def save(self,phase,**fields):self.call('save:'+phase)


class TransactionTest(unittest.TestCase):
    def test_new_plan_cannot_bypass_an_older_unknown_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);folder=root/'prior';folder.mkdir()
            read=lambda p:json.loads(p.read_text())
            with self.assertRaises(RuntimeError):tx.require_prior_repairs_terminal(root,read)
            record=folder/'RECORD.json'
            for phase in ('CONSUMED','SQL_SUBMISSION_INTENT','UNKNOWN','RECOVERY_REQUIRED'):
                record.write_text(json.dumps(dict(schema=tx.SCHEMA,phase=phase)))
                with self.assertRaises(RuntimeError):tx.require_prior_repairs_terminal(root,read)
            record.write_text(json.dumps(dict(schema=tx.SCHEMA,phase='NO_COMMIT_VERIFIED_RESUMED')))
            tx.require_prior_repairs_terminal(root,read)

    def test_success_single_submission_consumes_before_stop(self):
        b=Backend();self.assertEqual(tx.execute(b),'COMMITTED_VERIFIED_RESUMED')
        self.assertEqual(b.calls.count('submit_once'),1)
        self.assertLess(b.calls.index('consume'),b.calls.index('quiesce'))
        self.assertLess(b.calls.index('backup'),b.calls.index('submit_once'))

    def test_preflight_refusal_does_not_consume_or_stop(self):
        b=Backend(fail='preflight')
        with self.assertRaises(RuntimeError):tx.execute(b)
        self.assertEqual(b.calls,['preflight'])

    def test_backup_failure_no_sql_and_unchanged_service_resume(self):
        b=Backend(state='NO_COMMIT_VERIFIED',fail='backup');tx.execute(b)
        self.assertNotIn('submit_once',b.calls);self.assertEqual(b.calls.count('resume'),1)

    def test_ambiguous_client_error_after_commit_never_resubmits(self):
        b=Backend(fail='submit_once');tx.execute(b)
        self.assertEqual(b.calls.count('submit_once'),1);self.assertEqual(b.calls.count('resume'),1)
        self.assertIn('save:COMMITTED_VERIFIED',b.calls)

    def test_rollback_does_not_refund_or_retry(self):
        b=Backend(state='NO_COMMIT_VERIFIED',fail='submit_once');tx.execute(b)
        self.assertEqual(b.calls.count('consume'),1);self.assertEqual(b.calls.count('submit_once'),1)
        self.assertEqual(b.calls.count('resume'),1)

    def test_unknown_keeps_writers_stopped(self):
        for fail in (None,'submit_once'):
            b=Backend(state='UNKNOWN',fail=fail);self.assertEqual(tx.execute(b),'UNKNOWN')
            self.assertNotIn('resume',b.calls);self.assertEqual(b.calls.count('submit_once'),1)

    def test_recovery_only_reconciles_no_new_approval_or_sql(self):
        b=Backend();tx.execute(b,recover=True)
        self.assertIn('load_consumption',b.calls)
        for forbidden in ('consume','submit_once','backup','quiesce'):self.assertNotIn(forbidden,b.calls)

    def test_resume_failure_has_no_automatic_second_attempt(self):
        b=Backend(fail='resume');self.assertEqual(tx.execute(b),'RECOVERY_REQUIRED')
        self.assertEqual(b.calls.count('resume'),1);self.assertEqual(b.calls.count('submit_once'),1)

    def test_private_metadata_hash_and_links(self):
        with tempfile.TemporaryDirectory()as d:
            p=Path(d)/'file';p.write_bytes(b'evidence');p.chmod(0o600);owner=(os.getuid(),os.getgid())
            self.assertEqual(tx.private(p,tx.sha(b'evidence'),owner=owner),b'evidence')
            with self.assertRaises(RuntimeError):tx.private(p,'0'*64,owner=owner)
            p.chmod(0o700)
            with self.assertRaises(RuntimeError):tx.private(p,owner=owner)
            p.chmod(0o600);hard=Path(d)/'hard';os.link(p,hard)
            with self.assertRaises(RuntimeError):tx.private(p,owner=owner)
            hard.unlink();link=Path(d)/'link';link.symlink_to(p)
            with self.assertRaises(OSError):tx.private(link,owner=owner)

    def test_confirmation_binds_exact_plan_count_and_scope(self):
        text=tx.approval_text('a'*64,486)
        self.assertNotEqual(text,tx.approval_text('b'*64,486))
        self.assertNotEqual(text,tx.approval_text('a'*64,1))
        self.assertIn('不授权VMM启动、OPEN、部署或任何生成',text)


if __name__=='__main__':unittest.main()
