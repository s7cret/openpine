"""Raw live observations, portable recordings and real process death with varip."""
import subprocess,sys,tempfile,unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch
from apps.openpine.bootstrap import local_manager
from apps.openpine.history_service import HistoryJobs
from apps.openpine.live_service import LiveSessions
from apps.openpine.recordings import Recordings
from packages.contracts.intrabar import PROFILE
from packages.marketdata.catalog import normalize_exchange_info
from packages.marketdata.history import BinanceHistory
from test_marketdata import FakeExchange,instrument_payload,KEY
from test_intrabar import strategy
from test_intrabar_pipeline import create
from test_observed_broker import tape
from test_observed_live import message

def prepare(manager,v=6):
    history=HistoryJobs(manager,provider=BinanceHistory(FakeExchange(as_of=240000)),start=False)
    history.repository.replace_instruments(normalize_exchange_info(instrument_payload(),240000))
    live=LiveSessions(manager,history,start=False,clock=lambda:400000)
    body=('varip n=0\nn:=n+1\n' if v>=4 else '')+'if bar_index>=4 and close<95\n    strategy.entry("L",LONG)\nif strategy.position_size>0\n    strategy.close("L")\nplot(strategy.position_size)'+('\nplot(n)' if v>=4 else '')
    text=strategy(v,body)
    state=live.create({'input_ref':manager.freeze_input({'source':text,'source_name':'live-intrabar.pine'}),
        'instrument':asdict(KEY),'timeframe':'1','start_ms':0,'broker_settings':None,'timeout_seconds':120,'calculation_profile':PROFILE},connect=False)
    identifier=state['session_id'];live.tick(identifier);history.process(live.repository.session(identifier)['warmup_task']);live.tick(identifier)
    return history,live,identifier

class IntrabarLiveTests(unittest.TestCase):
    def test_live_update_commands_and_portable_active_recording_all_versions(self):
        for v in range(1,7):
            with self.subTest(version=v),tempfile.TemporaryDirectory() as d:
                root=Path(d)
                with local_manager(root/'live') as manager:
                    history,live,sid=prepare(manager,v);feed=live.repository.session(sid)['feed_id']
                    try:
                        for revision,price in enumerate((100,94,112)):
                            live.hub.ingest(feed,message(price,revision),received_at=400001+revision);live.tick(sid)
                        state=live.status(sid);replay=state['replay'];self.assertEqual(replay['confirmed_bars'],4)
                        self.assertEqual(state['configuration']['calculation_profile'],PROFILE)
                        self.assertEqual(replay['calculation']['passes'],7);self.assertEqual(replay['simulation']['statistics']['fills'],1)
                        self.assertEqual(replay['preview']['outputs'][0]['value'],1.)
                        live.hub.ingest(feed,message(112,2),received_at=400004);live.tick(sid)
                        self.assertEqual(live.status(sid)['replay']['calculation']['passes'],7)
                        live.stop(sid);bundle=root/'recording';Recordings(manager).export(sid,bundle)
                    finally:live.close();history.close()
                with local_manager(root/'offline') as manager,patch('urllib.request.urlopen',side_effect=AssertionError('Offline replay contacted network.')):
                    report=Recordings(manager).replay(bundle,batch_size=2)
                    self.assertEqual(report['status'],'passed');self.assertEqual(report['result']['calculation']['passes'],7)
                    self.assertEqual(report['result']['preview'],replay['preview'])
    def test_real_process_death_after_publish_preserves_varip_and_one_command(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            body='varip n=0\nn:=n+1\nif close<95\n    strategy.entry("L",LONG)\nif strategy.position_size>0\n    strategy.close("L")\nplot(n)'
            service,rid=create(manager,strategy(6,body));service.append(rid,[e.payload() for e in tape()]);service.advance(rid,max_events=2)
            attempt=Path(d)/'crash';attempt.mkdir()
            code='''import os,sys
from pathlib import Path
from apps.replay_worker import execute
from packages.storage.replay import ReplayRepository
original=ReplayRepository.publish
def publish(self,*args,**kwargs):
 original(self,*args,**kwargs)
 os._exit(37)
ReplayRepository.publish=publish
execute(sys.argv[1],sys.argv[2],Path(sys.argv[3]),max_events=1,checkpoint_every=1)
'''
            result=subprocess.run([sys.executable,'-X','utf8','-c',code,d,rid,str(attempt)],capture_output=True,timeout=45)
            self.assertEqual(result.returncode,37,result.stderr.decode());self.assertEqual(service.status(rid)['calculation']['passes'],3)
            final=service.advance(rid);self.assertEqual(final['outputs'][0]['last'],5)
            journal=service.view(rid,'ledger',0,500)['events']
            self.assertEqual([e['value']['price'] for e in journal if e['kind']=='fill'],['112','101'])
            self.assertEqual(len([e for e in journal if e['kind']=='order']),2)
    def test_gap_recovery_runs_only_received_updates_and_the_recovered_close(self):
        from packages.contracts import HistoricalBar
        from packages.marketdata.history import metadata
        from packages.contracts.market import HistoryRequest
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            history,live,sid=prepare(manager);feed=live.repository.session(sid)['feed_id']
            try:
                live.hub.ingest(feed,message(100,0),received_at=400001);live.tick(sid)
                live.repository.append(feed,{'kind':'gap','reason':'model disconnect','received_at':400002,'intrabar_lost':True});live.tick(sid)
                snapshot=manager.market.ingest(metadata(HistoryRequest(KEY,'1',240000,300000)),[HistoricalBar(240000,101.,100.,120.,80.,101.)],{'provider':'recorded-rest.1'})
                live.repository.append(feed,{'kind':'recovery','snapshot_id':snapshot,'start_ms':240000,'end_ms':300000,'coverage':None,'received_at':400003,'intrabar_reconstructed':False});live.tick(sid)
                replay=live.status(sid)['replay'];self.assertEqual(replay['calculation']['passes'],6)
                self.assertEqual(replay['outputs'][1]['last'],6);self.assertEqual(replay['simulation']['statistics']['orders'],0)
            finally:live.close();history.close()

if __name__=='__main__':unittest.main()
