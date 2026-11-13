"""Finite Reference successor through the complete retained Studio proof chain.

The full control-chain fixture uses synthetic JAR classes, Community receipts,
Java, PostgreSQL and supervision. Formal bundle verification, original flock,
plan validation, CAS, history traversal, installation and recovery execute their
real validators. Additional storage and library tests use real private files;
recovery-boundary tests use an actual owned fixture Node and a synthetic DB.
"""
from copy import deepcopy
from pathlib import Path
import signal
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import community_host as community
import core_release_guard as guard
import upgrade_assistant_controller as u
import vmm_controller as v
import test_studio_publication as studio_fixture


class Reference(unittest.TestCase):
    def setUp(self):
        parent=studio_fixture.Studio('runTest')
        parent.setUp();self.addCleanup(parent.doCleanups)
        parent.prepare();parent.f.install()
        parent.k.commit_current(parent.f.new,'b5'*16,parent.plan['current_release'])
        self.parent=parent;self.k=parent.k;self.f=parent.f;self.rt=parent.rt
        self.root=self.k.root;self.put=self.k.put
        self.studio_plan=deepcopy(parent.plan)
        self.f.old=deepcopy(self.f.new);self.f.oldplan=deepcopy(parent.plan)
        self.migrations=deepcopy(self.f.old['upgrade']['assistant']['migrations'])
        self.assertEqual(17,len(self.migrations))
        self.assertEqual('R25_STUDIO_CORE_PUBLICATION_V1',
                         self.f.old['upgrade']['core_followup']['schema'])
        # The original package upgrade is already committed. Only its immutable
        # proof is needed here; no pretend Node/PG execution is attributed to it.
        bound=self.studio_plan['vmm']['community']
        self.community=self.root/'runtime/community/installations'/bound['plan_sha256']
        package={'schema':'ARTLIVE_COMMUNITY_PACKAGE_UPGRADE_V1',
                 'original_plan_sha256':bound['plan_sha256'],
                 'fixture_only':'already committed first Community package'}
        raw=v.encoded(package);self.package_id=v.sha(raw)
        self.package_folder=self.community/'package-upgrades'/self.package_id
        self.put(self.package_folder/'approval.json',raw)
        self.put(self.package_folder/'journal.json',v.encoded({
            'schema':package['schema'],'plan_sha256':self.package_id,'phase':'COMMITTED'}))
        self.put(self.community/'RELEASE_ACTIVE.json',v.encoded({
            'schema':package['schema'],'plan_sha256':self.package_id}))
        self.before={p:p.read_bytes() for base in (
            self.rt.store,self.f.f.store/'controller-upgrades',
            self.f.f.store/'core-followup-baselines',self.root/'runtime/community')
            for p in base.rglob('*') if p.is_file()}
        self.lock_inode=self.rt.lock.stat().st_ino
        self.f.original_snapshot=self.f.snapshot()
        self.f.f.rt.events.clear();self.k.actions.clear()

    def prepare(self):
        result=self.f.prepare(reference=True)
        self.plan=self.f.plan;self.ident=result['plan_sha256']
        self.put(self.f.f.store/'publication-approvals'/(self.ident+'.json'),v.encoded(self.plan))
        self.f.ident=self.ident
        return result

    def effective(self):
        return v.apply_policy_overlay(self.f.new,v.env_values(self.f.g.live['handoff'].read_bytes()),
                                      self.root,fixture_runtime=self.rt)

    def retained(self):
        for path,raw in self.before.items():self.assertEqual(raw,path.read_bytes(),str(path))
        self.assertEqual(self.lock_inode,self.rt.lock.stat().st_ino)

    def commit_candidate(self):
        return self.k.commit_current(self.f.new,'b6'*16,self.plan['current_release'])

    def test_install_and_new_core_current_preserve_the_entire_studio_history(self):
        self.prepare()
        self.assertEqual(u.REFERENCE_SCHEMA,self.plan['schema'])
        self.assertEqual('INSTALLED',self.f.install())
        self.assertEqual(self.migrations,self.effective()['upgrade']['assistant']['migrations'])
        self.retained()
        self.commit_candidate()
        effective=self.effective()
        self.assertEqual('R25_REFERENCE_CORE_PUBLICATION_V1',
                         effective['upgrade']['core_followup']['schema'])
        self.assertEqual(self.migrations,effective['upgrade']['assistant']['migrations'])
        self.assertEqual(self.f.old['upgrade']['compatibility'],effective['upgrade']['compatibility'])
        self.assertEqual('INSTALLED_VERIFIED_NOOP',self.f.install())
        self.assertEqual('INSTALLED_NO_RECOVERY',self.f.recover())
        self.retained()

    def test_partial_policy_copy_restores_only_the_reference_attempt(self):
        self.prepare();copy=self.f.f.rt.storage.atomic_copy_file
        def lost_reply(src,dst,*args,**kwargs):
            result=copy(src,dst,*args,**kwargs)
            if Path(dst)==self.f.f.store/'POLICY.json':
                raise InterruptedError('fixture lost Reference policy-copy reply')
            return result
        with patch.object(self.f.f.rt.storage,'atomic_copy_file',side_effect=lost_reply):
            with self.assertRaisesRegex(InterruptedError,'lost Reference'):
                self.f.install()
        self.assertEqual('RESTORED',self.f.recover())
        self.assertFalse((self.rt.store/'REFERENCE_PUBLICATION_ACTIVE.json').exists())
        restored=v.apply_policy_overlay(self.f.old,v.env_values(self.f.g.live['handoff'].read_bytes()),
                                         self.root,fixture_runtime=self.rt)
        self.assertEqual('R25_STUDIO_CORE_PUBLICATION_V1',restored['upgrade']['core_followup']['schema'])
        self.retained()

    def test_durable_installed_receipt_loss_does_not_reauthorize_rollback(self):
        self.prepare();write=self.f.f.rt.write
        def lost_reply(path,value):
            result=write(path,value)
            if value.get('phase')=='INSTALLED':
                raise InterruptedError('fixture lost durable installed reply')
            return result
        with patch.object(self.f.f.rt,'write',side_effect=lost_reply):
            with self.assertRaisesRegex(InterruptedError,'durable installed'):
                self.f.install()
        self.effective()
        self.assertEqual('INSTALLED_NO_RECOVERY',self.f.recover())
        self.assertEqual('INSTALLED_VERIFIED_NOOP',self.f.install())
        self.retained()

    def test_current_control_pointer_and_studio_parent_drift_are_refused(self):
        self.prepare();self.f.install();self.commit_candidate();self.effective()
        current=self.f.f.store/'CURRENT.json';pointer=self.f.f.store/'CONTROL_UPGRADE_ACTIVE.json'
        parent=self.f.f.store/'controller-upgrades'/self.parent.ident/'approval.json'
        cases=[(current,lambda raw:v.encoded(dict(v.json_bytes(raw),release={
            **v.json_bytes(raw)['release'],'commit_sha':'f'*40}))),
            (pointer,lambda raw:v.encoded({'schema':'R25_CORE_CONTROLLER_UPGRADE_ACTIVE_V1',
                                         'approval_sha256':'f'*64})),
            (parent,lambda raw:raw+b' ')]
        for path,change in cases:
            with self.subTest(path=str(path)):
                raw=path.read_bytes();self.put(path,change(raw))
                try:
                    with self.assertRaises(Exception):self.effective()
                finally:self.put(path,raw)
                self.effective()
        self.retained()

    def test_reference_cannot_change_any_core_migration(self):
        self.prepare()
        for change in ('append','replace'):
            with self.subTest(change=change):
                altered=deepcopy(self.f.new)
                entries=altered['upgrade']['assistant']['migrations']
                if change=='append':entries.append({'version':'V018','fixture_only':True})
                else:entries[0]={'version':'V001','fixture_only':'foreign bytes'}
                with self.assertRaisesRegex(Exception,'assistant_policy_change'):
                    u.validate_managed_successor_bindings(
                        self.f.f.rt,self.plan,self.f.old,altered,self.f.bundle)
        self.assertEqual(self.migrations,self.f.new['upgrade']['assistant']['migrations'])
        self.retained()

    def test_existing_reference_marker_cannot_start_a_second_binding(self):
        self.put(self.rt.store/'REFERENCE_PUBLICATION_ACTIVE.json',v.encoded({
            'schema':v.REFERENCE_MARKER,'approval_sha256':'f'*64}))
        with self.assertRaisesRegex(Exception,'reference_already_bound'):
            self.prepare()
        self.assertEqual([],self.f.f.rt.events)
        self.retained()

    def test_candidate_bytes_and_old_package_receipt_drift_refuse_before_signals(self):
        self.prepare()
        candidate=self.f.new['upgrade']['candidate']
        jar=self.root/'incoming'/(
            candidate['commit_sha']+'-'+candidate['github_run_id']+'-'+candidate['github_run_attempt'])/'auralink-backend-0.0.1-SNAPSHOT.jar'
        for path in (jar,self.package_folder/'journal.json'):
            with self.subTest(path=str(path)):
                raw=path.read_bytes();self.put(path,raw+b' ')
                try:
                    with self.assertRaises(Exception):self.f.install()
                    self.assertEqual([],self.f.f.rt.events)
                    self.assertEqual(self.f.original_snapshot,self.f.snapshot())
                finally:self.put(path,raw)
        self.assertEqual('INSTALLED',self.f.install())
        self.effective();self.retained()

    def test_verify_intent_failure_removes_only_new_marker_during_recovery(self):
        self.prepare()
        def failed(mode,fd):
            self.k.actions.append(mode)
            raise RuntimeError('fixture Reference preserve failed')
        with patch.object(self.f.f.rt.engine_value,'community_action',side_effect=failed):
            with self.assertRaisesRegex(RuntimeError,'Reference preserve failed'):
                self.f.install()
        self.assertTrue((self.rt.store/'REFERENCE_PUBLICATION_ACTIVE.json').exists())
        self.assertEqual('RESTORED',self.f.recover())
        self.assertFalse((self.rt.store/'REFERENCE_PUBLICATION_ACTIVE.json').exists())
        self.assertEqual(self.before[self.rt.store/'STUDIO_PUBLICATION_ACTIVE.json'],
                         (self.rt.store/'STUDIO_PUBLICATION_ACTIVE.json').read_bytes())
        self.retained()

    def test_reference_requires_committed_studio_core_transaction(self):
        current=v.json_read(self.f.f.store/'CURRENT.json',self.rt.private)
        journal=self.f.f.store/'transactions'/current['transaction_id']/'journal.json'
        raw=journal.read_bytes();value=v.json_bytes(raw);value['phase']='RECOVERY_REQUIRED'
        self.put(journal,v.encoded(value))
        try:
            with self.assertRaises(Exception):self.prepare()
            self.assertEqual([],self.f.f.rt.events)
        finally:self.put(journal,raw)
        self.retained()


