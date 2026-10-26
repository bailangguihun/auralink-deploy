"""Actual retained BusinessGuard + actual Role/Node in an isolated host tree."""
import copy
import fcntl
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch
import uuid
import urllib.request

import core_release_guard as guard
from community_host import Host, InstalledLease, SCHEMA, PARENT_SCHEMA, encoded, identity, sha
import test_community_runtime as fixtures


class HostTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.CommunityRuntimeTest('runTest'); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        f = self.f
        fcntl.flock(f.lease, fcntl.LOCK_UN)
        g = guard.BusinessGuard(); g.lock = f.base/'shared.lock'
        original = guard.read_file
        # Relocate only owner identity; execute the real retained verifier.
        p = patch.object(guard, 'read_file', lambda path, **kw: original(path, owner=(os.getuid(),os.getgid()), **kw))
        p.start(); self.addCleanup(p.stop)
        s = g.lock.stat()
        self.lease = InstalledLease(g, g.lock, dict(device=s.st_dev,inode=s.st_ino,path=str(g.lock)))
        def put(name, data):
            p=f.base/name; p.write_bytes(data); p.chmod(0o600);return p
        self.ap=put('parent.json',encoded(dict(schema=PARENT_SCHEMA)))
        self.jp=put('parent-journal.json',encoded(dict(phase='INSTALLED')))
        self.preserved=put('historical-consumption.json',encoded(dict(consumed=['a'],UNKNOWN=['b'])))
        self.archive=put('bundle.zip',f.zip_bytes)
        self.manifest=f.release/'manifest.json'
        self.settings=f.config/'env.json'; self.node=f.base/'node'
        py=Path(sys.executable).resolve()
        self.plan=dict(schema=SCHEMA,parent=dict(schema=PARENT_SCHEMA,approval_path=str(self.ap),
            approval_identity=identity(self.ap),journal_path=str(self.jp),journal_identity=identity(self.jp)),
            preserved={str(self.preserved):identity(self.preserved)}, lease={}, owner=[os.getuid(),os.getgid()],
            runtime=dict(python=dict(path=str(py),identity=identity(py)),node=dict(identity=identity(self.node),version=f.binding['node_version'])),
            service=dict(port=f.port,node_heap_mib=256,log_limit_bytes=8*1024*1024,restart_budget=0),
            archive=dict(identity=identity(self.archive),manifest_identity=identity(self.manifest),provenance=f.provenance),
            settings=identity(self.settings),core_commit='a'*40,expected_active_sha256=None,review_sha256='b'*64,controller_manifest_sha256='c'*64,
            web_origin=dict(path=str(f.base/'web-origin.json'),before_sha256=None))
        self.host=Host(f.base/'host',self.plan,self.lease)
        self.approval=put('approval.json',encoded(self.plan))
        self.args=(self.archive,self.manifest,self.settings,self.node,self.approval)
        self.addCleanup(self.clean)

    def clean(self):
        if self.host._role:
            for child in self.host._role.children.values():
                if child.poll() is None:child.terminate()
                child.wait(timeout=5)

    def install(self, **kw):
        with self.lease.held():return self.host.install(*self.args,**kw)

    def wait(self,state):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            with self.lease.held():s=self.host.control('observe')
            if s==state:return
            time.sleep(.03)
        self.fail('expected '+state+' got '+s)

    def test_host_requires_bound_committed_core_not_restored_candidate(self):
        import community_host
        tx='c'*32;root=self.f.base/'committed-core-fixture'
        journal=root/'runtime/core-publications/transactions'/tx/'journal.json'
        journal.parent.mkdir(parents=True)
        current={'transaction_id':tx,'release':{'commit_sha':'a'*40,'artifact_sha256':'b'*64}}
        journal.write_bytes(encoded({'phase':'COMMITTED','candidate':current['release']}))
        self.plan['preserved'][str(journal)]=identity(journal)
        with patch.object(community_host,'ROOT',root):
            self.host.require_committed_core(current)
            journal.write_bytes(encoded({'phase':'RESTORED','candidate':current['release']}))
            with self.assertRaisesRegex(ValueError,'core_not_committed'):self.host.require_committed_core(current)
            del self.plan['preserved'][str(journal)]
            with self.assertRaisesRegex(ValueError,'journal_binding'):self.host.require_committed_core(current)

    def test_real_guard_install_full_inventory_node_and_drain_preserves_history(self):
        before=self.preserved.read_bytes();ino=self.lease.lock.stat().st_ino
        self.assertEqual(self.install(),'INSTALLED')
        with self.lease.held():self.assertEqual(self.host.control('start',uuid.uuid4().hex),'STARTING')
        self.wait('READY')
        # HTTP still works while another process owns the global lease.
        with self.lease.held():
            with urllib.request.urlopen('http://127.0.0.1:'+str(self.f.port)+'/health') as r:self.assertEqual(r.status,200)
            pid=self.host.role().current()[1]['process']['pid']
            for p in (Path('/proc')/str(pid)/'fd').iterdir():
                try:self.assertNotEqual(p.stat().st_ino,ino)
                except FileNotFoundError:pass
            self.assertEqual(self.host.control('stop'),'STOPPING')
        self.wait('STOPPED');self.assertEqual(self.preserved.read_bytes(),before)
        self.assertEqual(self.install(),'INSTALLED_VERIFIED_NOOP')

    def test_actual_retained_guard_busy_and_wrong_inode(self):
        fd=os.open(self.lease.lock,os.O_RDWR)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(guard.Refused,'shared_publication_busy'):self.install()
        finally:os.close(fd)
        self.assertFalse(self.host.root.exists())
        self.lease.expected['inode']+=1
        with self.assertRaisesRegex(ValueError,'lock_replaced'):self.install()

    def test_supported_predecessor_only_and_metadata_drift(self):
        self.plan['parent']['schema']='old-unreviewed'
        with self.assertRaisesRegex(ValueError,'unsupported_predecessor'):self.install()
        self.plan['parent']['schema']=PARENT_SCHEMA
        self.preserved.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'identity_drift'):self.install()
        self.assertFalse(self.host.root.exists())

    def test_interrupted_file_and_pointer_commit_recover_without_reinstall_or_refund(self):
        original=self.host.storage.atomic_write_bytes
        def interrupted(path,data,**kw):
            result=original(path,data,**kw)
            if path==self.host.folder/'release/src/main.mjs':raise InterruptedError('after durable copy')
            return result
        with patch.object(self.host.storage,'atomic_write_bytes',interrupted):
            with self.assertRaises(InterruptedError):self.install()
        with self.assertRaisesRegex(ValueError,'recovery_required'):self.install()
        self.assertEqual(self.install(recover=True),'INSTALLED')
        self.assertTrue((self.host.folder/'approval.json').exists())
        self.assertEqual(self.preserved.read_bytes(),encoded(dict(consumed=['a'],UNKNOWN=['b'])))

    def test_lost_final_receipt_reconciles_exact_afterimage(self):
        original=self.host.write
        def fail(path,value):
            if value.get('phase')=='INSTALLED':raise InterruptedError('lost receipt')
            return original(path,value)
        with patch.object(self.host,'write',fail):
            with self.assertRaises(InterruptedError):self.install()
        self.assertTrue((self.host.root/'ACTIVE.json').exists())
        self.assertEqual(self.install(recover=True),'INSTALLED')

    def test_cas_conflict_refuses_and_preserves_foreign_pointer(self):
        original=self.host.storage.atomic_write_bytes
        def conflict(path,data,**kw):
            if path==self.host.root/'ACTIVE.json':
                original(path,encoded(dict(foreign=True)))
            return original(path,data,**kw)
        with patch.object(self.host.storage,'atomic_write_bytes',conflict):
            with self.assertRaisesRegex(ValueError,'already_exists'):self.install()
        with self.assertRaisesRegex(ValueError,'active_cas'):self.install(recover=True)
        self.assertEqual(self.host.read(self.host.root/'ACTIVE.json'),dict(foreign=True))

    def test_no_pidfd_or_bad_approval_never_creates_state(self):
        self.approval.write_bytes(b'not approval')
        with self.assertRaisesRegex(ValueError,'exact_plan_approval'):self.install()
        self.assertFalse(self.host.root.exists())

    def test_archive_and_settings_identity_checked_before_any_write(self):
        self.settings.chmod(0o644)
        with self.assertRaisesRegex(ValueError,'identity_drift'):self.install()
        self.assertFalse(self.host.root.exists())

    def test_unknown_process_never_restart_or_resend_signal(self):
        self.install()
        with self.lease.held():
            self.host.control('start',uuid.uuid4().hex)
            path,r=self.host.role().current();r['process']=None;self.host.role().write(path,r)
            self.assertEqual(self.host.control('observe'),'UNKNOWN')
            with self.assertRaisesRegex(ValueError,'existing_or_unknown'):self.host.control('start',uuid.uuid4().hex)
            with patch('community_lifecycle.signal.pidfd_send_signal') as send:
                with self.assertRaisesRegex(ValueError,'unknown_process'):self.host.control('stop')
                send.assert_not_called()

    def test_log_threshold_drains_without_deleting_or_restart(self):
        self.install()
        with self.lease.held():self.host.control('start',uuid.uuid4().hex)
        self.wait('READY')
        record=self.host.role().current()[1];log=self.host.role().root/('run-'+record['operation_id']+'.log')
        with log.open('ab') as f:f.write(b'x'*(8*1024*1024))
        with self.lease.held():self.assertEqual(self.host.control('observe'),'LOG_LIMIT_DRAINING')
        self.wait('STOPPED');self.assertGreaterEqual(log.stat().st_size,8*1024*1024)

    def test_web_origin_only_after_ready_and_receipt_loss_does_not_reexecute(self):
        self.install()
        with self.lease.held():
            with self.assertRaisesRegex(ValueError,'not_ready'):self.host.bind_web()
            self.host.control('start',uuid.uuid4().hex)
        self.wait('READY')
        original=self.host.write
        def fail(path,value):
            if path.name=='web-origin-receipt.json':raise InterruptedError('lost web receipt')
            return original(path,value)
        with self.lease.held(),patch.object(self.host,'write',fail):
            with self.assertRaises(InterruptedError):self.host.bind_web()
        current=self.host.role().current()[1]
        with self.lease.held():self.assertEqual(self.host.bind_web(),'WEB_ORIGIN_BOUND_NOT_DEPLOYED')
        self.assertEqual(self.host.role().current()[1]['operation_id'],current['operation_id'])
        self.assertEqual(Path(self.plan['web_origin']['path']).read_bytes(),encoded({'COMMUNITY_INTERNAL_ORIGIN':'http://127.0.0.1:'+str(self.f.port)}))

    def test_runtime_does_not_couple_community_to_vmm_evidence_changes(self):
        self.install()
        self.preserved.write_bytes(encoded(dict(new_vmm_window='preserved forward change')))
        with self.lease.held():self.assertEqual(self.host.control('observe'),'NOT_STARTED')

    def test_large_inventory_member_is_atomic_and_hardlink_rejected(self):
        # Exercise the archive's >1 MiB file limit independently of JSON records.
        self.f.manifest['files']=[x for x in self.f.manifest['files'] if x['path']!='src/media.mjs']
        content=b'//' + b' '*1100000
        (self.f.release/'src/media.mjs').write_bytes(content)
        self.f.manifest['files'].append(dict(path='src/media.mjs',bytes=len(content),sha256=sha(content)))
        raw=self.f.make_zip();self.f.manifest['archive_sha256']=sha(raw)
        self.archive.write_bytes(raw);self.manifest.write_bytes(encoded(self.f.manifest))
        self.plan['archive'].update(identity=identity(self.archive),manifest_identity=identity(self.manifest))
        self.host=Host(self.f.base/'host',self.plan,self.lease);self.approval.write_bytes(encoded(self.plan))
        self.assertEqual(self.install(),'INSTALLED')
        path=self.host.folder/'release/src/media.mjs';self.assertEqual(path.read_bytes(),content)
        os.link(path,self.f.base/'forbidden-hardlink')
        with self.lease.held():
            with self.assertRaisesRegex(ValueError,'hardlink'):self.host.control('start',uuid.uuid4().hex)
        self.assertFalse((self.host.folder/'process/current.json').exists())

    def test_unsupported_heap_refused_before_process_intent(self):
        self.install();self.host.role().binding['node_heap_mib']=512
        with self.lease.held():
            with self.assertRaisesRegex(ValueError,'heap_policy'):self.host.control('start',uuid.uuid4().hex)
        self.assertFalse((self.host.folder/'process/current.json').exists())


if __name__=='__main__':unittest.main()
