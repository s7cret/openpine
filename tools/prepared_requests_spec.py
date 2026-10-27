"""Versioned evidence and reproducible parameterized-context examples and corpus."""
import argparse
from dataclasses import asdict
from packages.contracts import HistoricalBar,HistoricalData
from packages.contracts.contexts import MarketContext
from packages.contracts.prepared_requests import policy
from packages.contracts.wire import canonical,digest,read_json
from tools.specification import ROOT

def source(v,*,lower=False):
    declaration='indicator' if v>=5 else 'study';inp='input.string' if v>=5 else 'input';security='request.security' if v>=5 else 'security'
    head=f'//@version={v}\n{declaration}("Prepared contexts")\nsym={inp}("TEST:A")\ntf={inp}("'+('1' if lower else '2')+'")\n'
    return head+('a=request.security_lower_tf(sym,tf,close)\nplot(array.sum(a))\nplot(array.size(a))\n' if lower else f'plot({security}(sym,tf,close))\n')

def datasets(count=8):
    def data(cid,symbol,tf,n,prices):return HistoricalData(cid,symbol,tf,tuple(HistoricalBar(i*int(tf)*60000,p,p,p,p,1.) for i,p in enumerate(prices)),schema_version='openpine.historical-bars.2')
    rows={'parent':data('primary','TEST:CHART','1',count,[1.]*count),'lower-parent':data('lower_primary','TEST:CHART','4',count//4,[1.]*(count//4))}
    for name,base in (('a',10),('b',100)):
        for tf in (1,2):
            n=count//tf;prices=[float(base*(i+1) if tf==2 else base+i) for i in range(n)]
            rows[f'{name}{tf}']=data(name+str(tf),'TEST:'+name.upper(),str(tf),n,prices)
    return rows

def context(data):return asdict(MarketContext(data.context_id,data.symbol,data.timeframe,'uploaded-file.1'))

def examples():
    data=datasets();rows={name+'.json':asdict(d) for name,d in data.items()}
    for name,primary in (('contexts','parent'),('lower-contexts','lower-parent')):
        rows[name+'.json']={'schema_version':'openpine.context-inputs.1','primary_context':context(data[primary]),
            'contexts':[{'context':context(data[k]),'bars':k+'.json'} for k in ('a1','a2','b1','b2')]}
    rows['overrides.json']={'sym':'TEST:B','tf':'1'}
    rows['expected.json']={'default_on':[10.,10.,20.,20.,30.,30.,40.,40.],'default_off':[None,10.,10.,20.,20.,30.,30.,40.],
        'selected_b1':[100.,101.,102.,103.,104.,105.,106.,107.],'lower_b1_sum':[406.,422.],'lower_count':[4,4]}
    return {**{n:canonical(v)+b'\n' for n,v in rows.items()},**{f'v{v}.pine':source(v).encode() for v in range(1,7)},
        **{f'v{v}-lower.pine':source(v,lower=True).encode() for v in (5,6)}}

def corpus():
    files=examples();cases=[]
    for v in range(1,7):
        for name,inputs,values in (('default',{},[10.,10.,20.,20.,30.,30.,40.,40.] if v<3 else [None,10.,10.,20.,20.,30.,30.,40.]),
                ('override',{'sym':'TEST:B','tf':'1'},[100.,101.,102.,103.,104.,105.,106.,107.])):
            cases.append({'case_id':f'v{v}-{name}','language_version':v,'source':f'v{v}.pine','bars':'parent.json','context_inputs':'contexts.json','inputs':inputs,
                'expected':{'status':'executed','title':'Prepared contexts','shorttitle':'','overlay':False,'observation_count':8,
                'outputs':[{'title':'','type':'float','values':values}],'qualifiers':['series']}})
    for v in (5,6):
        cases.append({'case_id':f'v{v}-lower','language_version':v,'source':f'v{v}-lower.pine','bars':'lower-parent.json','context_inputs':'lower-contexts.json','inputs':{'sym':'TEST:B'},
            'expected':{'status':'executed','title':'Prepared contexts','shorttitle':'','overlay':False,'observation_count':2,
            'outputs':[{'title':'','type':'float','values':[406.,422.]},{'title':'','type':'int','values':[4,4]}],'qualifiers':['series','series']}})
    for v in range(1,7):
        text=source(v);inp='input.string' if v>=5 else 'input'
        text=text.replace(f'sym={inp}("TEST:A")','sym=close>0 ? "TEST:A" : "TEST:B"')
        name=f'v{v}-reject-series.pine';files[name]=text.encode()
        cases.append({'case_id':f'v{v}-reject-series','language_version':v,'source':name,'bars':'parent.json',
            'expected':{'status':'rejected','category':'LANGUAGE_INVALID','code':'ARGUMENT_QUALIFIER'}})
    files['cases.json']=canonical({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','basis':'Explicit step mapping of A2 closes and direct B1 values; sums of 100..107 in two groups of four.','external_pine_execution':False},
        'rule_refs':[],'assertion_refs':[],'cases':cases})+b'\n'
    return files

def profile():
    lock=read_json(ROOT/'spec/lock.json');versions={}
    for v in range(1,7):
        catalog=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json')
        names=('input','input.string','security','request.security','request.security_lower_tf')
        versions[str(v)]={'policy':policy(v),'reference_sha256':digest(catalog),'evidence':[
            {k:e[k] for k in ('name','entry_id','source_record_sha256')} for e in catalog['entries'] if e['name'] in names]}
    return {'schema_version':'openpine.prepared-request-profile.1','source_lock_sha256':digest(lock),'versions':versions,
        'decisions':['Existing generic string inputs v1-v6 and typed input.string v5/v6; no invented early typed API.',
        'Version-specific positional/named security binding and lookahead remain unchanged.',
        'Targets depend only on immutable global string literals/input declarations and aliases. Resolve before data I/O.',
        'Typed IR .11 uses existing typed LoadIR dependencies with separate prepared overloads; no source rewriting.',
        'Settings and selected snapshot bindings pin each run; research uses an explicit catalogue of at most eight market contexts.',
        'This profile extends the old literal-only request/input slices without rewriting immutable bundles. Exact external execution comparison is deferred.']}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args(argv)
    files={'spec/prepared-requests-profile.json':canonical(profile())+b'\n'}
    for folder,rows in (('examples/prepared-requests',examples()),('conformance/prepared-requests',corpus())):
        files.update((folder+'/'+n,raw) for n,raw in rows.items())
    for name,raw in files.items():
        path=ROOT/name
        if args.check:
            if not path.exists() or path.read_bytes()!=raw:raise ValueError('Stale prepared request evidence/example: '+name)
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Prepared target v1-v6 evidence and independent fixtures verified.');return 0
if __name__=='__main__':raise SystemExit(main())