class ReferenceCoreRecords(unittest.TestCase):
    """Real private storage boundaries; package and Core receipts are fixtures.

    This fixture checks retained-file access, not Node, PostgreSQL or deployment
    lease authenticity. Neither Host.read nor R25Storage is replaced.
    """
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        replacement=patch.object(community,'ROOT',self.root)
        replacement.start();self.addCleanup(replacement.stop)
        self.host=community.Host(self.root/'runtime/community',
            {'fixture_only':'private Community storage boundary'},
            SimpleNamespace(require=lambda:None))
        self.base=self.root/'runtime/core-publications'
        self.candidate={'commit_sha':'c1'*20,'artifact_sha256':'c2'*32}
        self.old_release={'commit_sha':'c3'*20,'artifact_sha256':'c4'*32}
        self.tx='c5'*16
        previous_context={'studio_approval_sha256':'c6'*32,'core_commit':'c7'*20,
                          'core_current_sha256':'c8'*32,'core_transaction_sha256':'c9'*32}
        old={'schema':'ARTLIVE_COMMUNITY_PACKAGE_UPGRADE_V1',
             'original_plan_sha256':self.host.digest,'context':previous_context}
        old_id=community.sha(community.encoded(old))
        oldfolder=self.host.folder/'package-upgrades'/old_id
        approval=oldfolder/'approval.json';self.put(approval,old)
        journal=oldfolder/'journal.json'
        self.put(journal,{'schema':old['schema'],'plan_sha256':old_id,'phase':'COMMITTED'})
        marker=self.host.folder/'RELEASE_ACTIVE.json'
        self.put(marker,{'schema':old['schema'],'plan_sha256':old_id})
        previous={'plan_sha256':old_id,'marker_sha256':community.identity(marker)['sha256'],
                  'journal_sha256':community.identity(journal)['sha256']}
        policy={'upgrade':{'candidate':self.candidate}}
        approved={'schema':'R25_REFERENCE_CORE_PUBLICATION_BINDING_V1',
                  'vmm':{'community':{'plan_sha256':self.host.digest,'package':previous}},
                  'operational':{'policy':{'after_sha256':community.sha(community.encoded(policy))}},
                  'current_release':self.old_release}
        self.reference_id=community.sha(community.encoded(approved))
        self.core_folder=self.base/'controller-upgrades'/self.reference_id
        self.put(self.core_folder/'approval.json',approved)
        self.put(self.core_folder/'journal.json',{'approval_sha256':self.reference_id,'phase':'INSTALLED'})
        self.put(self.base/'POLICY.json',policy)
        self.current(self.old_release)
        self.put(self.base/'transactions'/self.tx/'journal.json',
                 {'transaction_id':self.tx,'candidate':self.candidate,'phase':'COMMITTED'})
        self.expected={'reference_sha256':self.reference_id,'core_commit':self.candidate['commit_sha'],
                       'candidate':self.candidate,'predecessor':previous,'previous_context':previous_context}
        self.old_receipts={path:path.read_bytes() for path in (approval,journal,marker)}

    def put(self,path,value):
        for parent in reversed((path.parent,*path.parent.parents)):
            if parent == self.root or self.root in parent.parents:
                parent.mkdir(exist_ok=True);parent.chmod(0o700)
        path.write_bytes(community.encoded(value));path.chmod(0o600)

    def current(self,release):
        self.put(self.base/'CURRENT.json',{'release':release,'transaction_id':self.tx})

    def retained(self):
        for path,raw in self.old_receipts.items():self.assertEqual(raw,path.read_bytes(),str(path))

    def test_context_reads_retained_core_proofs_without_widening_community_storage(self):
        self.assertEqual(self.expected,community.reference_package_context(self.host,self.reference_id))
        with self.assertRaisesRegex(ValueError,'path_outside_allowed_roots'):
            self.host.read(self.base/'CURRENT.json')
        with self.assertRaisesRegex(ValueError,'path_outside_allowed_roots'):
            community.reference_core_record(self.host,self.host.folder/'RELEASE_ACTIVE.json')
        self.assertEqual((self.host.root,),tuple(Path(p) for p in self.host.storage.allowed_roots))
        self.current(self.candidate)
        self.assertEqual(self.expected,community.reference_package_context(self.host,self.reference_id))
        self.retained()

    def test_require_core_reads_actual_private_current_and_committed_transaction(self):
        successor=community.ReferencePackageUpgrade(self.host,None,self.expected)
        with self.assertRaisesRegex(ValueError,'community_reference_new_core_required'):
            successor.require_core()
        self.current(self.candidate)
        self.assertIsNone(successor.require_core())
        receipt=self.base/'transactions'/self.tx/'journal.json'
        raw=receipt.read_bytes()
        self.put(receipt,{'transaction_id':self.tx,'phase':'VERIFY_INTENT'})
        with self.assertRaisesRegex(ValueError,'community_reference_core_not_committed'):
            successor.require_core()
        receipt.write_bytes(raw)
        self.current(dict(self.candidate,artifact_sha256='ca'*32))
        with self.assertRaisesRegex(ValueError,'community_reference_new_core_required'):
            successor.require_core()
        self.current(self.candidate)
        self.assertIsNone(successor.require_core());self.retained()

    def test_core_proof_hash_and_private_mode_drift_are_rejected(self):
        path=self.core_folder/'approval.json';raw=path.read_bytes()
        path.write_bytes(raw+b' ')
        with self.assertRaises(ValueError):
            community.reference_package_context(self.host,self.reference_id)
        path.write_bytes(raw);path.chmod(0o644)
        with self.assertRaisesRegex(ValueError,'file_mode'):
            community.reference_package_context(self.host,self.reference_id)
        path.chmod(0o600)
        self.assertEqual(self.expected,community.reference_package_context(self.host,self.reference_id))
        self.retained()

    def test_core_commit_receipt_must_bind_the_current_transaction_and_release(self):
        successor=community.ReferencePackageUpgrade(self.host,None,self.expected)
        self.current(self.candidate)
        receipt=self.base/'transactions'/self.tx/'journal.json'
        for change in ({'transaction_id':'cb'*16},{'candidate':self.old_release}):
            with self.subTest(change=change):
                value={'transaction_id':self.tx,'candidate':self.candidate,'phase':'COMMITTED'}
                value.update(change);self.put(receipt,value)
                with self.assertRaisesRegex(ValueError,'community_reference_core_not_committed'):
                    successor.require_core()
        self.retained()


