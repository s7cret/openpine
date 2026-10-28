"""Cross-process leases, common worker fences, fair demand and streamed restore."""
import copy,gc,subprocess,sys,tempfile,time,unittest,weakref
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from packages.contracts.checkpoint_stream import RecordMapping,RecordSequence
from packages.contracts.disk import DiskBusy
from packages.contracts.wire import atomic_json,read_json
from packages.execution.supervisor import Supervisor,WorkerFault
from packages.execution.scheduling.fair import FairScheduler
from packages.storage.disk import DiskAdmission,MIB
from packages.storage.objects import ObjectStore
from packages.storage import checkpoint_records as records

class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)
        self.now=100.;self.process=SimpleNamespace(pid=123,returncode=None,poll=lambda:None,kill=lambda:None,wait=lambda **kw:None)
    def supervisor(self):return Supervisor(self.process,self.path,timeout=30,heartbeat_timeout=5,memory_mb=128,
        attempt_id='current',clock=lambda:self.now,probe=lambda pid:64*MIB)
    def test_timeout_cancel_memory_and_heartbeat_use_one_fence(self):
        for code,change in [('JOB_TIMEOUT',lambda s:setattr(self,'now',131.)),('CANCELLED',lambda s:None),
                ('WORKER_MEMORY',lambda s:setattr(s,'probe',lambda pid:129*MIB)),('WORKER_HEARTBEAT',lambda s:setattr(self,'now',106.))]:
            self.now=100.;s=self.supervisor();change(s)
            with self.assertRaises(WorkerFault) as caught:s.poll(cancelled=code=='CANCELLED')
            self.assertEqual(caught.exception.code,code)
    def test_stale_future_nan_and_wrong_attempt_cannot_extend_liveness(self):
        for tick,attempt in [(99.,'other'),(200.,'current'),(90.,'current')]:
            self.now=100.;s=self.supervisor();self.now=106.;atomic_json(self.path/'heartbeat.json',dict(tick=tick,attempt_id=attempt))
            with self.assertRaises(WorkerFault):s.poll()
    def test_close_kills_and_reaps_once_on_exception(self):
        from unittest.mock import Mock
        p=Mock();p.poll.return_value=None
        with self.assertRaisesRegex(ValueError,'boom'):
            with Supervisor(p,self.path,timeout=1,heartbeat_timeout=1,memory_mb=128):raise ValueError('boom')
        p.kill.assert_called_once();p.wait.assert_called_once()

class DiskTests(unittest.TestCase):
    def test_parallel_instances_reserve_pending_and_reclaim_dead_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            probe=lambda path:SimpleNamespace(free=1000*MIB)
            a=DiskAdmission(directory,probe=probe);b=DiskAdmission(directory,probe=probe)
            token=a.reserve(600*MIB)
            with self.assertRaises(DiskBusy):b.reserve(400*MIB)
            a.materialized(token,100*MIB);other=b.reserve(400*MIB);b.release(other);a.release(token)
            # Simulate the kernel releasing a dead owner's file handle.
            token=a.reserve(500*MIB);a.held.pop(token).close()
            self.assertEqual(b.snapshot()['pending_reservation_bytes'],0)
    def test_disk_refusal_does_not_start_attempt_and_recovers(self):
        from apps.openpine.bootstrap import local_manager
        from test_jobs import request,wait
        with tempfile.TemporaryDirectory() as directory:
            free=[4096*MIB];disk=DiskAdmission(directory,probe=lambda path:SimpleNamespace(free=free[0]))
            with local_manager(directory,disk_admission=disk,max_workers=1) as manager:
                with manager.lock:job=manager.submit(request())['job_id'];free[0]=0
                deadline=time.monotonic()+5
                while time.monotonic()<deadline and manager.get(job)['phase']!='waiting_resources':time.sleep(.02)
                state=manager.get(job);self.assertEqual((state['attempt_count'],state['waiting']['reason']),(0,'disk'))
                free[0]=4096*MIB;self.assertEqual(wait(manager,job)['status'],'succeeded');self.assertEqual(disk.held,{})
    def test_backup_refusal_leaves_no_published_destination(self):
        from apps.openpine.bootstrap import local_services
        from packages.storage.backup import backup
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with local_services(root/'store') as services,patch('packages.storage.disk.DiskAdmission.reserve',side_effect=DiskBusy({'reason':'disk'})):
                with self.assertRaises(DiskBusy):backup(services.db,root/'store',root/'backup')
            self.assertFalse((root/'backup').exists());self.assertFalse(list(root.glob('.backup-*')))

