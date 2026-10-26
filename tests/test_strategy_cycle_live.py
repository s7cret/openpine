"""Raw live frames, offline phase recordings and actual process termination."""
import subprocess,sys,tempfile,unittest
from pathlib import Path
from dataclasses import asdict
from unittest.mock import patch
from apps.openpine.bootstrap import local_manager
from apps.openpine.history_service import HistoryJobs
from apps.openpine.live_service import LiveSessions
from apps.openpine.recordings import Recordings,open_recording,CYCLE_SCHEMA
from packages.contracts.strategy_cycle import PROFILE,FLAGS
from packages.marketdata.catalog import normalize_exchange_info
from packages.marketdata.history import BinanceHistory
from test_marketdata import FakeExchange,instrument_payload,KEY
from test_observed_live import message
from test_observed_broker import tape
from test_strategy_cycle import source,PROTECTION
from test_strategy_cycle_pipeline import create

def prepare(manager,v):
    history=HistoryJobs(manager,provider=BinanceHistory(FakeExchange(as_of=240000)),start=False)
    history.repository.replace_instruments(normalize_exchange_info(instrument_payload(),240000))
    live=LiveSessions(manager,history,start=False,clock=lambda:400000)
    body=('varip n=0\nn:=n+1\n' if v>=4 else '')+'if bar_index>=4 and close<95\n    strategy.entry("L",LONG)\nif strategy.position_size>0\n    strategy.close("L")\nplot(strategy.position_size)'+('\nplot(n)' if v>=4 else '')
    text=source(v,body,tick=True)
    state=live.create({'input_ref':manager.freeze_input({'source':text,'source_name':'live-cycle.pine'}),
        'instrument':asdict(KEY),'timeframe':'1','start_ms':0,'broker_settings':None,'timeout_seconds':180,
        'calculation_profile':PROFILE,'cycle_settings':dict(zip(FLAGS,(True,True,True)))},connect=False)
    sid=state['session_id'];live.tick(sid);history.process(live.repository.session(sid)['warmup_task']);live.tick(sid)
    return history,live,sid

class StrategyCycleLiveTests(unittest.TestCase):
    def test_all_versions_live_fill_phase_and_active_recording_replay_offline(self):
        for v in range(1,7):
            with self.subTest(version=v),tempfile.TemporaryDirectory() as d:
                root=Path(d)
                with local_manager(root/'live') as manager:
                    history,live,sid=prepare(manager,v);feed=live.repository.session(sid)['feed_id']
                    try:
                        for revision,price in enumerate((100,94,112)):
                            live.hub.ingest(feed,message(price,revision),received_at=400001+revision);live.tick(sid)
                        status=live.status(sid);replay=status['replay'];self.assertEqual(replay['calculation']['passes'],8)
                        self.assertEqual(status['configuration']['cycle_settings'],dict(zip(FLAGS,(True,True,True))))
                        self.assertEqual(replay['calculation']['phase_counts']['after_fill'],1)
                        self.assertEqual(replay['simulation']['statistics']['fills'],1)
                        self.assertEqual(replay['preview']['outputs'][0]['value'],1.)
                        live.hub.ingest(feed,message(112,2),received_at=400004);live.tick(sid)
                        self.assertEqual(live.status(sid)['replay']['calculation']['passes'],8)
                        live.stop(sid);bundle=root/'recording';Recordings(manager).export(sid,bundle)
                        self.assertEqual(open_recording(bundle)[0]['schema_version'],CYCLE_SCHEMA)
                    finally:live.close();history.close()
                with local_manager(root/'offline') as manager,patch('urllib.request.urlopen',side_effect=AssertionError('Offline replay used network')):
                    report=Recordings(manager).replay(bundle,batch_size=2)
                    self.assertEqual(report['status'],'passed');self.assertEqual(report['result']['calculation']['passes'],8)
                    self.assertEqual(report['result']['preview'],replay['preview'])
    def test_process_death_between_phases_discards_the_whole_unpublished_observation(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            service,rid=create(manager,source(6,'varip n=0\nn:=n+1\n'+PROTECTION+'\nplot(n)'))
            service.append(rid,[e.payload() for e in tape()]);before=service.status(rid)
            attempt=Path(d)/'crash';attempt.mkdir()
            code='''import os,sys
from pathlib import Path
from apps.replay_worker import execute
from packages.execution.strategy_cycle import StrategyCycle
original=StrategyCycle.calculate
def calculate(self,*args,**kwargs):
 original(self,*args,**kwargs)
 if args[1]=='after_close_fill':os._exit(37)
StrategyCycle.calculate=calculate
execute(sys.argv[1],sys.argv[2],Path(sys.argv[3]),max_events=1,checkpoint_every=1)
'''
            result=subprocess.run([sys.executable,'-X','utf8','-c',code,d,rid,str(attempt)],capture_output=True,timeout=60)
            self.assertEqual(result.returncode,37,result.stderr.decode());self.assertEqual(service.status(rid),before)
            final=service.advance(rid);self.assertEqual(final['calculation']['passes'],4);self.assertEqual(final['outputs'][-1]['last'],4)
            self.assertEqual(final['simulation']['statistics']['fills'],2)
    def test_process_death_after_publish_keeps_one_close_fill_and_one_recalculation(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            service,rid=create(manager,source(6,'varip n=0\nn:=n+1\n'+PROTECTION+'\nplot(n)'))
            service.append(rid,[e.payload() for e in tape()]);attempt=Path(d)/'crash';attempt.mkdir()
            code='''import os,sys
from pathlib import Path
from apps.replay_worker import execute
from packages.storage.replay import ReplayRepository
original=ReplayRepository.publish
def publish(self,*args,**kwargs):
 original(self,*args,**kwargs);os._exit(37)
ReplayRepository.publish=publish
execute(sys.argv[1],sys.argv[2],Path(sys.argv[3]),max_events=1,checkpoint_every=1)
'''
            result=subprocess.run([sys.executable,'-X','utf8','-c',code,d,rid,str(attempt)],capture_output=True,timeout=60)
            self.assertEqual(result.returncode,37,result.stderr.decode());prefix=service.status(rid)
            self.assertEqual(prefix['calculation']['passes'],2);self.assertEqual(prefix['simulation']['statistics']['fills'],1)
            final=service.advance(rid);self.assertEqual(final['calculation']['passes'],4)
            self.assertEqual([e['value']['price'] for e in service.view(rid,'ledger',0,500)['events'] if e['kind']=='fill'],['100','112'])

if __name__=='__main__':unittest.main()
