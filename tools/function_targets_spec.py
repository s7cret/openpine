"""Reproduce the bounded pure-function target profile and independent v1-v6 corpus."""
import argparse
from packages.contracts.function_targets import policy
from packages.contracts.wire import canonical,digest,read_json
from tools.specification import ROOT
from tools.primary_targets_spec import examples as primary_examples

def source(v,mode='inputs',*,lower=False):
    api='security' if v<5 else 'request.security';decl='study' if v<5 else 'indicator';inp='input' if v<5 else 'input.bool'
    symbol='tickerid' if v<4 else 'syminfo.tickerid';period='period' if v<4 else 'timeframe.period'
    text=f'//@version={v}\n{decl}("Function targets")\n'
    if mode in ('references','conditional'):
        text+=f'pick(useChart,fallback) => useChart ? {symbol} : fallback\ninterval(useChart,fallback) =>\n    if useChart\n        {period}\n    else\n        fallback\nchoose={inp}('+('true' if mode=='references' else 'false')+')\nsym=pick(choose,"TEST:A")\ntf=interval(choose,"2")\n'
    elif mode=='literal':text+='empty() => ""\nsym=empty()\ntf=empty()\n'
    else:
        text+='identity(value) => value\nselect(value) =>\n    local=identity(value)\n    if local==""\n        ""\n    else\n        local\n'
        text+=('sym=input.symbol("")\ntf=input.timeframe("'+('1' if lower else '')+'",options=["","1","2"])\n' if v>=5 else 'sym=input("")\ntf=input("")\n')
    if lower:return text+'a=request.security_lower_tf(select(sym),identity(tf),close)\nplot(array.sum(a))\nplot(array.size(a))\n'
    return text+(f'plot({api}(select(sym),identity(tf),close))\n' if mode=='inputs' else f'plot({api}(sym,tf,close))\n')

def examples():
    files={n:v for n,v in primary_examples().items() if n.endswith('.json')}
    for v in range(1,7):
        for mode in ('references','conditional','literal','inputs'):files[f'v{v}-{mode}.pine']=source(v,mode).encode()
    for v in (5,6):files[f'v{v}-lower.pine']=source(v,lower=True).encode()
    return files

def corpus():
    files=examples();cases=[]
    for v in range(1,7):
        mapped=[10.,10.,20.,20.,30.,30.,40.,40.] if v<3 else [None,10.,10.,20.,20.,30.,30.,40.]
        for mode,inputs,values in (('references',{},list(map(float,range(100,108)))),('conditional',{},mapped),
                ('literal',{},list(map(float,range(100,108)))),('inputs',{},list(map(float,range(100,108)))),('inputs',{'sym':'TEST:A','tf':'2'},mapped)):
            cases.append({'case_id':f'v{v}-{mode}-'+('override' if inputs else 'default'),'language_version':v,'source':f'v{v}-{mode}.pine',
                'bars':'b1.json','context_inputs':'contexts.json','inputs':inputs,'expected':{'status':'executed','title':'Function targets','shorttitle':'','overlay':False,
                    'observation_count':8,'outputs':[{'title':'','type':'float','values':values}],'qualifiers':['series']}})
        text=f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Rejected")\nchoose(x) => x>0 ? "TEST:A" : "TEST:B"\nplot('+('security' if v<5 else 'request.security')+'(choose(close),"2",close))\n'
        name=f'v{v}-reject-series.pine';files[name]=text.encode();cases.append({'case_id':f'v{v}-series-rejected','language_version':v,'source':name,'bars':'b1.json',
            'expected':{'status':'rejected','category':'LANGUAGE_INVALID','code':'ARGUMENT_QUALIFIER'}})
    for v in (5,6):cases.append({'case_id':f'v{v}-lower','language_version':v,'source':f'v{v}-lower.pine','bars':'b4.json','context_inputs':'lower-contexts.json',
        'expected':{'status':'executed','title':'Function targets','shorttitle':'','overlay':False,'observation_count':2,
            'outputs':[{'title':'','type':'float','values':[406.,422.]},{'title':'','type':'int','values':[4,4]}],'qualifiers':['series','series']}})
    files['cases.json']=canonical({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','basis':'Direct primary closes 100..107, manual A2 mapping and sums 406/422; existing versioned function model with disclosed historical reconstruction.','external_pine_execution':False},
        'rule_refs':[],'assertion_refs':[],'cases':cases})+b'\n';return files

def profile():
    lock=read_json(ROOT/'spec/lock.json');functions=read_json(ROOT/lock['function_release']['path']/'functions.json')
    versions={}
    for v in range(1,7):
        refs=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json');fn=functions['versions'][str(v)]
        versions[str(v)]={'policy':policy(v),'reference_sha256':digest(refs),'function_profile_id':fn['profile_id'],
            'basis':fn['basis'],'function_evidence':fn['evidence'],'function_policy':fn['policy']}
    return {'schema_version':'openpine.function-target-profile.1','source_lock_sha256':digest(lock),'functions_sha256':digest(functions),
        'primary_profile_sha256':digest(read_json(ROOT/'spec/primary-targets-profile.json')),'versions':versions,
        'decisions':['Reuse the pinned v1-v6 scalar UDF model, specialized UserCallIR and binding IDs. No new source grammar or qualifier relaxation.',
            'Admit every argument, body statement and branch. Frozen targets exclude state, history, kernels, requests, loops and collections even in unselected branches.',
            'Actual lexical reads determine immutable global captures. Caller locals never become captures; unused visible globals do not create effects.',
            'Arguments execute once in source order. Private immutable declarations and value-if use existing versioned scalar semantics.',
            'One budget includes all calls, bodies, branches and global dependencies; 128 nodes, depth 16, existing call depth 12.',
            'Primary references and empty-result inheritance reuse the separate primary model, including its disclosed early historical reconstruction.',
            'Older profiles, immutable bundles and source lock remain unchanged. Exact external execution equivalence is deferred.']}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    files={'spec/function-targets-profile.json':canonical(profile())+b'\n'}
    for folder,rows in (('examples/function-targets',examples()),('conformance/function-targets',corpus())):files.update((folder+'/'+n,raw) for n,raw in rows.items())
    for name,raw in files.items():
        path=ROOT/name
        if a.check:
            if not path.exists() or path.read_bytes()!=raw:raise ValueError('Stale function-target file: '+name)
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Pure function target v1-v6 policy and independent fixtures verified.');return 0
if __name__=='__main__':raise SystemExit(main())