class ReferenceRestoreBoundary(unittest.TestCase):
    def fixture(self):
        import test_community_reference_upgrade as upgrade_fixture
        fixture=upgrade_fixture.ReferenceUpgrade('runTest')
        self.addCleanup(fixture.doCleanups);fixture.setUp()
        return fixture

    def test_original_ready_before_pointer_reply_loss_can_finish_restoration(self):
        # Reuse the separate actual-Node fixture without altering its tests.
        # Its database remains explicitly synthetic.
        fixture=self.fixture()
        def rolled_back():raise RuntimeError('fixture confirmed SQL rollback')
        fixture.database_state.on_migrate=rolled_back
        with self.assertRaisesRegex(RuntimeError,'confirmed SQL rollback'):
            fixture.install()
        original=fixture.service.activate
        def lose_reply(plan,channel):
            result=original(plan,channel)
            if channel == 'previous':raise InterruptedError('fixture original activation reply lost')
            return result
        with patch.object(fixture.service,'activate',side_effect=lose_reply):
            with self.assertRaisesRegex(InterruptedError,'original activation reply lost'):
                fixture.install(recover=True)
        self.assertEqual('RESTORE_PREVIOUS_INTENT',fixture.record()['phase'])
        with fixture.lease.held():
            self.assertEqual('READY',fixture.old.control('observe'))
        self.assertEqual('RESTORED',fixture.install(recover=True))
        self.assertEqual('RESTORED_VERIFIED_NOOP',fixture.install(recover=True))
        self.assertEqual(0,fixture.database_state.migrations)

    def test_original_resume_start_reply_loss_observes_starting_without_second_spawn(self):
        fixture=self.fixture()
        def rolled_back():raise RuntimeError('fixture confirmed SQL rollback')
        fixture.database_state.on_migrate=rolled_back
        with self.assertRaisesRegex(RuntimeError,'confirmed SQL rollback'):
            fixture.install()
        with fixture.lease.held():previous,supervisor=fixture.service.previous.active()
        role=previous.role()
        original_start=role.start;stopped=[]
        def started_then_lost(operation):
            original_start(operation)
            saved=role.current()[1];child=role.children[saved['process']['pid']]
            child.send_signal(signal.SIGSTOP);stopped.append(child)
            raise InterruptedError('fixture original resume start response lost')
        try:
            with patch.object(role,'start',side_effect=started_then_lost):
                with self.assertRaisesRegex(InterruptedError,'original resume start response lost'):
                    fixture.install(recover=True)
            self.assertEqual('RESTORE_PREVIOUS_INTENT',fixture.record()['phase'])
            with fixture.lease.held():
                before=deepcopy(supervisor.registered())
                self.assertEqual('STARTING',role.current()[1]['phase'])
                self.assertEqual(len(fixture.old_state['attempts'])+1,len(before['attempts']))
                self.assertEqual(len(fixture.old_state['resumptions'])+1,len(before['resumptions']))
            original_control=previous.control;observed=[]
            def observe_and_release(command,*args,**kwargs):
                status=original_control(command,*args,**kwargs)
                if command == 'observe' and status == 'STARTING' and not observed:
                    observed.append(status);stopped[0].send_signal(signal.SIGCONT)
                return status
            with patch.object(previous,'control',side_effect=observe_and_release):
                self.assertEqual('RESTORED',fixture.install(recover=True))
            self.assertEqual(['STARTING'],observed)
            with fixture.lease.held():
                after=fixture.service.active()[1].registered()
                for key in ('attempts','resumptions','budget_start'):
                    self.assertEqual(before[key],after[key],key)
            self.assertEqual(0,fixture.database_state.migrations)
            for path,raw in {**fixture.historical,**fixture.old_source}.items():
                self.assertEqual(raw,path.read_bytes(),str(path))
        finally:
            for child in stopped:
                if child.poll() is None:child.send_signal(signal.SIGCONT)

    def test_durable_stopped_desire_before_stop_call_is_drained_and_resumed_once(self):
        fixture=self.fixture()
        with fixture.lease.held():previous,supervisor=fixture.service.previous.active()
        original_control=previous.control
        def stop_reply_lost(command,*args,**kwargs):
            if command == 'stop':raise InterruptedError('fixture drain decision saved before stop call')
            return original_control(command,*args,**kwargs)
        with patch.object(previous,'control',side_effect=stop_reply_lost):
            with self.assertRaisesRegex(InterruptedError,'before stop call'):
                fixture.install()
        self.assertEqual('DRAIN_PREVIOUS_INTENT',fixture.record()['phase'])
        with fixture.lease.held():
            self.assertEqual('STOPPED',supervisor.registered()['desired'])
            self.assertEqual('READY',previous.control('observe'))
        self.assertEqual('RESTORED',fixture.install(recover=True))
        with fixture.lease.held():
            active,recovered=fixture.service.active()
            ledger=deepcopy(recovered.registered())
            self.assertEqual('READY',active.control('observe'))
            self.assertEqual(len(fixture.old_state['attempts'])+1,len(ledger['attempts']))
            self.assertEqual(len(fixture.old_state['resumptions'])+1,len(ledger['resumptions']))
        self.assertEqual('RESTORED_VERIFIED_NOOP',fixture.install(recover=True))
        with fixture.lease.held():self.assertEqual(ledger,fixture.service.active()[1].registered())
        self.assertEqual(0,fixture.database_state.migrations)

    def test_valid_watchdog_restart_before_recovery_retains_attempts_and_restores_original(self):
        fixture=self.fixture()
        def interrupted(phase):
            if phase == 'BRIDGE_READY':raise InterruptedError('fixture bridge ready reply lost')
        with self.assertRaisesRegex(InterruptedError,'bridge ready reply lost'):
            fixture.install(fault=interrupted)
        with fixture.lease.held():
            bridge,supervisor=fixture.service.active()
            initial=deepcopy(supervisor.registered())
            # A known owned exit can use the original automatic restart budget.
            fixture.service.drain(bridge.role());supervisor.tick()
            fixture.service.ready(fixture.plan,fixture.record(),'bridge')
            restarted=deepcopy(supervisor.registered())
            self.assertEqual(initial['resumptions'],restarted['resumptions'])
            self.assertEqual(2,len(restarted['attempts']))
            self.assertEqual(initial['attempts'],restarted['attempts'][:1])
        self.assertEqual('RESTORED',fixture.install(recover=True))
        with fixture.lease.held():
            # Old supervision legitimately advances after ownership is returned;
            # the retained stopped bridge uses the frozen recovery receipt.
            ledger=supervisor.load()
            self.assertEqual(restarted['attempts'],ledger['attempts'])
            self.assertEqual('STOPPED',ledger['desired'])
            self.assertEqual('READY',fixture.service.active()[0].control('observe'))
        self.assertEqual(0,fixture.database_state.migrations)


