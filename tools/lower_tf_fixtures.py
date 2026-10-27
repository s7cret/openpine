"""Small independent windows reused by the corpus, CLI, web and installed suite."""
import argparse
from dataclasses import asdict
from packages.contracts import HistoricalBar,HistoricalData
from packages.contracts.contexts import MarketContext
from packages.contracts.wire import canonical
from tools.specification import ROOT

def datasets(count=16):
    if count%4 or count<4:raise ValueError('Use complete four-child windows.')
    primary=HistoricalData('primary','TEST:CHART','4',tuple(HistoricalBar(i*240000,100.,100.,100.,100.,40.) for i in range(count//4)),schema_version='openpine.historical-bars.2')
    lower=HistoricalData('lower','TEST:LOWER','1',tuple(HistoricalBar(i*60000,float(i+1),float(i+1),float(i+1),float(i+1),10.) for i in range(count)),schema_version='openpine.historical-bars.2')
    return primary,lower

def context(data):return asdict(MarketContext(data.context_id,data.symbol,data.timeframe,'uploaded-file.1'))

def source(v=6):
    return f'''//@version={v}
strategy("Lower windows",default_qty_type=strategy.fixed,initial_capital=1000)
length=input.int(2,title="Length",minval=1,maxval=20)
base=ta.sma(close,length)
[prices,flags]=request.security_lower_tf("TEST:LOWER","1",[close,close>8])
averages=request.security_lower_tf("TEST:LOWER","1",base)
if strategy.position_size==0 and array.size(prices)>0
    strategy.entry("L",strategy.long)
if strategy.position_size>0
    strategy.close("L")
plot(array.sum(prices),title="Lower sum")
plot(array.size(prices),title="Children")
plot(array.avg(averages),title="Lower SMA mean")
plot(array.size(flags)>0 and array.get(flags,0) ? 1 : 0,title="Lower condition")
plot(strategy.equity,title="Equity")
'''

def examples():
    a,b=datasets()
    rows={'parent.json':asdict(a),'lower.json':asdict(b),'contexts.json':{'schema_version':'openpine.context-inputs.1',
        'primary_context':context(a),'contexts':[{'context':context(b),'bars':'lower.json'}]},
        'expected.json':{'prices_sum':[10.,26.,42.,58.],'children':[4,4,4,4],
            'sma_mean':[2.5,6.,10.,14.],'condition':[0,0,1,1],'equity':[1000.,1000.,1000.,1000.]}}
    return {**{n:canonical(v)+b'\n' for n,v in rows.items()},**{f'v{v}.pine':source(v).encode() for v in (5,6)}}

def corpus():
    files={k:v for k,v in examples().items() if k.endswith('.json')};cases=[]
    samples=[('sum','a=request.security_lower_tf("TEST:LOWER","1",close)\nplot(array.sum(a))','float',[10.,26.,42.,58.]),
        ('int','a=request.security_lower_tf("TEST:LOWER","1",1)\nplot(array.sum(a))','int',[4]*4),
        ('bool','a=request.security_lower_tf("TEST:LOWER","1",close>8)\nplot(array.get(a,0) ? 1 : 0)','int',[0,0,1,1]),
        ('tuple','[a,b]=request.security_lower_tf("TEST:LOWER","1",[close,1])\nplot(array.sum(a)+array.sum(b))','float',[14.,30.,46.,62.]),
        ('state','f(x) =>\n    var float n=0\n    n:=n+x\n    n\na=request.security_lower_tf("TEST:LOWER","1",f(close))\nplot(array.sum(a))','float',[20.,100.,244.,452.]),
        ('window','a=request.security_lower_tf("TEST:LOWER","1",close,calc_bars_count=8)\nplot(array.size(a))','int',[0,0,4,4])]
    for v in (5,6):
        for name,body,typ,values in samples:
            path=f'v{v}-{name}.pine';files[path]=f'//@version={v}\nindicator("lower corpus")\n{body}\n'.encode()
            cases.append({'case_id':f'v{v}-{name}','language_version':v,'source':path,'bars':'parent.json','context_inputs':'contexts.json',
                'expected':{'status':'executed','title':'lower corpus','shorttitle':'','overlay':False,'observation_count':4,
                    'outputs':[{'title':'','type':typ,'values':values}],'qualifiers':['series']}})
        for name,extra in (('symbol',',ignore_invalid_symbol=true'),('currency',',currency="USD"'),('count',',calc_bars_count=0')):
            path=f'v{v}-reject-{name}.pine';files[path]=f'//@version={v}\nindicator("lower corpus")\na=request.security_lower_tf("TEST:LOWER","1",close{extra})\nplot(array.size(a))\n'.encode()
            cases.append({'case_id':f'v{v}-reject-{name}','language_version':v,'source':path,'bars':'parent.json',
                'expected':{'status':'rejected','category':'PROFILE_UNCOVERED','code':'REQUEST_SCOPE'}})
    for v in range(1,5):
        path=f'v{v}-reject-version.pine';files[path]=f'//@version={v}\nstudy("lower corpus")\nrequest.security_lower_tf("TEST:LOWER","1",close)\n'.encode()
        cases.append({'case_id':f'v{v}-reject-version','language_version':v,'source':path,'bars':'parent.json',
            'expected':{'status':'rejected','category':'PROFILE_UNCOVERED','code':'LOWER_TF_VERSION'}})
    files['cases.json']=canonical({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','basis':'Manual sums of 1..16, integer counts, boolean thresholds, triangular sums and explicit last-eight-row windows.',
            'external_pine_execution':False},'rule_refs':[],'assertion_refs':[],'cases':cases})+b'\n'
    return files

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    for folder,files in (('examples/lower-tf',examples()),('conformance/lower-tf',corpus())):
        for name,raw in files.items():
            path=ROOT/folder/name
            if a.check:
                if path.read_bytes()!=raw:raise ValueError('Lower fixture differs: '+str(path))
            else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Lower request examples and independent corpus verified.');return 0
if __name__=='__main__':raise SystemExit(main())
