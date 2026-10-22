"""CLI for persisted history acquisition and finite live paper sessions."""

import argparse
import sys
import time
import apsw
from pathlib import Path
from dataclasses import asdict

from packages.contracts.wire import canonical, read_json
from apps.openpine.bootstrap import local_manager
from apps.openpine.history_service import HistoryJobs
from apps.openpine.live_service import LiveSessions
from packages.contracts.market import InstrumentKey
from packages.contracts.calendar import shift_open
from packages.marketdata.timeframes import EXECUTION,floor_open


def live_payload(args,manager,history):
    if args.request:return read_json(args.request)
    if not (args.source or args.artifact):raise ValueError('Live run requires --source, --artifact or --request.')
    key=InstrumentKey.from_pine(args.symbol)
    if args.timeframe not in EXECUTION or not 1<=args.warmup_bars<=100000:raise ValueError('Unsupported live interval/warmup count.')
    if history.repository.instrument(key) is None:
        result=history.wait(history.refresh_catalog()['task_id'],timeout=120)
        if result['status']!='succeeded':raise ValueError('Catalogue discovery failed: '+str(result['error']))
    if args.source:
        with args.source.open('rb') as stream:raw=stream.read(65537)
        if len(raw)>65536:raise ValueError('Pine source exceeds 64 KiB.')
        content={'source':raw.decode('utf-8'),'source_name':args.source.name}
    else:
        from packages.execution.artifacts import read_program,export_program
        content={'artifact':export_program(read_program(args.artifact))}
    start=args.start_ms
    if start is None:
        try:start=shift_open(int(time.time()*1000),args.timeframe,-args.warmup_bars)
        except ValueError:
            from packages.marketdata.timeframes import first_open
            start=first_open(0,args.timeframe)
    return {'input_ref':manager.freeze_input(content),'instrument':asdict(key),'timeframe':args.timeframe,'start_ms':start,
        'broker_settings':None,'timeout_seconds':args.timeout,'gap_policy':args.gap_policy,'fresh_feed':args.fresh_feed}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('area',choices=('catalog','history','live','recording','maintenance'))
    parser.add_argument('action',choices=('list','refresh','run','submit','status','resume','cancel','stop','chart','ledger','journal','export','verify','replay','preview','apply','rotate','segments'))
    parser.add_argument('--state-dir',type=Path,default=Path('.runtime/jobs'))
    inputs=parser.add_mutually_exclusive_group()
    inputs.add_argument('--request',type=Path,help='Closed history-request.1 or live session JSON')
    inputs.add_argument('--source',type=Path)
    inputs.add_argument('--artifact',type=Path)
    parser.add_argument('--symbol',default='BINANCE:BTCUSDT')
    parser.add_argument('--timeframe',default='1')
    parser.add_argument('--start-ms',type=int)
    parser.add_argument('--warmup-bars',type=int,default=1000)
    parser.add_argument('--timeout',type=float,default=60)
    parser.add_argument('--gap-policy',choices=('fail','allow_with_metadata'),default='fail')
    parser.add_argument('--fresh-feed',action='store_true',help='Start a new immutable source generation after confirmed corrections')
    parser.add_argument('--id')
    parser.add_argument('--query',default='')
    parser.add_argument('--duration',type=float,default=60,help='Seconds for live run; Ctrl+C preserves and stops the session')
    parser.add_argument('--start',type=int,default=0)
    parser.add_argument('--limit',type=int,default=1000)
    parser.add_argument('--bundle',type=Path,help='Portable session recording directory')
    parser.add_argument('--batch-size',type=int,default=1000)
    parser.add_argument('--before-ms',type=int)
    parser.add_argument('--preview-id')
    parser.add_argument('--grace-seconds',type=int,default=3600)
    args=parser.parse_args(argv)
    if not 0<args.duration<=86400:parser.error('--duration requires 0..86400 seconds')
    try:
        if args.area=='maintenance':
            from apps.openpine.bootstrap import local_services
            from apps.openpine.market_maintenance import MarketMaintenance
            with local_services(args.state_dir) as services:
                maintenance=MarketMaintenance(services)
                if args.action=='preview':value=maintenance.preview(args.before_ms,grace_seconds=args.grace_seconds)
                elif args.action=='apply':value=maintenance.apply(args.before_ms,args.preview_id,grace_seconds=args.grace_seconds)
                elif args.action=='rotate':value=maintenance.rotate()
                elif args.action=='segments':value=maintenance.segments()
                else:raise ValueError('Maintenance supports preview/apply/rotate/segments.')
            sys.stdout.buffer.write(canonical(value)+b'\n');return 0
        with local_manager(args.state_dir) as manager,HistoryJobs(manager) as history:
            if args.area=='recording' or args.area=='live' and args.action=='export':
                from apps.openpine.recordings import Recordings,open_recording
                if not args.bundle:raise ValueError('Recording commands require --bundle.')
                if args.action=='export':value=Recordings(manager).export(args.id,args.bundle)
                elif args.action=='replay':value=Recordings(manager).replay(args.bundle,batch_size=args.batch_size)
                elif args.action=='verify':
                    recording,_,sha=open_recording(args.bundle)
                    value={'status':'verified','recording_id':sha,'published_events':recording['published_events']}
                else:raise ValueError('Recording supports export/verify/replay.')
            elif args.area=='catalog':
                if args.action=='refresh':value=history.wait(history.refresh_catalog()['task_id'])
                elif args.action=='list':value=history.catalog.search(args.query,min(args.limit,200))
                else:raise ValueError('Catalogue supports list/refresh.')
            elif args.area=='history':
                if args.action in ('run','submit'):
                    if not args.request:raise ValueError('History submission requires --request.')
                    value=history.submit(read_json(args.request))
                    if args.action=='run':value=history.wait(value['task_id'])
                elif args.action=='list':value=history.repository.tasks()
                elif args.action in ('status','resume','cancel'):
                    value=getattr(history,args.action)(args.id)
                    if args.action=='resume':value=history.wait(args.id)
                else:raise ValueError('Invalid history action.')
            else:
                with LiveSessions(manager,history,start=args.action in ('run','resume')) as live:
                    if args.action=='run':
                        value=live.create(live_payload(args,manager,history));identifier=value['session_id']
                    elif args.action=='resume':
                        value=live.resume(args.id);identifier=args.id
                    elif args.action=='list':value=live.repository.sessions()
                    elif args.action in ('status','stop'):value=getattr(live,args.action)(args.id)
                    elif args.action in ('chart','ledger'):value=live.view(args.id,args.action,args.start,args.limit)
                    elif args.action=='journal':
                        state=live.repository.session(args.id)
                        value=list(live.repository.records(state['feed_id'],args.start,min(args.limit,1000)))
                    else:raise ValueError('Invalid live action.')
                    if args.action in ('run','resume'):
                        deadline=time.monotonic()+args.duration
                        try:
                            while time.monotonic()<deadline:
                                value=live.status(identifier)
                                if value['status'] in ('failed','source_failed'):break
                                time.sleep(.2)
                        except KeyboardInterrupt:pass
                        value=live.stop(identifier)
        sys.stdout.buffer.write(canonical(value)+b'\n')
        return 2 if type(value) is dict and value.get('status') in ('failed','source_failed','cancelled') else 0
    except (ValueError,OSError,apsw.Error) as error:
        sys.stdout.buffer.write(canonical({'status':'rejected','diagnostics':[{'code':'MARKET_INVALID','message':str(error)}]})+b'\n');return 2
    except KeyboardInterrupt:
        sys.stdout.buffer.write(canonical({'status':'interrupted'})+b'\n');return 130


if __name__=='__main__':raise SystemExit(main())
