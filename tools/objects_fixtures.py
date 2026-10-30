"""Owned flat-object examples and independent hand-calculated oracles."""
import argparse
from tools.specification import ROOT
from packages.contracts.wire import canonical

def routing(version):
    header=f'//@version={version}\n'+('study' if version<5 else 'indicator')+'("Object routing")\n'
    if version<5:return header+'var total=0.\nvar count=0\ntotal:=total+close\ncount:=count+1\nplot(total/count)\n'
    return header+'''type Stats
    float total=0.
    int count=0
method add(Stats self,float value)=>
    self.total:=self.total+value
    self.count:=self.count+1
    self
method mean(Stats self)=>self.total/self.count
var state=Stats.new()
alias=state
alias.add(close)
snapshot=state.copy()
snapshot.total:=0.
plot(state.mean())
'''

def fixtures():
    files={};cases=[]
    def add(v,name,body,values=None,*,types=None,code=None,category='LANGUAGE_INVALID',complete=False):
        filename=f'v{v}-{name}.pine'
        files[filename]=(body if complete else f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Objects")\n'+body+'\n').encode()
        expected=({'status':'rejected','category':category,'code':code} if code else
            {'status':'executed','title':'Object routing' if complete else 'Objects','shorttitle':'','overlay':False,'observation_count':4,
                'outputs':[{'title':'','type':t,'values':row} for t,row in zip(types or ['float']*len(values),values,strict=True)],'qualifiers':['series']*len(values)})
        cases.append({'case_id':f'v{v}-{name}','language_version':v,'source':filename,'bars':'bars.json','expected':expected})
    for v in range(1,7):
        add(v,'routing',routing(v),[[10.,15.,20.,25.]],complete=True)
        if v<5:
            add(v,'reject-type','type T\n    int x\nplot(1)',code='UDT_VERSION');continue
        add(v,'defaults','type T\n    float p=close\n    int i=bar_index\nvar a=T.new()\nb=T.new()\nplot(a.p)\nplot(b.p)\nplot(b.i)',[[10.]*4,[10.,20.,30.,40.],[0,1,2,3]],types=['float','float','int'])
        add(v,'fields','type T\n    int n=2\n    float p\n    bool active\n    string name\na=T.new(p=close)\nplot(a.n)\nplot(a.p)\nplot(a.active ? 1 : 0)\nplot(na(a.name) ? 1 : 0)',[[2]*4,[10.,20.,30.,40.],[0]*4,[1]*4],types=['int','float','int','int'])
        add(v,'aliases','type T\n    float p=0.\nvar a=T.new()\nb=a\nb.p:=close\nc=a.copy()\nc.p:=99.\nplot(a.p)\nplot(c.p)',[[10.,20.,30.,40.],[99.]*4])
        add(v,'order','var c=array.new_int(1,0)\nnext()=>\n    array.set(c,0,array.get(c,0)+1)\n    array.get(c,0)\ntype T\n    int a=7\n    int b=8\nx=T.new(b=next(),a=next())\nplot(x.a*10+x.b)',[[21,43,65,87]],types=['int'])
        add(v,'nominal-overloads','type A\n    int x\ntype B\n    int x\nf(A a)=>a.x+1\nf(B b)=>b.x+2\nplot(f(A.new(1)))\nplot(f(B.new(1)))',[[2]*4,[3]*4],types=['int','int'])
        add(v,'udf-methods','type T\n    float x=0.\nmethod add(T self,float value)=>\n    self.x:=self.x+value\n    self\nf(T self)=>self\nvar a=T.new()\nplot(f(a).add(close).x)',[[10.,30.,60.,100.]])
        add(v,'copy-override','type T\n    int x\nmethod copy(T self)=>T.new(self.x+1)\na=T.new(1)\nb=a.copy()\nc=T.copy(a)\nplot(b.x)\nplot(c.x)',[[2]*4,[1]*4],types=['int','int'])
        add(v,'object-history','type T\n    float p\na=T.new(close)\nplot(bar_index>0 ? (a[1]).p : 0.)',[[0.,10.,20.,30.]])
        add(v,'alias-history','type T\n    float p\nvar a=T.new(0.)\na.p:=close\nx=a.p\nplot(bar_index>0 ? (a[1]).p : 0.)\nplot(x[1])',[[0.,20.,30.,40.],[None,10.,20.,30.]])
        add(v,'nullable','type T\n    int x\na=bar_index>0 ? T.new(bar_index) : na\nplot(na(a) ? 0 : a.x)',[[0,1,2,3]],types=['int'])
        add(v,'loop-mutation','type T\n    float total=0.\nvar a=T.new()\nfor i=1 to 2\n    a.total:=a.total+close\nplot(a.total)',[[20.,60.,120.,200.]])
        rejections=[
            ('duplicate-type','type T\n    int x\ntype T\n    int y\nplot(1)','UDT_NAME','LANGUAGE_INVALID'),
            ('duplicate-field','type T\n    int x\n    int x\nplot(1)','UDT_DUPLICATE_FIELD','LANGUAGE_INVALID'),
            ('reference-field','type T\n    array<float> x\nplot(1)','UDT_FIELD_TYPE','PROFILE_UNCOVERED'),
            ('recursive-field','type T\n    T next\nplot(1)','UDT_FIELD_TYPE','PROFILE_UNCOVERED'),
            ('field-default','type T\n    float x=close+1\nplot(1)','UDT_FIELD_DEFAULT','PROFILE_UNCOVERED'),
            ('function-default','type T\n    int x=math.abs(-1)\nplot(1)','UDT_FIELD_DEFAULT','PROFILE_UNCOVERED'),
            ('unknown-field','type T\n    int x\na=T.new()\nplot(a.bad)','UDT_UNKNOWN_FIELD','LANGUAGE_INVALID'),
            ('field-type','type T\n    int x\na=T.new()\na.x:="bad"\nplot(1)','UDT_FIELD_TYPE','LANGUAGE_INVALID'),
            ('arguments','type T\n    int x\na=T.new(bad=missing)\nplot(1)','UNKNOWN_ARGUMENT','LANGUAGE_INVALID'),
            ('nominal','type A\n    int x\ntype B\n    int x\nf(A a)=>a.x\nplot(f(B.new(1)))','FUNCTION_ARGUMENT_TYPE','LANGUAGE_INVALID'),
            ('simple','type T\n    int x\nf(simple T a)=>a.x\nplot(1)','UDT_QUALIFIER','LANGUAGE_INVALID'),
            ('history','type T\n    float x\na=T.new(close)\nplot(a.x[1])','UDT_FIELD_HISTORY_VERSION' if v==6 else 'UDT_FIELD_HISTORY_PROFILE','LANGUAGE_INVALID' if v==6 else 'PROFILE_UNCOVERED'),
            ('requests','type T\n    float x\na=T.new(close)\nplot(request.security("TEST:A","2",a.x))','UDT_REQUEST','PROFILE_UNCOVERED'),
            ('equality','type T\n    int x\na=T.new()\nplot(a==a)','UDT_EQUALITY','PROFILE_UNCOVERED'),
            ('missing','type T\n    int x\nT a=na\nplot(a.x)','UDT_NA','RUNTIME_ERROR'),
            ('tuple','type T\n    int x\na=T.new()\n[x,y]=[a,1]\nplot(y)','UDT_TUPLE','PROFILE_UNCOVERED'),
            ('object-array','type T\n    int x\na=array.new<T>()\nplot(1)','ARRAY_ELEMENT','PROFILE_UNCOVERED')]
        for name,body,code,category in rejections:add(v,'reject-'+name,body,code=code,category=category)
    files['bars.json']=(ROOT/'conformance/functions/fixed.json').read_bytes()
    files['cases.json']=canonical({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','basis':'Four closes 10,20,30,40. Hand-computed cumulative totals/means, source-order counter, alias/copy effects and historical reference values. No runtime-generated expectations.','external_pine_execution':False},
        'rule_refs':[],'assertion_refs':[],'cases':cases})+b'\n'
    return files

def corpus_sources():
    files={f'object-routing-v{v}.pine':routing(v).encode() for v in range(1,7)}
    for v in (5,6):
        files[f'object-strategy-v{v}.pine']=(f'//@version={v}\n'
            'strategy("Object strategy",initial_capital=1000,default_qty_type=strategy.fixed,default_qty_value=2,commission_type=strategy.commission.percent,commission_value=1)\n'
            'type TradeState\n    float units=2.\n    bool entered=false\nmethod size(TradeState self)=>self.units\n'
            'var state=TradeState.new()\nif bar_index==0\n    state.entered:=true\n    strategy.entry("L",strategy.long,qty=state.size())\n'
            'if bar_index==3 and state.entered\n    strategy.close("L")\nplot(strategy.equity)\n').encode()
    return files

def corpus_cases(record):
    from tools.semantic_core_fixtures import corpus_cases as previous
    result=previous(record)
    for c in result:
        c['id']=c['id'].replace('semantic-','object-');c['family']=c['family'].replace('semantic-','object-')
        c['files']['source']=record('examples/corpus/'+c['id']+'.pine')
        if c['family']=='object-routing':c['oracle']={'reason':'Cumulative close means: 10,15,20,25. Scalar equivalents v1-v4; nominal alias plus independent copy v5/v6.','outputs':[[10.,15.,20.,25.]]}
        else:c['oracle']['reason']='State object supplies quantity2 and remembers entry. Entry2/exit2 at open10; commissions .2 each side; close mark-to-market.'
    return result

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args(argv)
    files={'conformance/objects/'+n:b for n,b in fixtures().items()}
    files.update({'examples/corpus/'+n:b for n,b in corpus_sources().items()})
    for name,raw in files.items():
        path=ROOT/name
        if args.check:assert path.read_bytes()==raw,name
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Owned flat-object fixtures and independent manual expectations verified.')

if __name__=='__main__':main()
