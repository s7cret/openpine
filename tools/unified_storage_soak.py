"""Composed streaming proof across two exited worker processes and disk state."""
import argparse,json,math,os,subprocess,sys,time
from pathlib import Path
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.contexts import MarketContext,ContextBinding
from packages.contracts.memory import job_memory
from packages.contracts.wire import atomic_json,read_json
from packages.execution.session import Session
from packages.execution.identity import implementation_hash,build_identity
from packages.storage.objects import ObjectStore
from packages.storage.results import ResultSink
from packages.storage.columnar import connection
from tools.history_storage_soak import Source,expected,peak_rss

def source(bars):
    from tools.history_storage_soak import source as signals_source
    text=signals_source(bars)
    text=text.replace('ta.lowestbars(close,length())]',
        'ta.lowestbars(close,length()),ta.sma(close,length()),close[1],ta.change(close,3)]')
    for prefix in ('p','a','b'):
        text=text.replace(','.join(prefix+str(i) for i in range(7)),','.join(prefix+str(i) for i in range(10)))
        text=text.replace(f'plot({prefix}6)\n',f'plot({prefix}6)\n'+''.join(f'plot({prefix}{i})\n' for i in range(7,10)))
    return text

class CheckedSink:
    def __init__(self,sink,bars,counts=None):
        self.sink,self.bars=sink,bars;self.counts=counts or {};self.points=0
    def emit(self,site,title,typ,value,index,timestamp):
        column=list(self.counts).index(site) if site in self.counts else len(self.counts)
        if index!=self.counts.get(site,0) or timestamp!=index*60000:raise AssertionError('Dropped/duplicate output cursor.')
        wanted=oracle(index,column,self.bars)
        if not (value is None if wanted is None else value is not None and math.isclose(value,wanted,rel_tol=1e-12,abs_tol=1e-12)):
            raise AssertionError((index,column,value,wanted))
        self.sink.emit(site,title,typ,value,index,timestamp);self.counts[site]=index+1;self.points+=1

