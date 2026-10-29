"""Causality for bounded arrays, requested contexts, observed phases and model points."""
import unittest,tempfile,copy
from pathlib import Path
from types import SimpleNamespace
from packages.contracts import HISTORICAL_PROFILE_ID,HistoricalData,HistoricalBar
from packages.contracts.wire import canonical
from packages.execution.session import Session
from packages.execution.observation import Observer
from packages.storage.objects import ObjectStore
from packages.storage.traces import Writer
from packages.contracts.trace import options
import test_magnifier as magnifier_helpers
import test_intrabar as intrabar_helpers
from test_realtime import Sink,PRIMARY,text,tape
from test_observed_broker import Sink as ObservedSink
from packages.execution.realtime import ReplayEngine
from packages.contracts.trading import BrokerSettings
from packages.contracts.strategy_cycle import PROFILE as CYCLE

class TraceModeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.objects=ObjectStore(Path(self.temp.name)/'objects');self.session=Session(profile_id=HISTORICAL_PROFILE_ID)
    def writer(self,**extra):
        p=options({'end_bar':4,**extra});writer=Writer(self.objects,{'options':p});self.session.observer=Observer(writer,p);return writer
    def records(self,writer):
        ref=writer.save(status='test');return [e for p in self.objects.get(ref)['parts'] for e in self.objects.get(p['object_id'])]
    def test_magnifier_and_broker_decisions_all_versions(self):
        for version in range(1,7):
            helper=SimpleNamespace(session=self.session,programs={});result=[]
            for traced in (False,True):
                writer=self.writer() if traced else None
                if not traced:self.session.observer=None
                driver,out=magnifier_helpers.MagnifierTests.driver(helper,magnifier_helpers.source(version))
                try:driver.run();result.append((out.rows,out.events,driver.checkpoint()))
                finally:driver.simulation.close()
                if traced:
                    records=self.records(writer);self.assertTrue(any(e['kind']=='branch_commit' for e in records))
                    self.assertTrue(any(e['kind']=='broker' and e['broker_event']['kind']=='fill' for e in records))
                    self.assertTrue(any(e.get('model_point',{}).get('child_time')==120000 for e in records))
                    self.assertTrue(any(e.get('pass_phase')=='after_fill' for e in records))
            self.assertEqual(canonical(result[0]),canonical(result[1]))
    def test_observed_cycle_all_versions_has_intents_and_phase_identity(self):
        for v in range(1,7):
            program=self.session.compile(text(v,True));result=[]
            for traced in (False,True):
                writer=self.writer() if traced else None
                if not traced:self.session.observer=None
                sink=ObservedSink();engine=ReplayEngine(self.session,program,(PRIMARY,),'primary',sink,broker_settings=BrokerSettings(tick_size='1'),
                    calculation_profile=CYCLE,cycle_settings={'calc_on_every_tick':True,'calc_on_order_fills':True,'process_orders_on_close':True})
                try:
                    for event in tape():engine.apply(event)
                    result.append((sink.rows,sink.events,engine.checkpoint()))
                finally:engine.simulation.close()
                if traced:
                    records=self.records(writer);self.assertTrue(any(e['kind']=='order_intent' and e['span'] for e in records))
                    self.assertTrue(any(e['kind']=='branch_commit' for e in records));self.assertTrue(any(e.get('event',{}).get('revision')==1 for e in records))
            self.assertEqual(canonical(result[0]),canonical(result[1]))
    def test_array_preview_and_varip_v4_v6_do_not_copy_full_heap(self):
        for v in (4,5,6):
            source=intrabar_helpers.indicator(v,'varip a=array.new_float(100,2.0)\nplot(array.get(a,0))')
            p=self.session.compile(source);writer=self.writer(array_preview=2);sink=intrabar_helpers.Sink()
            engine=ReplayEngine(self.session,p,(intrabar_helpers.CONTEXT,),'primary',sink)
            for event in intrabar_helpers.tape():engine.apply(event)
            records=self.records(writer);arrays=[e['value'] for e in records if e.get('value',{}).get('type')=='array']
            self.assertTrue(arrays);self.assertTrue(all(r['length']==100 and len(r['preview'])==2 for r in arrays))
            self.assertTrue(any(e['kind']=='varip_carry' for e in records))

if __name__=='__main__':unittest.main()
