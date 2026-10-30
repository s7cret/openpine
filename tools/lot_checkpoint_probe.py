"""Offline diagnostic: separate driver execution, checkpoint export and durable IO.

This is not installed-worker qualification. The installed lots suite separately
records inclusive coordinator timings under its unchanged 120-second limit.
"""
import argparse,json,sys,tempfile,time
from pathlib import Path
from tools.specification import ROOT,require_python
from tools.lot_oracle import expected
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.inputs import settings_for
from packages.contracts.memory import job_memory
from packages.execution.session import Session
from packages.execution.driver import MemorySource
from packages.marketdata import read_bars
from packages.storage.objects import ObjectStore
from packages.storage.results import ResultSink,series_rows
from packages.storage import checkpoint_records


def probe(family,version,directory):
    session=Session(profile_id=HISTORICAL_PROFILE_ID)
    root=ROOT/'examples/lots';data=read_bars(root/f'{family}-bars.json')
    program=session.compile((root/f'{family}-v{version}.pine').read_text(),f'{family}-v{version}.pine')
    settings=settings_for(program,{'part':1});objects=ObjectStore(directory/'objects')
    limit=job_memory(512)['restore_reserve_bytes'];cut=5 if family=='conditional' else 3
    def make(name):
        sink=ResultSink(objects,directory/name)
        source=MemorySource(data);source.bind_identity()
        return session.stream_driver(program,source,sink,settings=settings),sink
    def result(driver,sink):
        value=sink.finish(driver.finish_stream(driver.source.identity))
        rows=list(series_rows(objects,value));outputs=[[] for _ in value['outputs']]
        indices={o['output_id']:i for i,o in enumerate(value['outputs'])}
        for index,timestamp,output_id,scalar in rows:outputs[indices[output_id]].append(scalar)
        if tuple(map(tuple,outputs))!=expected(family,1):raise AssertionError('Independent lot output oracle differs.')
        return value
    baseline,sink=make('baseline');started=time.perf_counter()
    try:
        baseline.run();baseline_seconds=time.perf_counter()-started;base=result(baseline,sink)
    finally:sink.close()
    phases={'export':0.,'runtime_encode_store_validate':0.,'output_export_encode_store_validate':0.,'restore':0.};roots=[]
    def save(driver):
        started=time.perf_counter();state=driver.checkpoint(streaming=True);now=time.perf_counter();phases['export']+=now-started
        runtime=checkpoint_records.put(objects,state,limit_bytes=limit*3//4);after=time.perf_counter();phases['runtime_encode_store_validate']+=after-now
        output=checkpoint_records.put(objects,driver.output_sink.checkpoint(driver.count),limit_bytes=limit//4)
        phases['output_export_encode_store_validate']+=time.perf_counter()-after;roots.append((runtime,output))
    first,sink=make('prefix');started=time.perf_counter()
    try:first.run(checkpoint=save,interval=1,pause_after=cut)
    finally:sink.close()
    prefix_seconds=time.perf_counter()-started;second,sink=make('resumed');started=time.perf_counter()
    try:
        runtime,output=roots[-1]
        second.restore(checkpoint_records.get(objects,runtime,limit_bytes=limit*3//4))
        sink.restore(checkpoint_records.get(objects,output,limit_bytes=limit//4),cut)
        phases['restore']=time.perf_counter()-started;started=time.perf_counter()
        second.run(checkpoint=save,interval=1);tail_seconds=time.perf_counter()-started;resumed=result(second,sink)
    finally:sink.close()
    if base['simulation']['statistics']!=resumed['simulation']['statistics']:raise AssertionError('Resumed lot statistics differ.')
    inclusive=prefix_seconds+tail_seconds
    return {'family':family,'version':version,'bars':len(data.bars),'checkpoint_interval_bars':1,'checkpoint_count':len(roots),
        'baseline_driver_seconds':round(baseline_seconds,6),'checkpointed_driver_seconds':round(inclusive,6),
        'checkpointed_execution_seconds':round(inclusive-sum(v for k,v in phases.items() if k!='restore'),6),
        'phases_seconds':{k:round(v,6) for k,v in phases.items()},'independent_oracle':True,'resume_at_bar':cut,
        'scope':'Same HistoricalDriver, ResultSink and checkpoint-record codec; excludes process startup, coordinator and manifest publication.'}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--family',choices=('fifo','any','conditional'),default='conditional')
    p.add_argument('--version',type=int,choices=range(1,7),default=3);p.add_argument('--output',type=Path,required=True);args=p.parse_args(argv)
    require_python()
    if sys.version_info[:3]!=(3,14,8):p.error('Requires Python 3.14.8.')
    with tempfile.TemporaryDirectory(prefix='openpine-lot-probe-') as directory:value=probe(args.family,args.version,Path(directory))
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(value));return 0


if __name__=='__main__':raise SystemExit(main())