class FairTests(unittest.TestCase):
    def test_all_lanes_progress_and_old_work_beats_continuous_live_arrivals(self):
        now=[0.];s=FairScheduler(clock=lambda:now[0]);s.offer('old','history',persistent=True)
        seen=[]
        for i in range(20):
            s.offer('live'+str(i),'live');s.offer('research'+str(i),'research',persistent=True)
            key=s.choose();seen.append(s.pending[key][0]);s.started(key);now[0]+=.5
        self.assertTrue(set(seen)=={'live','history','research'});self.assertNotIn('old',s.pending)
    def test_abandoned_retries_expire_and_queue_is_bounded(self):
        now=[0.];s=FairScheduler(clock=lambda:now[0],capacity=2);s.offer('a','live');s.offer('b','research',persistent=True)
        self.assertFalse(s.offer('c','history'));now[0]=3.;self.assertEqual(s.choose(),'b');self.assertTrue(s.offer('c','history'))
    def test_last_free_slot_protects_live_demand_without_losing_old_tickets(self):
        now=[0.];s=FairScheduler(clock=lambda:now[0]);s.offer('h','history',persistent=True)
        now[0]=10.;s.offer('l','live');self.assertEqual(s.choose(live_reserved=True),'l')
        s.started('l');self.assertEqual(s.choose(),'h')

class StreamTests(unittest.TestCase):
    def test_records_decode_one_at_a_time_and_traverse_exact_closure(self):
        with tempfile.TemporaryDirectory() as directory:
            objects=ObjectStore(directory);value={'history':RecordMapping(('a','b'),lambda k:{'items':list(range(20000))}),
                'states':RecordSequence(3,lambda i:{'kernel':i,'pending':[float(i)]})}
            sha=records.put(objects,value);refs=set();records.references(objects,sha,refs.add)
            self.assertGreater(len(refs),3)
            with patch.object(objects,'get',wraps=objects.get) as read:
                decoded=records.get(objects,sha);self.assertEqual(read.call_count,1)
                self.assertIsInstance(decoded['history'],RecordMapping);self.assertEqual(decoded['states'][2]['kernel'],2)
            self.assertEqual(records.get(objects,sha,streaming=False)['history']['b']['items'],list(range(20000)))
    def test_save_uses_restore_budget_before_publishing_root(self):
        with tempfile.TemporaryDirectory() as directory:
            objects=ObjectStore(directory);value={'states':RecordSequence(1,lambda i:{'items':list(range(20000))})}
            with self.assertRaisesRegex(ValueError,'memory budget'):records.put(objects,value,limit_bytes=MIB)
            for path in Path(directory).glob('[0-9a-f][0-9a-f]/*'):
                self.assertNotEqual(objects.get(path.name).get('schema_version') if isinstance(objects.get(path.name),dict) else None,records.SCHEMA)
    def test_late_record_corruption_does_not_commit_runtime(self):
        from packages.execution.session import Session
        from packages.contracts import HISTORICAL_PROFILE_ID,BarFrame
        session=Session(profile_id=HISTORICAL_PROFILE_ID);program=session.compile('//@version=6\nindicator("s")\nplot(ta.sma(close,3))\nplot(ta.ema(close,4))')
        runtime=session.validate_program(program);original=runtime.prepare(program)
        for i in range(10):original.step(BarFrame('p',i,i,float(i)))
        with tempfile.TemporaryDirectory() as directory:
            objects=ObjectStore(directory);sha=records.put(objects,original.checkpoint(streaming=True));state=records.get(objects,sha)
            # Corrupt the last independently loaded kernel, after earlier state
            # has already been validated into private restore allocations.
            header=objects.get(sha);nodes=dict(header['tree'][1]);last=nodes['states'][1][-1]
            objects.path(last).write_bytes(b'corrupt')
            fresh=runtime.prepare(program)
            with self.assertRaisesRegex(ValueError,'hash mismatch'):fresh.restore(state)
            self.assertEqual(fresh.count,0);self.assertEqual(fresh.states,{})
    def test_legacy_tree_stays_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            objects=ObjectStore(directory);sha=objects.put({'legacy':[1,2,3]})
            self.assertEqual(records.get(objects,sha),{'legacy':[1,2,3]})

if __name__=='__main__':unittest.main()
