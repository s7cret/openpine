"""Reproduce frozen-target evidence and independently specified fixtures."""
import argparse
from packages.contracts.computed_targets import policy
from packages.contracts.wire import canonical,digest,read_json
from tools.specification import ROOT
from tools.prepared_requests_spec import examples as base_examples

def source(v,lower=False,specialized=False):
    declaration='indicator' if v>=5 else 'study';inp='input' if v<5 else 'input.string';boolean='input' if v<5 else 'input.bool';integer='input' if v<5 else 'input.int'
    request='security' if v<5 else 'request.security'
    text=f'//@version={v}\n{declaration}("Computed contexts")\n'
    if specialized:
        text+='sym=input.symbol("TEST:A")\ntf=input.timeframe("2",options=["1","2"])\n'
    else:
        text+=f'prefix={inp}("TEST:")\nchoose={boolean}(true)\ninterval={integer}(2)\n'
        text+='sym=prefix+(choose ? "A" : "B")\ntf=not choose and interval>0 ? "1" : "2"\n'
        if lower:text+='lower=choose ? "2" : "1"\n'
    return text+('a=request.security_lower_tf(sym,lower,close)\nplot(array.sum(a))\nplot(array.size(a))\n' if lower else f'plot({request}(sym,tf,close))\n')

def examples():
    rows={n:raw for n,raw in base_examples().items() if n.endswith('.json')}
    rows.update({f'v{v}.pine':source(v).encode() for v in range(1,7)})
    rows.update({f'v{v}-lower.pine':source(v,lower=True).encode() for v in (5,6)})
    rows.update({f'v{v}-specialized.pine':source(v,specialized=True).encode() for v in (5,6)})
    rows['overrides.json']=canonical({'choose':False})+b'\n'
    return rows

def corpus():
    files=examples();cases=[]
    def add(v,name,filename,inputs,values,lower=False):
        cases.append({'case_id':f'v{v}-{name}','language_version':v,'source':filename,'bars':'lower-parent.json' if lower else 'parent.json',
            'context_inputs':'lower-contexts.json' if lower else 'contexts.json','inputs':inputs,
            'expected':{'status':'executed','title':'Computed contexts','shorttitle':'','overlay':False,'observation_count':2 if lower else 8,
                'outputs':[{'title':'','type':'float','values':values}]+([{'title':'','type':'int','values':[4,4]}] if lower else []),
                'qualifiers':['series','series'] if lower else ['series']}})
    for v in range(1,7):
        defaults=[10.,10.,20.,20.,30.,30.,40.,40.] if v<3 else [None,10.,10.,20.,20.,30.,30.,40.]
        add(v,'default',f'v{v}.pine',{},defaults)
        add(v,'selected',f'v{v}.pine',{'choose':False},list(map(float,range(100,108))))
        if v>=5:
            add(v,'lower',f'v{v}-lower.pine',{'choose':False},[406.,422.],True)
            add(v,'specialized',f'v{v}-specialized.pine',{'sym':'TEST:B','tf':'1'},list(map(float,range(100,108))))
        name=f'v{v}-reject-series.pine';text=source(v).replace('sym=prefix+(choose ? "A" : "B")','sym=close>0 ? "TEST:A" : "TEST:B"');files[name]=text.encode()
        cases.append({'case_id':f'v{v}-series-rejected','language_version':v,'source':name,'bars':'parent.json',
            'expected':{'status':'rejected','category':'LANGUAGE_INVALID','code':'ARGUMENT_QUALIFIER'}})
    files['cases.json']=canonical({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','basis':'Explicit A2 step mapping, B1 100..107, and four-element sums 406/422.','external_pine_execution':False},
        'rule_refs':[],'assertion_refs':[],'cases':cases})+b'\n'
    return files

def profile():
    lock=read_json(ROOT/'spec/lock.json');versions={}
    for v in range(1,7):
        reference=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json')
        names=('input','input.bool','input.int','input.string','input.symbol','input.timeframe','security','request.security','request.security_lower_tf')
        versions[str(v)]={'policy':policy(v),'reference_sha256':digest(reference),'evidence':[
            {k:e[k] for k in ('name','entry_id','source_record_sha256')} for e in reference['entries'] if e['name'] in names]}
    return {'schema_version':'openpine.computed-target-profile.1','source_lock_sha256':digest(lock),'versions':versions,
        'decisions':['Extend existing versioned scalar language with a finite const/input target evaluator; no external equivalence claim.',
        'input.symbol and input.timeframe functions are attested in v5/v6; the v4 input.symbol constant is not a callable API.',
        'Admit existing arithmetic, comparisons, boolean operations, conditional strings and concatenation with immutable global dependencies.',
        'Validate all branches; evaluate selected branches with shared versioned scalar semantics before data acquisition.',
        'Explicit nonempty qualified symbols and canonical timeframes only; empty chart-relative defaults, display and interactive confirm are outside the slice.',
        'Separate computed.prepared overloads reuse Typed IR .11; the previous prepared contracts, policies and immutable bundles are retained.']}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args(argv)
    files={'spec/computed-targets-profile.json':canonical(profile())+b'\n'}
    for folder,rows in (('examples/computed-targets',examples()),('conformance/computed-targets',corpus())):
        files.update((folder+'/'+n,raw) for n,raw in rows.items())
    for name,raw in files.items():
        path=ROOT/name
        if args.check:
            if not path.exists() or path.read_bytes()!=raw:raise ValueError('Stale computed-target fixture: '+name)
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Computed target v1-v6 policy, reference evidence and manual fixtures verified.');return 0
if __name__=='__main__':raise SystemExit(main())
