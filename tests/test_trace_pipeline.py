"""Actual workers, independent inputs and checkpoint-aligned trace publication."""
import unittest,tempfile,time,uuid
from pathlib import Path
from apps.openpine.bootstrap import local_manager
from apps.openpine.launches import Launches
from apps.openpine.traces import Traces
from packages.contracts.storage import ResourcePolicy
from packages.execution.host_memory import HostAdmission
from packages.marketdata.providers import input_snapshot
from packages.contracts.wire import read_json,canonical
from packages.compiler.semantic.specification import ROOT
import test_bound_passport as bound_helpers
from test_realtime import configuration,text,tape,PRIMARY
from apps.openpine.replay import Replays

class TracePipelineTests(unittest.TestCase):
    prepared=bound_helpers.BoundPassportTests.prepared
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.host=HostAdmission(probe=lambda:{'total_bytes':16*1024**3,'available_bytes':8*1024**3,'source':'test.fixed'})
        self.policy=ResourcePolicy(workers=1,memory_mb_per_worker=256,total_memory_mb=256)
        self.directory=Path(self.temp.name)/'jobs';self.open(False)
        self.addCleanup(lambda:self.traces.close());self.addCleanup(lambda:self.manager.close())
        self.primary=self.manager.market.save(input_snapshot(read_json(ROOT/'examples/pipeline/bars.json')))
    def open(self,start):
        self.manager=local_manager(self.directory,autostart=start,host_admission=self.host,policy=self.policy)
        self.launches=Launches(self.manager,None);self.traces=Traces(self.manager,start=False)
    def create(self,key,**opts):
        return self.traces.create({'request_id':uuid.uuid4().hex,'launch_id':key,
            'binding_sha256':self.launches.inspection(key)['passport']['binding']['sha256'],'options':{'end_bar':4,**opts}})
    def wait(self,key,target='succeeded'):
        until=time.monotonic()+90
        while time.monotonic()<until:
            state=self.manager.get(key)
            if state['status']==target or state['status'] in ('failed','timed_out','cancelled'):break
            time.sleep(.03)
        self.assertEqual(state['status'],target,state);return state
    def test_keyed_creation_preserves_original_plan_and_preparation(self):
        key=self.prepared();before=self.launches.repository.get(key);value=self.create(key);trace=value['trace_id']
        payload={'request_id':trace,'launch_id':key,'binding_sha256':value['configuration']['binding']['sha256'],'options':{'end_bar':4}}
        self.assertEqual(self.traces.create(payload)['trace_id'],trace)
        self.assertEqual(self.launches.repository.get(key),before)
        self.assertEqual(self.manager.plan(trace).payload(),self.manager.objects.get(before['state']['prepared_ref'])['plan'])
        payload['options']['end_bar']=3
        with self.assertRaises(ValueError):self.traces.create(payload)
    def test_real_worker_resume_keeps_trace_prefix_and_computation(self):
        key=self.prepared();trace=self.create(key)['trace_id']
        state=self.manager.repository.get(trace);state['pause_after_bars']=2;self.manager.repository.save(state)
        self.manager.close();self.open(True);self.wait(trace,'paused')
        prefix=self.traces.page(trace,0,500);self.assertGreater(prefix['total'],0)
        self.manager.resume(trace);self.wait(trace)
        final=self.traces.page(trace,0,500)
        self.assertEqual(final['events'][:len(prefix['events'])],prefix['events'])
        self.assertEqual(len({e['attempt_id'] for e in final['events']}),2)
        self.assertEqual([e['sequence'] for e in final['events']],list(range(len(final['events']))))
        self.launches.start(key);self.wait(key)
        self.assertEqual(canonical(self.manager.result(trace)['result']),canonical(self.manager.result(key)['result']))
        self.assertEqual(self.manager.services.lifecycle.inspect(grace_seconds=0)['reclaimable_bytes']>=0,True)
    def test_replay_clone_executes_without_advancing_original(self):
        replays=Replays(self.manager);source=replays.create(configuration(self.manager,text()))['replay_id']
        replays.append(source,[e.payload() for e in tape()]);before=replays.repository.row(source)
        binding=replays.passport(source)['passport']['replay_binding']['sha256']
        value=self.traces.create({'request_id':uuid.uuid4().hex,'replay_id':source,'binding_sha256':binding,'options':{'end_bar':4}})
        key=value['trace_id'];self.traces.launch(key,background=False);status=self.traces.status(key)
        self.assertEqual(status['job']['status'],'completed',status)
        self.assertEqual(replays.repository.row(source),before)
        self.assertEqual(status['replay']['sequence'],11);self.assertGreater(self.traces.page(key)['total'],0)
        self.assertFalse(status['whole_program_execution_verified'])

    def test_archived_prefix_clone_shares_verified_blocks_and_fences_late_append(self):
        from packages.contracts.realtime import MarketEvent
        replays=Replays(self.manager);repo=replays.repository
        source=replays.create(configuration(self.manager,text()))['replay_id']
        events=[MarketEvent(i,PRIMARY.context_id,'close',i*60000,(i+1)*60000,0,1.,1.,1.,1.,1.).payload() for i in range(10000)]
        replays.append(source,events);row=repo.row(source);clone=repo.clone(source,(row[0],row[3],row[4]))
        a=self.manager.db.execute('SELECT object_id FROM replay_parts WHERE replay_id=?',(source,))
        from packages.storage.replay_tape import tape as frozen_tape
        b=[(p['object_id'],) for p in frozen_tape(repo,clone)['parts']]
        self.assertTrue(a);self.assertEqual(a,b)
        self.assertEqual(self.manager.db.execute('SELECT count(*) FROM replay_events WHERE replay_id=?',(clone,)),[(0,)])
        self.assertEqual([e.payload() for e in repo.events(clone,0,3)],events[:3])
        extra=MarketEvent(10000,PRIMARY.context_id,'close',600000000,600060000,0,1.,1.,1.,1.,1.).payload()
        replays.append(source,[extra])
        with self.assertRaises(ValueError):repo.clone(source,(row[0],row[3],row[4]))
        self.assertEqual(repo.row(clone)[3],10000)

if __name__=='__main__':unittest.main()
