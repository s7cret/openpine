"""Real worker, Parquet, catalog publication and varip recovery boundaries."""
import copy,tempfile,unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch
from apps.openpine.bootstrap import local_manager
from apps.openpine.replay import Replays
from apps.replay_worker import execute
from packages.contracts.intrabar import PROFILE
from packages.contracts.realtime import MarketEvent
from packages.storage.backup import backup,restore
from test_intrabar import strategy,indicator
from test_observed_broker import CONTEXT,tape
from test_observed_pipeline import logical

def create(manager,text):
    service=Replays(manager);state=service.create({'input_ref':manager.freeze_input({'source':text,'source_name':'intrabar.pine'}),
        'contexts':[asdict(CONTEXT)],'primary_id':'primary','broker_settings':None,'timeout_seconds':180,'calculation_profile':PROFILE})
    return service,state['replay_id']

class IntrabarPipelineTests(unittest.TestCase):
    def test_all_versions_real_workers_preserve_commands_preview_and_next_price_fill(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            for v in range(1,7):
                service,rid=create(manager,strategy(v,'if close<95\n    strategy.entry("L",LONG)\nplot(strategy.position_size)'))
                service.append(rid,[e.payload() for e in tape()]);prefix=service.advance(rid,max_events=3,checkpoint_every=1)
                self.assertEqual(prefix['confirmed_bars'],1);self.assertEqual(prefix['calculation']['passes'],3)
                self.assertEqual(prefix['simulation']['statistics']['fills'],0);self.assertIsNotNone(prefix['preview'])
                config=service.repository.config(rid);self.assertEqual(config['calculation_profile'],PROFILE)
                self.assertEqual(config['broker_profile'],'openpine.observed-broker.1')
                final=service.advance(rid,checkpoint_every=1);self.assertEqual(final['outputs'][0]['last'],1.)
                self.assertEqual([e['value']['price'] for e in service.view(rid,'ledger',0,500)['events'] if e['kind']=='fill'],['112'])
    def test_varip_arrays_resume_before_first_confirmed_bar(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            service,rid=create(manager,indicator(6,'varip a=array.new_float(0)\narray.push(a,close)\nplot(array.size(a))'))
            events=tape()[1:];events=[MarketEvent(i,'primary',e.kind,0,e.event_time-60000,e.revision,e.open,e.high,e.low,e.close,e.volume) for i,e in enumerate(events)]
            service.append(rid,[e.payload() for e in events]);prefix=service.advance(rid,max_events=3)
            self.assertEqual(prefix['confirmed_bars'],0);self.assertEqual(prefix['preview']['outputs'][0]['value'],3)
            final=service.advance(rid);self.assertEqual(final['outputs'][0]['last'],4)
    def test_backup_restores_active_array_roots_and_identical_broker_journal(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with local_manager(root/'jobs') as manager:
                service,rid=create(manager,strategy(6,'varip n=0\nn:=n+1\nif close<95\n    strategy.entry("L",LONG)\nplot(n)'))
                service.append(rid,[e.payload() for e in tape()]);service.advance(rid,max_events=3)
                backup(manager.db,manager.directory,root/'backup');reference=logical(manager,service.advance(rid))
            restore(root/'backup',root/'restored')
            with local_manager(root/'restored') as manager:self.assertEqual(logical(manager,Replays(manager).advance(rid)),reference)
    def test_publication_failure_replays_order_commands_once(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            service,rid=create(manager,strategy(6,'if close<95\n    strategy.entry("L",LONG)\nplot(strategy.position_size)'))
            service.append(rid,[e.payload() for e in tape()]);before=service.advance(rid,max_events=2)
            from packages.storage.replay import ReplayRepository
            with tempfile.TemporaryDirectory(dir=d) as attempt,patch.object(ReplayRepository,'publish',side_effect=OSError('simulated disk full before commit')):
                with self.assertRaises(OSError):execute(d,rid,attempt,max_events=1)
            self.assertEqual(service.status(rid),before);final=service.advance(rid)
            self.assertEqual(final['simulation']['statistics']['fills'],1)
            orders=[e for e in service.view(rid,'ledger',0,500)['events'] if e['kind']=='order'];self.assertEqual(len(orders),1)
    def test_dense_single_candle_compacts_uncommitted_array_identities(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            text=indicator(6,'varip a=array.new_float(0)\na:=array.new_float(1,close)\nvarip n=0\nn:=n+1\nplot(n)\nplot(array.size(a))')
            service,rid=create(manager,text)
            events=[MarketEvent(i,'primary','open' if i==0 else 'update',0,i,i,100.,100.,100.,100.,float(i)) for i in range(4100)]
            events.append(MarketEvent(4100,'primary','close',0,60000,4100,100.,100.,100.,100.,4100.))
            service.append(rid,[e.payload() for e in events]);prefix=service.advance(rid,max_events=4100)
            self.assertEqual(prefix['preview']['outputs'][0]['value'],4100)
            saved=service.repository.checkpoint(rid);self.assertLessEqual(saved['engine']['intrabar']['variables']['heap']['next_id'],257)
            final=service.advance(rid);self.assertEqual([o['last'] for o in final['outputs']],[4101,1])
    def test_secondary_only_prefix_restores_before_primary_calculation(self):
        from packages.contracts.contexts import MarketContext
        from packages.contracts.live import RECEIVED_PROFILE
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            service=Replays(manager);higher=MarketContext('higher',CONTEXT.symbol,'2','sampled.1')
            state=service.create({'input_ref':manager.freeze_input({'source':indicator(6,'varip n=0\nn:=n+1\nplot(n)'),'source_name':'secondary.pine'}),
                'contexts':[asdict(CONTEXT),asdict(higher)],'primary_id':'primary','broker_settings':None,'timeout_seconds':120,
                'calculation_profile':PROFILE,'event_profile':RECEIVED_PROFILE});rid=state['replay_id']
            service.append(rid,[MarketEvent(0,'higher','open',0,0,0,100.,100.,100.,100.,1.).payload(),MarketEvent(1,'primary','close',0,60000,0,100.,100.,100.,100.,1.).payload()])
            prefix=service.advance(rid,max_events=1);self.assertEqual(prefix['calculation']['passes'],0)
            final=service.advance(rid);self.assertEqual(final['outputs'][0]['last'],1)
    def test_invalid_calculation_selection_creates_no_replay(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            text=strategy(6,'plot(close)').replace('calc_on_every_tick=true','calc_on_every_tick=false')
            with self.assertRaises(ValueError):create(manager,text)
            self.assertEqual(manager.db.execute('SELECT count(*) FROM replays'),[(0,)])

if __name__=='__main__':unittest.main()
