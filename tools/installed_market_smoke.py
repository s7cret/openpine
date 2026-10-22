"""Offline acceptance of acquisition and received replay from an installed wheel."""

import argparse
import json
from pathlib import Path
import tempfile
import time
from dataclasses import asdict
from importlib.metadata import version

from apps.openpine.bootstrap import local_manager
from apps.openpine.history_service import HistoryJobs
from apps.openpine.live_service import LiveSessions
from apps.openpine.recordings import Recordings
from apps.openpine.market_overview import overview
from apps.openpine.market_maintenance import MarketMaintenance
from packages.contracts.calendar import floor_open,next_open
from packages.contracts.market import HistoryRequest
from packages.marketdata.history import BinanceHistory
from tools.market_benchmark import Exchange,KEY,candle
from tools.session_soak import Exchange as ContextExchange


class Fixture(Exchange):
    def __call__(self,path,params,*,control=None):
        if path.endswith('/time'):return {'serverTime':self.count*60000}
        if path.endswith('/klines') and params['interval']!='1m':return ContextExchange()(path,params,control=control)
        return super().__call__(path,params,control=control)


def run(expected_root=None):
    if expected_root and not Path(__file__).resolve().is_relative_to(Path(expected_root).resolve()):
        raise AssertionError('Market smoke imported another checkout.')
    if version('websockets')!='17.2':raise AssertionError('Pinned WebSocket dependency was not installed.')
    with tempfile.TemporaryDirectory(prefix='openpine-market-installed-') as directory:
        root=Path(directory)
        with local_manager(root/'live') as manager,HistoryJobs(manager,provider=BinanceHistory(Fixture(6))) as history:
            task=history.submit(HistoryRequest(KEY,'1',0,360000).payload());done=history.wait(task['task_id'],timeout=30)
            if done['status']!='succeeded' or done['coverage']['bars']!=6:raise AssertionError(done)
            with LiveSessions(manager,history,start=False,clock=lambda:500000) as live:
                state=live.create({'input_ref':manager.freeze_input({'source':'//@version=6\nindicator("installed market")\nplot(close)\nplot(request.security("BINANCE:BTCUSDT","5",close))\nplot(request.security("BINANCE:BTCUSDT","60",close))',
                    'source_name':'installed.pine'}),'instrument':asdict(KEY),'timeframe':'1','start_ms':0,
                    'broker_settings':None,'timeout_seconds':30},connect=False)
                identifier=state['session_id'];deadline=time.monotonic()+60
                while time.monotonic()<deadline:
                    live.tick(identifier,execute=False);live._dispatch();state=live.status(identifier)
                    if state['warmup_done'] and state['replay']['pending_events']==0 and state['replay']['confirmed_bars']==6:break
                    if state['status']=='failed':raise AssertionError(state)
                    time.sleep(.05)
                if state['replay']['confirmed_bars']!=6:raise AssertionError(state)
                raw=candle(6)
                for closed in (False,True):
                    payload={'e':'kline','s':'BTCUSDT','E':420001 if closed else 390000,
                        'k':{'t':360000,'T':419999,'s':'BTCUSDT','i':'1m','x':closed,
                            **dict(zip(('o','h','l','c','v'),raw[1:6],strict=True))}}
                    live.hub.ingest(state['feed_id'],payload,received_at=500001+int(closed))
                deadline=time.monotonic()+60
                while time.monotonic()<deadline:
                    live.tick(identifier,execute=False);live._dispatch();state=live.status(identifier)
                    if state['replay']['confirmed_bars']==7 and state['unprojected_records']==0 and state['replay']['pending_events']==0:break
                    if state['status'] in ('failed','execution_failed'):raise AssertionError(state)
                if state['replay']['confirmed_bars']!=7 or state['replay']['accepted_events']!=9 or len(state['sources'])!=3:raise AssertionError(state)
                chart=live.view(identifier,'chart',0,100)
                if len(chart['bars'])!=7 or chart['bars'][-1]['close']!=float(raw[4]):raise AssertionError(chart)
                records=list(live.repository.records(state['feed_id']))
                if len(records)!=2:raise AssertionError('Installed raw journal differs.')
                if overview(manager,live)['total']<1:raise AssertionError('Installed storage overview omitted data.')
                live.stop(identifier);MarketMaintenance(manager).rotate();Recordings(manager).export(identifier,root/'recording')
        with local_manager(root/'offline') as manager:
            if Recordings(manager).replay(root/'recording',batch_size=10000)['status']!='passed':raise AssertionError('Installed recording differs.')
        if next_open(floor_open(1709251200000,'M'),'M')!=1711929600000:raise AssertionError('Installed UTC calendar differs.')
    return {'installed_market_acquisition':True,'installed_received_replay':True,'installed_raw_journal':True,
        'installed_multi_context':True,'installed_batch_dispatch':True,'installed_recording_replay':True,
        'installed_calendar':True,'installed_storage_overview':True,'websockets':version('websockets')}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--expected-root',type=Path)
    args=parser.parse_args(argv);print(json.dumps(run(args.expected_root),sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
