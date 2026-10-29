"""Reproduce the context-environment overlay and independent version fixtures."""
import argparse
import json
from tools.specification import ROOT
from packages.contracts.request_environment import policy
from packages.contracts.wire import canonical,digest,read_json

def profile():
    lock=read_json(ROOT/'spec/lock.json');versions={};evidence={}
    for v in range(1,7):
        refs=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json')
        versions[str(v)]={'policy':policy(v),'reference_sha256':digest(refs)}
        evidence[str(v)]=[{k:row[k] for k in ('name','entry_id','source_record_sha256')} for row in refs['entries'] if row['name'] in ('bar_index','time','time_close')]
    return {'schema_version':'openpine.request-environment-spec.1','release':'pine-request-environment-20261029.1',
        'source_lock_sha256':digest(lock),'versions':versions,'evidence':evidence,
        'decisions':['Primary bar_index v1-v3 availability is an explicit reconstruction; Wiki variable evidence starts v4.',
        'Keep existing time/calendar slots. time_close is available v4-v6 only.',
        'The shared bound IR walker admits counters in arithmetic, history, captures, tuples and UDFs.',
        'Each requested evaluation owns its counter/environment; no primary counter, host clock or synthetic gap bars.',
        'barstate, strings, nested requests and mutable global captures remain excluded.',
        'Old request/environment bundles and reference signatures are immutable. Exact TradingView verification is deferred.']}

def fixtures():
    files={};cases=[]
    primary={'schema_version':'openpine.historical-bars.2','context_id':'env.primary','symbol':'TEST:ENVREQUEST','timeframe':'1',
        'bars':[dict(time=i*60000,open=100.,high=101.,low=99.,close=100.,volume=1.) for i in range(8)]}
    higher={**primary,'context_id':'env.higher','timeframe':'2','bars':[dict(time=i*60000,open=10.,high=11.,low=9.,close=10.,volume=1.) for i in (0,2,6)]}
    def context(data):return {key:data[key] for key in ('context_id','symbol','timeframe')}|{'source':'uploaded-file.1','session':'24x7','timezone':'UTC','time_rule':'fixed-duration-utc.1'}
    files['bars.json']=canonical(primary)+b'\n';files['higher.json']=canonical(higher)+b'\n'
    files['contexts.json']=canonical({'schema_version':'openpine.context-inputs.1','primary_context':context(primary),'contexts':[{'context':context(higher),'bars':'higher.json'}]})+b'\n'
    for v in range(1,7):
        api='security' if v<5 else 'request.security';decl='study' if v<5 else 'indicator'
        mapping=[0,0,1,1,1,1,2,2] if v<3 else [None,0,0,1,1,1,1,2]
        def add(name,prefix,expression,values=None,reject=None):
            file=f'v{v}-{name}.pine';files[file]=(f'//@version={v}\n{decl}("Requested environment")\n'+prefix+f'plot({api}("TEST:ENVREQUEST","2",{expression}))\n').encode()
            expected=({'status':'rejected','category':reject[0],'code':reject[1]} if reject else {'status':'executed','title':'Requested environment','shorttitle':'','overlay':False,'observation_count':8,'outputs':[{'title':'','type':'float','values':values}],'qualifiers':['series']})
            cases.append({'case_id':f'v{v}-{name}','language_version':v,'source':file,'bars':'bars.json','context_inputs':'contexts.json','expected':expected})
        add('counter','','bar_index',[None if i is None else float(i) for i in mapping])
        add('capture','n=bar_index+2\nf(x) => x+n\n','f(bar_index)',[None if i is None else float(2*i+2) for i in mapping])
        add('history','','bar_index[1]',[None if i is None or i==0 else float(i-1) for i in mapping])
        opens=[0,120000,360000]
        add('time','','time',[None if i is None else float(opens[i]) for i in mapping])
        add('close-time','','time_close',[None if i is None else float(opens[i]+120000) for i in mapping] if v>=4 else None,None if v>=4 else ('PROFILE_UNCOVERED','ENVIRONMENT_VERSION'))
        add('reject-mutable','var n=bar_index\n','n',reject=('PROFILE_UNCOVERED','REQUEST_SCOPE'))
        add('reject-flags','','barstate.islast ? 1 : 0',reject=('PROFILE_UNCOVERED','REQUEST_SCOPE') if v>=2 else ('PROFILE_UNCOVERED','ENVIRONMENT_VERSION'))
    files['cases.json']=canonical({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','basis':'Requested opens [0,120000,360000], counters [0,1,2], manual gaps/lookahead mapping and calendar ends; no runtime oracle.','external_pine_execution':False},
        'rule_refs':[],'assertion_refs':[],'cases':cases})+b'\n'
    return files

def context_transitions():
    """Six existing IDs/sources acquire independent successful counter oracles."""
    value=read_json(ROOT/'conformance/contexts/cases.json')
    for case in value['cases']:
        if not case['case_id'].endswith('-unsupported-expression'):continue
        v=case['language_version']
        values=([float(i) for i in range(4) for _ in range(12)] if v<3
                else [None]*11+[float(i) for i in range(3) for _ in range(12)]+[3.])
        case['expected']={'status':'executed','title':f'Contexts v{v}','shorttitle':'','overlay':False,
            'observation_count':48,'outputs':[{'title':'','type':'float','values':values}],'qualifiers':['series']}
    return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode()

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    files={'spec/request-environment-profile.json':canonical(profile())+b'\n',
        'conformance/contexts/cases.json':context_transitions()}|{'conformance/request-environment/'+name:raw for name,raw in fixtures().items()}
    for name,raw in files.items():
        path=ROOT/name
        if a.check:assert path.read_bytes()==raw,name
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Requested environment profile and independent fixtures verified.');return 0

if __name__=='__main__':raise SystemExit(main())
