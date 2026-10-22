"""Shared multi-timeframe arrivals, delayed primary causality and joint recovery."""
import copy
import tempfile
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from apps.openpine.bootstrap import local_manager
from apps.openpine.history_service import HistoryJobs
from apps.openpine.live_service import LiveSessions
from apps.openpine.recordings import Recordings
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.contexts import MarketContext
from packages.contracts.live import RECEIVED_PROFILE
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical
from packages.execution.session import Session
from packages.execution.realtime import ReplayEngine
from packages.marketdata.history import BinanceHistory
from packages.marketdata.catalog import normalize_exchange_info
from packages.marketdata.timeframes import INTERVALS,next_open
from packages.storage.backup import backup,restore
from test_realtime import Sink
from test_marketdata import KEY,instrument_payload


def source(version=6):
    declaration='indicator' if version>=5 else 'study';request='request.security' if version>=5 else 'security'
    return f'//@version={version}\n{declaration}("multi arrivals")\nplot({request}("BINANCE:BTCUSDT","5",close))\nplot({request}("BINANCE:BTCUSDT","60",close))'


class Exchange:
    def __init__(self):self.now=7200000;self.fail=False
    def __call__(self,path,params,*,control=None):
        if control:control.check()
        if path.endswith('/time'):return {'serverTime':self.now}
        if path.endswith('/exchangeInfo'):return instrument_payload()
        tf=next(k for k,v in INTERVALS.items() if v==params['interval']);duration=int(tf)*60000
        start=(params['startTime']+duration-1)//duration*duration;stop=params['endTime']
        price=100.+int(tf)
        return [[t,str(price),str(price+2),str(price-1),str(price+.5),'100',t+duration-1,'0',1,'0','0','0']
            for t in range(start,min(stop+1,start+duration*params['limit']),duration)]


def observation(tf,t,*,closed=False,close=None):
    price=100.+int(tf);end=next_open(t,tf)
    return {'e':'kline','s':KEY.symbol,'E':end+1 if closed else t+1,'k':{'s':KEY.symbol,'i':INTERVALS[tf],
        't':t,'T':end-1,'x':closed,'o':str(price),'h':str(price+2),'l':str(price-1),
        'c':str(price+.5 if close is None else close),'v':'100'}}


def settle(live,history,identifier,condition,timeout=90):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        for task in history.repository.tasks():
            if task['status']=='queued':history.process(task['task_id'])
        live.tick(identifier);state=live.status(identifier)
        if state['status'] in ('failed','execution_failed'):raise AssertionError(state)
        if condition(state):return state
    raise AssertionError(live.status(identifier))


def create(manager,history,live,version=6):
    history.repository.replace_instruments(normalize_exchange_info(instrument_payload(),7200000))
    return live.create({'input_ref':manager.freeze_input({'source':source(version),'source_name':f'multi-v{version}.pine'}),
        'instrument':asdict(KEY),'timeframe':'1','start_ms':6600000,'broker_settings':None,'timeout_seconds':60},connect=False)['session_id']


