"""Observations must not change values, state, branches or broker decisions."""
import tempfile,unittest,copy
from pathlib import Path
from packages.contracts import HISTORICAL_PROFILE_ID,HistoricalData,HistoricalBar
from packages.contracts.trace import options
from packages.contracts.wire import canonical
from packages.execution.session import Session
from packages.execution.observation import Observer
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.execution.realtime import ReplayEngine
from packages.storage.objects import ObjectStore
from packages.storage.traces import Writer,rows
from test_realtime import Sink,PRIMARY,text,tape

class TraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.objects=ObjectStore(Path(temp.name)/'objects')
    def observer(self,**selected):
        config={'trace_id':'a'*32,'options':options({'start_bar':0,'end_bar':4,**selected})}
        writer=Writer(self.objects,config);return Observer(writer,config['options']),writer
    def events(self,writer):
        ref=writer.save(status='test');return [r for p in self.objects.get(ref)['parts'] for r in self.objects.get(p['object_id'])]
    def test_closed_bounded_policy(self):
        for bad in ({'end_bar':1001},{'max_bytes':True},{'lines':[0]},{'sites':['x','x']},{'unknown':1}):
            with self.assertRaises(ValueError):options(bad)
    def test_all_versions_history_values_and_native_state_identical(self):
        data=HistoricalData('primary','TEST:TRACE','1',tuple(HistoricalBar(i*60000,float(10+i),float(10+i),float(10+i),float(10+i),1.) for i in range(4)))
        for v in range(1,7):
            declaration='study' if v<5 else 'indicator'
            source=f'//@version={v}\n{declaration}("trace")\nx=close+1\nif x>11\n    x:=x*2\nplot(x)'
            program=self.session.compile(source,'selected.pine');out=[]
            for traced in (False,True):
                observer,writer=self.observer(start_bar=1,end_bar=3)
                self.session.observer=observer if traced else None
                runtime=self.session.validate_program(program);sink=Sink();driver=HistoricalDriver(runtime.prepare(program,output_sink=sink),MemorySource(data))
                driver.run();out.append((sink.rows,driver.checkpoint()))
                if traced:
                    events=self.events(writer);self.assertTrue(any(e['kind']=='store' and e['span']['line']==3 for e in events))
                    self.assertTrue(any(e['kind']=='branch' for e in events));self.assertEqual({e['primary_bar'] for e in events},{1,2})
            self.assertEqual(canonical(out[0]),canonical(out[1]));self.session.observer=None
    def test_all_versions_replay_preview_and_restoration_identical(self):
        for v in range(1,7):
            program=self.session.compile(text(v));results=[]
            for traced in (False,True):
                observer,writer=self.observer();self.session.observer=observer if traced else None;sink=Sink()
                engine=ReplayEngine(self.session,program,(PRIMARY,),'primary',sink)
                events=list(tape())
                for event in events[:6]:engine.apply(event)
                checkpoint=copy.deepcopy(engine.checkpoint())
                ref=writer.save(status='checkpoint') if traced else None
                tail=Sink()
                if traced:
                    writer=Writer(self.objects,writer.configuration,previous=ref);self.session.observer=Observer(writer,writer.policy)
                resumed=ReplayEngine(self.session,program,(PRIMARY,),'primary',tail);resumed.restore(checkpoint)
                for event in events[6:]:resumed.apply(event)
                results.append((sink.rows+tail.rows,resumed.checkpoint(),resumed.status()))
                if traced:
                    captured=self.events(writer);self.assertTrue(any(e['branch_id']>0 for e in captured))
                    self.assertEqual([e['sequence'] for e in captured],list(range(len(captured))))
            self.assertEqual(canonical(results[0]),canonical(results[1]));self.session.observer=None
    def test_capture_limits_and_chunk_integrity(self):
        observer,writer=self.observer(max_events=2)
        for i in range(4):writer.emit({'kind':'owned','value':i})
        ref=writer.save(status='test');page=rows(self.objects,ref,1,1)
        self.assertEqual(page['events'][0]['sequence'],1);self.assertTrue(page['manifest']['truncated']);self.assertEqual(page['manifest']['reason'],'event_limit')
        part=self.objects.get(ref)['parts'][0]['object_id'];self.objects.path(part).write_bytes(b'bad')
        with self.assertRaises(ValueError):rows(self.objects,ref)
    def test_off_path_uses_original_evaluator(self):
        self.session.observer=None;program=self.session.compile(text());runtime=self.session.validate_program(program);evaluation=runtime.prepare(program)
        self.assertEqual(evaluation.evaluate.__func__,evaluation._evaluate.__func__)

    def test_after_selected_range_restores_direct_evaluator_and_reuses_manifest(self):
        observer,writer=self.observer(end_bar=1);self.session.observer=observer;program=self.session.compile(text())
        data=HistoricalData('primary','TEST:TRACE','1',tuple(HistoricalBar(i*60000,float(i+10)) for i in range(4)))
        driver=HistoricalDriver(self.session.validate_program(program).prepare(program,output_sink=Sink()),MemorySource(data));driver.run()
        self.assertIsNone(driver.evaluation.observer)
        self.assertEqual(driver.evaluation.evaluate.__func__,driver.evaluation._evaluate.__func__)
        first=writer.save(status='checkpoint',completed=4);self.assertEqual(first,writer.save(status='checkpoint',completed=1000000))
        self.session.observer=None

    def test_trace_io_failure_does_not_change_pine_values(self):
        from unittest.mock import patch
        from dataclasses import asdict
        program=self.session.compile(text());data=HistoricalData('primary','TEST:TRACE','1',tuple(HistoricalBar(i*60000,float(i+10)) for i in range(4)))
        self.session.observer=None;normal=self.session.execute(program,data=data)
        observer,writer=self.observer();self.session.observer=observer
        with patch.object(self.objects,'put',side_effect=OSError('owned trace write fault')):
            traced=self.session.execute(program,data=data);writer.flush()
        self.assertEqual(canonical(asdict(normal)),canonical(asdict(traced)));self.assertTrue(writer.stopped);self.assertEqual(writer.reason,'storage_error')
        self.session.observer=None

if __name__=='__main__':unittest.main()
