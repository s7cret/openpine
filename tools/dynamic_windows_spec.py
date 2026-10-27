"""Reviewed changing-window profile, examples and independent Fraction oracles."""
import argparse,json,hashlib
from fractions import Fraction
from tools.specification import ROOT
from packages.contracts.wire import read_json,canonical,digest
from packages.contracts.dynamic_windows import policy
from tools.primary_targets_spec import examples as primary_examples,source as primary_source

DECISIONS=['v5/v6 explicitly attest series int lengths. v1-v4 integer terminology leaves qualifier gaps: changing lengths are an explicit reconstruction for this restricted profile.',
 'Existing SMA already supports dynamic windows; its ABI and implementation remain unchanged. Existing fixed-length contracts and state paths remain intact.',
 'Only series-qualified length arguments select a new operation/contract; simple/const/input lengths use previous implementations.',
 'Retain the last 100000 reached calls for extrema and last 100000 defined samples for statistics independently of selected length. Invalid/NA lengths fail before updating state; skipped calls do not update.',
 'Statistics requery the newly selected window even on NA sources; extrema include NA holes but ignore them as candidates. Existing warmup rules apply.',
 'One million shared storage cells; exact prefix statistics and logarithmic range extrema. Checkpoints store bounded samples/prefixes and validate consistency; previews modify overlays only.',
 'Six existing signal WMA series-length rejection cases retain source and case IDs and become executions with independently expected all-NA output; the initial missing source prevents full warmup.',
 'Exact TradingView equivalence remains a separate stage.']

def profile():
    lock=read_json(ROOT/'spec/lock.json');e=read_json(ROOT/'spec/dynamic-windows-evidence.json');versions={}
    for v in range(1,7):
        refs=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json');known={x['entry_id']:x['source_record_sha256'] for x in refs['entries']}
        rows=e['versions'][str(v)]['entries'];assert all(known[r['entry_id']]==r['source_record_sha256'] for r in rows)
        if v>=5:
            for name in ('ta.sma','math.sum','ta.wma','ta.highest','ta.lowest','ta.variance','ta.stdev'):
                assert any(r['name']==name and any(p['name']=='length' and 'series int' in p['types'] for p in r['parameters']) for r in rows),name
        versions[str(v)]={'policy':policy(v),'reference_sha256':digest(refs)}
    admissions=[{'corpus':'signals.json','case_id':f'v{v}-series-length','source_sha256':hashlib.sha256((ROOT/f'conformance/signals/v{v}-series-length.pine').read_bytes()).hexdigest(),'previous_status':'rejected','new_status':'executed','expected_values':[None]*9} for v in range(1,7)]
    return {'schema_version':'openpine.dynamic-window-profile.1','source_lock_sha256':digest(lock),'legacy_abi_sha256':digest(read_json(ROOT/'spec/dynamic-windows-legacy-abi.json')),'evidence_sha256':digest(e),'versions':versions,'decisions':DECISIONS,'intentional_admissions':admissions}

def source(v,mode='inputs',*,lower=False):
    p='' if v<5 else 'ta.'
    text=primary_examples()[f'v{v}-lower.pine'].decode() if lower else primary_source(v,mode)
    return text.replace('Primary contexts','Changing windows').replace(',close)',f',{p}highest(close,bar_index%3+1))')

def examples():
    files={n:b for n,b in primary_examples().items() if n.endswith('.json')}
    for v in range(1,7):
        for mode in ('references','inputs'):files[f'v{v}-{mode}.pine']=source(v,mode).encode()
        p='' if v<5 else 'ta.';mx='max' if v<5 else 'math.max'
        text=f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Since signal")\n'
        text+=f'condition=bar_index%4==0\nn={mx}(1,nz({p}barssince(condition))+1)\nplot({p}highest(high,n))\nplot({p}sma(close,n))\n'
        files[f'v{v}-since-signal.pine']=text.encode()
    for v in (5,6):files[f'v{v}-lower.pine']=source(v,lower=True).encode()
    return files

