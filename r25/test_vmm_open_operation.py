"""CPU-only producer -> independent receipt receiver -> actual managed consumer.

Private fixture roots, real /proc child identity, loopback status, flock and
fsync. No real model import, production path, generation or business client.
"""
import datetime as dt
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import vmm_lifecycle as life
import vmm_service as service
import test_vmm_lifecycle as fixture


def startup(engine, role):
    role.recover(); state = role.state(); now = time.monotonic(); wall = dt.datetime.now(dt.timezone.utc)
    installed = {}
    for name in ('vmm_lifecycle.py', 'vmm_service.py', 'vmm_controller.py'):
        target = engine.root / 'operations/r25-publisher' / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes((Path(__file__).parent/name).read_bytes()); target.chmod(0o600)
        installed[str(target.relative_to(engine.root))] = life.sha(target)
    tool = Path(__file__).read_bytes()
    plan = {'schema': life.OPEN_STARTUP_SCHEMA, 'installed_root': str(engine.root),
            'role_sha256': engine.v['R25_VMM_ROLE_SHA256'], 'limits': life.OPEN_STARTUP_LIMITS,
            'installed_code_hashes': installed, 'tool_hashes': {'synthetic_receiver.py': life.hashlib.sha256(tool).hexdigest()},
            'prepared_at': wall.isoformat(), 'synthetic_only': True}
    ident = life.object_sha(plan); folder = engine.root / 'runtime/vmm-website-windows' / ident
    folder.mkdir(mode=0o700, parents=True); (folder/'tools').mkdir(mode=0o700)
    (folder/'tools/synthetic_receiver.py').write_bytes(tool); (folder/'tools/synthetic_receiver.py').chmod(0o600)
    process = state['process']; health = {'contract':'artlive-vmm-managed-v1','process_alive':True,'model_ready':True,
        'mode':'CLOSED','dispatch_permitted':False,'in_flight':False,'uncertain':False,'attempts':len(role.gate.read()['requests'])}
    first={'process':process,'monotonic':now,'deadline_monotonic':now+600,'at':wall.isoformat(),
           'deadline_at':(wall+dt.timedelta(seconds=600)).isoformat(),'health':health,'gate_sha256':life.sha(role.gate.path)}
    records={'STARTUP_PLAN.json':plan,'STARTUP_APPROVAL.json':{'schema':'ARTLIVE_USER_CLOSED_START_APPROVAL_V1',
        'plan_sha256':ident,'scope':'ONE_INITIALIZATION_CLOSED_ZERO_GENERATION','authorized':True},
        'EXECUTION.json':{'startup_sha256':ident,'start_calls':1,'enable_calls':1,'tool_generation_requests':0,
         'completed':False,'boot_id':process['boot_id'],'intent_monotonic':state['started_monotonic']-.01,
         'startup_deadline':state['started_monotonic']-.01+900}, 'FIRST_READY.json':first,
        'READY_GATE.raw.json':role.gate.read(), 'READY_EVIDENCE_COMPLETE.json':{'first_ready':first,
         'second_ready':{'process':process,'health':health},'native_ready':{'status':role.status()}}}
    for name,value in records.items():life.write(folder/name,value)
    return ident,folder


def approve(engine, result):
    """Explicit synthetic approval, never supplied by the production producer."""
    payload={'schema':'ARTLIVE_HUMAN_OPEN_OPERATION_APPROVAL_V1','operation_sha256':result['operation_sha256'],
             'command_sha256':result['command_sha256'],'scope':'ONE_NORMAL_WEBSITE_OPEN','authorized':True}
    ack=life.receive_open_approval(engine,result['operation_sha256'],payload)
    assert ack['acknowledged'] and ack['opened'] is False
    config=engine.root/'runtime/config';config.mkdir(mode=0o700,exist_ok=True,parents=True)
    life.write(config/'vmm-gate-command.json',life.read(Path(result['directory'])/'COMMAND.json'))
    engine.vmm_gate_sha256=result['command_sha256']
    return payload


def prepare_approved(engine, role):
    ident,_=startup(engine,role)
    with engine.lease():result=life.prepare_open_operation(engine,role,ident)
    approve(engine,result);return result


