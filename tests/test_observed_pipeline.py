"""Real Parquet/journal checkpoints, workers and backup at active candle cuts."""
import copy,json,subprocess,sys,tempfile,unittest
from dataclasses import asdict,replace
from pathlib import Path
from unittest.mock import patch
from apps.openpine.bootstrap import local_manager
from apps.openpine.replay import Replays
from apps.replay_worker import execute
from packages.contracts.observed_broker import PROFILE
from packages.contracts.trading import BrokerSettings
from packages.contracts.wire import canonical
from packages.storage.backup import backup,restore
from packages.storage.trades import event_rows
from test_observed_broker import CONTEXT,source,tape

def create(manager,text):
    service=Replays(manager)
    state=service.create({'input_ref':manager.freeze_input({'source':text,'source_name':'observed.pine'}),
        'contexts':[asdict(CONTEXT)],'primary_id':'primary','broker_settings':asdict(BrokerSettings(tick_size='1')),
        'timeout_seconds':120,'broker_profile':PROFILE})
    return service,state['replay_id']

def logical(manager,state):
    journal=state['simulation']['journal']
    return {k:v for k,v in state.items() if k not in ('replay_id','checkpoint_ref','series','simulation')}|{
        'simulation':{k:v for k,v in state['simulation'].items() if k!='journal'},
        'events':list(event_rows(manager.objects,journal))}

