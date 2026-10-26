"""Received raw frames, live recovery, recordings and event-publication crashes."""
import copy,subprocess,sys,tempfile,unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch
from apps.openpine.bootstrap import local_manager
from apps.openpine.history_service import HistoryJobs
from apps.openpine.live_service import LiveSessions
from apps.openpine.recordings import Recordings
from packages.contracts.observed_broker import PROFILE
from packages.marketdata.catalog import normalize_exchange_info
from packages.marketdata.history import BinanceHistory
from test_marketdata import FakeExchange,instrument_payload,KEY
from test_observed_broker import source
from test_observed_pipeline import create
from tools.observed_broker_fixtures import events

def message(close,revision,*,closed=False):
    return {'e':'kline','s':'BTCUSDT','E':300001+revision,'k':{'t':240000,'T':299999,'s':'BTCUSDT','i':'1m','x':closed,
        'o':'100','h':'120','l':'80','c':str(close),'v':str(100+revision)}}

def prepare(manager,v=6):
    history=HistoryJobs(manager,provider=BinanceHistory(FakeExchange(as_of=240000)),start=False)
    history.repository.replace_instruments(normalize_exchange_info(instrument_payload(),240000))
    live=LiveSessions(manager,history,start=False,clock=lambda:400000)
    text=source(v,['strategy.entry("L",LONG,limit=95)','strategy.exit("X","L",stop=90,limit=110)'],qty=2,fee=1).replace('bar_index==0','bar_index==3')
    state=live.create({'input_ref':manager.freeze_input({'source':text,'source_name':'live-observed.pine'}),
        'instrument':asdict(KEY),'timeframe':'1','start_ms':0,'broker_settings':None,'timeout_seconds':120,'broker_profile':PROFILE},connect=False)
    identifier=state['session_id'];live.tick(identifier);history.process(live.repository.session(identifier)['warmup_task']);live.tick(identifier)
    if live.status(identifier)['replay']['confirmed_bars']!=4:raise AssertionError(live.status(identifier))
    return history,live,identifier

class ObservedLiveTests(unittest.TestCase):
    def test_gap_and_rest_close_do_not_invent_a_missing_intrabar_fill(self):
        from packages.contracts import HistoricalBar
        from packages.marketdata.history import metadata
        from packages.contracts.market import HistoryRequest
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            history,live,identifier=prepare(manager);feed=live.repository.session(identifier)['feed_id']
            try:
                live.hub.ingest(feed,message(100,0),received_at=400001);live.tick(identifier)
                live.repository.append(feed,{'kind':'gap','reason':'model disconnect','received_at':400002,'intrabar_lost':True});live.tick(identifier)
                snapshot=manager.market.ingest(metadata(HistoryRequest(KEY,'1',240000,300000)),
                    [HistoricalBar(240000,101.,100.,120.,80.,101.)],{'provider':'recorded-rest.1'})
                live.repository.append(feed,{'kind':'recovery','snapshot_id':snapshot,'start_ms':240000,'end_ms':300000,
                    'coverage':None,'received_at':400003,'intrabar_reconstructed':False});live.tick(identifier)
                state=live.status(identifier)
                self.assertEqual(state['mapper']['gaps'],1);self.assertEqual(state['mapper']['recovered_bars'],1)
                self.assertEqual(state['replay']['confirmed_bars'],5)
                self.assertEqual(state['replay']['simulation']['statistics']['fills'],0)
                self.assertFalse(state['intrabar_reconstructed'])
            finally:live.close();history.close()

    def test_live_parent_entry_protection_and_offline_recording_all_versions(self):
        for v in range(1,7):
            with self.subTest(version=v),tempfile.TemporaryDirectory() as d:
                root=Path(d)
                with local_manager(root/'live') as manager:
                    history,live,identifier=prepare(manager,v);feed=live.repository.session(identifier)['feed_id']
                    try:
                        for revision,price in enumerate((100,94,112)):
                            live.hub.ingest(feed,message(price,revision),received_at=400001+revision);live.tick(identifier)
                        state=live.status(identifier);sim=state['replay']['simulation']
                        self.assertEqual(state['replay']['confirmed_bars'],4)
                        self.assertEqual(sim['statistics']['equity'],'1031.88');self.assertFalse(sim['observed_execution']['bar_confirmed'])
                        self.assertEqual(sim['statistics']['fills'],2)
                        # Exact raw duplicate cannot mint another execution.
                        live.hub.ingest(feed,message(112,2),received_at=400004);live.tick(identifier)
                        self.assertEqual(live.status(identifier)['replay']['simulation']['statistics']['fills'],2)
                        live.stop(identifier);bundle=root/'recording';Recordings(manager).export(identifier,bundle)
                    finally:live.close();history.close()
                with local_manager(root/'offline') as manager,patch('urllib.request.urlopen',side_effect=AssertionError('Offline replay contacted network.')):
                    replay=Recordings(manager).replay(bundle,batch_size=2)
                    self.assertEqual(replay['status'],'passed');self.assertEqual(replay['result']['simulation']['statistics']['equity'],'1031.88')
    def test_actual_process_death_after_catalog_publication_recovers_one_fill(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            service,identifier=create(manager,source(6,['strategy.entry("L",LONG,limit=95)']))
            service.append(identifier,[e.payload() for e in events('limit')]);service.advance(identifier,max_events=2)
            attempt=Path(d)/'crash-attempt';attempt.mkdir()
            code="""import os,sys
from pathlib import Path
from apps.replay_worker import execute
from packages.storage.replay import ReplayRepository
original=ReplayRepository.publish
def publish(self,*args,**kwargs):
 original(self,*args,**kwargs)
 os._exit(37)
ReplayRepository.publish=publish
execute(sys.argv[1],sys.argv[2],Path(sys.argv[3]),max_events=1,checkpoint_every=1)
"""
            process=subprocess.run([sys.executable,'-X','utf8','-c',code,d,identifier,str(attempt)],capture_output=True,timeout=45)
            self.assertEqual(process.returncode,37,process.stderr.decode())
            self.assertEqual(service.status(identifier)['sequence'],2)
            self.assertEqual(service.status(identifier)['simulation']['statistics']['fills'],1)
            service.advance(identifier)
            fills=[e for e in service.view(identifier,'ledger',0,500)['events'] if e['kind']=='fill']
            self.assertEqual(len(fills),1);self.assertEqual(fills[0]['value']['price'],'94')

if __name__=='__main__':unittest.main()
