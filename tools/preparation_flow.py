"""File -> saved IR -> worker -> durable replay -> offline replay, v1-v6."""
import argparse,json,time
from pathlib import Path
from dataclasses import asdict
from fractions import Fraction
from apps.openpine.bootstrap import local_manager
from apps.openpine.replay import Replays
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import read_json,atomic_json
from packages.marketdata.providers import make_snapshot
from tools.prepared_requests_spec import datasets,context

def source(v,lower=False):
    inp='input' if v<5 else 'input.string';integer='input' if v<5 else 'input.int';sma='sma' if v<5 else 'ta.sma';request='security' if v<5 else 'request.security'
    text=f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Preparation flow")\n'
    text+=f'sym={inp}("TEST:A")\ntf={inp}("'+('1' if lower else '2')+f'")\ngain={integer}(1)\nselected=sym\n'
    text+=f'filter(x) =>\n    var float total = 0\n    total := total + x\n    average = {sma}(x,2)\n    total + nz(average)\n'
    return text+('a=request.security_lower_tf(selected,tf,filter(close))\nplot(array.sum(a)*gain)\nplot(array.size(a))\n' if lower else f'plot({request}(selected,tf,filter(close))*gain)\n')

def expected():
    values=[Fraction(n) for n in range(100,108)]
    return [float((sum(values[:i+1])+(values[i-1]+p)/2 if i else p)*2) for i,p in enumerate(values)]

def finish(m,key,status='succeeded'):
    deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        row=m.get(key)
        if row['status']==status:return
        if row['status'] in ('failed','cancelled','timed_out'):raise AssertionError(row)
        time.sleep(.02)
    raise TimeoutError(row)

def run(directory):
    root=Path(directory).resolve();root.mkdir();rows=[];manual=expected();data=datasets()
    for v in range(1,7):
        work=root/f'v{v}';work.mkdir();pine=work/'source.pine';pine.write_text(source(v),encoding='utf-8')
        store=work/'store';events=[]
        for i in range(8):
            for cid,value in (('b1',float(100+i)),('primary',1.)):
                events.append(MarketEvent(len(events),cid,'close',i*60000,(i+1)*60000,0,value,value,value,value,1.).payload())
        tape=work/'events.ndjson';tape.write_text(''.join(json.dumps(e)+'\n' for e in events),encoding='utf-8')
        config={'contexts':[context(data[k]) for k in ('parent','b1')],'primary_id':'primary','inputs':{'sym':'TEST:B','tf':'1','gain':2},'broker_settings':None,'timeout_seconds':120}
        atomic_json(work/'contexts.json',config);atomic_json(work/'expected.json',manual)
        with local_manager(store) as m:
            frozen=m.freeze_input({'source':pine.read_text(encoding='utf-8'),'source_name':pine.name});content=m.objects.get(frozen)
            artifact=work/'program.json';atomic_json(artifact,m.objects.get(content['artifact_ref']))
            ref=m.freeze_input({'artifact':read_json(artifact)})
            snapshots={k:m.market.save(make_snapshot(d,{'provider':'uploaded-file.1'})) for k,d in data.items()}
            request={'schema_version':'openpine.job-request.6','input_ref':ref,'snapshot_id':snapshots['parent'],'primary_context':context(data['parent']),
                'contexts':[{'context':context(data[k]),'snapshot_id':snapshots[k]} for k in ('a2','b1')],'inputs':config['inputs'],'timeout_seconds':120}
            key=m.submit(request,pause_after=3)['job_id'];finish(m,key,'paused');m.resume(key);finish(m,key)
            assert m.result(key)['result']['outputs'][0]['values']==manual
            assert [b.snapshot_id for b in m.plan(key).contexts]==[snapshots['b1']]
            replay=Replays(m);rid=replay.create({'input_ref':ref,**config})['replay_id'];replay.append(rid,events)
            prefix=replay.advance(rid,max_events=7,checkpoint_every=2);assert prefix['confirmed_bars']==3
            if v>=5:
                lp=work/'lower.pine';lp.write_text(source(v,True),encoding='utf-8');lr=m.freeze_input({'source':lp.read_text(),'source_name':lp.name})
                lower={**request,'input_ref':lr,'snapshot_id':snapshots['lower-parent'],'primary_context':context(data['lower-parent'])}
                lk=m.submit(lower,pause_after=1)['job_id'];finish(m,lk,'paused');m.resume(lk);finish(m,lk)
                outputs=m.result(lk)['result']['outputs'];assert [o['values'] for o in outputs]==[[sum(manual[:4]),sum(manual[4:])],[4,4]]
        with local_manager(store) as m:
            replay=Replays(m);final=replay.advance(rid,checkpoint_every=2);assert final['confirmed_bars']==8 and final['pending_events']==0
            points=replay.view(rid,'chart')['rows'];assert [r[-1] for r in points]==manual
        if v in (1,6):
            with local_manager(work/'offline') as m:
                ref=m.freeze_input({'artifact':read_json(artifact)});replay=Replays(m)
                other=replay.create({'input_ref':ref,**read_json(work/'contexts.json')})['replay_id']
                replay.append(other,[json.loads(line) for line in tape.read_text().splitlines()]);replay.advance(other,checkpoint_every=3)
                assert replay.view(other,'chart')['rows']==points
        rows.append({'version':v,'historical_pause_resume':True,'replay_restart':True,'offline':v in (1,6),'lower_tf':v>=5,'outputs':manual})
    result={'schema_version':'openpine.preparation-flow.1','status':'passed','python':'3.14.8','versions':rows,'oracle':'Fraction sums and two-point means; expected values never use the runtime.'}
    atomic_json(root/'report.json',result);return result

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);args=p.parse_args(argv)
    print(json.dumps(run(args.directory)));return 0
if __name__=='__main__':raise SystemExit(main())
