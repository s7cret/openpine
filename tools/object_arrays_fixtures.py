"""Owned nominal queues and hand-derived expectations, never runtime oracles."""
import argparse
from tools.specification import ROOT
from packages.contracts.wire import canonical

def routing(v):
    header=f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Object queue")\n'
    if v<4:return header+'total=close+nz(close[1])+nz(close[2])\ncount=bar_index<2 ? bar_index+1 : 3\nplot(total/count)\n'
    if v==4:return header+'var values=array.new_float(0)\narray.push(values,close)\nif array.size(values)>3\n    array.shift(values)\nplot(array.avg(values))\n'
    return header+'''type Sample
    float price
    int index
method enqueue(array<Sample> self,float price,int index)=>
    self.push(Sample.new(price,index))
    if self.size()>3
        self.shift()
    self
method mean(array<Sample> self)=>
    total=0.
    for point in self
        total:=total+point.price
    total/self.size()
var points=array.new<Sample>()
points.enqueue(close,bar_index)
plot(points.mean())
'''

def corpus_sources():
    result={f'object-queue-routing-v{v}.pine':routing(v).encode() for v in range(1,7)}
    for v in (5,6):
        result[f'object-queue-strategy-v{v}.pine']=(f'//@version={v}\n'
            'strategy("Object queue strategy",initial_capital=1000,default_qty_type=strategy.fixed,default_qty_value=2,commission_type=strategy.commission.percent,commission_value=1)\n'
            'type Signal\n    float units=2.\n    int submitted=0\nvar pending=array.new<Signal>()\n'
            'if bar_index==0\n    pending.push(Signal.new(2.,bar_index))\n'
            'if pending.size()>0\n    signal=pending.shift()\n    strategy.entry("L",strategy.long,qty=signal.units)\n'
            'if bar_index==3\n    strategy.close("L")\nplot(strategy.equity)\n').encode()
    return result

def corpus_cases(record):
    from tools.semantic_core_fixtures import corpus_cases as previous
    result=previous(record)
    for c in result:
        c['id']=c['id'].replace('semantic-','object-queue-');c['family']=c['family'].replace('semantic-','object-queue-')
        c['files']['source']=record('examples/corpus/'+c['id']+'.pine')
        if c['family']=='object-queue-routing':c['oracle']={'reason':'Last up to three closes: means 10,15,20,30. Scalar history v1-v3, primitive array v4, bounded nominal queue v5/v6.','outputs':[[10.,15.,20.,30.]]}
        else:c['oracle']['reason']='A queued signal supplies quantity2 at bar0; consume once, entry fills bar1 open10, close bar3 fills bar4 open10; .2 commission each side.'
    return result

