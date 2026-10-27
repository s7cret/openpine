"""Pin versioned numeric evidence/ABI and independent arithmetic/request oracles."""
import argparse
from packages.contracts.numeric_functions import apis,contracts,LEGACY_OPERATIONS
from packages.contracts.numeric_targets import policy
from packages.contracts.wire import read_json,canonical,digest
from tools.specification import ROOT
from tools.scalar_targets_spec import source as scalar_source,examples as scalar_examples

def source(v,mode='inputs',*,lower=False):
    text=scalar_source(v,mode,lower=lower).replace('Function targets','Numeric targets')
    name='tostring' if v<5 else 'str.tostring';round_='round' if v<5 else 'math.round'
    p='' if v<5 else 'math.'
    return text.replace(f'{name}(0,"#")',f'{name}({round_}(0.0),"#")').replace(',close)',f',{p}sqrt({p}pow(close,2)))')

def examples():
    files={n:b for n,b in scalar_examples().items() if n.endswith('.json')}
    for v in range(1,7):
        for mode in ('references','inputs'):files[f'v{v}-{mode}.pine']=source(v,mode).encode()
    for v in (5,6):files[f'v{v}-lower.pine']=source(v,lower=True).encode()
    return files

def corpus():
    files=examples();cases=[]
    def add(v,key,body,types,values,quals=None,*,rejected=None,contexts=False):
        name=f'v{v}-{key}.pine';head=f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Numbers")\n';files[name]=(head+body).encode()
        expected={'status':'rejected','category':rejected[0],'code':rejected[1]} if rejected else {'status':'executed','title':'Numbers','shorttitle':'','overlay':False,'observation_count':8,
            'outputs':[{'title':'','type':t,'values':x if type(x) is list else [x]*8} for t,x in zip(types,values,strict=True)],'qualifiers':quals or ['const']*len(types)}
        row={'case_id':f'v{v}-{key}','language_version':v,'source':name,'bars':'b1.json','expected':expected}
        if contexts:row['context_inputs']='contexts.json'
        cases.append(row)
    for v in range(1,7):
        p='' if v<5 else 'math.';fmt='tostring' if v<5 else 'str.tostring';req='security' if v<5 else 'request.security';itype='float' if v<3 else 'int'
        add(v,'elementary',f'plot({p}floor(-1.2))\nplot({p}ceil(-1.2))\nplot({p}sign(-3))\nplot({p}sqrt(9))\nplot({p}pow(-2,3))\nplot({p}log(1))\nplot({p}log10(1000))\nplot({p}exp(0))\n',[itype,itype,*(['float']*6)],[-2.0 if v<3 else -2,-1.0 if v<3 else -1,-1.0,3.0,-8.0,0.0,3.0,1.0])
        add(v,'domains',f'plot({p}sqrt(-1))\nplot({p}log(0))\nplot({p}log10(-1))\nplot({p}pow(-2,0.5))\nplot({p}pow(0,-1))\nplot({p}pow(0,0))\nplot({p}exp(na))\n',['float']*7,[None,None,None,None,None,1.0,None])
        add(v,'rounding',f'plot({p}round(-1.5))\nplot({p}round(1.5))\nplot({p}round(-1.25,1))\nplot({fmt}(-1.5,"#")=="-2" ? 1 : 0)\n',['int','int','float','int'],[-1,2,-1.2,1],['const','const','const','simple'])
        add(v,'targets',f'choose(x) => {fmt}({p}max(1,{p}round(x)),"#")\nminutes='+('input(2.0)' if v<5 else 'input.float(2.0)')+f'\nplot({req}("TEST:A",choose(minutes),{p}sqrt(close*close)))\n',['float'],[[10.,10.,20.,20.,30.,30.,40.,40.] if v<3 else [None,10.,10.,20.,20.,30.,30.,40.]],['series'],contexts=True)
        add(v,'requested',f'calc(x) => {p}sqrt({p}pow(x,2))+{p}log(1)+{p}exp(0)\nplot({req}("TEST:A","2",calc(close)))\n',['float'],[[11.,11.,21.,21.,31.,31.,41.,41.] if v<3 else [None,11.,11.,21.,21.,31.,31.,41.]],['series'],contexts=True)
        add(v,'overflow',f'plot({p}exp(1000))\n',[],[],rejected=('RUNTIME_ERROR','NUMERIC_OVERFLOW'))
        add(v,'pow-overflow',f'plot({p}pow(10,1000000000))\n',[],[],rejected=('RUNTIME_ERROR','NUMERIC_OVERFLOW'))
        add(v,'series-target',f'plot({req}("TEST:A",{fmt}({p}floor(close)),close))\n',[],[],rejected=('LANGUAGE_INVALID','ARGUMENT_QUALIFIER'))
        if v>=3:add(v,'int-overflow',f'plot({p}floor(1e30))\n',[],[],rejected=('RUNTIME_ERROR','NUMERIC_RANGE'))
        add(v,'irrational',f'plot({p}sqrt(2))\nplot({p}log(2))\nplot({p}log10(2))\nplot({p}exp(1))\n',['float']*4,[1.4142135623730951,0.6931471805599453,0.3010299956639812,2.718281828459045])
        cases[-1]['numeric_tolerance']={'absolute':1e-12,'relative':1e-12}
    files['cases.json']=canonical({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','basis':'Exact integers, elementary identities, decimal tie examples, declared domain NA and manual A2 close mapping. No execution kernel computes expectations.','external_pine_execution':False},'rule_refs':[],'assertion_refs':[],'cases':cases})+b'\n'
    return files

def profile():
    lock=read_json(ROOT/'spec/lock.json');e=read_json(ROOT/'spec/numeric-functions-evidence.json');abi=read_json(ROOT/'spec/numeric-legacy-abi.json');versions={}
    for v in range(1,7):
        refs=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json');known={x['entry_id']:x['source_record_sha256'] for x in refs['entries']};rows=e['versions'][str(v)]['entries']
        assert all(known[r['entry_id']]==r['source_record_sha256'] for r in rows)
        assert {a['name'] for a in apis(v)}<={x['name'] for x in rows}
        versions[str(v)]={'policy':policy(v),'apis':apis(v),'reference_sha256':digest(refs),'legacy_contracts':len(abi['versions'][str(v)])}
    return {'schema_version':'openpine.numeric-target-profile.1','source_lock_sha256':digest(lock),'evidence_sha256':digest(e),'legacy_abi_sha256':digest(abi),
        'scalar_profile_sha256':digest(read_json(ROOT/'spec/scalar-targets-profile.json')),'versions':versions,
        'decisions':['Legacy contracts, parameter identities, qualifiers and results are preserved byte-for-byte as captured; compiler verifies their complete ABI.',
            'The v1 archive and v2 compatibility baseline explicitly return float for floor/ceil; v3-v6 return int. Historical qualifier gaps adopt max argument qualifier.',
            'Existing numeric round ties go toward positive infinity; string formatting keeps its separately adopted HALF_UP. No common rounding rewrite.',
            'Undefined real domains return numeric NA; zero power zero is adopted as 1.0. Overflow raises a diagnostic; underflow may produce finite zero.',
            'Power uses binary64 math.pow and never arbitrary-size Python integer exponentiation. New integer results are int64.',
            'One shared scalar budget covers nested numeric/string calls in new numeric programs. Legacy-only programs retain their existing budget envelope.',
            'A new numeric target profile admits all arguments/branches before data I/O; older target profiles stay closed. Same numeric kernels run in requested contexts.',
            'Full external TradingView execution equivalence is deferred; adopted domain/overflow details are explicit.']}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    files={'spec/numeric-targets-profile.json':canonical(profile())+b'\n'}
    for folder,rows in (('examples/numeric-targets',examples()),('conformance/numeric-targets',corpus())):files.update((folder+'/'+n,b) for n,b in rows.items())
    for name,raw in files.items():
        path=ROOT/name
        if a.check:
            if not path.exists() or path.read_bytes()!=raw:raise ValueError('Stale numeric file: '+name)
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Numeric v1-v6 ABI, evidence and independent fixtures verified.');return 0
if __name__=='__main__':raise SystemExit(main())
