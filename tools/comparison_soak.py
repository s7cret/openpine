"""Stream real Parquet publications without Pine execution; report sampled RSS and exact counts."""
import argparse,copy,tempfile,time,threading,os
from pathlib import Path
from types import SimpleNamespace
from packages.contracts.wire import atomic_json,digest
from packages.storage.objects import ObjectStore
from packages.storage.columnar import connection,quoted
from packages.execution.resources import resident_bytes
from apps.openpine.comparison_engine import compare

def run(count,root):
    root=Path(root);objects=ObjectStore(root/'objects');parts=[];block=100000
    for start in range(0,count,block):
        end=min(count,start+block);path=root/'part.parquet'
        with connection(memory_mb=32) as db:
            db.execute(f"COPY (SELECT i AS bar_index,i*60000 AS time,'owned:0' AS output_id,CAST(i%97 AS VARCHAR)||'.0' AS value_json FROM range({start},{end}) t(i)) TO {quoted(path)} (FORMAT PARQUET, COMPRESSION ZSTD)")
        parts.append({'object_id':objects.publish(path),'start':start,'end':end,'rows':end-start});path.unlink()
    side={'publication':{'series':parts},'outputs':[{'output_id':'owned:0'}],'bars':count,'program_sha256':'owned'}
    initial={'status':'running','cursor':{'channel':'rows','index':0},'checked':{'rows':0,'events':0,'statistics':0},'chains':{'left':digest(None),'right':digest(None)},'compatibility':{'status':'comparable'}}
    peak=[resident_bytes(os.getpid())];stop=threading.Event()
    def sample():
        while not stop.wait(.05):peak[0]=max(peak[0],resident_bytes(os.getpid()))
    thread=threading.Thread(target=sample,daemon=True);thread.start();started=time.monotonic();saved=[]
    try:
        state=compare(SimpleNamespace(directory=root,objects=objects),'a'*32,[side,side],copy.deepcopy(initial),lambda value:saved.__setitem__(slice(None),[copy.deepcopy(value)]),lambda:False)
    finally:stop.set();thread.join()
    assert state['status']=='equal' and state['checked']=={'rows':count,'events':0,'statistics':1} and state['chains']['left']==state['chains']['right']
    return {'bars':count,'status':'passed','checked':state['checked'],'seconds':time.monotonic()-started,'sampled_peak_rss_bytes':peak[0],'chain':state['chains']['left'],'parts':len(parts),'scope':'Parquet comparison only; no Pine execution or TradingView oracle.'}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bars',type=int,nargs='+',default=[100000,1000000,10000000]);p.add_argument('--output',type=Path,required=True);a=p.parse_args(argv)
    if any(not 1<=n<=10000000 for n in a.bars):p.error('Use 1..10000000 bars.')
    results=[]
    for count in a.bars:
        with tempfile.TemporaryDirectory(prefix='openpine-comparison-') as temporary:results.append(run(count,temporary))
        atomic_json(a.output,{'status':'running' if len(results)<len(a.bars) else 'passed','results':results});print(f'Compared {count} bars: {results[-1]["seconds"]:.2f}s; peak RSS {results[-1]["sampled_peak_rss_bytes"]}',flush=True)
    return 0
if __name__=='__main__':raise SystemExit(main())
