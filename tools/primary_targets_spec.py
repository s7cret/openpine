"""Reproduce primary-relative target evidence and independent all-version examples."""
import argparse
from dataclasses import asdict,replace
from packages.contracts.primary_targets import policy
from packages.contracts.wire import canonical,digest,read_json
from tools.specification import ROOT
from tools.prepared_requests_spec import datasets,context

def source(v,mode='references'):
    api='security' if v<5 else 'request.security';decl='study' if v<5 else 'indicator'
    symbol='tickerid' if v<4 else 'syminfo.tickerid';period='period' if v<4 else 'timeframe.period'
    head=f'//@version={v}\n{decl}("Primary contexts")\n'
    if mode=='references':body=f'sym={symbol}\ntf={period}\n'
    elif mode=='literal':return head+f'plot({api}("","",close))\n'
    elif mode=='conditional':body=f'useChart='+('input.bool' if v>=5 else 'input')+f'(false)\nsym={symbol}\ntf=useChart ? {period} : "2"\n'
    else:body=('sym=input.symbol("")\ntf=input.timeframe("",options=["","1","2"])\n' if v>=5 else 'sym=input("")\ntf=input("")\n')
    return head+body+f'plot({api}(sym,tf,close))\n'

def examples():
    data=datasets();data['b4']=replace(data['lower-parent'],context_id='b4',symbol='TEST:B')
    rows={name+'.json':asdict(d) for name,d in data.items()}
    rows['contexts.json']={'schema_version':'openpine.context-inputs.1','primary_context':context(data['b1']),
        'contexts':[{'context':context(data[n]),'bars':n+'.json'} for n in ('a2','b2')]}
    rows['self-contexts.json']={'schema_version':'openpine.context-inputs.1','primary_context':context(data['b1']),'contexts':[]}
    rows['lower-contexts.json']={'schema_version':'openpine.context-inputs.1','primary_context':context(data['b4']),
        'contexts':[{'context':context(data['b1']),'bars':'b1.json'}]}
    rows['overrides.json']={'sym':'TEST:A','tf':'2'}
    rows['expected.json']={'primary':list(map(float,range(100,108))),
        'a2_on':[10.,10.,20.,20.,30.,30.,40.,40.],'a2_off':[None,10.,10.,20.,20.,30.,30.,40.],
        'b2_on':[100.,100.,200.,200.,300.,300.,400.,400.],'b2_off':[None,100.,100.,200.,200.,300.,300.,400.],
        'lower_sums':[406.,422.]}
    files={n:canonical(v)+b'\n' for n,v in rows.items()}
    for v in range(1,7):
        for mode in ('references','literal','inputs','conditional'):files[f'v{v}-{mode}.pine']=source(v,mode).encode()
    for v in (5,6):files[f'v{v}-lower.pine']=f'//@version={v}\nindicator("Primary contexts")\nsym=input.symbol("")\ntf=input.timeframe("1")\na=request.security_lower_tf(sym,tf,close)\nplot(array.sum(a))\nplot(array.size(a))\n'.encode()
    return files

def corpus():
    files=examples();cases=[]
    manual={'primary':list(map(float,range(100,108))),'a2_on':[10.,10.,20.,20.,30.,30.,40.,40.],
        'a2_off':[None,10.,10.,20.,20.,30.,30.,40.],'b2_on':[100.,100.,200.,200.,300.,300.,400.,400.],
        'b2_off':[None,100.,100.,200.,200.,300.,300.,400.]}
    for v in range(1,7):
        for mode,inputs,key in (('references',{},'primary'),('literal',{},'primary'),('inputs',{},'primary'),
                ('inputs',{'sym':'TEST:A','tf':'2'},'a2_on' if v<3 else 'a2_off'),('conditional',{},'b2_on' if v<3 else 'b2_off')):
            cases.append({'case_id':f'v{v}-{mode}-'+('override' if inputs else 'default'),'language_version':v,'source':f'v{v}-{mode}.pine',
                'bars':'b1.json','context_inputs':'contexts.json','inputs':inputs,'expected':{'status':'executed','title':'Primary contexts','shorttitle':'','overlay':False,
                    'observation_count':8,'outputs':[{'title':'','type':'float','values':manual[key]}],'qualifiers':['series']}})
        text=source(v).replace('sym='+('tickerid' if v<4 else 'syminfo.tickerid'),'sym=close>0 ? "TEST:A" : "TEST:B"')
        name=f'v{v}-reject-series.pine';files[name]=text.encode()
        cases.append({'case_id':f'v{v}-series-rejected','language_version':v,'source':name,'bars':'b1.json',
            'expected':{'status':'rejected','category':'LANGUAGE_INVALID','code':'ARGUMENT_QUALIFIER'}})
    for v in (5,6):cases.append({'case_id':f'v{v}-lower-primary-symbol','language_version':v,'source':f'v{v}-lower.pine','bars':'b4.json',
        'context_inputs':'lower-contexts.json','expected':{'status':'executed','title':'Primary contexts','shorttitle':'','overlay':False,'observation_count':2,
            'outputs':[{'title':'','type':'float','values':[406.,422.]},{'title':'','type':'int','values':[4,4]}],'qualifiers':['series','series']}})
    files['cases.json']=canonical({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','basis':'Primary closes 100..107, explicit A2/B2 step mapping, and four-close sums 406/422. Early empty defaults are declared reconstructions.','external_pine_execution':False},
        'rule_refs':[],'assertion_refs':[],'cases':cases})+b'\n'
    return files

def profile():
    lock=read_json(ROOT/'spec/lock.json');evidence=read_json(ROOT/'spec/primary-targets-evidence.json');versions={}
    names=('tickerid','period','syminfo.tickerid','timeframe.period','input.symbol','input.timeframe','security','request.security','request.security_lower_tf')
    for v in range(1,7):
        reference=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json')
        versions[str(v)]={'policy':policy(v),'reference_sha256':digest(reference),'evidence':[
            {k:r[k] for k in ('name','entry_id','source_record_sha256')} for r in reference['entries'] if r['name'] in names]}
    return {'schema_version':'openpine.primary-target-profile.1','source_lock_sha256':digest(lock),'evidence_sha256':digest(evidence),'versions':versions,
        'decisions':['Compile symbolic references without a selected primary market; resolve concrete values from frozen primary metadata before data I/O.',
        'Keep simple qualifiers for tickerid/period; no blanket admission of simple, series, mutable state or UDF target calls.',
        'New specialized-input profiles permit empty strings while retaining raw settings. Old input profiles remain strict.',
        'Empty timeframe text is explicit from v3 and empty symbol from v5; earlier values use the declared historical reconstruction.',
        'Only logical symbol/timeframe/UTC/session fields participate in the graph; existing RunPlan bindings pin dataset revisions and provenance.',
        'Existing literal, alias-only and computed profiles retain their contracts and budgets; exact external execution comparison is deferred.']}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    files={'spec/primary-targets-profile.json':canonical(profile())+b'\n'}
    for folder,rows in (('examples/primary-targets',examples()),('conformance/primary-targets',corpus())):files.update((folder+'/'+n,raw) for n,raw in rows.items())
    for name,raw in files.items():
        path=ROOT/name
        if a.check:
            if not path.exists() or path.read_bytes()!=raw:raise ValueError('Stale primary-target fixture: '+name)
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Primary target v1-v6 policy, evidence and independent fixtures verified.');return 0
if __name__=='__main__':raise SystemExit(main())