def oracle(index,column,bars):
        base=(0,100,200)[column//10];n=4096 if index>=bars//2 else index%4+1
        signals=expected(index,base,n)
        return (*signals,signals[0],float(base+(index-1)%32+1) if index else None,expected(index,base,3)[1])[column%10]

def verify_persisted(objects,summary,bars,*,columns=30):
    from packages.contracts.wire import loads
    sites=[row['output_id'] for row in summary['outputs']];totals={site:0 for site in sites};rows=0
    if len(sites)!=columns:raise AssertionError('Wrong persisted output inventory.')
    with connection(memory_mb=64) as db:
        for block in summary['series']:
            cursor=db.execute('SELECT output_id,bar_index,time,value_json FROM read_parquet(?) ORDER BY bar_index,output_id',[str(objects.verify(block['object_id']))])
            while batch:=cursor.fetchmany(2048):
                for site,index,timestamp,raw in batch:
                    column=sites.index(site)
                    if index!=totals[site] or timestamp!=index*60000:raise AssertionError('Persisted output gap/duplicate/time.')
                    wanted=oracle(index,column,bars);value=loads(raw)
                    if not (value is None if wanted is None else value is not None and math.isclose(value,wanted,rel_tol=1e-12,abs_tol=1e-12)):
                        raise AssertionError(('persisted',index,column,value,wanted))
                    totals[site]+=1;rows+=1
    if set(totals.values())!={bars} or rows!=columns*bars:raise AssertionError('Incomplete persisted oracle range.')
    return rows

def phase(root,bars,part):
    start=time.monotonic();session=Session(profile_id=HISTORICAL_PROFILE_ID)
    program=session.compile(source(bars));objects=ObjectStore(root/'objects')
    sink=ResultSink(objects,root/part,memory_mb=job_memory(512)['per_io_mb'])
    saved=read_json(root/'restart.json') if part=='second' else None
    checked=CheckedSink(sink,bars,dict(saved['counts']) if saved else None)
    contexts=[MarketContext(k,s,'1','generated.1') for k,s in [('primary','TEST:P'),('a','TEST:A'),('b','TEST:B')]]
    sources=[Source(c,bars,b) for c,b in zip(contexts,(0,100,200))]
    driver=session.stream_driver(program,sources[0],checked,primary_context=contexts[0],
        context_sources=tuple((ContextBinding(c,s.identity.sha256),s) for c,s in zip(contexts[1:],sources[1:])))
    try:
        if saved:
            if saved['implementation']!=implementation_hash():raise AssertionError('Changed implementation during restart.')
            sink.restore(saved['output'],saved['cut']);driver.restore(saved['runtime'])
        if part=='first':
            cut=bars//2-1;driver.run(pause_after=cut,checkpoint=lambda e:None)
            atomic_json(root/'restart.json',dict(runtime=driver.checkpoint(),output=sink.checkpoint(cut),
                counts=list(checked.counts.items()),cut=cut,implementation=implementation_hash()))
        else:
            driver.run();summary=sink.finish(driver.finish_stream(sources[0].identity))
            if len(checked.counts)!=30 or set(checked.counts.values())!={bars}:raise AssertionError('Incomplete output prefix.')
            atomic_json(root/'summary.json',summary)
            # Read every sealed block back through DuckDB. Distinct composite
            # keys plus per-site complete ranges prove no file duplication/gap.
            totals={};rows=0
            with connection(memory_mb=64) as db:
                for block in summary['series']:
                    path=str(objects.verify(block['object_id']))
                    for site,count,unique,lo,hi in db.execute('SELECT output_id,count(*),count(DISTINCT bar_index),min(bar_index),max(bar_index) FROM read_parquet(?) GROUP BY output_id',[path]).fetchall():
                        if count!=unique:raise AssertionError('Duplicate persisted row.')
                        current=totals.setdefault(site,0)
                        if lo!=current or hi-lo+1!=count:raise AssertionError('Gap/overlap in persisted output blocks.')
                        totals[site]+=count;rows+=count
            if totals!=checked.counts or rows!=30*bars:raise AssertionError('Persisted rows differ from checked outputs.')
            verify_persisted(objects,summary,bars)
        value=dict(status='passed',part=part,pid=os.getpid(),python=sys.version.split()[0],
            points_checked=checked.points,completed=driver.evaluation.count,seconds=round(time.monotonic()-start,3),
            peak_resident_bytes=peak_rss(),memory=driver.evaluation.memory_status(),
            implementation_sha256=implementation_hash(),build=build_identity())
        atomic_json(root/(part+'.json'),value)
    finally:sink.close()

def run(directory,bars=333334):
    if sys.version_info[:3]!=(3,14,8) or type(bars) is not int or bars<8:raise ValueError('Use Python 3.14.8 and at least eight bars.')
    root=Path(directory).resolve();root.mkdir(parents=True,exist_ok=False);start=time.monotonic()
    for part in ('first','second'):
        with (root/(part+'.log')).open('w',encoding='utf-8') as log:
            subprocess.run([sys.executable,'-X','utf8','-m','tools.unified_storage_soak','--directory',str(root),'--bars',str(bars),'--phase',part],
                stdout=log,stderr=subprocess.STDOUT,check=True,timeout=7200)
    a,b=(read_json(root/(p+'.json')) for p in ('first','second'))
    if a['pid']==b['pid'] or a['pid']==os.getpid() or a['build']!=b['build']:raise AssertionError('Worker process was not replaced.')
    points=a['points_checked']+b['points_checked']
    if points!=bars*30:raise AssertionError('Wrong observation count.')
    value=dict(status='passed',schema_version='openpine.unified-storage-soak.1',python=sys.version.split()[0],
        events=3*bars,primary_bars=bars,private_contexts=2,signals=7,additional=['sma','history[]','change'],
        worker_pids=[a['pid'],b['pid']],process_restarted=True,verified_output_points=points,persisted_output_points=points,
        checkpoint_bytes=(root/'restart.json').stat().st_size,seconds=round(time.monotonic()-start,3),
        peak_resident_bytes=max(a['peak_resident_bytes'],b['peak_resident_bytes']),
        peak_charged_bytes=max(p['memory']['ledger']['peak_charged_bytes'] for p in (a,b)),
        implementation_sha256=b['implementation_sha256'],build=b['build'],
        oracle='Every online and persisted Parquet value checked against cyclic integer/rational oracle, including complete unique per-site ranges.')
    atomic_json(root/'report.json',value);return value

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--bars',type=int,default=333334);parser.add_argument('--phase',choices=('first','second'))
    args=parser.parse_args(argv)
    if args.phase:phase(args.directory,args.bars,args.phase)
    else:print(json.dumps(run(args.directory,args.bars)))
    return 0
if __name__=='__main__':raise SystemExit(main())
