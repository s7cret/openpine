"""Owned array editing workflows and independently authored finite oracles."""
import argparse
from tools.specification import ROOT
from packages.contracts.wire import canonical

def routing(v):
    header=f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Array editing")\n'
    if v<4:return header+'''scale=input(2,"Scale")
count=0
count:=nz(count[1])+(close==20 ? 0 : 1)
last=close
last:=close==20 ? nz(last[1]) : close
plot(last*scale+1)
plot(count)
plot(close==20 ? -1 : count-1)
'''
    if v==4:return header+'''scale=input(2,"Scale")
var values=array.new_float(0)
array.insert(values,array.size(values),close*scale)
if array.includes(values,20*scale)
    array.remove(values,array.indexof(values,20*scale))
scratch=array.copy(values)
array.reverse(scratch)
array.fill(scratch,0.,index_from=1)
array.concat(scratch,array.from(1.))
plot(array.sum(scratch))
plot(array.size(values))
plot(array.lastindexof(values,close*scale))
'''
    return header+'''enum Mode
    direct
    doubled
mode=input.enum(Mode.doubled,"Mode")
type Sample
    float price
    Mode mode=Mode.direct
method value(Sample self)=>self.mode==Mode.doubled ? self.price*2 : self.price
var values=array.new<Sample>()
sample=Sample.new(close,mode)
values.insert(values.size(),sample)
if sample.price==20 and values.includes(sample)
    values.remove(values.indexof(sample))
scratch=values.copy()
scratch.reverse()
scratch.fill(Sample.new(0.,Mode.direct),index_from=1)
scratch.concat(array.from(Sample.new(1.,Mode.direct)))
total=0.
for point in scratch
    total:=total+point.value()
plot(total)
plot(values.size())
plot(values.lastindexof(sample))
'''

def corpus_sources():
    result={f'array-ops-routing-v{v}.pine':routing(v).encode() for v in range(1,7)}
    for v in (5,6):
        result[f'array-ops-strategy-v{v}.pine']=(f'//@version={v}\n'
            'strategy("Array editing strategy",initial_capital=1000,default_qty_type=strategy.fixed,default_qty_value=2,commission_type=strategy.commission.percent,commission_value=1)\n'
            'type Signal\n    float units=2.\nvar pending=array.new<Signal>()\n'
            'if bar_index==0\n    pending.insert(0,Signal.new())\n'
            'if pending.size()>0\n    signal=pending.get(0)\n    if pending.includes(signal)\n        removed=pending.remove(pending.indexof(signal))\n        strategy.entry("L",strategy.long,qty=removed.units)\n'
            'if bar_index==3\n    strategy.close("L")\nplot(strategy.equity)\n').encode()
    return result

def corpus_cases(record):
    from tools.semantic_core_fixtures import corpus_cases as previous
    result=previous(record)
    for c in result:
        c['id']=c['id'].replace('semantic-','array-ops-');c['family']=c['family'].replace('semantic-','array-ops-')
        c['files']['source']=record('examples/corpus/'+c['id']+'.pine')
        if c['family']=='array-ops-routing':
            c['inputs']={'scale':2} if c['version']<5 else {'mode':'enum:3:1:2:1'}
            c['oracle']={'reason':'Remove close20, retain 10/30/40; reverse a scratch copy, zero remaining prices, append neutral1. Scale2 yields 21,21,61,81; retained counts1,1,2,3 and current identities0,-1,1,2. Scalar equivalents v1-v3, arrays v4, nominal UDT/enum v5/v6.',
                'outputs':[[21.,21.,61.,81.],[1,1,2,3],[0,-1,1,2]]}
        else:c['oracle']['reason']='Insert one Signal at bar0, locate by object identity and remove before submitting qty2. Fill entry bar1 open10, exit bar4 open10; .2 commission per side.'
    return result

