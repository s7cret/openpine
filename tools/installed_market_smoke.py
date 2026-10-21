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
from packages.contracts.market import HistoryRequest
from packages.marketdata.history import BinanceHistory
from tools.market_benchmark import Exchange,KEY,candle


class Fixture(Exchange):
    def __call__(self,path,params,*,control=None):
        if path.endswith('/time'):return {'serverTime':self.count*60000}
        return super().__call__(path,params,control=control)


def run(expected_root=None):
    if expected_root and not Path(__file__).resolve().is_relative_to(Path(expected_root).resolve()):
        raise AssertionError('Market smoke imported another checkout.')
    if version('websockets')!='17.2':raise AssertionError('Pinned WebSocket dependency was not installed.')
    with tempfile.TemporaryDirectory(prefix='openpine-market-installed-') as directory:
        with local_manager(directory) as manager,HistoryJobs(manager,provider=BinanceHistory(Fixture(6))) as history:
            task=history.submit(HistoryRequest(KEY,'1',0,360000).payload());done=history.wait(task['task_id'],timeout=30)
            if done['status']!='succeeded' or done['coverage']['bars']!=6:raise AssertionError(done)
            with LiveSessions(manager,history,start=False,clock=lambda:500000) as live:
                state=live.create({'input_ref':manager.freeze_input({'source':'//@version=6\nindicator("installed market")\nplot(close)',
                    'source_name':'installed.pine'}),'instrument':asdict(KEY),'timeframe':'1','start_ms':0,
                    'broker_settings':None,'timeout_seconds':30},connect=False)
                identifier=state['session_id'];deadline=time.monotonic()+60
                while time.monotonic()<deadline:
                    live.tick(identifier);state=live.status(identifier)
                    if state['replay']['confirmed_bars']==6:break
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
                    live.tick(identifier);state=live.status(identifier)
                    if state['replay']['confirmed_bars']==7 and state['unprojected_records']==0:break
                    if state['status'] in ('failed','execution_failed'):raise AssertionError(state)
                if state['replay']['confirmed_bars']!=7 or state['replay']['accepted_events']!=8:raise AssertionError(state)
                chart=live.view(identifier,'chart',0,100)
                if len(chart['bars'])!=7 or chart['bars'][-1]['close']!=float(raw[4]):raise AssertionError(chart)
                records=list(live.repository.records(state['feed_id']))
                if len(records)!=2:raise AssertionError('Installed raw journal differs.')
    return {'installed_market_acquisition':True,'installed_received_replay':True,'installed_raw_journal':True,'websockets':version('websockets')}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--expected-root',type=Path)
    args=parser.parse_args(argv);print(json.dumps(run(args.expected_root),sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
