"""Measure finite replay dispatch costs without changing worker budgets."""
import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path
from dataclasses import asdict

from apps.openpine.bootstrap import local_manager
from apps.openpine.replay import Replays
from apps.openpine.recordings import logical_result
from packages.contracts.contexts import MarketContext
from packages.contracts.live import RECEIVED_PROFILE
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import atomic_json
from packages.execution.identity import build_identity


def run(root,sessions,rounds,mode):
    if root.exists():raise ValueError('Benchmark requires a new directory.')
    with local_manager(root) as manager:
        replay=Replays(manager);ids=[]
        for index in range(sessions):
            ref=manager.freeze_input({'source':f'//@version=6\nindicator("session {index}")\nvar n=0\nn:=n+1\nplot(n)\nplot(ta.ema(close,2))','source_name':'dispatch.pine'})
            ids.append(replay.create({'input_ref':ref,'contexts':[asdict(MarketContext('primary','TEST:DISPATCH','1','recorded.1'))],
                'primary_id':'primary','broker_settings':None,'timeout_seconds':60,'event_profile':RECEIVED_PROFILE})['replay_id'])
        started=time.monotonic();cpu=time.process_time();latencies=[];launches=0;child_cpu=0.;peak=0;batch_metrics=[]
        for round_index in range(rounds):
            appended={}
            for index,identifier in enumerate(ids):
                stamp=round_index*60000;price=100.+index+round_index
                events=[MarketEvent(round_index*2+revision,'primary',kind,stamp,90000000+round_index*10+revision,revision,
                    price,price+1,price-1,price+.5,float(revision+1)).payload() for revision,kind in enumerate(('open','close'))]
                replay.append(identifier,events);appended[identifier]=time.monotonic()
            if mode=='cold':
                for identifier in ids:
                    replay.advance(identifier,max_events=1000,checkpoint_every=1000)
                    latencies.append(time.monotonic()-appended[identifier]);metrics=replay.last_metrics
                    launches+=metrics['worker_launches'];child_cpu+=metrics.get('child_cpu_seconds',0.);peak=max(peak,metrics['peak_rss_sampled_bytes'])
            else:
                report=replay.advance_many(ids,max_events=1000,checkpoint_every=1000);metrics=report['metrics'];batch_metrics.append(metrics)
                for row in report['results']:
                    if row['status']!='succeeded':raise AssertionError(row)
                    latencies.append(row['finished_tick']-appended[row['replay_id']])
                launches+=metrics['worker_launches'];child_cpu+=metrics['child_cpu_seconds'];peak=max(peak,metrics['peak_rss_sampled_bytes'])
        elapsed=time.monotonic()-started
        hashes=[logical_result(manager,replay.status(identifier)) for identifier in ids]
        report={'schema_version':'openpine.live-dispatch-benchmark.1','mode':mode,'python':sys.version.split()[0],
            'build':build_identity(),'sessions':sessions,'rounds':rounds,'events':sessions*rounds*2,
            'wall_seconds':round(elapsed,6),'parent_cpu_seconds':round(time.process_time()-cpu,6),'child_cpu_seconds':round(child_cpu,6),
            'worker_launches':launches,'worker_peak_rss_sampled_bytes':peak,'latency_p50_seconds':statistics.median(latencies),
            'latency_p95_seconds':sorted(latencies)[math.ceil(.95*len(latencies))-1],
            'results':hashes,'batch_metrics':batch_metrics,'network_required':False}
    atomic_json(root/'report.json',report);return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True);parser.add_argument('--sessions',type=int,default=8)
    parser.add_argument('--rounds',type=int,default=3);parser.add_argument('--mode',choices=('cold','batch','both'),default='both')
    args=parser.parse_args(argv)
    if sys.version_info[:3]!=(3,14,8):parser.error('Use Python 3.14.8.')
    if not 1<=args.sessions<=8 or not 1<=args.rounds<=100:parser.error('Use sessions 1..8 and rounds 1..100.')
    reports=[run(args.directory/mode,args.sessions,args.rounds,mode) for mode in (('cold','batch') if args.mode=='both' else (args.mode,))]
    if len(reports)==2 and reports[0]['results']!=reports[1]['results']:raise AssertionError('Dispatch optimization changes logical outputs.')
    print(json.dumps([{k:v for k,v in r.items() if k not in ('results','batch_metrics')} for r in reports]));return 0


if __name__=='__main__':raise SystemExit(main())
