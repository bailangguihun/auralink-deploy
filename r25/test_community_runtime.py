import fcntl
import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid
import zipfile

from community_bundle import REQUIRED, verify_archive
from community_lifecycle import Role, process
from r25_storage import StorageRefused


def digest(raw): return hashlib.sha256(raw).hexdigest()


class CommunityRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'control';self.release=self.base/'release';self.config=self.base/'config'
        for p in (self.root,self.release,self.config):p.mkdir(mode=0o700)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));self.port=sock.getsockname()[1]
        for name in REQUIRED:
            p=self.release/name;p.parent.mkdir(mode=0o700,parents=True,exist_ok=True);p.write_text('fixture');p.chmod(0o600)
        (self.release/'package.json').write_text('{"type":"module"}')
        (self.release/'src/main.mjs').write_text('''import http from 'node:http';
const server=http.createServer((req,res)=>{res.setHeader('content-type','application/json');res.end(JSON.stringify({status:'UP',contractVersion:1}));});
server.listen(Number(process.env.COMMUNITY_PORT),'127.0.0.1');
process.once('SIGTERM',()=>{server.close();setTimeout(()=>console.log('fixture_drained'),400);});
''')
        self.provenance=dict(commit='a'*40,run_id='1',run_attempt='1')
        self.manifest=dict(schema='artlive-community-bundle/v1',repository='bailangguihun/auralink-core-api',ref='refs/heads/release',
            **self.provenance,build_node='v22.13.0',runtime_node_min='22.13.0',contract_version=1,database_schema_version=1,
            files=[dict(path=p,bytes=len((self.release/p).read_bytes()),sha256=digest((self.release/p).read_bytes())) for p in sorted(REQUIRED)])
        self.zip_bytes=self.make_zip()
        self.manifest['archive_sha256']=digest(self.zip_bytes)
        raw=json.dumps(self.manifest).encode();(self.release/'manifest.json').write_bytes(raw);(self.release/'manifest.json').chmod(0o600)
        config=json.dumps(dict(COMMUNITY_DATABASE_URL='postgres://fixture:fixture@127.0.0.1:5432/community_fixture',
            COMMUNITY_CORE_ORIGIN='http://127.0.0.1:39111',COMMUNITY_WEB_ORIGIN='https://fixture.invalid',COMMUNITY_PORT=str(self.port))).encode()
        (self.config/'env.json').write_bytes(config);(self.config/'env.json').chmod(0o600)
        # Tool-cache executables may be group-writable on hosted CI. Exercise
        # the real private-runtime contract using an exact isolated byte copy;
        # do not relax the production executable policy for the runner image.
        node=self.base/'node'
        shutil.copyfile(Path(shutil.which('node')).resolve(),node);node.chmod(0o700)
        import subprocess
        self.binding=dict(release_root=str(self.release),manifest_sha256=digest(raw),provenance=self.provenance,
            node=str(node),node_sha256=digest(node.read_bytes()),node_version=subprocess.check_output([str(node),'--version'],text=True).strip(),
            config_path=str(self.config/'env.json'),config_sha256=digest(config),port=self.port)
        self.lease=os.open(self.base/'shared.lock',os.O_RDWR|os.O_CREAT,0o600);fcntl.flock(self.lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.active=True
        def require():
            if not self.active:raise StorageRefused('original_shared_lease_required')
            os.fstat(self.lease)
        self.role=Role(self.root,self.binding,require)
        self.addCleanup(self.cleanup_processes)

    def cleanup_processes(self):
        # Only children created by this disposable fixture, never a pid scan.
        for child in self.role.children.values():
            if child.poll() is None:child.terminate()
            child.wait(timeout=5)
        os.close(self.lease)

    def make_zip(self, special=None):
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:
            for p in sorted(REQUIRED):
                info=zipfile.ZipInfo(p);info.create_system=3;info.external_attr=0o100600<<16
                if special and p=='src/main.mjs':info.external_attr=special<<16
                z.writestr(info,(self.release/p).read_bytes())
        return out.getvalue()

    def wait(self, expected):
        end=time.monotonic()+5
        while time.monotonic()<end:
            if self.role.observe()==expected:return
            time.sleep(.03)
        self.fail('fixture did not reach '+expected)

    def start(self):
        operation=uuid.uuid4().hex
        self.assertEqual(self.role.start(operation),'STARTING');self.wait('READY')
        return operation

    def test_start_drain_restart_preserves_prior_proofs_and_closes_shared_fd(self):
        first=self.start();_,record=self.role.current();pid=record['process']['pid']
        identity=os.fstat(self.lease)
        for fd in (Path('/proc')/str(pid)/'fd').iterdir():
            try:other=fd.stat()
            except FileNotFoundError:continue
            self.assertNotEqual((identity.st_dev,identity.st_ino),(other.st_dev,other.st_ino))
        self.assertEqual(self.role.stop(0),'STOPPING');self.wait('STOPPED')
        proof=(self.root/('run-'+first+'.json')).read_bytes()
        self.start();self.assertEqual((self.root/('run-'+first+'.json')).read_bytes(),proof)
        self.assertEqual(self.role.stop(2),'STOPPED')

    def test_original_shared_lease_required(self):
        self.active=False
        for operation in (lambda:self.role.start(uuid.uuid4().hex),self.role.observe,self.role.stop):
            with self.assertRaisesRegex(StorageRefused,'original_shared'):operation()
        self.assertEqual(list(self.root.iterdir()),[])

    def test_owned_process_can_stop_when_health_endpoint_is_unavailable(self):
        self.start()
        with patch('community_lifecycle.http.client.HTTPConnection',side_effect=AssertionError('stop must not require HTTP health')):
            self.assertEqual(self.role.stop(2),'STOPPED')

    def test_stop_observation_does_not_require_disappearing_exe_or_cwd_links(self):
        self.start();self.assertEqual(self.role.stop(0),'STOPPING')
        with patch('community_lifecycle.process',side_effect=AssertionError('exit is checked by start ticks, not exe/cwd')):
            self.wait('STOPPED')

    def test_release_and_private_config_drift_refused(self):
        (self.release/'src/core.mjs').write_text('drift')
        with self.assertRaisesRegex(StorageRefused,'sha256'):self.role.start(uuid.uuid4().hex)
        self.assertFalse((self.root/'current.json').exists())

    def test_private_config_mode_refused(self):
        (self.config/'env.json').chmod(0o644)
        with self.assertRaisesRegex(StorageRefused,'file_mode'):self.role.start(uuid.uuid4().hex)

    def test_foreign_port_refused_without_signal(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',self.port));sock.listen()
            with self.assertRaisesRegex(StorageRefused,'port_busy'):self.role.start(uuid.uuid4().hex)
        self.assertFalse((self.root/'current.json').exists())

    def test_pid_identity_change_never_signals(self):
        self.start();path,record=self.role.current();original=dict(record['process']);record['process']['ticks']='0'
        self.role.write(path,record)
        with patch('community_lifecycle.signal.pidfd_send_signal') as send:
            with self.assertRaisesRegex(StorageRefused,'identity_changed'):self.role.stop()
            send.assert_not_called()
        self.assertIsNotNone(process(original['pid']))
        record['process']=original;self.role.write(path,record);self.role.stop(2)

    def test_lost_start_identity_remains_unknown_and_cannot_restart(self):
        original=self.role.write
        def fail(path,value):
            if value.get('phase')=='STARTING':raise OSError('fixture lost persist')
            return original(path,value)
        with patch.object(self.role,'write',side_effect=fail):
            with self.assertRaisesRegex(OSError,'lost persist'):self.role.start(uuid.uuid4().hex)
        self.assertEqual(self.role.observe(),'UNKNOWN')
        with self.assertRaisesRegex(StorageRefused,'existing_or_unknown'):self.role.start(uuid.uuid4().hex)
        with self.assertRaisesRegex(StorageRefused,'unknown_process'):self.role.stop()

    def test_lost_stop_ack_reconciles_without_repeated_signal(self):
        self.start();original=self.role.write
        def fail(path,value):
            if value.get('phase')=='STOPPING':raise OSError('fixture lost stop receipt')
            return original(path,value)
        with patch.object(self.role,'write',side_effect=fail):
            with self.assertRaisesRegex(OSError,'lost stop'):self.role.stop()
        with patch('community_lifecycle.signal.pidfd_send_signal') as send:
            self.role.stop(0);send.assert_not_called();self.wait('STOPPED')

    def test_artifact_digest_provenance_and_symlink_rejections(self):
        raw=json.dumps(self.manifest).encode()
        self.assertEqual(verify_archive(self.zip_bytes,raw,self.provenance)['files'],self.manifest['files'])
        with self.assertRaisesRegex(StorageRefused,'archive_digest'):verify_archive(self.zip_bytes+b'x',raw,self.provenance)
        with self.assertRaisesRegex(StorageRefused,'release_mismatch'):verify_archive(self.zip_bytes,raw,{**self.provenance,'run_attempt':'2'})
        unsafe=self.make_zip(0o120777);self.manifest['archive_sha256']=digest(unsafe)
        with self.assertRaisesRegex(StorageRefused,'file_type'):verify_archive(unsafe,json.dumps(self.manifest).encode(),self.provenance)

    def test_manifest_traversal_and_duplicate_inventory_refused(self):
        self.manifest['files'][0]['path']='../outside'
        with self.assertRaisesRegex(StorageRefused,'file_path'):verify_archive(self.zip_bytes,json.dumps(self.manifest).encode(),self.provenance)
        self.manifest['files'][0]['path']=self.manifest['files'][1]['path']
        with self.assertRaisesRegex(StorageRefused,'duplicate_file'):verify_archive(self.zip_bytes,json.dumps(self.manifest).encode(),self.provenance)

    def test_controller_extension_packages_exact_reviewed_modules(self):
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('community_extension',root/'scripts/package-community-extension.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        provenance=dict(repository='bailangguihun/auralink-deploy',ref='refs/heads/release',**self.provenance)
        archive,raw=module.materialize(root,provenance)
        self.assertEqual((archive,raw),module.materialize(root,provenance))
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            self.assertEqual(z.namelist(),list(module.MODULES))
            for name in module.MODULES:self.assertEqual(z.read(name),(root/'r25'/name).read_bytes())
        self.assertEqual(json.loads(raw)['archive_sha256'],digest(archive))