class OpenOperation(unittest.TestCase):
    setUp=fixture.LifecycleTest.setUp
    tearDown=fixture.LifecycleTest.tearDown
    start=fixture.LifecycleTest.start
    ready=fixture.LifecycleTest.ready

    def prepare(self):
        self.start();self.engine=fixture.FixtureEngine(self.root);self.startup,self.folder=startup(self.engine,self.role)
        return self.next_operation()

    def next_operation(self, approved=True):
        with self.engine.lease():result=life.prepare_open_operation(self.engine,self.role,self.startup)
        if approved:approve(self.engine,result)
        return result

    def consume(self):
        with patch.object(life,'managed_role',return_value=self.role):return life.manage(self.engine,'open')

    def test_real_reopening_without_requests_uses_consumed_operation_sequence(self):
        first=self.prepare();gate_before=self.store.path.read_bytes();inode=self.engine.lock.stat().st_ino
        self.assertEqual('OPEN',self.consume()['mode']);self.store.configure('CLOSED')
        self.assertEqual(gate_before,self.store.path.read_bytes())
        second=self.next_operation();self.assertNotEqual(first['command_sha256'],second['command_sha256'])
        plan=life.read(Path(second['directory'])/'PLAN.json')
        self.assertEqual(2,plan['sequence']);self.assertEqual(first['operation_sha256'],plan['previous_operation_sha256'])
        self.assertEqual('OPEN',self.consume()['mode']);self.assertEqual(0,self.store.snapshot()['attempts'])
        self.assertEqual([first['operation_sha256'],second['operation_sha256']],self.role.state()['open_operations'])
        self.assertEqual(inode,self.engine.lock.stat().st_ino)

    def test_consumed_v1_and_v2_commands_cannot_replay(self):
        self.prepare();legacy=self.engine.command(self.store,'open');value=self.role.state();value['gate_commands']=[legacy];life.write(self.role.record,value)
        with self.assertRaisesRegex(life.Refused,'command_consumed'):self.consume()
        fresh=self.next_operation();self.consume();self.store.configure('CLOSED')
        with self.assertRaisesRegex(life.Refused,'command_consumed'):self.consume()
        self.assertIn(legacy,self.role.state()['gate_commands']);self.assertIn(fresh['operation_sha256'],self.role.state()['open_operations'])

    def test_unconsumed_v1_and_salt_are_rejected(self):
        self.prepare();self.engine.command(self.store,'open',random_salt='not_authority')
        with self.assertRaisesRegex(life.Refused,'operation_required'):self.consume()
        self.assertNotIn('open_operations',self.role.state())

    def test_consumed_operation_cannot_replay_with_different_serialization(self):
        self.prepare();self.consume();self.store.configure('CLOSED')
        path=self.root/'runtime/config/vmm-gate-command.json'
        path.write_bytes(path.read_bytes()+b'\n');self.engine.vmm_gate_sha256=life.sha(path)
        with self.assertRaisesRegex(life.Refused,'operation_consumed'):self.consume()
        self.assertEqual(1,len(self.role.state()['open_operations']))

    def test_stop_disable_and_new_process_preserve_open_consumption(self):
        first=self.prepare();self.consume();self.store.configure('CLOSED')
        self.role.stop(disable=True);self.role.enable();self.role.start(explicit=True);self.ready()
        self.startup,self.folder=startup(self.engine,self.role)
        second=self.next_operation();self.assertNotEqual(first['operation_sha256'],second['operation_sha256'])
        self.assertEqual('OPEN',self.consume()['mode'])
        self.assertEqual([first['operation_sha256'],second['operation_sha256']],self.role.state()['open_operations'])

    def test_missing_invalid_or_music_approval_cannot_open(self):
        self.prepare();self.consume();self.store.configure('CLOSED');second=self.next_operation(approved=False)
        path=Path(second['directory']);self.engine.vmm_gate_sha256=second['command_sha256']
        life.write(self.root/'runtime/config/vmm-gate-command.json',life.read(path/'COMMAND.json'))
        with self.assertRaisesRegex(life.Refused,'invalid_record'):self.consume()
        bad={'schema':'ARTLIVE_USER_MUSIC_ACCEPTANCE_APPROVAL_V1','authorized':True}
        with self.assertRaisesRegex(life.Refused,'separate_human'):life.receive_open_approval(self.engine,second['operation_sha256'],bad)
        life.write(path/'APPROVAL.json',bad)
        with self.assertRaisesRegex(life.Refused,'separate_human'):self.consume()
        self.assertEqual(1,len(self.role.state()['open_operations']))

    def test_foreign_operation_command_or_modified_plan_rejected(self):
        result=self.prepare();path=self.root/'runtime/config/vmm-gate-command.json';command=life.read(path)
        command['operation_sha256']='d'*64;life.write(path,command);self.engine.vmm_gate_sha256=life.sha(path)
        with self.assertRaisesRegex(life.Refused,'invalid_record'):self.consume()
        approve(self.engine,result);p=Path(result['directory'])/'PLAN.json';value=life.read(p);value['sequence']=False;life.write(p,value)
        with self.assertRaisesRegex(life.Refused,'plan_identity'):self.consume()

    def test_stale_consumption_and_gate_preimages_rejected(self):
        self.prepare();state=self.role.state();state['gate_commands']=['e'*64];life.write(self.role.record,state)
        with self.assertRaisesRegex(life.Refused,'consumption_preimage'):self.consume()
        state.pop('gate_commands');life.write(self.role.record,state)
        self.store.configure('OPEN');self.store.reserve('synthetic-existing',{});self.store.finish('synthetic-existing','SUCCEEDED');self.store.configure('CLOSED')
        with self.assertRaisesRegex(life.Refused,'command_binding'):self.consume()

    def test_changed_process_tool_or_ready_evidence_rejected(self):
        result=self.prepare();p=self.folder/'tools/synthetic_receiver.py';raw=p.read_bytes();p.write_bytes(raw+b'\n')
        with self.assertRaisesRegex(life.Refused,'tool_hash'):self.consume()
        p.write_bytes(raw);p=self.folder/'FIRST_READY.json';value=life.read(p);value['process']['ticks']='0';life.write(p,value)
        with self.assertRaisesRegex(life.Refused,'process_identity'):self.consume()
        self.assertNotIn('open_operations',self.role.state())

    def test_original_expiry_never_resets_on_new_operation(self):
        self.prepare();first=life.read(self.folder/'FIRST_READY.json')
        with patch.object(life.time,'monotonic',return_value=first['deadline_monotonic']):
            with self.assertRaisesRegex(life.Refused,'observation_expired'):self.consume()
            with self.assertRaisesRegex(life.Refused,'observation_expired'):self.next_operation()
        self.assertNotIn('open_operations',self.role.state())

    def test_failure_after_durable_consumption_never_refunds(self):
        result=self.prepare()
        with patch.object(self.store,'configure',side_effect=InterruptedError('synthetic interruption')):
            with self.assertRaises(InterruptedError):self.consume()
        self.assertEqual([result['operation_sha256']],self.role.state()['open_operations']);self.assertEqual('CLOSED',self.store.snapshot()['mode'])
        with self.assertRaisesRegex(life.Refused,'command_consumed'):self.consume()
        next_result=self.next_operation();self.assertNotEqual(result['operation_sha256'],next_result['operation_sha256'])
        self.assertEqual('OPEN',self.consume()['mode'])

    def test_durable_write_crossing_deadline_stays_consumed_closed(self):
        result=self.prepare();first=life.read(self.folder/'FIRST_READY.json');persisted=[False]
        original_write,original_clock=life.write,time.monotonic
        def write(path,value):
            original_write(path,value)
            if Path(path)==self.role.record:persisted[0]=True
        def clock():return first['deadline_monotonic'] if persisted[0] else original_clock()
        with patch.object(life,'write',side_effect=write),patch.object(life.time,'monotonic',side_effect=clock):
            with self.assertRaisesRegex(life.Refused,'observation_expired'):self.consume()
        self.assertIn(result['operation_sha256'],self.role.state()['open_operations'])
        self.assertEqual('CLOSED',self.store.snapshot()['mode'])
        with self.assertRaisesRegex(life.Refused,'command_consumed'):self.consume()

    def test_unknown_preserved_and_requires_existing_reconciliation(self):
        self.prepare();self.consume();self.store.reserve('synthetic-uncertain',{});self.store.finish('synthetic-uncertain','UNKNOWN');self.store.configure('CLOSED')
        raw=self.store.path.read_bytes();state=self.role.record.read_bytes()
        with self.assertRaisesRegex(life.Refused,'current_closed_ready'):self.next_operation()
        self.assertEqual(raw,self.store.path.read_bytes());self.assertEqual(state,self.role.record.read_bytes())
        self.assertEqual('UNKNOWN',self.store.read()['requests']['synthetic-uncertain']['state'])

    def test_locked_store_enforces_approval_expiry_without_open_ttl(self):
        deadline={'monotonic':time.monotonic()-1,'wall':time.time()+600};before=self.store.path.read_bytes()
        with self.assertRaisesRegex(service.Refused,'OPEN_APPROVAL_EXPIRED'):
            self.store.configure('OPEN',expected_sha256=life.sha(self.store.path),open_deadline=deadline)
        self.assertEqual(before,self.store.path.read_bytes())
        self.store.configure('OPEN',open_deadline={'monotonic':time.monotonic()+10,'wall':time.time()+10})
        self.assertNotIn('expiry',self.store.read());self.assertEqual('OPEN',self.store.snapshot()['mode'])

    def test_real_gate_lock_delay_rechecks_open_deadline(self):
        holder=subprocess.Popen([sys.executable,'-B','-c',
            "import fcntl,sys,time; f=open(sys.argv[1],'rb'); fcntl.flock(f,fcntl.LOCK_EX); "
            "print('LOCK_HELD',flush=True); time.sleep(.2)",str(self.store.lock_path)],stdout=subprocess.PIPE,text=True)
        self.processes.append(holder)
        try:
            self.assertEqual('LOCK_HELD',holder.stdout.readline().strip());raw=self.store.path.read_bytes();start=time.monotonic()
            with self.assertRaisesRegex(service.Refused,'OPEN_APPROVAL_EXPIRED'):
                self.store.configure('OPEN',expected_sha256=life.sha(self.store.path),
                    open_deadline={'monotonic':start+.05,'wall':time.time()+600})
            self.assertGreater(time.monotonic()-start,.1);self.assertEqual(raw,self.store.path.read_bytes())
        finally:holder.wait(2);holder.stdout.close()

    def test_producer_never_writes_human_approval_and_material_is_immutable(self):
        self.prepare();self.consume();self.store.configure('CLOSED');result=self.next_operation(False)
        path=Path(result['directory']);self.assertFalse((path/'APPROVAL.json').exists())
        with self.assertRaisesRegex(life.Refused,'immutable_conflict'):life.immutable_open_record(path/'PLAN.json',{'invalid':True})
        self.assertEqual(result,self.next_operation(False))