def fixtures():
    files={};cases=[]
    def add(v,name,body,values=None,types=None,code=None,category='LANGUAGE_INVALID',complete=False):
        filename=f'v{v}-{name}.pine';files[filename]=(body if complete else f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Object arrays")\n'+body+'\n').encode()
        expected={'status':'rejected','category':category,'code':code} if code else {'status':'executed','title':'Object queue' if complete else 'Object arrays','shorttitle':'','overlay':False,'observation_count':4,'outputs':[{'title':'','type':t,'values':row} for t,row in zip(types or ['float']*len(values),values,strict=True)],'qualifiers':['series']*len(values)}
        cases.append({'case_id':f'v{v}-{name}','language_version':v,'source':filename,'bars':'bars.json','expected':expected})
    for v in range(1,7):
        add(v,'queue',routing(v),[[10.,15.,20.,30.]],complete=True)
        if v<5:
            add(v,'reject-version','type T\n    int x\na=array.new<T>()\nplot(1)',code='UDT_VERSION');continue
        positives=[
            ('empty','type T\n    int x\na=array.new<T>()\nplot(a.size())',[[0]*4],['int']),
            ('typed-na','type T\n    int x\narray<T> a=na\nplot(na(a) ? 1 : 0)',[[1]*4],['int']),
            ('missing-elements','type T\n    int x\na=array.new<T>(2)\nplot(na(a.get(0)) ? 1 : 0)',[[1]*4],['int']),
            ('repeated-reference','type T\n    int x=1\na=array.new<T>(3,T.new())\na.get(1).x:=7\nplot(a.get(0).x+a.get(2).x)',[[14]*4],['int']),
            ('shallow-copy','type T\n    int x=1\na=array.from(T.new())\nb=a.copy()\nb.get(0).x:=7\nb.set(0,T.new(8))\nplot(a.get(0).x)\nplot(b.get(0).x)',[[7]*4,[8]*4],['int','int']),
            ('object-copy','type T\n    int x=1\na=array.from(T.new())\nb=array.from(a.get(0).copy())\nb.get(0).x:=9\nplot(a.get(0).x)\nplot(b.get(0).x)',[[1]*4,[9]*4],['int','int']),
            ('deque','type T\n    int x\na=array.new<T>()\na.push(T.new(2))\na.unshift(T.new(1))\na.set(1,T.new(3))\nfirst=a.shift()\nlast=a.pop()\nplot(first.x*10+last.x)',[[13]*4],['int']),
            ('from-na','type T\n    int x\na=array.from(na,T.new(3))\nplot(na(a.get(0)) ? 1 : 0)\nplot(a.get(1).x)',[[1]*4,[3]*4],['int','int']),
            ('iteration','type T\n    int x\na=array.from(T.new(3),T.new(4))\ntotal=0\nfor [i,item] in a\n    item.x:=item.x+i\n    total:=total+item.x\nplot(total)',[[8]*4],['int']),
            ('nominal-overload','type A\n    int x\ntype B\n    int x\nf(array<A> a)=>a.get(0).x+1\nf(array<B> a)=>a.get(0).x+2\nplot(f(array.from(A.new(1))))\nplot(f(array.from(B.new(1))))',[[2]*4,[3]*4],['int','int']),
            ('new-array-history','type T\n    float x\na=array.from(T.new(close))\npast=a[1]\nplot(bar_index>0 ? past.get(0).x : 0.)',[[0.,10.,20.,30.]],['float']),
            ('alias-history','type T\n    float x\nvar a=array.from(T.new(0.))\na.get(0).x:=close\npast=a[1]\nplot(bar_index>0 ? past.get(0).x : 0.)',[[0.,20.,30.,40.]],['float']),
            ('clear','type T\n    int x\na=array.from(T.new(1))\na.clear()\nplot(a.size())',[[0]*4],['int']),
            ('pop-root','type T\n    float x\nvar T kept=na\na=array.from(T.new(close))\nkept:=a.pop()\nplot(kept.x)',[[10.,20.,30.,40.]],['float']),
            ('array-result','type T\n    int x\nf(T x)=>array.from(x)\ng(array<T> a)=>a\na=g(f(T.new(4)))\nplot(a.get(0).x)',[[4]*4],['int']),
            ('named-order','var n=array.new_int(1,0)\nnext()=>\n    n.set(0,n.get(0)+1)\n    n.get(0)\ntype T\n    int x\na=array.new<T>(initial_value=T.new(next()),size=next())\nplot(a.size()*10+a.get(0).x)',[[21,43,65,87]],['int']),
            ('container-clear-copy','type T\n    int x\na=array.from(T.new(1))\nb=a.copy()\nb.clear()\nplot(a.size())\nplot(b.size())',[[1]*4,[0]*4],['int','int'])]
        for name,body,values,types in positives:add(v,name,body,values,types)
        rejections=[
            ('push-nominal','type A\n    int x\ntype B\n    int x\na=array.new<A>()\narray.push(a,B.new())','ARRAY_ELEMENT_TYPE','LANGUAGE_INVALID'),
            ('assignment-nominal','type A\n    int x\ntype B\n    int x\na=array.new<A>()\narray<B> b=a\nplot(1)','ASSIGNMENT_TYPE','LANGUAGE_INVALID'),
            ('mixed-from','type T\n    int x\na=array.from(T.new(),1)\nplot(1)','ARRAY_ELEMENT_TYPE','LANGUAGE_INVALID'),
            ('sum','type T\n    int x\na=array.new<T>()\nplot(a.sum())','ARRAY_NUMERIC','LANGUAGE_INVALID'),
            ('nested-array','type T\n    int x\na=array.new<array<T>>()\nplot(1)','ARRAY_ELEMENT','PROFILE_UNCOVERED'),
            ('reference-field','type T\n    array<float> values\nplot(1)','UDT_FIELD_TYPE','PROFILE_UNCOVERED'),
            ('empty-pop','type T\n    int x\na=array.new<T>()\nb=a.pop()\nplot(1)','ARRAY_EMPTY','RUNTIME_ERROR'),
            ('missing-object','type T\n    int x\na=array.new<T>(1)\nplot(a.get(0).x)','UDT_NA','RUNTIME_ERROR'),
            ('missing-array','type T\n    int x\narray<T> a=na\nplot(a.size())','ARRAY_NA','RUNTIME_ERROR'),
            ('out-of-range','type T\n    int x\na=array.from(T.new())\nplot(a.get(2).x)','ARRAY_INDEX','RUNTIME_ERROR'),
            ('request','type T\n    int x\na=array.from(T.new(1))\nplot(request.security("TEST:A","2",a.get(0).x))','UDT_REQUEST','PROFILE_UNCOVERED'),
            ('simple-parameter','type T\n    int x\nf(simple array<T> a)=>a.size()\na=array.new<T>()\nplot(f(a))','FUNCTION_ARGUMENT_QUALIFIER','LANGUAGE_INVALID')]
        for name,body,code,category in rejections:add(v,'reject-'+name,body,code=code,category=category)
    files['bars.json']=(ROOT/'conformance/functions/fixed.json').read_bytes()
    files['cases.json']=canonical({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1','oracle':{'kind':'independent_manual_expectations','basis':'Four closes 10,20,30,40. Last-three prefix means, nominal alias/copy mutations and source-order counters computed by hand.','external_pine_execution':False},'rule_refs':[],'assertion_refs':[],'cases':cases})+b'\n'
    return files

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args(argv)
    files={'conformance/object-arrays/'+n:b for n,b in fixtures().items()};files.update({'examples/corpus/'+n:b for n,b in corpus_sources().items()})
    for name,raw in files.items():
        path=ROOT/name
        if args.check:assert path.read_bytes()==raw,name
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Owned nominal collection fixtures and manual expectations verified.')

if __name__=='__main__':main()