class ReferenceLibraryBinding(unittest.TestCase):
    def test_library_alias_cannot_be_retargeted_to_another_approved_local_library(self):
        import test_community_reference_upgrade as upgrade_fixture
        fixture=upgrade_fixture.DatabaseIdentity('runTest')
        self.addCleanup(fixture.doCleanups);fixture.setUp()
        library=fixture.root/'lib';library.mkdir(mode=0o700)
        targets=[]
        for name in ('libpq.so.5.16','libother.so.1'):
            target=library/name;target.write_bytes(('inert '+name).encode());target.chmod(0o600)
            targets.append(target)
        alias=library/'libpq.so.5';alias.symlink_to(targets[0].name)
        fixture.plan['postgres'].update(library_dir=str(library),library_identity={
            entry.name:{'target':entry.resolve().name,'identity':community.identity(entry.resolve())}
            for entry in library.iterdir()})
        database=community.ReferenceDatabase(fixture.host,fixture.plan)
        alias.unlink();alias.symlink_to(targets[1].name)
        with patch.object(community.subprocess,'run',return_value=SimpleNamespace(
                returncode=0,stdout=b'1\n',stderr=b'')) as invoke:
            with self.assertRaisesRegex(ValueError,'community_reference_library_'):
                database.query('SELECT 1;')
            invoke.assert_not_called()


if __name__=='__main__':unittest.main()
