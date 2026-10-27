"""Real replay workers, provider warmup, arrivals, restart and portable recordings."""
import tempfile,unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch
from apps.openpine.bootstrap import local_manager
from apps.openpine.history_service import HistoryJobs
from apps.openpine.live_service import LiveSessions
from apps.openpine.replay import Replays
from apps.openpine.recordings import Recordings
from packages.marketdata.history import BinanceHistory
from packages.marketdata.catalog import normalize_exchange_info
from packages.storage.backup import backup,restore
from test_lower_events import CONTEXTS,tape
from test_lower_tf import source
from test_live_contexts import Exchange,settle,observation
from test_marketdata import KEY,instrument_payload

class LowerEventPipelineTests(unittest.TestCase):
    def test_real_worker_resume_backup_and_frozen_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with local_manager(root/'state') as m:
                r=Replays(m);key=r.create({'input_ref':m.freeze_input({'source':source(),'source_name':'lower.pine'}),
                    'contexts':[asdict(c) for c in CONTEXTS],'primary_id':'primary','broker_settings':None,'timeout_seconds':60})['replay_id']
                r.pin_lower_warmup(key,{'lower':{'count':16,'end_ms':960000}})
                r.append(key,[e.payload() for e in tape()]);r.advance(key,max_events=7,checkpoint_every=2)
                with self.assertRaisesRegex(ValueError,'after accepting'):r.pin_lower_warmup(key,{'lower':{'count':15,'end_ms':960000}})
                backup(m.db,m.directory,root/'backup')
            restore(root/'backup',root/'restored')
            with local_manager(root/'restored') as m:
                r=Replays(m);state=r.advance(key,checkpoint_every=2)
                self.assertEqual(state['confirmed_bars'],4);self.assertEqual(state['pending_events'],0)
                self.assertEqual([row[-1] for row in r.view(key,'chart',0,4)['rows']],[10.,26.,42.,58.])
                self.assertEqual(state['lower_tf']['pending_cells'],0)
    def test_live_frozen_warmup_shared_feeds_restart_and_offline_export(self):
        code='//@version=6\nindicator("Lower live")\na=request.security_lower_tf("BINANCE:BTCUSDT","1",ta.cum(close),calc_bars_count=3)\nplot(array.size(a))\nplot(array.sum(a))'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with local_manager(root/'state') as m:
                history=HistoryJobs(m,provider=BinanceHistory(Exchange()),start=False)
                live=LiveSessions(m,history,start=False,clock=lambda:9000000)
                try:
                    history.repository.replace_instruments(normalize_exchange_info(instrument_payload(),7200000))
                    payload={'input_ref':m.freeze_input({'source':code,'source_name':'live-lower.pine'}),
                        'instrument':asdict(KEY),'timeframe':'5','start_ms':6600000,'broker_settings':None,'timeout_seconds':60}
                    key=live.create(payload,connect=False)['session_id']
                    state=settle(live,history,key,lambda s:s['warmup_done'] and s['replay']['pending_events']==0)
                    self.assertEqual(state['replay']['confirmed_bars'],2)
                    config=live.repository.session_config(key);cid=next(c for c in config['bindings'] if c!='primary')
                    frozen=live.replays.repository.config(state['replay_id'])['lower_tf']['warmup']
                    self.assertEqual(frozen,{cid:{'count':10,'end_ms':7200000}})
                    values=live.replays.view(state['replay_id'],'chart',0,2)['rows']
                    ids=[o['output_id'] for o in state['replay']['outputs']]
                    self.assertEqual([[row[-1] for row in values if row[2]==site] for site in ids],[[0,3],[None,609.]])
                    feeds={config['bindings'][c]['timeframe']:s['feed_id'] for c,s in state['sources'].items()}
                    live.hub.ingest(feeds['1'],observation('1',7200000,closed=True),received_at=9000001)
                    live.hub.ingest(feeds['5'],observation('5',7200000),received_at=9000002)
                    state=settle(live,history,key,lambda s:s['unprojected_records']==0 and s['replay']['pending_events']==0)
                    self.assertEqual([o['value'] for o in state['replay']['preview']['outputs']],[1,406.])
                    self.assertEqual(state['replay']['lower_tf']['closed_children'],{next(iter(state['replay']['lower_tf']['closed_children'])):1})
                    backup(m.db,m.directory,root/'backup')
                finally:live.close();history.close()
            restore(root/'backup',root/'restored')
            with local_manager(root/'restored') as m:
                history=HistoryJobs(m,provider=BinanceHistory(Exchange()),start=False)
                live=LiveSessions(m,history,start=False,clock=lambda:9000010)
                try:
                    state=settle(live,history,key,lambda s:s['replay']['pending_events']==0)
                    self.assertEqual([o['value'] for o in state['replay']['preview']['outputs']],[1,406.])
                    live.stop(key);Recordings(m).export(key,root/'bundle')
                finally:live.close();history.close()
            with local_manager(root/'offline') as m:
                with patch('packages.marketdata.history.BinanceHistory.server_time',side_effect=AssertionError('network')):
                    receipt=Recordings(m).replay(root/'bundle',batch_size=5)
                self.assertEqual(receipt['status'],'passed');self.assertEqual(receipt['result']['confirmed_bars'],2)

if __name__=='__main__':unittest.main()
