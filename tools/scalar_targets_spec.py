"""Reproduce version-specific scalar ABI, target policy and independent cases."""
import argparse
from packages.contracts.scalar_functions import apis
from packages.contracts.scalar_targets import policy
from packages.contracts.wire import canonical,digest,read_json
from tools.specification import ROOT
from tools.function_targets_spec import source as function_source
from tools.primary_targets_spec import examples as primary_examples

def source(v,mode='inputs',*,lower=False):
    text=function_source(v,mode,lower=lower)
    name='tostring' if v<5 else 'str.tostring'
    if mode=='references':
        ref='period' if v<4 else 'timeframe.period'
        text=text.replace('tf=interval(choose,"2")',f'tf=interval(choose,"2")+{name}(0,"#")=="never" ? "2" : interval(choose,"2")')
    elif mode=='inputs':
        # Empty raw settings retain primary inheritance; explicit settings pass
        # through an independently testable pure scalar kernel inside a UDF.
        text=text.replace('identity(value) => value',f'identity(value) => value+{name}(0,"#")=="never" ? "2" : value')
    else:text=text.replace('sym=',f'ignored={name}(1)\nsym=',1)
    if v>=5:
        text=text.replace('identity(value) => value+', 'identity(value) => str.replace_all(value,"never","unused")+')
    return text

def examples():
    files={n:v for n,v in primary_examples().items() if n.endswith('.json')}
    for v in range(1,7):
        for mode in ('references','inputs'):files[f'v{v}-{mode}.pine']=source(v,mode).encode()
    for v in (5,6):files[f'v{v}-lower.pine']=source(v,lower=True).encode()
    return files

def corpus():
    files=examples();cases=[]
    def add(v,key,text,types,values,reject=None,qualifiers=None):
        name=f'v{v}-{key}.pine';files[name]=text.encode()
        expected={'status':'rejected','category':reject[0],'code':reject[1]} if reject else {'status':'executed','title':'Scalars','shorttitle':'','overlay':False,'observation_count':8,
            'outputs':[{'title':'','type':t,'values':[value]*8} for t,value in zip(types,values,strict=True)],'qualifiers':qualifiers or ['simple']*len(types)}
        row={'case_id':f'v{v}-{key}','language_version':v,'source':name,'bars':'b1.json','expected':expected}
        cases.append(row)
    for v in range(1,7):
        head=f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Scalars")\n';name='tostring' if v<5 else 'str.tostring'
        add(v,'format',head+f'plot({name}(3.99,"#")=="4" ? 1 : 0)\nplot({name}(-1.125,"#.00")=="-1.13" ? 1 : 0)\nplot({name}(na)=="NaN" ? 1 : 0)\n',['int']*3,[1]*3)
        for mode in ('references','inputs'):
            key=f'v{v}-target-{mode}';text=source(v,mode).replace('Function targets','Scalars');files[key+'.pine']=text.encode()
            cases.append({'case_id':key,'language_version':v,'source':key+'.pine','bars':'b1.json','expected':{'status':'executed','title':'Scalars','shorttitle':'','overlay':False,'observation_count':8,'outputs':[{'title':'','type':'float','values':list(map(float,range(100,108)))}],'qualifiers':['series']}})
        add(v,'format-invalid',head+f'plot({name}(3.0,"percent")=="3" ? 1 : 0)\n',[],[],('RUNTIME_ERROR','SCALAR_LIMIT'))
        if v>=4:
            add(v,'unicode',head+'plot(str.length("A😀é"))\nplot(str.length(str.replace_all("aaa","aa","b")))\nplot(str.length(str.replace_all("ab","","-")))\n',['int']*3,[4,2,5],qualifiers=['const','simple','simple'])
        if v>=5:
            add(v,'substring',head+'plot(str.substring("A😀é",1,2)=="😀" ? 1 : 0)\nplot(str.substring("abc",3)=="" ? 1 : 0)\nplot(str.contains("Abc","bc") ? 1 : 0)\n',['int']*3,[1]*3,qualifiers=['const']*3)
            add(v,'timeframe',head+'plot(timeframe.in_seconds())\nplot(timeframe.in_seconds("M"))\nplot(timeframe.from_seconds(604799)=="7D" ? 1 : 0)\nplot(timeframe.from_seconds(604800)=="1W" ? 1 : 0)\n',['int']*4,[60,2628003,1,1])
            add(v,'substring-invalid',head+'plot(str.length(str.substring("abc",-1)))\n',[],[],('RUNTIME_ERROR','SCALAR_LIMIT'))
        else:
            add(v,'unavailable',head+'plot(timeframe.in_seconds("1"))\n',[],[],('LANGUAGE_INVALID','SIGNATURE_UNAVAILABLE'))
    files['cases.json']=canonical({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','basis':'Hand-calculated decimal HALF_UP, Unicode code points, literal substring and replacement, documented seconds examples and direct primary closes.','external_pine_execution':False},'rule_refs':[],'assertion_refs':[],'cases':cases})+b'\n'
    return files

def profile():
    lock=read_json(ROOT/'spec/lock.json');evidence=read_json(ROOT/'spec/scalar-targets-evidence.json');versions={}
    for v in range(1,7):
        refs=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json');known={r['entry_id']:r['source_record_sha256'] for r in refs['entries']}
        rows=evidence['versions'][str(v)]['entries']
        assert all(known[r['entry_id']]==r['source_record_sha256'] for r in rows)
        assert {a['name'] for a in apis(v)}<={r['name'] for r in rows}
        versions[str(v)]={'policy':policy(v),'apis':apis(v),'reference_sha256':digest(refs)}
    return {'schema_version':'openpine.scalar-target-profile.1','source_lock_sha256':digest(lock),'evidence_sha256':digest(evidence),
        'function_profile_sha256':digest(read_json(ROOT/'spec/function-targets-profile.json')),'versions':versions,
        'decisions':['Only APIs present in the saved version reference are admitted. v2 inherits the explicitly disclosed v1 compatibility baseline.',
            'Qualifiers use const overloads where documented, otherwise simple floor; per-bar operands produce series. Early qualifier behavior is bounded reconstruction.',
            'Unicode code points, strict substring errors, empty-target replacement and HALF_UP ties are adopted decisions where artifacts are incomplete.',
            'Numeric pattern subset only; no collections, enums, format.mintick, format.percent or format.volume.',
            'Default in_seconds binds the existing primary period reference. Explicit empty timeframe argument is rejected.',
            'Conversions admit second/multiplier strings; market data requests still admit only existing minutes and single D/W/M. Single 1D/1W/1M normalize at the new resolver boundary.',
            'Month conversion uses documented 2628003 seconds. Calendar candle boundaries retain real variable-length months.',
            'New closed scalar target profile; all arguments/branches/UDF bodies admitted before data I/O. String allocation and nested-call work bounded.',
            'Exact TradingView equivalence is deferred; older overlays, bundles and source lock remain unchanged.']}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    files={'spec/scalar-targets-profile.json':canonical(profile())+b'\n'}
    for folder,rows in (('examples/scalar-targets',examples()),('conformance/scalar-targets',corpus())):files.update((folder+'/'+n,raw) for n,raw in rows.items())
    for name,raw in files.items():
        path=ROOT/name
        if a.check:
            if not path.exists() or path.read_bytes()!=raw:raise ValueError('Stale scalar-target file: '+name)
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Scalar v1-v6 ABI, evidence and independent fixtures verified.');return 0
if __name__=='__main__':raise SystemExit(main())
