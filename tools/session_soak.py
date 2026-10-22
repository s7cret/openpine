"""Deterministic multi-context continuity acceptance with real native process kills."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch

from apps.openpine.bootstrap import local_manager
from apps.openpine.history_service import HistoryJobs
from apps.openpine.live_service import LiveSessions
from apps.openpine.market_maintenance import MarketMaintenance
from apps.openpine.recordings import Recordings,logical_result
from apps.worker.__main__ import watch_parent
from packages.contracts.market import InstrumentKey
from packages.contracts.wire import atomic_json,read_json,digest
from packages.marketdata.catalog import normalize_exchange_info
from packages.marketdata.history import BinanceHistory
from packages.marketdata.timeframes import INTERVALS
from packages.storage.backup import backup,restore,audit

ROOT=Path(__file__).resolve().parents[1]
KEY=InstrumentKey('binance','spot','BTCUSDT')
MINUTE=60000
WARMUP=120
KILL_PROJECT_RECORDS=16


def price(tf,t):return 100.+(t//(int(tf)*MINUTE))%97/100+int(tf)/1000


class Exchange:
    def __init__(self):self.now=WARMUP*MINUTE
    def __call__(self,path,params,*,control=None):
        if control:control.check()
        if path.endswith('/time'):return {'serverTime':self.now}
        if path.endswith('/exchangeInfo'):
            return {'symbols':[{'symbol':'BTCUSDT','baseAsset':'BTC','quoteAsset':'USDT','status':'TRADING',
                'filters':[{'filterType':'PRICE_FILTER','tickSize':'0.001'},{'filterType':'LOT_SIZE','stepSize':'0.00001'}]}]}
        tf=next(k for k,v in INTERVALS.items() if v==params['interval']);duration=int(tf)*MINUTE
        start=(params['startTime']+duration-1)//duration*duration
        return [[t,str(price(tf,t)),str(price(tf,t)+1),str(price(tf,t)-1),str(price(tf,t)+.5),'100',t+duration-1,'0',1,'0','0','0']
            for t in range(start,min(params['endTime']+1,start+duration*params['limit']),duration)]


def observation(tf,t,closed):
    p=price(tf,t);end=t+int(tf)*MINUTE
    return {'e':'kline','s':'BTCUSDT','E':end+1 if closed else t+1,
        'k':{'s':'BTCUSDT','i':INTERVALS[tf],'t':t,'T':end-1,'x':closed,'o':str(p),'h':str(p+1),
            'l':str(p-1),'c':str(p+(.5 if closed else .25)),'v':'100' if closed else '50'}}


def source(version):
    request='request.security' if version>=5 else 'security';ema='ta.ema' if version>=5 else 'ema'
    strategy=version%2==0
    declaration='strategy("continuity",default_qty_type=strategy.fixed)' if strategy else ('indicator' if version>=5 else 'study')+'("continuity")'
    code=f'//@version={version}\n{declaration}\nplot(close,"close")\nplot({request}("BINANCE:BTCUSDT","5",close),"five")\nplot({request}("BINANCE:BTCUSDT","60",close),"hour")\nplot({ema}(close,3),"ema")'
    if strategy:code+='\nif bar_index%17==0\n    strategy.entry("L",strategy.long)\nif bar_index%17==9\n    strategy.close("L")\nplot(strategy.equity,"equity")'
    return code


def check(live,identifiers):
    for identifier in identifiers:
        state=live.status(identifier)
        if state['desired']!='running' or state['status'] in ('failed','execution_failed'):raise AssertionError(state)


def pump(live,identifiers):
    """Use the production finite dispatch, filling its bounded input budget first."""
    deadline=time.monotonic()+900
    while True:
        check(live,identifiers)
        for identifier in identifiers:
            while True:
                state=live.status(identifier)
                if state['replay']['pending_events']>=1000:break
                if state['warmup_done'] and state['unprojected_records']==0:break
                live.tick(identifier,execute=False)
                check(live,[identifier])
                if time.monotonic()>deadline:raise TimeoutError('Continuity projection did not settle.')
        live._dispatch()
        check(live,identifiers)
        states=[live.status(identifier) for identifier in identifiers]
        if all(s['warmup_done'] and s['unprojected_records']==0 and s['replay']['pending_events']==0 for s in states):return
        if time.monotonic()>deadline:raise TimeoutError('Continuity execution did not settle.')


def ingest(live,exchange,feeds,minutes):
    lost=(minutes//3)//5*5
    for step in range(minutes):
        t=(WARMUP+step)*MINUTE;end=t+MINUTE
        if step!=lost:live.hub.ingest(feeds['1'],observation('1',t,False),received_at=t+1)
        for tf in ('60','5'):
            duration=int(tf)*MINUTE
            if end%duration==0:live.hub.ingest(feeds[tf],observation(tf,end-duration,True),received_at=end+1)
        if step==lost:
            exchange.now=end+10
            live.hub._record(feeds['1'],{'kind':'gap','reason':'deterministic lost socket candle',
                'received_at':end,'intrabar_lost':True,'source':'network'})
            live.hub._reconcile(feeds['1'],threading.Event())
        else:live.hub.ingest(feeds['1'],observation('1',t,True),received_at=end+2)
    t=(WARMUP+minutes)*MINUTE
    live.hub.ingest(feeds['1'],observation('1',t,False),received_at=t+3)
    return lost


def child(args):
    threading.Thread(target=watch_parent,args=(args.parent_pid,),daemon=True).start()
    exchange=Exchange()
    with local_manager(args.directory/'original') as manager,HistoryJobs(manager,provider=BinanceHistory(exchange)) as history:
        with LiveSessions(manager,history,start=False,clock=lambda:exchange.now+100) as live:
            history.repository.replace_instruments(normalize_exchange_info(exchange('/exchangeInfo',{}),exchange.now))
            identifiers=[]
            for version in args.versions:
                identifiers.append(live.create({'input_ref':manager.freeze_input({'source':source(version),'source_name':f'continuity-v{version}.pine'}),
                    'instrument':asdict(KEY),'timeframe':'1','start_ms':0,'broker_settings':None,'timeout_seconds':120},connect=False)['session_id'])
            pump(live,identifiers)
            state=live.status(identifiers[0]);bindings=live.repository.session_config(identifiers[0])['bindings']
            feeds={bindings[key]['timeframe']:row['feed_id'] for key,row in state['sources'].items()}
            if len(feeds)!=3 or any(len(live.repository.sessions(feed,active=True))!=len(identifiers) for feed in feeds.values()):
                raise AssertionError('Sessions did not share exactly three feeds.')
            lost=ingest(live,exchange,feeds,args.minutes)
            cuts=[]
            for identifier in identifiers:
                # The kill point is an input-count boundary. A timed tick can
                # drain the entire small fixture on a fast runner (notably Linux).
                for _ in range(KILL_PROJECT_RECORDS):
                    if not live._project(live.repository.session(identifier)):
                        raise AssertionError('Coordinator exhausted raw records before its bounded kill cut.')
                state=live.status(identifier)
                if not state['replay']['pending_events'] or not state['unprojected_records']:raise AssertionError('Kill point must have durable pending and raw tails.')
                cuts.append({'normalized_pending':state['replay']['pending_events'],'raw_pending':state['unprojected_records']})
            atomic_json(args.directory/'ready.json',{'sessions':identifiers,'versions':args.versions,'feeds':feeds,
                'lost_candle_step':lost,'states':[live.status(i)['replay']['sequence'] for i in identifiers],
                'coordinator_cut':{'projection_limit':KILL_PROJECT_RECORDS,'sessions':cuts}})
            while True:time.sleep(.1)  # Parent kills the real coordinator at this durable cut.


def kill_worker(live,identifiers):
    """Kill a real batch after it publishes progress; no production fault hooks."""
    service=live.replays;ids=[live.status(i)['replay_id'] for i in identifiers]
    before={key:service.status(key)['sequence'] for key in ids};native_popen=subprocess.Popen
    killed=threading.Event();stop=threading.Event();monitors=[]
    def launch(*args,**kwargs):
        process=native_popen(*args,**kwargs)
        def monitor():
            while not stop.wait(.01) and process.poll() is None:
                if any(service.status(key)['sequence']>before[key] for key in ids):
                    try:process.kill();killed.set()
                    except ProcessLookupError:pass
                    return
        thread=threading.Thread(target=monitor,daemon=True);thread.start();monitors.append(thread)
        return process
    try:
        with patch('apps.openpine.replay_batch.subprocess.Popen',side_effect=launch):
            try:service.advance_many(ids,max_events=1000,checkpoint_every=10)
            except ValueError:
                if not killed.is_set():raise
    finally:
        stop.set()
        for thread in monitors:thread.join(timeout=5)
    after={key:service.status(key)['sequence'] for key in ids}
    if not killed.is_set() or not any(after[key]>before[key] for key in ids):raise AssertionError('Worker was not killed after a durable publication.')
    if not any(service.status(key)['pending_events'] for key in ids):raise AssertionError('Kill did not leave a resumable tail.')
    return {'real_worker_kill':True,'before':list(before.values()),'after':list(after.values())}


def verify_rows(live,identifier,minutes):
    count=WARMUP+minutes;service=live.replays;state=live.status(identifier);replay=state['replay']
    if replay['confirmed_bars']!=count:raise AssertionError('Confirmed count differs.')
    if replay['simulation'] is None and replay['preview'] is None:raise AssertionError('Indicator lost its final preview.')
    if replay['simulation'] is not None and replay['preview'] is not None:raise AssertionError('Close-only strategy evaluated an unconfirmed candle.')
    outputs={row['output_id']:row['title'] for row in replay['outputs']};verified=0
    for start in range(0,count,1000):
        for index,t,site,value in service.view(state['replay_id'],'chart',start,1000)['rows']:
            title=outputs[site]
            if title not in ('close','five','hour'):continue
            tf={'close':'1','five':'5','hour':'60'}[title];duration=int(tf)*MINUTE
            expected_t=(t+MINUTE)//duration*duration-duration
            expected=None if expected_t<0 else price(tf,expected_t)+.5
            if value!=expected:raise AssertionError((index,title,value,expected))
            verified+=1
    if verified!=count*3:raise AssertionError('Independent OHLCV/request verification omitted rows.')
    return verified


def run(args):
    root=args.directory.resolve()
    if root.exists():raise ValueError('Use a new continuity acceptance directory.')
    root.mkdir(parents=True);started=time.monotonic()
    command=[sys.executable,'-X','utf8','-m','tools.session_soak','--directory',str(root),'--minutes',str(args.minutes),
        '--versions',','.join(map(str,args.versions)),'--child','--parent-pid',str(os.getpid())]
    with (root/'coordinator.log').open('wb') as log:
        process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=log,stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            deadline=time.monotonic()+600
            while not (root/'ready.json').exists():
                if process.poll() is not None:
                    with (root/'coordinator.log').open('rb') as diagnostic:
                        diagnostic.seek(0,2);diagnostic.seek(max(0,diagnostic.tell()-12288))
                        tail=diagnostic.read().decode('utf-8',errors='replace')
                    raise AssertionError('Coordinator exited before kill point. coordinator.log tail:\n'+tail)
                if time.monotonic()>deadline:raise TimeoutError('Coordinator did not reach durable kill point.')
                time.sleep(.05)
            process.kill();process.wait(timeout=10)
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=10)
    ready=read_json(root/'ready.json');identifiers=ready['sessions']
    with local_manager(root/'original') as manager,HistoryJobs(manager,provider=BinanceHistory(Exchange()),start=False) as history:
        with LiveSessions(manager,history,start=False) as live:
            if [live.status(i)['replay']['sequence'] for i in identifiers]!=ready['states']:raise AssertionError('Coordinator restart changed the published prefix.')
            worker=kill_worker(live,identifiers)
            segments=MarketMaintenance(manager,live=live).rotate()
            if not segments['raw_segments'] or not segments['event_segments']:raise AssertionError('Rotation omitted a journal.')
            audit(manager.db,manager.directory);backup(manager.db,manager.directory,root/'backup')
    restore(root/'backup',root/'restored')
    results=[];verified=0
    with local_manager(root/'restored') as manager,HistoryJobs(manager,provider=BinanceHistory(Exchange()),start=False) as history:
        with LiveSessions(manager,history,start=False) as live:
            pump(live,identifiers)
            maintenance=MarketMaintenance(manager,live=live)
            if maintenance.preview(int(time.time()*1000),grace_seconds=0)['apply_allowed']:raise AssertionError('Active sessions were not protected.')
            for version,identifier in zip(args.versions,identifiers,strict=True):
                verified+=verify_rows(live,identifier,args.minutes)
                state=live.status(identifier)
                if state['mapper']['gaps']!=1 or state['mapper']['recovered_bars']!=1:raise AssertionError('Missing candle recovery was not explicitly accounted.')
                live.stop(identifier)
                results.append({'version':version,'recording':Recordings(manager).export(identifier,root/f'recording-v{version}'),
                    'expected':logical_result(manager,state['replay'])})
            maintenance.rotate();segments=maintenance.segments();audit(manager.db,manager.directory)
    for result in results:
        with patch('urllib.request.urlopen',side_effect=AssertionError('Offline replay attempted network.')):
            with local_manager(root/f"offline-v{result['version']}") as manager:
                receipt=Recordings(manager).replay(root/f"recording-v{result['version']}",batch_size=10000)
                if receipt['result']!=result['expected']:raise AssertionError('Offline published result differs.')
                result['offline_match']=receipt['status']=='passed'
    report={'schema_version':'openpine.session-continuity.1','python':sys.version.split()[0],'passed':True,
        'source':'synthetic_exchange_with_real_native_workers','model_minutes':args.minutes,'warmup_minutes':WARMUP,
        'wall_seconds':round(time.monotonic()-started,3),'versions':args.versions,'shared_feeds':len(ready['feeds']),
        'real_coordinator_kill':True,**worker,'backup_restored':True,'lost_socket_candles':1,
        'coordinator_cut':ready['coordinator_cut'],
        'intrabar_reconstructed':False,'independently_verified_output_values':verified,'segments':segments,
        'results':results,'all_offline_outputs_equal':True}
    report['results_sha256']=digest([r['expected'] for r in results]);atomic_json(root/'report.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='results'},ensure_ascii=False),flush=True)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',required=True,type=Path)
    parser.add_argument('--minutes',type=int,default=1440);parser.add_argument('--versions',default='1,2,3,4,5,6')
    parser.add_argument('--child',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--parent-pid',type=int,default=0,help=argparse.SUPPRESS)
    args=parser.parse_args(argv);args.versions=[int(value) for value in args.versions.split(',')]
    if sys.version_info[:3]!=(3,14,8):parser.error('Continuity acceptance requires Python 3.14.8.')
    if not 120<=args.minutes<=10080 or args.minutes%60:parser.error('Use 120..10080 model minutes, aligned to hours.')
    if not args.versions or len(set(args.versions))!=len(args.versions) or any(v not in range(1,7) for v in args.versions):parser.error('Use distinct Pine versions 1..6.')
    if args.child:child(args)
    else:run(args)
    return 0


if __name__=='__main__':raise SystemExit(main())