class ObservedPipelineTests(unittest.TestCase):
    def test_real_workers_resume_inside_candle_all_versions_without_duplicate_fills(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            for v in range(1,7):
                text=source(v,['strategy.entry("L",LONG,limit=95)','strategy.exit("X","L",stop=90,limit=110)'],fee=1)
                service,identifier=create(manager,text);events=tape()
                service.append(identifier,[e.payload() for e in events]);state=service.advance(identifier,max_events=3,checkpoint_every=1)
                self.assertEqual(state['confirmed_bars'],1);self.assertEqual(state['simulation']['statistics']['position_size'],'1')
                saved=service.repository.checkpoint(identifier)
                self.assertEqual(saved['output']['schema_version'],'openpine.output-prefix.3')
                self.assertEqual(saved['output']['simulation_completed'],2)
                final=service.advance(identifier,checkpoint_every=1)
                self.assertEqual(final['simulation']['statistics']['equity'],'1015.94')
                fills=[e for e in service.view(identifier,'ledger',0,500)['events'] if e['kind']=='fill']
                self.assertEqual([e['value']['price'] for e in fills],['94','112'])
                self.assertEqual(final['simulation']['observed_execution']['observations'],5)
    def test_zero_confirmed_bars_restore_with_durable_observation_journal(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            service,identifier=create(manager,source(6,['strategy.entry("L",LONG)']))
            from packages.contracts.realtime import MarketEvent
            events=[MarketEvent(0,'primary','open',0,0,0,100.,100.,100.,100.,0.),MarketEvent(1,'primary','close',0,60000,1,100.,100.,100.,100.,1.)]
            service.append(identifier,[e.payload() for e in events]);first=service.advance(identifier,max_events=1)
            self.assertEqual(first['confirmed_bars'],0);self.assertGreater(first['simulation']['journal']['records'],0)
            final=service.advance(identifier);self.assertEqual(final['confirmed_bars'],1)
            self.assertEqual(final['simulation']['statistics']['orders'],1)
    def test_parquet_and_statistics_equal_every_worker_cut_and_backup_restore(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);text=source(6,['strategy.entry("L",LONG)','strategy.exit("X","L",stop=90,limit=110)'])
            with local_manager(root/'store') as manager:
                service,identifier=create(manager,text);service.append(identifier,[e.payload() for e in tape()])
                first=service.advance(identifier,max_events=3,checkpoint_every=1)
                frozen=copy.deepcopy(first);backup(manager.db,manager.directory,root/'backup')
                reference=logical(manager,service.advance(identifier,checkpoint_every=2))
            restore(root/'backup',root/'restored')
            with local_manager(root/'restored') as manager:
                service=Replays(manager);self.assertEqual(service.status(identifier),frozen)
                final=logical(manager,service.advance(identifier,checkpoint_every=3))
                self.assertEqual(final,reference)
    def test_unpublished_fill_is_replayed_once_after_atomic_publish_failure(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            service,identifier=create(manager,source(6,['strategy.entry("L",LONG,limit=95)']))
            service.append(identifier,[e.payload() for e in tape()]);service.advance(identifier,max_events=2,checkpoint_every=1)
            before=service.status(identifier)
            from packages.storage.replay import ReplayRepository
            with tempfile.TemporaryDirectory(dir=d) as attempt,patch.object(ReplayRepository,'publish',side_effect=OSError('simulated disk full before catalog publication')):
                with self.assertRaises(OSError):execute(d,identifier,attempt,max_events=1,checkpoint_every=1)
            self.assertEqual(service.status(identifier),before)
            final=service.advance(identifier)
            self.assertEqual(len([e for e in service.view(identifier,'ledger',0,500)['events'] if e['kind']=='fill']),1)
    def test_performance_reader_rebuilds_intrabar_trade_index(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            text=source(6,['strategy.entry("L",LONG)','strategy.exit("X","L",limit=110)'])+'\nplot(strategy.netprofit)\nplot(strategy.closedtrades)\nplot(strategy.closedtrades.entry_bar_index(0))'
            service,identifier=create(manager,text);service.append(identifier,[e.payload() for e in tape()])
            prefix=service.advance(identifier,max_events=4,checkpoint_every=1)
            self.assertEqual(prefix['confirmed_bars'],1)
            self.assertEqual(prefix['simulation']['statistics']['performance']['wins'],1)
            final=service.advance(identifier)
            self.assertEqual([o['last'] for o in final['outputs']][-3:],[12.,1,1])
            self.assertEqual(final['simulation']['journal']['closed_records'],1)

    def test_dense_open_candle_prefix_exceeds_legacy_bar_event_bound_safely(self):
        from packages.contracts.realtime import MarketEvent
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            text='//@version=6\nstrategy("Dense",default_qty_type=strategy.fixed)\nplot(close)'
            service,identifier=create(manager,text)
            rows=[MarketEvent(i,'primary','open' if i==0 else 'update',0,i, i,100.,100.,100.,100.,float(i)).payload() for i in range(4100)]
            rows.append(MarketEvent(4100,'primary','close',0,60000,4100,100.,100.,100.,100.,4100.).payload())
            service.append(identifier,rows);prefix=service.advance(identifier,max_events=4100)
            self.assertEqual(prefix['confirmed_bars'],0);self.assertEqual(prefix['simulation']['journal']['records'],4100)
            final=service.advance(identifier);self.assertEqual(final['confirmed_bars'],1)
            self.assertEqual(final['simulation']['journal']['records'],4101)

    def test_secondary_only_prefix_restores_before_first_primary_observation(self):
        from packages.contracts.contexts import MarketContext
        from packages.contracts.live import RECEIVED_PROFILE
        from packages.contracts.realtime import MarketEvent
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            service=Replays(manager);higher=MarketContext('higher',CONTEXT.symbol,'2','sampled.1')
            state=service.create({'input_ref':manager.freeze_input({'source':source(6,['strategy.entry("L",LONG)']),'source_name':'secondary.pine'}),
                'contexts':[asdict(CONTEXT),asdict(higher)],'primary_id':'primary','broker_settings':None,'timeout_seconds':120,
                'broker_profile':PROFILE,'event_profile':RECEIVED_PROFILE});identifier=state['replay_id']
            events=[MarketEvent(0,'higher','close',0,130000,0,100.,100.,100.,100.,1.),MarketEvent(1,'primary','close',0,130001,0,100.,100.,100.,100.,1.)]
            service.append(identifier,[e.payload() for e in events]);prefix=service.advance(identifier,max_events=1)
            self.assertEqual(prefix['simulation']['observed_execution']['observations'],0)
            self.assertEqual(prefix['simulation']['journal']['records'],0)
            final=service.advance(identifier);self.assertEqual(final['confirmed_bars'],1)

if __name__=='__main__':unittest.main()