class LiveContextTests(unittest.TestCase):
    def test_joint_cursor_disk_failure_restart_and_backup_keep_the_same_raw_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with local_manager(root/'live') as manager:
                history=HistoryJobs(manager,provider=BinanceHistory(Exchange()),start=False)
                live=LiveSessions(manager,history,start=False,clock=lambda:9000000);identifier=create(manager,history,live)
                state=settle(live,history,identifier,lambda s:s['warmup_done'] and s['replay']['pending_events']==0)
                bindings=live.repository.session_config(identifier)['bindings']
                feeds={bindings[key]['timeframe']:row['feed_id'] for key,row in state['sources'].items()}
                live.hub.ingest(feeds['5'],observation('5',7200000),received_at=9000001)
                live.hub.ingest(feeds['1'],observation('1',7200000,closed=True),received_at=9000002)
                before=live.repository.session(identifier)
                with patch.object(live.replays.repository,'append',side_effect=OSError('recorded disk full')):
                    with self.assertRaises(OSError):live._project(before)
                self.assertEqual(live.repository.session(identifier),before)
                self.assertEqual(live.status(identifier)['replay']['accepted_events'],13)
                backup(manager.db,manager.directory,root/'backup');live.close();history.close()
            restore(root/'backup',root/'restored')
            with local_manager(root/'restored') as manager:
                history=HistoryJobs(manager,provider=BinanceHistory(Exchange()),start=False)
                live=LiveSessions(manager,history,start=False,clock=lambda:9000010)
                state=settle(live,history,identifier,lambda s:s['unprojected_records']==0 and s['replay']['pending_events']==0)
                self.assertEqual(state['replay']['confirmed_bars'],11)
                self.assertEqual(state['replay']['accepted_events'],15)
                self.assertEqual(state['last_arrival'],live.repository.arrival(feeds['1'],0))
                live.stop(identifier);live.close();history.close()

    def test_delayed_primary_never_reads_future_higher_values_all_versions(self):
        session=Session(profile_id=HISTORICAL_PROFILE_ID)
        contexts=(MarketContext('primary',KEY.pine_symbol,'1','recorded'),MarketContext('higher',KEY.pine_symbol,'5','recorded'))
        for version in range(1,7):
            code=source(version).splitlines();code=code[:-1]
            program=session.compile('\n'.join(code));sink=Sink()
            engine=ReplayEngine(session,program,contexts,'primary',sink,profile=RECEIVED_PROFILE)
            specs=(('higher',0,10.),('higher',300000,20.),('primary',60000,5.),('primary',240000,6.))
            for sequence,(context,t,value) in enumerate(specs):
                event=MarketEvent(sequence,context,'close',t,9000000+sequence,0,value,value+1,value-1,value,100.)
                engine.apply(event);restored=ReplayEngine(session,program,contexts,'primary',Sink(),profile=RECEIVED_PROFILE)
                restored.restore(engine.checkpoint());self.assertEqual(restored.status(),engine.status())
            self.assertEqual([row[3] for row in sink.rows],[None,10.])
            corrupt=copy.deepcopy(engine.checkpoint());record=next(iter(corrupt['requests'].values()));record['history'][-1]['end']+=1
            with self.assertRaises(ValueError):ReplayEngine(session,program,contexts,'primary',Sink(),profile=RECEIVED_PROFILE).restore(corrupt)

    def test_three_context_warmup_global_order_shared_sources_and_offline_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with local_manager(root/'live') as manager:
                history=HistoryJobs(manager,provider=BinanceHistory(Exchange()),start=False)
                live=LiveSessions(manager,history,start=False,clock=lambda:9000000)
                first=create(manager,history,live);second=create(manager,history,live,version=1)
                for identifier in (first,second):
                    state=settle(live,history,identifier,lambda s:s['warmup_done'] and s['replay']['confirmed_bars']==10)
                    self.assertEqual(len(state['sources']),3)
                    self.assertEqual(state['replay']['accepted_events'],13)
                configs=live.repository.session_config(first)['bindings'];states=live.repository.session(first)['sources']
                feeds={configs[key]['timeframe']:row['feed_id'] for key,row in states.items()}
                for feed in feeds.values():self.assertEqual(len(live.repository.sessions(feed,active=True)),2)
                live.hub.ingest(feeds['5'],observation('5',7200000),received_at=9000001)
                live.hub.ingest(feeds['1'],observation('1',7200000),received_at=9000002)
                live.hub.ingest(feeds['60'],observation('60',7200000),received_at=9000003)
                live.hub.ingest(feeds['1'],observation('1',7200000,closed=True),received_at=9000004)
                for identifier in (first,second):
                    state=settle(live,history,identifier,lambda s:s['unprojected_records']==0 and s['replay']['pending_events']==0)
                    self.assertEqual(state['replay']['confirmed_bars'],11)
                accepted=list(live.replays.repository.events(state['replay_id'],13,17))
                self.assertEqual([e.context_id for e in accepted],
                    [next(k for k,v in configs.items() if v['timeframe']=='5'),'primary',next(k for k,v in configs.items() if v['timeframe']=='60'),'primary'])
                live.stop(first)
                for feed in feeds.values():self.assertEqual(len(live.repository.sessions(feed,active=True)),1)
                live.stop(second);Recordings(manager).export(second,root/'bundle');live.close();history.close()
            with local_manager(root/'offline') as manager:
                result=Recordings(manager).replay(root/'bundle',batch_size=1000)
                self.assertEqual(result['status'],'passed');self.assertEqual(result['result']['confirmed_bars'],11)


if __name__=='__main__':unittest.main()
