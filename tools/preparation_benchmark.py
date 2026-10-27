"""Profile cold/warm admission and preparation of 1/32/256 frozen variants."""
from pathlib import Path
import sys,time,json,cProfile,collections,contextlib
from unittest.mock import patch
from tools.specification import ROOT as R
from apps.openpine.bootstrap import local_manager
from apps.openpine.experiments import Experiments
from packages.execution.session import Session
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.wire import atomic_json,digest
from tools.prepared_requests_spec import source,datasets,context
from packages.marketdata.providers import make_snapshot
from tools.ci_artifacts import source_identity
import packages.execution.session as sessions
import packages.execution.preparation as preparation
import packages.execution.artifacts as artifacts
from packages.compiler.semantic import snapshot_cache
metrics=[];counts=collections.Counter();bytes_read=0
read_bytes=Path.read_bytes;load=sessions.load_snapshot;import_program=artifacts.import_program
def counted_read(path):
 global bytes_read
 raw=read_bytes(path)
 if path.is_relative_to(R/'spec'):counts['spec_reads']+=1;bytes_read+=len(raw)
 return raw
def counted_load(*a,**kw):counts['snapshot_requests']+=1;return load(*a,**kw)
def counted_import(*a,**kw):counts['program_decodes']+=1;return import_program(*a,**kw)
def measure(name,action):
 global bytes_read
 counts.clear();bytes_read=0;prof=cProfile.Profile();start=time.perf_counter();prof.enable()
 value=action();prof.disable();prof.dump_stats(str(out/(name+'.prof')))
 row={'phase':name,'seconds':round(time.perf_counter()-start,6),'counts':dict(counts),'spec_bytes_read':bytes_read,'logical':value,'cache':snapshot_cache.info()}
 metrics.append(row);atomic_json(out/'metrics.json',{'source':source_identity(),'metrics':metrics});print(json.dumps({k:v for k,v in row.items() if k!='logical'}),flush=True)
 return value
def main(argv=None):
    global out
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);args=p.parse_args(argv)
    out=args.directory.resolve();out.mkdir();snapshot_cache.clear();metrics.clear()
    with contextlib.ExitStack() as stack:
     stack.enter_context(patch.object(Path,'read_bytes',counted_read));stack.enter_context(patch.object(sessions,'load_snapshot',counted_load))
     stack.enter_context(patch.object(preparation,'import_program',counted_import,create=True));stack.enter_context(patch.object(artifacts,'import_program',counted_import))
     measure('session-cold',lambda:len(Session(profile_id=HISTORICAL_PROFILE_ID).catalog.contracts))
     measure('sessions-warm',lambda:[len(Session(profile_id=HISTORICAL_PROFILE_ID).catalog.contracts) for _ in range(3)])
     with local_manager(out/'state') as m:
      data=datasets();snapshots={k:m.market.save(make_snapshot(d,{'provider':'uploaded-file.1'})) for k,d in data.items()}
      text=source(6).replace('plot(request.security(sym,tf,close))','scale=input.int(1)\nplot(request.security(sym,tf,close)*scale)')
      ref=measure('compile',lambda:m.freeze_input({'source':text,'source_name':'preparation-benchmark.pine'}))
      e=Experiments(m,start=False)
      base={'name':'Preparation growth','input_ref':ref,'snapshot_id':snapshots['parent'],'timeout_seconds':120,
       'primary_context':context(data['parent']),'contexts':[{'context':context(data[k]),'snapshot_id':snapshots[k]} for k in ('a1','a2','b1','b2')]}
      try:
       for n in (1,32,256):
        def action(n=n):
         manifest,prepared=e.prepare({**base,'variants':[{'scale':i+1,'sym':'TEST:B' if i%2 else 'TEST:A','tf':'1' if i%2 else '2'} for i in range(n)]})
         return {'variants':n,'graphs':len(manifest['context_graphs']),'settings':[p.settings['values'] for _,p in prepared],
          'selected_snapshots':[[b.snapshot_id for b in p.contexts] for _,p in prepared]}
        measure('variants-'+str(n),action)
      finally:e.close()
    return 0

if __name__=='__main__':raise SystemExit(main())
