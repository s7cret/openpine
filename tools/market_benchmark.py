"""Bounded synthetic history acceptance with a real coordinator kill/restart."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from apps.openpine.bootstrap import local_services
from apps.openpine.history_service import HistoryJobs
from apps.worker.__main__ import watch_parent
from packages.contracts.market import InstrumentKey,HistoryRequest
from packages.contracts.wire import atomic_json,read_json
from packages.execution.resources import resident_bytes
from packages.marketdata.history import BinanceHistory

ROOT=Path(__file__).resolve().parents[1]
KEY=InstrumentKey('binance','spot','BTCUSDT')


def candle(index):
    timestamp=index*60000;price=100.+index%97/100
    return [timestamp,str(price),str(price+1),str(price-1),str(price+.5),str(100+index%31),timestamp+59999,'0',1,'0','0','0']


class Exchange:
    def __init__(self,count,*,pause_at=None,ready=None):self.count,self.pause_at,self.ready=count,pause_at,ready;self.starts=[]
    def __call__(self,path,params,*,control=None):
        if control:control.check()
        if path.endswith('/time'):return {'serverTime':(self.count+1)*60000}
        if path.endswith('/exchangeInfo'):
            return {'symbols':[{'symbol':'BTCUSDT','baseAsset':'BTC','quoteAsset':'USDT','status':'TRADING',
                'filters':[{'filterType':'PRICE_FILTER','tickSize':'0.01'},{'filterType':'LOT_SIZE','stepSize':'0.00001'}]}]}
        first=(params['startTime']+59999)//60000;self.starts.append(params['startTime'])
        if self.pause_at is not None and first>=self.pause_at:
            atomic_json(self.ready,{'blocked_at':first,'pid':os.getpid()})
            while True:time.sleep(.1)  # The parent deliberately kills this fixture process.
        last=min(self.count,params['endTime']//60000+1,first+params['limit'])
        return [candle(index) for index in range(first,last)]


def child(args):
    threading.Thread(target=watch_parent,args=(args.parent_pid,),daemon=True).start()
    with local_services(args.directory) as services:
        exchange=Exchange(args.bars,pause_at=args.kill_after,ready=args.directory/'ready.json')
        jobs=HistoryJobs(services,provider=BinanceHistory(exchange),start=False)
        task=jobs.submit(HistoryRequest(KEY,'1',0,args.bars*60000).payload())
        atomic_json(args.directory/'task.json',task)
        jobs.process(task['task_id'])


def run(args):
    root=args.directory.resolve()
    if root.exists():raise ValueError('Use a new empty acceptance directory.')
    root.mkdir(parents=True)
    started=time.monotonic();process=None
    command=[sys.executable,'-X','utf8','-m','tools.market_benchmark','--directory',str(root),
        '--bars',str(args.bars),'--kill-after',str(args.kill_after),'--child','--parent-pid',str(os.getpid())]
    with (root/'child.log').open('wb') as log:
        try:
            process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=log,stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            deadline=time.monotonic()+180
            while not (root/'ready.json').exists():
                if process.poll() is not None:raise AssertionError('Acquisition child exited before its durable checkpoint.')
                if time.monotonic()>deadline:raise TimeoutError('Acquisition child did not reach its pause point.')
                time.sleep(.05)
            before_kill=resident_bytes(process.pid)
            process.kill();process.wait(timeout=10)
        finally:
            if process is not None and process.poll() is None:process.kill();process.wait(timeout=10)
    task=read_json(root/'task.json')['task_id']
    with local_services(root) as services:
        original=HistoryJobs(services,provider=BinanceHistory(Exchange(args.bars)),start=False)
        recovered=original.status(task)
        if recovered['status']!='queued' or recovered['bars']!=args.kill_after:raise AssertionError(recovered)
        cursor=recovered['cursor'];as_of=recovered['as_of']
        done=original.process(task)
        if done['status']!='succeeded' or done['bars']!=args.bars or not done['coverage']['confirmed_complete']:
            raise AssertionError(done)
        if original.provider.transport.starts[0]!=cursor or done['as_of']!=as_of:raise AssertionError('Fetch did not resume its pinned cursor/as_of.')
        source=services.market.source(done['snapshot_id']);count=0
        for index,bar in enumerate(source):
            expected=candle(index)
            if (bar.time!=expected[0] or tuple(getattr(bar,k) for k in ('open','high','low','close','volume'))
                    !=tuple(float(value) for value in expected[1:6])):raise AssertionError('Restored series differs from the independent candle model.')
            count+=1
        if count!=args.bars:raise AssertionError('Candle count differs after real coordinator death.')
        services.db.check()
        report={'schema_version':'openpine.market-acceptance.1','python':sys.version.split()[0],
            'bars':count,'kill_after_durable_bars':recovered['bars'],'resumed_cursor':cursor,
            'snapshot_id':done['snapshot_id'],'parts':len(services.market.manifest(done['snapshot_id'])['parts']),
            'child_rss_bytes_at_kill':before_kill,'parent_rss_bytes':resident_bytes(os.getpid()),
            'same_as_of':True,'coverage':done['coverage'],'elapsed_seconds':round(time.monotonic()-started,3),
            'source':'synthetic_exchange_page_fixture','real_coordinator_kill':True,'passed':True}
        report['all_ohlcv_fields_verified']=True
    atomic_json(root/'report.json',report);print(json.dumps(report,ensure_ascii=False));return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--bars',type=int,default=100000)
    parser.add_argument('--kill-after',type=int,default=30000)
    parser.add_argument('--child',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--parent-pid',type=int,default=0,help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    if sys.version_info[:3]!=(3,14,8):parser.error('Acceptance requires Python 3.14.8.')
    if not 10000<=args.kill_after<args.bars<=10000000 or args.kill_after%10000:parser.error('Use 10k-aligned kill-after below bars, maximum ten million.')
    if args.child:child(args)
    else:run(args)
    return 0


if __name__=='__main__':raise SystemExit(main())