def fixtures():
    files={};cases=[]
    def add(v,name,body,values=None,types=None,code=None,category='LANGUAGE_INVALID',complete=False):
        filename=f'v{v}-{name}.pine';files[filename]=(body if complete else f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Array operations")\n'+body+'\n').encode()
        expected={'status':'rejected','category':category,'code':code} if code else {'status':'executed','title':'Array editing' if complete else 'Array operations','shorttitle':'','overlay':False,'observation_count':4,
            'outputs':[{'title':'','type':t,'values':row} for t,row in zip(types or ['int']*len(values),values,strict=True)],'qualifiers':['series']*len(values)}
        cases.append({'case_id':f'v{v}-{name}','language_version':v,'source':filename,'bars':'bars.json','expected':expected})
    for v in range(1,7):
        add(v,'routing',routing(v),[[21.,21.,61.,81.],[1,1,2,3],[0,-1,1,2]],types=['float','int','int'],complete=True)
        if v<4:
            for op in ('insert','remove','reverse','fill','includes','indexof','lastindexof','concat'):
                add(v,'reject-'+op,'array.'+op+'(na)\nplot(1)',code='ARRAY_VERSION')
            continue
        positives=[
            ('insert-remove','a=array.from(1,3)\narray.insert(a,1,2)\nplot(array.remove(a,1))\nplot(array.get(a,0)*10+array.get(a,1))',[[2]*4,[13]*4]),
            ('empty-append','a=array.new_int(0)\narray.insert(a,0,7)\nplot(array.get(a,0))',[[7]*4]),
            ('reverse','a=array.from(1,2,3)\narray.reverse(a)\nplot(array.get(a,0)*100+array.get(a,1)*10+array.get(a,2))',[[321]*4]),
            ('fill-all','a=array.from(1,2,3)\narray.fill(a,9)\nplot(array.sum(a))',[[27]*4]),
            ('fill-range','a=array.from(1,2,3,4)\narray.fill(a,7,1,3)\nplot(array.sum(a))',[[19]*4]),
            ('fill-explicit-na-end','a=array.from(1,2,3,4)\narray.fill(a,9,index_from=2,index_to=na)\nplot(array.sum(a))',[[21]*4]),
            ('fill-empty-range','a=array.new_int(0)\narray.fill(a,9)\na2=array.from(1,2)\narray.fill(a2,9,1,1)\nplot(array.size(a))\nplot(array.sum(a2))',[[0]*4,[3]*4]),
            ('search-duplicates','a=array.from(2,3,2)\nplot(array.includes(a,2) ? 1 : 0)\nplot(array.indexof(a,2))\nplot(array.lastindexof(a,2))\nplot(array.indexof(a,7))',[[1]*4,[0]*4,[2]*4,[-1]*4]),
            ('search-missing','a=array.from(2,na,3,na)\nplot(array.indexof(a,na))\nplot(array.lastindexof(a,na))\nplot(array.includes(a,na) ? 1 : 0)',[[1]*4,[3]*4,[1]*4]),
            ('search-empty','a=array.new_int(0)\nplot(array.indexof(a,7))\nplot(array.includes(a,na) ? 1 : 0)',[[-1]*4,[0]*4]),
            ('concat-alias','a=array.from(1,2)\nb=array.from(3)\nc=array.concat(a,b)\narray.push(c,4)\nplot(array.sum(a))\nplot(array.sum(b))',[[10]*4,[3]*4]),
            ('self-concat','a=array.from(1,2)\narray.concat(a,a)\nplot(array.size(a))\nplot(array.sum(a))',[[4]*4,[6]*4]),
            ('concat-empty','a=array.from(1)\nb=array.new_int(0)\narray.concat(a,b)\nplot(array.sum(a))',[[1]*4]),
            ('strings','a=array.from("ёж","x","ёж")\narray.reverse(a)\narray.fill(a,na,1,2)\nplot(array.indexof(a,"ёж"))\nplot(array.lastindexof(a,"ёж"))\nplot(array.indexof(a,na))',[[0]*4,[2]*4,[1]*4]),
            ('udf','f(a)=>\n    array.insert(a,0,9)\n    array.reverse(a)\n    array.remove(a,0)\na=array.from(1,2)\nplot(f(a))\nplot(array.sum(a))',[[2]*4,[10]*4])]
        for name,body,values in positives:add(v,name,body,values)
        ann='int[]' if v==4 else 'array<int>'
        negatives=[
            ('insert-bound','a=array.from(1)\narray.insert(a,2,9)','ARRAY_INDEX','RUNTIME_ERROR'),
            ('remove-empty','a=array.new_int(0)\narray.remove(a,0)','ARRAY_INDEX','RUNTIME_ERROR'),
            ('insert-na','a=array.from(1)\narray.insert(a,na,2)','ARRAY_INDEX','RUNTIME_ERROR'),
            ('fill-reversed','a=array.from(1,2)\narray.fill(a,9,2,1)','ARRAY_RANGE','RUNTIME_ERROR'),
            ('fill-negative','a=array.from(1,2)\narray.fill(a,9,-1,2)','ARRAY_RANGE','RUNTIME_ERROR'),
            ('fill-end','a=array.from(1,2)\narray.fill(a,9,0,3)','ARRAY_RANGE','RUNTIME_ERROR'),
            ('fill-wrong-type','a=array.from(1,2)\narray.fill(a,"bad")','ARRAY_ELEMENT_TYPE','LANGUAGE_INVALID'),
            ('concat-type','a=array.from(1)\narray.concat(a,array.from(2.))','ARRAY_ELEMENT_TYPE','LANGUAGE_INVALID'),
            ('concat-missing',f'{ann} a=na\nb=array.from(1)\narray.concat(b,a)','ARRAY_NA','RUNTIME_ERROR'),
            ('search-type','a=array.from(1)\narray.includes(a,true)','ARRAY_ELEMENT_TYPE','LANGUAGE_INVALID'),
            ('insert-storage','a=array.new_int(100000,1)\narray.insert(a,0,2)','ARRAY_STORAGE','RUNTIME_ERROR'),
            ('concat-storage','a=array.new_int(50001,1)\narray.concat(a,a)','ARRAY_STORAGE','RUNTIME_ERROR'),
            ('slice-excluded','a=array.from(1,2)\narray.slice(a,0,1)','ARRAY_OPERATION','PROFILE_UNCOVERED')]
        for name,body,code,category in negatives:add(v,'reject-'+name,body+'\nplot(1)',code=code,category=category)
        neg='a=array.from(1,3)\narray.insert(a,-1,2)\nplot(array.remove(a,-2))\nplot(array.get(a,0)*10+array.get(a,1))'
        if v==6:add(v,'negative-indices',neg,[[2]*4,[13]*4])
        else:add(v,'negative-indices',neg,code='ARRAY_INDEX',category='RUNTIME_ERROR')
        if v>=5:
            add(v,'methods','a=array.from(1,2)\na.insert(1,9)\na.remove(0)\na.reverse()\na.fill(3,index_from=1)\na.concat(array.from(4))\nplot(a.get(0)*100+a.get(1)*10+a.get(2))\nplot(a.includes(3) ? a.lastindexof(3) : -1)',[[234]*4,[1]*4])
            add(v,'object-identity','type T\n    int x\nx=T.new(1)\ny=T.new(1)\na=array.from(x)\na.insert(0,y)\na.concat(array.from(x))\nplot(a.indexof(x))\nplot(a.lastindexof(x))\nplot(a.indexof(x.copy()))',[[1]*4,[2]*4,[-1]*4])
            add(v,'object-fill','type T\n    int x\nx=T.new(1)\na=array.new<T>(3)\na.fill(x)\na.remove(0).x:=7\nplot(a.get(0).x)',[[7]*4])
            add(v,'enum-operations','enum Mode\n    buy\n    sell\na=array.from(Mode.buy,Mode.sell)\na.insert(0,Mode.sell)\na.remove(1)\na.reverse()\na.fill(na,index_from=1)\na.concat(array.from(Mode.buy))\nplot(a.indexof(Mode.sell))\nplot(a.lastindexof(na))\nplot(a.includes(Mode.buy) ? 1 : 0)',[[0]*4,[1]*4,[1]*4])
            add(v,'reject-enum-foreign','enum A\n    x\nenum B\n    x\na=array.from(A.x)\na.includes(B.x)\nplot(1)',code='METHOD_NO_MATCH')
    files['bars.json']=(ROOT/'conformance/functions/fixed.json').read_bytes()
    files['cases.json']=canonical({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','basis':'Fixed 10,20,30,40 closes; explicit edits, sums and indices; identity tests deliberately allocate equal-valued distinct objects. Search-NA and empty fill are bounded interpretations, not external observations.','external_pine_execution':False},
        'rule_refs':[],'assertion_refs':[],'cases':cases})+b'\n'
    return files

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    files={'conformance/array-operations/'+n:b for n,b in fixtures().items()};files.update({'examples/corpus/'+n:b for n,b in corpus_sources().items()})
    for name,raw in files.items():
        path=ROOT/name
        if a.check:assert path.read_bytes()==raw,name
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Owned array-operation fixtures and independent manual expectations verified.')

if __name__=='__main__':main()
