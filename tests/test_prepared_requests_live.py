"""Chosen subscriptions, shared physical feed, pinned settings and portable recordings."""
import tempfile,unittest
from pathlib import Path
from dataclasses import asdict
from apps.openpine.bootstrap import local_manager
from apps.openpine.history_service import HistoryJobs
from apps.openpine.live_service import LiveSessions
from apps.openpine.recordings import Recordings
from packages.marketdata.history import BinanceHistory
from packages.marketdata.catalog import normalize_exchange_info
from packages.storage.backup import backup,restore
from test_live_contexts import Exchange,settle,observation
from test_marketdata import KEY,instrument_payload

def code(v):
    d='indicator' if v>=5 else 'study';inp='input.string' if v>=5 else 'input';api='request.security' if v>=5 else 'security'
    return f'//@version={v}\n{d}("Prepared live")\nsym={inp}("BINANCE:BTCUSDT")\ntf={inp}("5")\nplot({api}(sym,tf,close))'

class PreparedRequestLiveTests(unittest.TestCase):
    def test_selected_tf_primary_feed_is_shared_and_recording_survives_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with local_manager(root/'state') as m:
                history=HistoryJobs(m,provider=BinanceHistory(Exchange()),start=False);live=LiveSessions(m,history,start=False,clock=lambda:9000000)
                try:
                    history.repository.replace_instruments(normalize_exchange_info(instrument_payload(),7200000));keys=[]
                    for v,tf in ((1,'5'),(6,'1')):
                        ref=m.freeze_input({'source':code(v),'source_name':f'prepared-live-v{v}.pine'})
                        key=live.create({'input_ref':ref,'instrument':asdict(KEY),'timeframe':'1','start_ms':6600000,
                            'inputs':{'tf':tf},'broker_settings':None,'timeout_seconds':120},connect=False)['session_id'];keys.append(key)
                        state=settle(live,history,key,lambda s:s['warmup_done'] and s['replay']['pending_events']==0)
                        self.assertEqual(len(state['sources']),2 if tf=='5' else 1)
                        self.assertEqual(state['replay']['outputs'][0]['last'],105.5 if tf=='5' else 101.5)
                        self.assertEqual(state['replay']['context_graph']['requests'][0]['timeframe'],tf)
                    self.assertEqual(live.status(keys[0])['sources']['primary']['feed_id'],live.status(keys[1])['sources']['primary']['feed_id'])
                    key=keys[1];state=live.status(key);feed=state['sources']['primary']['feed_id']
                    live.hub.ingest(feed,observation('1',7200000,closed=True),received_at=9000001)
                    state=settle(live,history,key,lambda s:s['unprojected_records']==0 and s['replay']['pending_events']==0)
                    self.assertEqual(state['replay']['confirmed_bars'],11)
                    live.stop(keys[0]);live.stop(key);backup(m.db,m.directory,root/'backup')
                finally:live.close();history.close()
            restore(root/'backup',root/'restored')
            with local_manager(root/'restored') as m:Recordings(m).export(key,root/'bundle')
            with local_manager(root/'offline') as m:
                result=Recordings(m).replay(root/'bundle',batch_size=5)
                self.assertEqual(result['status'],'passed');self.assertEqual(result['result']['confirmed_bars'],11)
                self.assertEqual(result['result']['context_graph']['requests'][0]['timeframe'],'1')

    def test_invalid_selected_target_is_rejected_before_connecting_or_creating_session(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as m:
            history=HistoryJobs(m,provider=BinanceHistory(Exchange()),start=False);live=LiveSessions(m,history,start=False)
            try:
                history.repository.replace_instruments(normalize_exchange_info(instrument_payload(),7200000))
                ref=m.freeze_input({'source':code(6),'source_name':'prepared-live.pine'})
                with self.assertRaises(ValueError):live.create({'input_ref':ref,'instrument':asdict(KEY),'timeframe':'1','start_ms':6600000,
                    'inputs':{'tf':'0'},'broker_settings':None,'timeout_seconds':120},connect=False)
                self.assertEqual(live.repository.sessions(),[]);self.assertEqual(history.repository.tasks(),[])
            finally:live.close();history.close()

if __name__=='__main__':unittest.main()
