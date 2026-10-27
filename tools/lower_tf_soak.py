"""A million actual lower candles through Parquet, private UDF, arrays and restart."""
import argparse,json,time
from dataclasses import asdict
from pathlib import Path
from apps.openpine.bootstrap import local_manager
from packages.contracts import HistoricalBar
from packages.contracts.contexts import MarketContext
from packages.contracts.storage import ResourcePolicy
from packages.contracts.wire import atomic_json
from packages.execution.identity import implementation_hash,build_identity

def source(count):return f'''//@version=6
indicator("Lower growth")
accumulate(x) =>
    var float total=0
    total:=total+x
    total
[prices,totals,events]=request.security_lower_tf("TEST:LOWER","1",[close,accumulate(close),close>16],calc_bars_count={count})
plot(array.sum(prices),title="Sum")
plot(array.avg(totals),title="Cumulative mean")
plot(array.size(prices),title="Count")
plot(array.get(events,0) ? 1 : 0,title="Flag")
'''

def expected(index):
    cycle,k=divmod(index,8);s=4*k
    totals=[528*cycle+n*(n+1)//2 for n in range(s+1,s+5)]
    return (float(16*k+10),sum(totals)/4,4,1 if k>=4 else 0)

def run(directory,count):
    root=Path(directory).resolve()
    if root.exists() or type(count) is not int or count<32 or count%32:raise ValueError('Use a fresh directory and a multiple of 32 lower candles.')
    frozen=implementation_hash();build=build_identity();started=time.monotonic();parents=count//4
    policy=ResourcePolicy(workers=1,total_memory_mb=512,max_attempts=2)
    def wait(manager,key,status):
        end=time.monotonic()+3600
        while time.monotonic()<end:
            row=manager.get(key)
            if row['status']==status:return row
            if row['status'] in ('failed','timed_out','cancelled','succeeded','paused'):raise AssertionError(row)
            time.sleep(.1)
        raise TimeoutError(row)
    with local_manager(root/'jobs',policy=policy) as manager:
        def candles(n,tf):
            for i in range(n):
                p=float(i%32+1) if tf=='1' else 100.
                yield HistoricalBar(i*int(tf)*60000,p,p,p,p,10.)
        snapshots=[];contexts=[]
        for cid,symbol,tf,n in (('primary','TEST:CHART','4',parents),('lower','TEST:LOWER','1',count)):
            snapshots.append(manager.market.ingest({'context_id':cid,'symbol':symbol,'timeframe':tf,'bar_schema':'openpine.historical-bars.2'},
                candles(n,tf),{'provider':'lower-growth.1'}))
            contexts.append(asdict(MarketContext(cid,symbol,tf,'lower-growth.1')))
        request={'schema_version':'openpine.job-request.6','input_ref':manager.freeze_input({'source':source(count),'source_name':'lower-growth.pine'}),
            'snapshot_id':snapshots[0],'primary_context':contexts[0],'contexts':[{'context':contexts[1],'snapshot_id':snapshots[1]}],
            'inputs':{},'timeout_seconds':3600}
        key=manager.submit(request,checkpoint_interval_bars=10000,pause_after=parents//2)['job_id'];prefix=wait(manager,key,'paused')
    with local_manager(root/'jobs',policy=policy) as manager:
        manager.resume(key);final=wait(manager,key,'succeeded')
        assert final['resumed_from_bar']==parents//2 and final['peak_resident_bytes']<330*1024**2,final
        verified=0
        for start in range(0,parents,4096):
            chart=manager.chart(key,start=start,limit=min(4096,parents-start));columns=chart['outputs']
            assert len(columns)==4
            for offset,actual in enumerate(zip(*(o['values'] for o in columns),strict=True)):
                assert actual==expected(start+offset),(start+offset,actual,expected(start+offset));verified+=1
        assert verified==parents
        assert manager.plan(key).contexts[0].snapshot_id==snapshots[1]
        assert implementation_hash()==frozen and build_identity()==build
        report={'schema_version':'openpine.lower-tf-soak.1','status':'passed','lower_candles':count,'parent_bars':parents,
            'verified_parent_rows':verified,'verified_output_points':verified*4,'resumed_from_bar':final['resumed_from_bar'],
            'coordinator_restart':True,'fresh_worker':True,'peak_resident_bytes':final['peak_resident_bytes'],
            'seconds':round(time.monotonic()-started,3),'implementation_sha256':frozen,'build':build,
            'oracle':'All rows checked using independent cyclic and triangular integer formulas.'}
    atomic_json(root/'report.json',report);return report

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--lower-candles',type=int,default=1000000);a=p.parse_args(argv)
    print(json.dumps(run(a.directory,a.lower_candles)));return 0
if __name__=='__main__':raise SystemExit(main())
