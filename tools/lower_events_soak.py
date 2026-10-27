"""One million closed market events, durable restart and independent full-series oracle."""
import argparse,json,time
from dataclasses import asdict
from pathlib import Path
from apps.openpine.bootstrap import local_manager
from apps.openpine.replay import Replays
from packages.contracts.contexts import MarketContext
from packages.contracts.realtime import MarketEvent
from packages.contracts.storage import ResourcePolicy
from packages.contracts.wire import atomic_json
from packages.execution.identity import implementation_hash,build_identity
from packages.storage.results import series_rows
from tools.lower_tf_soak import source,expected

def run(directory,count,*,program_source=None,inputs=None):
    root=Path(directory).resolve()
    if root.exists() or type(count) is not int or count<32 or count%32:raise ValueError('Use a new directory and a multiple of 32 lower closes.')
    root.mkdir(parents=True);started=time.monotonic();frozen=implementation_hash();build=build_identity();parents=count//4
    contexts=[MarketContext('primary','TEST:CHART','4','lower-event-soak.1'),MarketContext('lower','TEST:LOWER','1','lower-event-soak.1')]
    policy=ResourcePolicy(workers=1,total_memory_mb=512)
    with local_manager(root/'store',policy=policy) as manager:
        replay=Replays(manager);key=replay.create({'input_ref':manager.freeze_input({'source':program_source or source(count),'source_name':'lower-events-growth.pine'}),
            'contexts':[asdict(c) for c in contexts],'primary_id':'primary','broker_settings':None,'timeout_seconds':3600,
            'lower_tf':{'schema_version':'openpine.lower-events.1','late':'reject','warmup':{'lower':{'count':count,'end_ms':count*60000}}},
            **({'inputs':inputs} if inputs is not None else {})})['replay_id']
        batch=[];seq=0
        for i in range(count):
            t=i*60000;p=float(i%32+1);batch.append(MarketEvent(seq,'lower','close',t,t+60000,0,p,p,p,p,10.).payload());seq+=1
            if i%4==3:
                t=i//4*240000;batch.append(MarketEvent(seq,'primary','close',t,t+240000,0,100.,100.,100.,100.,10.).payload());seq+=1
            if len(batch)>=8000:replay.append(key,batch);batch=[]
        if batch:replay.append(key,batch)
        print(f'Accepted {seq} events; starting partial real worker.',flush=True)
        cut=seq//2+2;prefix=replay.advance(key,max_events=cut,checkpoint_every=10000)
        assert prefix['sequence']==cut-1 and prefix['lower_tf']['pending_cells']==6
        resumed_from=prefix['confirmed_bars'];peak=replay.last_metrics['peak_rss_sampled_bytes']
    with local_manager(root/'store',policy=policy) as manager:
        replay=Replays(manager);final=replay.advance(key,checkpoint_every=10000);peak=max(peak,replay.last_metrics['peak_rss_sampled_bytes'])
        assert final['confirmed_bars']==parents and final['pending_events']==0 and final['lower_tf']['pending_cells']==0
        assert peak<400*1024**2,peak
        indices={row['output_id']:i for i,row in enumerate(final['outputs'])};verified=0
        summary={'observation_count':parents,'series':final['series']}
        for index,t,site,value in series_rows(manager.objects,summary):
            assert t==index*240000 and value==expected(index)[indices[site]],(index,site,value)
            verified+=1
        assert verified==parents*4 and implementation_hash()==frozen and build_identity()==build
        report={'schema_version':'openpine.lower-events-soak.1','status':'passed','lower_closes':count,'events':seq,'parent_bars':parents,
            'verified_output_points':verified,'resumed_from_bar':resumed_from,'coordinator_restart':True,'pending_cells_at_restart':6,
            'fresh_worker':True,'peak_resident_bytes':peak,'seconds':round(time.monotonic()-started,3),'implementation_sha256':frozen,'build':build,
            'oracle':'Every point checked against cyclic and triangular formulas; no execution engine used for expectations.',
            **({'context_graph':final['context_graph']} if final.get('context_graph') else {})}
    atomic_json(root/'report.json',report);return report

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--lower-candles',type=int,default=800000);a=p.parse_args(argv)
    print(json.dumps(run(a.directory,a.lower_candles)));return 0
if __name__=='__main__':raise SystemExit(main())
