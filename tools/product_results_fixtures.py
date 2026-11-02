"""Owned mixed-result programs and finite arithmetic oracles, without execution."""
import argparse
from tools.specification import ROOT
from packages.contracts.wire import canonical

def routing(v):
    header=f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Product routing")\n'
    setting='scale=input(2,"Scale")\n' if v<5 else 'scale=input.int(2,"Scale")\n'
    if v<=3:
        body='pair(x)=>[x,bar_index+1]\n[value,count]=pair(close*scale)\n'
        if v>=2:body+='label=for i=0 to 1\n    "ready"\n'
    elif v==4:body='pair(x)=>[array.new_float(1,x),bar_index+1]\n[values,count]=pair(close*scale)\nvalue=array.get(values,0)\n'
    elif v==5:body='''enum Side
    up
type N
    float value
pair(x)=>
    for i=0 to 1
        [N.new(x),Side.up,bar_index+1]
[node,side,count]=pair(close*scale)
value=side==Side.up ? node.value : 0.
'''
    else:body='''type N
    float value
method pair(N self)=>[self,map.new<string,N>(),matrix.new<float>(1,1,self.value),bar_index+1]
[node,book,grid,count]=N.new(close*scale).pair()
book.put("current",node)
value=for i=0 to 1
    grid.get(0,0)
'''
    return header+setting+body+'var total=0.\ntotal:=total+value\nplot(total)\nplot(value)\nplot(count)\n'

def corpus_sources():
    result={f'product-results-routing-v{v}.pine':routing(v).encode() for v in range(1,7)}
    for v in (5,6):
        body='''type Order
    float qty
make()=>[Order.new(2.),array.from(2.)]
[order,values]=make()
if bar_index==0
    strategy.entry("L",strategy.long,qty=order.qty)
if bar_index==3
    strategy.close("L")
plot(strategy.equity)
'''
        result[f'product-results-strategy-v{v}.pine']=(f'//@version={v}\nstrategy("Product strategy",initial_capital=1000,default_qty_type=strategy.fixed,default_qty_value=2,commission_type=strategy.commission.percent,commission_value=1)\n'+body).encode()
    return result

def corpus_cases(record):
    from tools.semantic_core_fixtures import corpus_cases as previous
    result=previous(record)
    for c in result:
        c['id']=c['id'].replace('semantic-','product-results-');c['family']=c['family'].replace('semantic-','product-results-')
        c['files']['source']=record('examples/corpus/'+c['id']+'.pine')
        if c['family']=='product-results-routing':
            c['inputs']={'scale':2};c['oracle']={'reason':'Four closes10/20/30/40 times2, cumulative20/60/120/200 and counts1/2/3/4; older scalar controls, v4 arrays, v5 mixed enum/UDT loop tuple, v6 method map/matrix tuple.',
                'outputs':[[20.,60.,120.,200.],[20.,40.,60.,80.],[1,2,3,4]]}
        else:c['oracle']['reason']='Mixed reference tuple supplies Order quantity2. Entry2 at10 and exit2 at10, commission .2 each; original independent broker arithmetic.'
    return result

def fixtures():
    files={};cases=[]
    def add(v,name,body,values=None,types=None,code=None,category='LANGUAGE_INVALID',complete=False,qualifiers=None):
        path=f'v{v}-{name}.pine';files[path]=(body if complete else f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Product results")\n'+body+'\n').encode()
        expected={'status':'rejected','category':category,'code':code} if code else {
            'status':'executed','title':'Product routing' if complete else 'Product results','shorttitle':'','overlay':False,'observation_count':4,
            'outputs':[{'title':'','type':t,'values':row} for t,row in zip(types or ['int']*len(values),values,strict=True)],'qualifiers':qualifiers or ['series']*len(values)}
        case={'case_id':f'v{v}-{name}','language_version':v,'source':path,'bars':'bars.json','expected':expected}
        if complete:case['inputs']={'scale':2}
        cases.append(case)
    for v in range(1,7):
        add(v,'routing',routing(v),[[20.,60.,120.,200.],[20.,40.,60.,80.],[1,2,3,4]],['float','float','int'],complete=True)
        add(v,'scalar-control','pair()=>[close,2]\n[a,b]=pair()\nplot(a*b)',[[20.,40.,60.,80.]],['float'])
        add(v,'reject-nested','[a,b]=[[1,2],3]\nplot(b)',code='TUPLE_TYPE')
        add(v,'reject-bare-na','[a,b]=[na,2]\nplot(b)',code='TUPLE_TYPE')
        if v>=2:add(v,'string-loop','s=for i=0 to 2\n    "ready"\nplot(s=="ready" ? 1 : 0)',[[1]*4])
        if v<4:continue
        add(v,'arrays','pair()=>[array.new_int(1,7),bar_index]\n[a,n]=pair()\nplot(array.get(a,0)+n)',[[7,8,9,10]])
        add(v,'underscore','pair()=>[array.new_int(1,7),close]\n[_,n]=pair()\nplot(n)',[[10.,20.,30.,40.]],['float'])
        add(v,'array-loop','a=for i=0 to 2\n    array.new_int(1,i)\nplot(array.get(a,0))',[[2]*4])
        if v<5:continue
        add(v,'enum-udt','enum Side\n    up\ntype N\n    int x\nf()=>[N.new(7),Side.up]\n[a,b]=f()\nplot(b==Side.up ? a.x : 0)',[[7]*4])
        add(v,'nominal-loop','type N\n    int x\na=for i=0 to 2\n    N.new(i)\nplot(a.x)',[[2]*4])
        add(v,'empty','type N\n    int x\n[a,b]=while false\n    [N.new(7),true]\nplot(na(a) ? 1 : 0)\nplot(b ? 1 : 0)',[[1]*4,[0]*4])
        add(v,'string-empty','s=while false\n    "ready"\nplot(na(s) ? 1 : 0)',[[1]*4])
        add(v,'break-continue','type N\n    int x\n[a,n]=for i=0 to 4\n    if i==1\n        continue\n    if i==3\n        break\n    [N.new(i),i]\nplot(a.x+n)',[[4]*4])
        add(v,'history','type N\n    int x\nf()=>[N.new(bar_index),array.from(bar_index)]\n[a,b]=f()\nplot(bar_index>0 ? (a[1]).x : -1)\nplot(bar_index>0 ? (b[1]).get(0) : -1)',[[-1,0,1,2],[-1,0,1,2]])
        add(v,'collections','f()=>[map.new<string,int>(),matrix.new<int>(1,1,4)]\n[m,x]=f()\nm.put("k",3)\nplot(m.get("k")+x.get(0,0))',[[7]*4])
    files['bars.json']=(ROOT/'conformance/functions/fixed.json').read_bytes()
    files['cases.json']=canonical({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','basis':'Finite closes10/20/30/40; source-order, nominal identities, last completed loop tail and aliases. No external Pine execution.','external_pine_execution':False},
        'rule_refs':[],'assertion_refs':[],'cases':cases})+b'\n'
    return files

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    files={'conformance/product-results/'+n:b for n,b in fixtures().items()};files.update({'examples/corpus/'+n:b for n,b in corpus_sources().items()})
    for name,raw in files.items():
        path=ROOT/name
        if a.check:assert path.read_bytes()==raw,name
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Owned product result fixtures and manual expectations verified.')
if __name__=='__main__':main()
