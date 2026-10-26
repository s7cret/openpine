"""Durable phase boundaries, real workers and original journal equality."""
import tempfile,unittest
from pathlib import Path
from dataclasses import asdict
from unittest.mock import patch
from apps.openpine.bootstrap import local_manager
from apps.openpine.replay import Replays
from apps.replay_worker import execute
from packages.contracts.strategy_cycle import PROFILE,FLAGS
from packages.storage.backup import backup,restore
from test_observed_broker import CONTEXT,tape
from test_strategy_cycle import source,PROTECTION
from test_observed_pipeline import logical

def create(manager,text,**extra):
    service=Replays(manager);state=service.create({'input_ref':manager.freeze_input({'source':text,'source_name':'cycle.pine'}),
        'contexts':[asdict(CONTEXT)],'primary_id':'primary','broker_settings':None,'timeout_seconds':180,'calculation_profile':PROFILE,**extra})
    return service,state['replay_id']

class StrategyCyclePipelineTests(unittest.TestCase):
    def test_all_versions_workers_resume_after_close_fill_with_protection(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            for v in range(1,7):
                text=source(v,('varip n=0\nn:=n+1\n' if v>=4 else '')+PROTECTION+('\nplot(n)' if v>=4 else ''))
                service,rid=create(manager,text,cycle_settings=dict(zip(FLAGS,(False,True,True))))
                service.append(rid,[e.payload() for e in tape()]);prefix=service.advance(rid,max_events=1,checkpoint_every=1)
                self.assertEqual(prefix['calculation']['passes'],2);self.assertEqual(prefix['simulation']['statistics']['fills'],1)
                self.assertEqual(len(prefix['simulation']['pending_protections']),1)
                config=service.repository.config(rid);self.assertEqual(config['schema_version'],'openpine.replay-config.5')
                self.assertEqual(config['cycle_settings'],dict(zip(FLAGS,(False,True,True))))
                final=service.advance(rid,checkpoint_every=1);self.assertEqual(final['calculation']['passes'],4)
                self.assertEqual(final['simulation']['statistics']['equity'],'1012')
                self.assertEqual([e['value']['price'] for e in service.view(rid,'ledger',0,500)['events'] if e['kind']=='fill'],['100','112'])
                if v>=4:self.assertEqual(final['outputs'][-1]['last'],4)
    def test_backup_restore_preserves_active_fill_phase_and_arrays(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with local_manager(root/'jobs') as manager:
                text=source(6,'varip a=array.new_float(0)\narray.push(a,close)\n'+PROTECTION+'\nplot(array.size(a))')
                service,rid=create(manager,text);service.append(rid,[e.payload() for e in tape()]);service.advance(rid,max_events=4)
                backup(manager.db,manager.directory,root/'backup');reference=logical(manager,service.advance(rid))
            restore(root/'backup',root/'restored')
            with local_manager(root/'restored') as manager:self.assertEqual(logical(manager,Replays(manager).advance(rid)),reference)
    def test_failure_between_regular_and_close_phase_republishes_no_partial_fills(self):
        from packages.execution.strategy_cycle import StrategyCycle
        original=StrategyCycle.calculate
        def fail(self,*args,**kwargs):
            original(self,*args,**kwargs)
            raise OSError('disk full between phases')
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            service,rid=create(manager,source(6,PROTECTION));service.append(rid,[e.payload() for e in tape()]);before=service.status(rid)
            with tempfile.TemporaryDirectory(dir=d) as attempt,patch.object(StrategyCycle,'calculate',fail):
                with self.assertRaises(OSError):execute(d,rid,attempt,max_events=1,checkpoint_every=1)
            self.assertEqual(service.status(rid),before)
            final=service.advance(rid);self.assertEqual(final['simulation']['statistics']['fills'],2)
    def test_failure_after_close_fill_before_publication_keeps_previous_prefix(self):
        from packages.storage.replay import ReplayRepository
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            service,rid=create(manager,source(6,PROTECTION));service.append(rid,[e.payload() for e in tape()]);before=service.status(rid)
            with tempfile.TemporaryDirectory(dir=d) as attempt,patch.object(ReplayRepository,'publish',side_effect=OSError('publication failed after close fill')):
                with self.assertRaises(OSError):execute(d,rid,attempt,max_events=1,checkpoint_every=1)
            self.assertEqual(service.status(rid),before);final=service.advance(rid)
            self.assertEqual(final['calculation']['passes'],4);self.assertEqual(final['simulation']['statistics']['fills'],2)
    def test_invalid_settings_create_no_catalog_replay(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            for options in ({'calc_on_order_fills':False},{'unknown':True},{'calc_on_order_fills':1}):
                with self.assertRaises(ValueError):create(manager,source(6,PROTECTION),cycle_settings=options)
            self.assertEqual(manager.db.execute('SELECT count(*) FROM replays'),[(0,)])
    def test_historical_checkpoint_contains_phase_counts_and_resumes_without_reexecution(self):
        from packages.execution.driver import HistoricalDriver,MemorySource
        from packages.execution.session import Session
        from packages.contracts import HISTORICAL_PROFILE_ID,HistoricalBar,HistoricalData
        from packages.contracts.strategy_ir import broker_policy
        from packages.contracts.wire import canonical,loads
        from packages.contracts.trading import BrokerSettings
        from test_observed_broker import Sink
        text=source(6,'varip n=0\nn:=n+1\n'+PROTECTION+'\nplot(n)')
        session=Session(profile_id=HISTORICAL_PROFILE_ID);program=session.compile(text);runtime=session.validate_program(program)
        data=HistoricalData('primary','TEST:OBSERVED','1',(HistoricalBar(0,100.,100.,100.,100.,1.),HistoricalBar(60000,101.,100.,112.,94.,4.)),schema_version='openpine.historical-bars.2')
        def driver(sink):return HistoricalDriver(runtime.prepare(program,output_sink=sink),MemorySource(data),simulation_policy=broker_policy(program,BrokerSettings(tick_size='1')))
        out=Sink();first=driver(out);states=[];first.run(checkpoint=lambda d:states.append(loads(canonical(d.checkpoint()))),pause_after=1)
        tail=Sink();second=driver(tail);second.restore(states[0]);second.run()
        self.assertEqual(second.cycle.count,4);self.assertEqual(second.simulation.broker.statistics()['equity'],'1010')
        self.assertEqual([r[3] for r in out.rows+tail.rows],[1.,2,0.,4])

if __name__=='__main__':unittest.main()
