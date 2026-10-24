"""Durable environment identity, recorded bar phases and requested contexts."""

import copy
from dataclasses import asdict,replace
from types import SimpleNamespace
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.contracts.environment import Environment
from packages.contracts.contexts import MarketContext,ContextBinding
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical,loads
from packages.execution.driver import MemorySource,HistoricalDriver
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.marketdata import data_identity
from test_environment import data,source,INSTRUMENT
from test_realtime import Sink,tape,PRIMARY


class EnvironmentStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)

    def driver(self,program,dataset):return HistoricalDriver(self.session.validate_program(program).prepare(program),MemorySource(dataset))

    def test_every_historical_resume_boundary_has_only_one_last_bar(self):
        for v in range(2,7):
            body='plot(time)\nplot(time[1])\nplot(barstate.islast ? 1 : 0)'
            p=self.session.compile(source(v,body));dataset=data();expected=self.session.execute(p,data=dataset)
            for cut in (1,2,3):
                first=self.driver(p,dataset);saved=[];first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                self.assertEqual(saved[0]['schema_version'],'openpine.runtime-state.6')
                self.assertEqual(saved[0]['environment_sha256'],first.environment.sha256)
                second=self.driver(p,dataset);second.restore(saved[0]);second.run()
                self.assertEqual(second.finish(data_identity(dataset)),expected)

    def test_environment_and_checksum_tampering_rejected_before_state_assignment(self):
        p=self.session.compile(source(6,'plot(time)'));first=self.driver(p,data());saved=[]
        first.run(pause_after=1,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
        for env in (Environment('environment','TEST:OTHER','1',3),Environment('environment','TEST:ENV','5',3),Environment('environment','TEST:ENV','1',4),Environment('environment','TEST:ENV','1',3,INSTRUMENT)):
            second=self.driver(p,data());second.evaluation.environment=env
            with self.assertRaises(ValueError):second.restore(saved[0])
            self.assertEqual(second.count,0)
        second=self.driver(p,data());bad=copy.deepcopy(saved[0]);bad['environment_sha256']='0'*64
        with self.assertRaises(ValueError):second.restore(bad)
        self.assertEqual(second.count,0)

    def test_observed_phases_preview_rollback_and_resume_at_every_event(self):
        body='var n=0\nn:=n+1\nplot(n)\nplot(time)\nplot(time_close)\nplot(barstate.ishistory ? 1 : 0)\nplot(barstate.isrealtime ? 1 : 0)\nplot(barstate.isnew ? 1 : 0)\nplot(barstate.isconfirmed ? 1 : 0)\nplot(barstate.islast ? 1 : 0)'
        p=self.session.compile(source(6,body));events=list(tape(3))
        def fresh():
            sink=Sink();return ReplayEngine(self.session,p,(PRIMARY,),PRIMARY.context_id,sink),sink
        engine,sink=fresh()
        for event in events:
            before=canonical(engine.evaluation.checkpoint()) if engine.evaluation.count else None
            engine.apply(event)
            if event.kind!='close':
                values=[o['value'] for o in engine.preview['outputs']]
                self.assertEqual(values,[event.sequence//3+1,event.time,event.time+60000,0,1,int(event.kind=='open'),0,1])
                if before:self.assertEqual(canonical(engine.evaluation.checkpoint()),before)
        for i in range(3):
            self.assertEqual([r[3] for r in sink.rows if r[0]==i],[i+1,i*60000,(i+1)*60000,0,1,0,1,1])
        for cut in range(1,len(events)+1):
            first,prefix=fresh()
            for e in events[:cut]:first.apply(e)
            second,tail=fresh();second.restore(loads(canonical(first.checkpoint())))
            for e in events[cut:]:second.apply(e)
            self.assertEqual(prefix.rows+tail.rows,sink.rows)
            self.assertEqual(canonical(second.checkpoint()),canonical(engine.checkpoint()))

    def test_historical_warmup_boundary_and_observed_close_are_distinct(self):
        p=self.session.compile(source(6,'plot(barstate.islastconfirmedhistory ? 1 : 0)\nplot(barstate.ishistory ? 1 : 0)\nplot(barstate.isnew ? 1 : 0)'))
        env={'primary':{'instrument':INSTRUMENT,'history_last_ms':60000}};sink=Sink()
        engine=ReplayEngine(self.session,p,(PRIMARY,),'primary',sink,environments=env)
        for i in range(2):engine.apply(MarketEvent(i,'primary','close',i*60000,(i+1)*60000,0,10.,10.,10.,10.,1.))
        observed=[replace(e,sequence=e.sequence+2) for e in tape(1)]
        observed=[replace(e,time=e.time+120000,event_time=e.event_time+120000) for e in observed]
        for e in observed:engine.apply(e)
        self.assertEqual([r[3] for r in sink.rows],[0,1,1,1,1,1,0,0,0])
        changed=copy.deepcopy(env);changed['primary']['instrument']['mintick']='0.5'
        other=ReplayEngine(self.session,p,(PRIMARY,),'primary',Sink(),environments=changed)
        with self.assertRaises(ValueError):other.restore(loads(canonical(engine.checkpoint())))

    def test_standalone_close_without_declared_warmup_is_an_observed_new_close(self):
        p=self.session.compile(source(6,'plot(barstate.ishistory ? 1:0)\nplot(barstate.isrealtime ? 1:0)\nplot(barstate.isnew ? 1:0)\nplot(barstate.isconfirmed ? 1:0)'))
        sink=Sink();engine=ReplayEngine(self.session,p,(PRIMARY,),'primary',sink)
        engine.apply(MarketEvent(0,'primary','close',0,60000,0,10.,10.,10.,10.,1.))
        self.assertEqual([r[3] for r in sink.rows],[0,1,1,1])
        historical=ReplayEngine(self.session,p,(PRIMARY,),'primary',Sink(),environments={'primary':{'instrument':None,'history_last_ms':0}})
        with self.assertRaises(ValueError):historical.apply(next(tape(1)))

    def test_arrays_and_loops_keep_their_checkpoint_heap_with_environment_IR(self):
        p=self.session.compile(source(6,'var a = array.new_int(0)\narray.push(a, time)\nif array.size(a)>2\n    array.shift(a)\ntotal=0\nfor x in a\n    total:=total+x\nplot(total)'))
        self.assertEqual(p.schema_version,'openpine.typed-ir.10');d=self.driver(p,data());saved=[]
        d.run(pause_after=2,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))));self.assertIn('heap',saved[0])
        second=self.driver(p,data());second.restore(saved[0]);second.run()
        self.assertEqual(second.finish(data_identity(data())).outputs[0].values,(0,60000,180000))

    def test_requested_time_and_tick_use_requested_environment(self):
        primary=data((0,60000,120000,180000));secondary=data((0,120000),'2','TEST:OTHER')
        secondary=replace(secondary,context_id='higher');selected=MemorySource(secondary)
        selected.manifest={'snapshot_id':'a'*64,'provenance':{'provider':'recorded.1','environment':{'schema_version':'openpine.instrument-environment.1','instrument':{**INSTRUMENT,'mintick':'0.5'}}}}
        for v in range(1,7):
            request='security' if v<5 else 'request.security'
            p=self.session.compile(source(v,f'plot({request}("TEST:OTHER","2",time))\nplot({request}("TEST:OTHER","2",syminfo.mintick))'))
            context=MarketContext('higher','TEST:OTHER','2','recorded.1');binding=ContextBinding(context,'a'*64)
            r=self.session.execute(p,data=primary,primary_context=MarketContext('environment','TEST:ENV','1','recorded.1'),context_sources=((binding,selected),))
            self.assertEqual(r.outputs[0].values,(0,0,120000,120000) if v<3 else (None,0,0,120000))
            self.assertEqual(r.outputs[1].values,(.5,)*4 if v<3 else (None,.5,.5,.5))

    def test_request_barstate_is_rejected_instead_of_assuming_chart_flags(self):
        for v in (3,4,5,6):
            request='security' if v<5 else 'request.security'
            with self.assertRaises(OpenPineError) as caught:self.session.compile(source(v,f'plot({request}("TEST:ENV","2",barstate.isconfirmed ? 1 : 0))'))
            self.assertEqual(caught.exception.diagnostic.code,'REQUEST_SCOPE')


if __name__=='__main__':unittest.main()
