"""Reproduce context dependency cases from independent Fraction/list oracles."""
import argparse
from fractions import Fraction
from pathlib import Path
from tools.specification import ROOT,encode

OHLCV=((10,13,8,11,100),(11,16,10,14,120),(14,15,9,10,90),
       (10,20,9,19,150),(19,21,16,18,180),(18,24,17,22,200))

def mapped(values,version):
    return [values[i//2] if version<3 else None if i==0 else values[(i-1)//2] for i in range(12)]

def prepare():
    symbol='TEST:GRAPH'
    def bars(identifier,timeframe,rows):
        return {'schema_version':'openpine.historical-bars.2','context_id':identifier,'symbol':symbol,'timeframe':str(timeframe),
            'bars':[dict(time=i*timeframe*60000,open=float(o),high=float(h),low=float(l),close=float(c),volume=float(v)) for i,(o,h,l,c,v) in enumerate(rows)]}
    def context(identifier,tf):
        return dict(context_id=identifier,symbol=symbol,timeframe=str(tf),source='uploaded-file.1',timezone='UTC',session='24x7',time_rule='fixed-duration-utc.1')
    files={'bars.json':encode(bars('graph',1,[(99+i,102+i,98+i,100+i,10) for i in range(12)])),
           'higher.json':encode(bars('higher',2,OHLCV)),
           'contexts.json':encode({'schema_version':'openpine.context-inputs.1','primary_context':context('graph',1),
                'contexts':[{'context':context('higher',2),'bars':'higher.json'}]})}
    mid=[Fraction(h+l,2) for o,h,l,c,v in OHLCV]
    sma=[None,*[float((a+b)/2) for a,b in zip(mid,mid[1:])]]
    wma=[None,*[float((a+2*b)/3) for a,b in zip(mid,mid[1:])]]
    cum=[];total=0
    for o,h,l,c,v in OHLCV:total+=v;cum.append(float(total))
    rows=[]
    for version in range(1,7):
        ta='ta.' if version>=5 else '';api='request.security' if version>=5 else 'security';decl='indicator' if version>=5 else 'study'
        def request(expression):return f'{api}("{symbol}","2",{expression})'
        samples=[
            ('transitive-history',f'base=(high+low)/2\navg={ta}sma(base,2)',request('avg[1]'),'float',[None,*sma[:-1]]),
            ('weighted-history',f'base=(high+low)/2\navg={ta}wma(base,2)',request('avg[1]'),'float',[None,*wma[:-1]]),
            ('bool','event='+request('close>open'),'event ? 1 : 0','int',[True,True,False,True,False,True]),
            ('mixed-tuple',f'[price,event,count]={request("[close,close>open,1]")}', 'price','float',[float(c) for o,h,l,c,v in OHLCV]),
            ('private-var','f(x) =>\n    var float n=0\n    n:=n+x\n    n',request('f(volume)'),'float',cum),
            ('captured-udf','base=close*2\nf(x) =>\n    x+base',request('f(open)'),'float',[float(o+2*c) for o,h,l,c,v in OHLCV]),
            ('diamond',f'base={ta}cum(volume)\na=base+1\nb=base-1',request('a+b+base'),'float',[3*x for x in cum]),
            ('conditional-history','',request(f'(close>open ? {ta}cum(volume) : -close)[1]'),'float',[None,100.,220.,-10.,370.,-18.]),
            ('tuple-dependency','[hi,lo]=[high,low]',request('hi+lo'),'float',[float(h+l) for o,h,l,c,v in OHLCV]),
            ('nested-functions','base=close*2\nf(x) => x+base\ng(x) => f(x)*2',request('g(open)'),'float',[float(2*(o+2*c)) for o,h,l,c,v in OHLCV]),
        ]
        for name,prefix,expr,typ,expected in samples:
            body=f'//@version={version}\n{decl}("context graph")\n{prefix}\nplot({expr})\n'
            values=mapped(expected,version)
            if name=='bool':values=[1 if v else 0 for v in values]
            rows.append({'case_id':f'v{version}-{name}','language_version':version,'source':f'v{version}-{name}.pine',
                'bars':'bars.json','context_inputs':'contexts.json','numeric_tolerance':{'absolute':1e-12,'relative':1e-12},
                'expected':{'status':'executed','title':'context graph','shorttitle':'','overlay':False,'observation_count':12,
                    'outputs':[{'title':'','type':typ,'values':values}],'qualifiers':['series']}})
            files[rows[-1]['source']]=body.encode()
        failures={
            'mutable':'n=close\na=n+1\nn:=open\nplot('+request('a')+')',
            'persistent':'var n=close\nplot('+request('n')+')',
            'nested':'n='+request('close')+'\nplot('+request('n')+')',
            'dynamic-offset':'offset=1\nplot('+request('close[offset]')+')',
            'effectful-udf':'f(x) =>\n    '+request('x')+'\nplot('+request('f(close)')+')',
        }
        for name,body in failures.items():
            filename=f'v{version}-reject-{name}.pine';files[filename]=f'//@version={version}\n{decl}("context graph")\n{body}\n'.encode()
            rows.append({'case_id':f'v{version}-reject-{name}','language_version':version,'source':filename,'bars':'bars.json',
                'expected':{'status':'rejected','category':'PROFILE_UNCOVERED','code':'REQUEST_SCOPE'}})
    files['cases.json']=encode({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','basis':'Exact Fraction/list arithmetic on six declared requested bars, manual event/visibility mapping; never runtime-produced expectations.','external_pine_execution':False},
        'rule_refs':[],'assertion_refs':[],'cases':rows})
    return files

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args(argv)
    target=ROOT/'conformance/context-graph'
    for name,raw in prepare().items():
        path=target/name
        if args.check:
            if not path.exists() or path.read_bytes()!=raw:raise ValueError('Context graph fixture differs: '+name)
        else:target.mkdir(exist_ok=True);path.write_bytes(raw)
    print('Context graph independent fixtures verified.');return 0

if __name__=='__main__':raise SystemExit(main())