def oracle(values,lengths):
    defined=[];reached=[];outputs=[[] for _ in range(7)]
    for value,n in zip(values,lengths,strict=True):
        reached.append(value)
        if value is not None:defined.append(Fraction(value))
        window=defined[-n:];physical=[x for x in reached[-n:] if x is not None]
        if len(window)<n:mean=total=wma=variance=deviation=None
        else:
            total=sum(window);mean=total/n;wma=sum((i+1)*x for i,x in enumerate(window))/(n*(n+1)//2)
            variance=sum((x-mean)**2 for x in window)/n;deviation=float(variance)**.5
        row=(mean,total,wma,max(physical) if physical else None,min(physical) if physical else None,variance,deviation)
        for output,x in zip(outputs,row,strict=True):output.append(None if x is None else float(x))
    return outputs

def corpus():
    files=examples();cases=[];values=[float(x) for x in range(100,108)]
    def add(v,key,body,outputs=None,rejected=None,contexts=False):
        name=f'v{v}-{key}.pine';files[name]=(f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Windows")\n'+body).encode()
        expected={'status':'rejected','category':rejected[0],'code':rejected[1]} if rejected else {'status':'executed','title':'Windows','shorttitle':'','overlay':False,'observation_count':8,'outputs':[{'title':'','type':'float','values':x} for x in outputs],'qualifiers':['series']*len(outputs)}
        row={'case_id':f'v{v}-{key}','language_version':v,'source':name,'bars':'b1.json','expected':expected}
        if contexts:row['context_inputs']='contexts.json'
        if not rejected:row['numeric_tolerance']={'absolute':1e-12,'relative':1e-12}
        cases.append(row)
    for v in range(1,7):
        p='' if v<5 else 'ta.';sum_='sum' if v<5 else 'math.sum';req='security' if v<5 else 'request.security'
        funcs=(p+'sma',sum_,p+'wma',p+'highest',p+'lowest',p+'variance',p+'stdev')
        for key,source_,samples in [('growing-shrinking','close',values),('na-requery','bar_index==2 ? float(na) : close',[100.,101.,None,103.,104.,105.,106.,107.])]:
            add(v,key,'n=bar_index%4+1\nx='+source_+'\n'+''.join(f'plot({f}(x,n))\n' for f in funcs),oracle(samples,[i%4+1 for i in range(8)]))
        add(v,'implicit-source','n=bar_index%4+1\n'+f'plot({p}highest(n))\nplot({p}lowest(n))\n',[[float(x) for x in range(100,108)],[100.,100.,100.,100.,104.,104.,104.,104.]])
        add(v,'requested',f'calc(x) => {p}highest(x,bar_index%2+1)\nplot({req}("TEST:A","2",calc(close)))\n',[[10.,10.,20.,20.,30.,30.,40.,40.] if v<3 else [None,10.,10.,20.,20.,30.,30.,40.]],contexts=True)
        for key,length in [('zero','0'),('negative','-1'),('over-limit','100001'),('missing','int(na)')]:
            add(v,'reject-'+key,f'n=bar_index==2 ? {length} : 2\nplot({p}highest(close,n))\n',rejected=('RUNTIME_ERROR','WINDOW_LENGTH'))
        add(v,'reject-rma',f'plot({p}rma(close,bar_index%3+1))\n',rejected=('LANGUAGE_INVALID','ARGUMENT_QUALIFIER'))
        fmt='tostring' if v<5 else 'str.tostring'
        add(v,'reject-window-target',f'n=bar_index%3+1\nplot({req}("TEST:A",{fmt}({p}highest(close,n)),close))\n',rejected=('LANGUAGE_INVALID','ARGUMENT_QUALIFIER'))
    for v in (5,6):
        cases.append({'case_id':f'v{v}-lower','language_version':v,'source':f'v{v}-lower.pine','bars':'b4.json','context_inputs':'lower-contexts.json','expected':{'status':'executed','title':'Changing windows','shorttitle':'','overlay':False,'observation_count':2,'outputs':[{'title':'','type':'float','values':[406.,422.]},{'title':'','type':'int','values':[4,4]}],'qualifiers':['series','series']}})
    files['cases.json']=canonical({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1','oracle':{'kind':'independent_manual_expectations','basis':'Direct bounded Python lists and Fraction sums/weights/variance, manual requested-context mapping; no runtime state computes expectations.','external_pine_execution':False},'rule_refs':[],'assertion_refs':[],'cases':cases})+b'\n'
    return files

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    files={'spec/dynamic-windows-profile.json':canonical(profile())+b'\n'}
    files.update({'examples/dynamic-windows/'+n:b for n,b in examples().items()})
    files.update({'conformance/dynamic-windows/'+n:b for n,b in corpus().items()})
    for name,raw in files.items():
        path=ROOT/name
        if a.check:assert path.read_bytes()==raw,name
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Dynamic windows v1-v6 evidence, unchanged fixed ABI and independent fixtures verified.');return 0
if __name__=='__main__':raise SystemExit(main())
