"""Owned method programs and hand-calculated expectations, without Pine execution."""
import argparse
from tools.specification import ROOT
from packages.contracts.wire import canonical


def routing(v):
    h=f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Method routing")\n'
    if v>=5:
        return h+'''method enqueue(array<float> a,float x,int cap=3) =>
    a.push(x)
    if a.size()>cap
        a.shift()
    a
method offset(int x)=>x+1
var a=array.new_float(0)
plot(a.enqueue(close).avg()+(2).offset())
'''
    return h+'offset(int x)=>x+1\ncount=bar_index<2 ? bar_index+1 : 3\nmean=(close+nz(close[1])+nz(close[2]))/count\nplot(mean+offset(2))\n'


def fixtures():
    files={};cases=[]
    def add(v,name,body,values=None,*,types=None,qualifiers=None,reject=None,complete=False):
        filename=f'v{v}-{name}.pine';files[filename]=(body if complete else f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Methods")\n'+body+'\n').encode()
        expected=({'status':'rejected','category':'LANGUAGE_INVALID','code':reject} if reject else
            {'status':'executed','title':'Method routing' if complete else 'Methods','shorttitle':'','overlay':False,'observation_count':4,
             'outputs':[{'title':'','type':typ,'values':row} for typ,row in zip(types or ['float']*len(values),values,strict=True)],'qualifiers':qualifiers or ['series']*len(values)})
        cases.append({'case_id':f'v{v}-{name}','language_version':v,'source':filename,'bars':'bars.json','expected':expected})
    for v in range(1,7):
        add(v,'routing',routing(v),[[13.,18.,23.,33.]],complete=True)
        if v<5:
            add(v,'reject-declaration','method f(int x)=>x\nplot(1)',reject='METHOD_VERSION')
            add(v,'reject-instance','plot(close.f())',reject='METHOD_VERSION')
            continue
        add(v,'scalar','method f(int x)=>x+1\nmethod f(float x)=>x+0.5\nplot(close.f())',[[10.5,20.5,30.5,40.5]])
        add(v,'once','var a=array.new_int(1,0)\nnext()=>\n    array.set(a,0,array.get(a,0)+1)\n    array.get(a,0)\nmethod pair(int self,int x,int y,int z=7)=>self*1000+x*100+y*10+z\nplot(next().pair(y=next(),x=next()))',[[1327,4657,7987,11317]],types=['int'])
        add(v,'copy','a=array.from(1,2)\nb=a.copy()\nb.set(0,9)\nplot(a.get(0))\nplot(b.get(0))',[[1]*4,[9]*4],types=['int','int'])
        add(v,'override','method get(array<int> a,int index)=>77\na=array.from(1,2)\nplot(a.get(0))\nplot(array.get(a,0))',[[77]*4,[1]*4],types=['int','int'])
        add(v,'defaults','method add(int self,int x=3)=>self+x\nplot((2).add())',[[5]*4],types=['int'],qualifiers=['const'])
        add(v,'history','var a=array.new_float(1,0.)\na.set(0,close)\nplot(a.get(0)[1])\nplot(bar_index>0 ? a[1].get(0) : 0.)',[[None,10.,20.,30.],[0.,20.,30.,40.]])
        add(v,'state','method accumulate(int x)=>\n    var n=0\n    n:=n+x\n    n\nplot((2).accumulate())\nplot((3).accumulate())',[[2,4,6,8],[3,6,9,12]],types=['int','int'])
        for name,body,code in [
            ('untyped','method f(x)=>x\nplot(1)','METHOD_RECEIVER'),
            ('default-receiver','method f(int x=2)=>x\nplot(1)','METHOD_RECEIVER'),
            ('duplicate','method f(int x)=>x\nmethod f(int y)=>y\nplot(1)','METHOD_OVERLOAD_DUPLICATE'),
            ('ambiguous','method f(int self,int x,float y)=>x+y\nmethod f(int self,float x,int y)=>x+y\nplot((1).f(2,3))','METHOD_AMBIGUOUS'),
            ('receiver','method f(float x)=>x\nplot((1).f())','METHOD_RECEIVER_TYPE'),
            ('missing','plot(close.missing())','METHOD_UNAVAILABLE'),
            ('arguments','method f(int x)=>x\nplot((1).f(bad=2))','UNKNOWN_ARGUMENT'),
            ('recursive','method f(int x)=>x.f()\nplot(1)','FUNCTION_RECURSION')]:add(v,'reject-'+name,body,reject=code)
    files['bars.json']=(ROOT/'conformance/functions/fixed.json').read_bytes()
    files['cases.json']=canonical({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','basis':'close 10,20,30,40; queue means 10,15,20,30 plus3; explicit scalar results, source-order counters, aliases and prior object references. No runtime oracle.','external_pine_execution':False},
        'rule_refs':[],'assertion_refs':[],'cases':cases})+b'\n'
    return files


def corpus_sources():
    files={f'method-routing-v{v}.pine':routing(v).encode() for v in range(1,7)}
    for v in (5,6):
        files[f'method-strategy-v{v}.pine']=(f'//@version={v}\n'
            'strategy("Method strategy",initial_capital=1000,default_qty_type=strategy.fixed,default_qty_value=2,commission_type=strategy.commission.percent,commission_value=1)\n'
            'method size(int qty)=>qty\nmethod size(float qty)=>qty\nif bar_index==0\n    strategy.entry("L",strategy.long,qty=(2).size())\n'
            'if bar_index==3\n    strategy.close("L")\nplot(strategy.equity)\n').encode()
    return files


def corpus_cases(record):
    from tools.semantic_core_fixtures import corpus_cases as previous
    result=previous(record)
    for c in result:
        c['id']=c['id'].replace('semantic-','method-');c['family']=c['family'].replace('semantic-','method-')
        c['files']['source']=record('examples/corpus/'+c['id']+'.pine')
        if c['family']=='method-routing':c['oracle']={'reason':'Queue means 10,15,20,30 plus constant offset3. v1-v4 use scalar history equivalents.','outputs':[[13.,18.,23.,33.]]}
        else:c['oracle']['reason']='Integer method selects quantity2. Entry2/exit2 at open10; commissions .2 each side; close mark-to-market.'
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    files={'conformance/methods/'+n:b for n,b in fixtures().items()}
    files.update({'examples/corpus/'+n:b for n,b in corpus_sources().items()})
    for n,b in files.items():
        path=ROOT/n
        if a.check:assert path.read_bytes()==b,n
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b)
    print('Owned method fixtures and independent expectations verified.')


if __name__=='__main__':main()
