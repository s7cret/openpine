"""Owned ordering workflows and finite oracles authored without executing Pine."""
import argparse
from tools.specification import ROOT
from packages.contracts.wire import canonical

def routing(v):
    header=f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Array ranking")\n'
    setting='scale=input(2,"Scale")\n' if v<5 else 'scale=input.int(2,"Scale")\n'
    if v<4:return header+setting+'top=close*scale\nsecond=nz(close[1],close)*scale\nplot(top+second)\nplot(bar_index+1)\nplot(bar_index)\n'
    if v==4:return header+setting+'''var values=array.new_float(0)
array.push(values,close*scale)
scratch=array.copy(values)
array.sort(scratch,order.descending)
second=array.get(scratch,array.size(scratch)>1 ? 1 : 0)
plot(array.get(scratch,0)+second)
plot(array.size(values))
plot(array.size(values)-1)
'''
    if v==5:return header+setting+'''var values=array.new_float(0)
values.push(close*scale)
indices=values.sort_indices(order.descending)
top=values.get(indices.get(0))
second=values.max(values.size()>1 ? 1 : 0)
scratch=values.copy()
scratch.sort(order.descending)
plot(scratch.get(0)==top ? top+second : -999.)
plot(values.size())
plot(values.binary_search(close*scale))
'''
    return header+setting+'''type Level
    float price
    int sequence
var values=array.new<Level>()
var numeric=array.new_float(0)
values.push(Level.new(close*scale,bar_index))
numeric.push(close*scale)
indices=values.sort_indices(order.descending,sort_field="price")
scratch=values.copy()
scratch.sort(order.descending,sort_field=0)
top=scratch.get(0).price
second=values.get(indices.get(values.size()>1 ? 1 : 0)).price
ranked=numeric.max(numeric.size()>1 ? 1 : 0)
plot(second==ranked ? top+second : -999.)
plot(values.size())
plot(values.binary_search(close*scale,sort_field="price"))
'''

def corpus_sources():
    result={f'array-order-routing-v{v}.pine':routing(v).encode() for v in range(1,7)}
    for v in (5,6):
        selection=('priority=array.from(3,1)\nunits=array.from(1.,2.)\nindices=priority.sort_indices()\nqty=units.get(indices.get(0))\n' if v==5 else
            'type Signal\n    int priority\n    float units\npending=array.from(Signal.new(3,1.),Signal.new(1,2.))\npending.sort(sort_field="priority")\nqty=pending.get(0).units\n')
        result[f'array-order-strategy-v{v}.pine']=(f'//@version={v}\n'
            'strategy("Array ranking strategy",initial_capital=1000,default_qty_type=strategy.fixed,default_qty_value=2,commission_type=strategy.commission.percent,commission_value=1)\n'+selection+
            'if bar_index==0\n    strategy.entry("L",strategy.long,qty=qty)\nif bar_index==3\n    strategy.close("L")\nplot(strategy.equity)\n').encode()
    return result

def corpus_cases(record):
    from tools.semantic_core_fixtures import corpus_cases as previous
    result=previous(record)
    for c in result:
        c['id']=c['id'].replace('semantic-','array-order-');c['family']=c['family'].replace('semantic-','array-order-')
        c['files']['source']=record('examples/corpus/'+c['id']+'.pine')
        if c['family']=='array-order-routing':
            c['inputs']={'scale':2}
            c['oracle']={'reason':'Scale2 produces 20/40/60/80. The two highest observed values sum to 40/60/100/140 (reuse first when only one); counts1/2/3/4 and last original index0/1/2/3. Scalar equivalents v1-v3, sorted copy v4, index sorting and nth v5, UDT fields plus numeric ranks v6.',
                'outputs':[[40.,60.,100.,140.],[1,2,3,4],[0,1,2,3]]}
        else:c['oracle']['reason']='Priority1 has qty2, ahead of priority3 qty1. Select by sorted indices v5 or UDT sort_field v6. Submit once bar0, entry fill bar1 open10, exit bar4 open10, .2 commission per side.'
    return result

def fixtures():
    files={};cases=[]
    def add(v,name,body,values=None,types=None,code=None,category='LANGUAGE_INVALID',complete=False):
        filename=f'v{v}-{name}.pine';files[filename]=(body if complete else f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Array order")\n'+body+'\n').encode()
        expected={'status':'rejected','category':category,'code':code} if code else {'status':'executed','title':'Array ranking' if complete else 'Array order','shorttitle':'','overlay':False,'observation_count':4,
            'outputs':[{'title':'','type':t,'values':row} for t,row in zip(types or ['int']*len(values),values,strict=True)],'qualifiers':['series']*len(values)}
        cases.append({'case_id':f'v{v}-{name}','language_version':v,'source':filename,'bars':'bars.json','expected':expected})
    for v in range(1,7):
        add(v,'routing',routing(v),[[40.,60.,100.,140.],[1,2,3,4],[0,1,2,3]],['float','int','int'],complete=True)
        if v<4:
            for op in ('sort','sort_indices','binary_search','binary_search_leftmost','binary_search_rightmost'):
                add(v,'reject-'+op,'array.'+op+'(na)\nplot(1)',code='ARRAY_VERSION')
            continue
        for name,body,values,types in [
            ('ascending','a=array.from(3,1,2)\narray.sort(a)\nplot(array.get(a,0)*100+array.get(a,1)*10+array.get(a,2))',[[123]*4],None),
            ('descending','a=array.from(3,1,2)\narray.sort(a,order.descending)\nplot(array.get(a,0)*100+array.get(a,1)*10+array.get(a,2))',[[321]*4],None),
            ('alias','a=array.from(2,1)\nb=a\narray.sort(b)\nplot(array.get(a,0))',[[1]*4],None),
            ('copy','a=array.from(2,1)\nb=array.copy(a)\narray.sort(b)\nplot(array.get(a,0)*10+array.get(b,0))',[[21]*4],None),
            ('missing','a=array.from(2.,na,1.)\narray.sort(a)\nplot(array.get(a,0))\nplot(na(array.get(a,2)) ? 1 : 0)',[[1.]*4,[1]*4],['float','int']),
            ('empty','a=array.new_int(0)\narray.sort(a)\nplot(array.size(a))',[[0]*4],None),
            ('dynamic-order','a=array.from(3,1)\narray.sort(a,close>20 ? order.descending : order.ascending)\nplot(array.get(a,0))',[[1,1,3,3]],None),
            ('namespace-udf','f(a)=>\n    array.sort(a)\n    array.get(a,0)\na=array.from(9,2)\nplot(f(a))',[[2]*4],None)]:add(v,name,body,values,types)
        for name,body,code,category in [
            ('bool','a=array.from(true)\narray.sort(a)','ARRAY_ORDER_TYPE','LANGUAGE_INVALID'),
            ('wrong-order','a=array.from(1)\narray.sort(a,"ascending")','ARRAY_ORDER_TYPE','LANGUAGE_INVALID'),
            ('missing-id','array.sort(order=order.ascending)','MISSING_ARGUMENT','LANGUAGE_INVALID'),
            ('unknown-name','a=array.from(1)\narray.sort(a,direction=order.ascending)','UNKNOWN_ARGUMENT','LANGUAGE_INVALID'),
            ('repeated','a=array.from(1)\narray.sort(a,id=a)','DUPLICATE_ARGUMENT','LANGUAGE_INVALID'),
            ('positional-after-named','a=array.from(1)\narray.sort(id=a,order.ascending)','POSITIONAL_AFTER_NAMED','LANGUAGE_INVALID'),
            ('na-array','float[] a=na\narray.sort(a)','ARRAY_NA','RUNTIME_ERROR')]:add(v,'reject-'+name,body+'\nplot(1)',code=code,category=category)
        if v==4:
            for op in ('sort_indices(a)','binary_search(a,1)','binary_search_leftmost(a,1)','binary_search_rightmost(a,1)','min(a,1)','max(a,1)'):
                add(v,'reject-'+op.split('(')[0],'a=array.from(1)\narray.'+op+'\nplot(1)',code='ARRAY_ORDER_VERSION')
            add(v,'reject-string','a=array.from("b","a")\narray.sort(a)\nplot(1)',code='ARRAY_ORDER_TYPE')
            continue
        positives=[
            ('methods','a=array.from(3,1,2)\na.sort(order.descending)\nplot(a.get(0))',[[3]*4]),
            ('indices','a=array.from(4,2,2,1)\np=a.sort_indices()\nplot(p.get(0)*1000+p.get(1)*100+p.get(2)*10+p.get(3))\nplot(a.get(0))',[[3120]*4,[4]*4]),
            ('descending-stability','a=array.from(4,2,2,1)\np=a.sort_indices(order.descending)\nplot(p.get(0)*1000+p.get(1)*100+p.get(2)*10+p.get(3))',[[123]*4]),
            ('indices-missing','a=array.from(2.,na,1.,na)\np=a.sort_indices()\nplot(p.get(0)*1000+p.get(1)*100+p.get(2)*10+p.get(3))',[[2013]*4]),
            ('string-indices','a=array.from("ёж","A","","猫")\np=a.sort_indices()\nplot(p.get(0)*1000+p.get(1)*100+p.get(2)*10+p.get(3))',[[1032]*4]),
            ('search','a=array.from(1,3,3,3,8)\nplot(a.binary_search(3))\nplot(a.binary_search_leftmost(3))\nplot(a.binary_search_rightmost(3))',[[2]*4,[1]*4,[3]*4]),
            ('search-gap','a=array.from(1,3,3,3,8)\nplot(a.binary_search(2))\nplot(a.binary_search_leftmost(2))\nplot(a.binary_search_rightmost(2))',[[-1]*4,[0]*4,[1]*4]),
            ('search-endpoints','a=array.from(1,3)\nplot(a.binary_search_leftmost(-9))\nplot(a.binary_search_rightmost(99))\nplot(a.binary_search_leftmost(99))\nplot(a.binary_search_rightmost(-9))',[[0]*4,[2]*4,[1]*4,[0]*4]),
            ('search-empty','a=array.new_int(0)\nplot(a.binary_search(1))\nplot(a.binary_search_leftmost(1))\nplot(a.binary_search_rightmost(1))',[[-1]*4]*3),
            ('fractional-target','a=array.from(1,3)\nplot(a.binary_search_leftmost(2.5))\nplot(a.binary_search_rightmost(2.5))',[[0]*4,[1]*4]),
            ('rank','a=array.from(5,na,2,2,9)\nplot(a.min(1))\nplot(a.max(2))\nplot(a.get(0))',[[2]*4,[2]*4,[5]*4]),
            ('rank-empty','a=array.new_int(0)\nplot(a.min(9))\nb=array.new_int(3)\nplot(b.max(0))',[[None]*4]*2),
            ('named-rank','a=array.from(7,3,1)\nplot(array.min(nth=1,id=a))',[[3]*4]),
            ('order-udf','f(sort_order order)=>order\na=array.from(3,1)\na.sort(f(order.descending))\nplot(a.get(0))',[[3]*4])]
        positives.append(('order-history','var sort_order direction=order.ascending\ndirection:=close>20 ? order.descending : order.ascending\nplot(direction[1]==order.ascending ? 1 : 0)',[[0,1,1,0]]))
        for name,body,values in positives:add(v,name,body,values)
        for name,body,code,category in [
            ('bool-order','a=array.from(1)\na.sort(true)','METHOD_NO_MATCH','LANGUAGE_INVALID'),
            ('enum','enum E\n    a\na=array.from(E.a)\na.sort()','ARRAY_ORDER_TYPE','LANGUAGE_INVALID'),
            ('rank-negative','a=array.from(1)\nplot(a.min(-1))','ARRAY_RANK','RUNTIME_ERROR'),
            ('rank-missing','a=array.from(1)\nplot(a.max(na))','ARRAY_RANK','RUNTIME_ERROR'),
            ('rank-too-large','a=array.from(1)\nplot(a.min(1))','ARRAY_RANK','RUNTIME_ERROR'),
            ('rank-float','a=array.from(1)\nplot(a.min(1.5))','METHOD_NO_MATCH','LANGUAGE_INVALID'),
            ('search-missing','a=array.from(1)\na.binary_search(na)','ARRAY_SEARCH_NA','RUNTIME_ERROR'),
            ('search-missing-key','a=array.new_int(1)\na.binary_search(1)','ARRAY_SEARCH_NA','RUNTIME_ERROR'),
            ('search-string','a=array.from("a")\na.binary_search("a")','ARRAY_ORDER_TYPE','LANGUAGE_INVALID'),
            ('primitive-field','a=array.from(1)\narray.sort(a,sort_field=0)','UNKNOWN_ARGUMENT','LANGUAGE_INVALID')]:add(v,'reject-'+name,body+'\nplot(1)',code=code,category=category)
        if v==5:add(v,'reject-udt','type T\n    int x\na=array.from(T.new(1))\na.sort()\nplot(1)',code='ARRAY_ORDER_VERSION');continue
        for name,body,values in [
            ('udt-default','type T\n    int x\na=array.from(T.new(3),T.new(1))\na.sort()\nplot(a.get(0).x)',[[1]*4]),
            ('udt-name','type T\n    int x\n    int y\na=array.from(T.new(3,1),T.new(1,2))\na.sort(sort_field="y")\nplot(a.get(0).x)',[[3]*4]),
            ('udt-indices','type T\n    int x\na=array.from(T.new(3),T.new(1))\np=a.sort_indices()\nplot(p.get(0))\nplot(a.get(0).x)',[[1]*4,[3]*4]),
            ('udt-search','type T\n    int x\na=array.from(T.new(1),T.new(3),T.new(3))\nplot(a.binary_search_leftmost(3))\nplot(a.binary_search_rightmost(3))',[[1]*4,[2]*4]),
            ('udt-string-search','type T\n    string name\na=array.from(T.new("a"),T.new("c"))\nplot(a.binary_search_rightmost("b",sort_field="name"))',[[1]*4]),
            ('udt-const','type T\n    int x\n    int y\nfield=1-1\na=array.from(T.new(3,1),T.new(1,2))\na.sort(sort_field=field)\nplot(a.get(0).x)',[[1]*4]),
            ('udt-shallow','type T\n    int x\nx=T.new(3)\ny=T.new(1)\na=array.from(x,y)\nb=a.copy()\na.sort()\na.get(1).x:=7\nplot(b.get(0).x)',[[7]*4])]:add(v,name,body,values)
        add(v,'udt-stable','type T\n    int key\n    int identity\na=array.from(T.new(2,0),T.new(1,1),T.new(1,2))\na.sort()\nplot(a.get(0).identity*100+a.get(1).identity*10+a.get(2).identity)',[[120]*4])
        for name,body,code,category in [
            ('udt-na','type T\n    int x\na=array.new<T>(1)\na.sort()','UDT_NA','RUNTIME_ERROR'),
            ('udt-field-negative','type T\n    int x\na=array.from(T.new(1))\na.sort(sort_field=-1)','ARRAY_SORT_FIELD','LANGUAGE_INVALID'),
            ('udt-field-name','type T\n    int x\na=array.from(T.new(1))\na.sort(sort_field="absent")','ARRAY_SORT_FIELD','LANGUAGE_INVALID'),
            ('udt-field-input','type T\n    int x\nfield=input.int(0)\na=array.from(T.new(1))\na.sort(sort_field=field)','ARRAY_SORT_FIELD','LANGUAGE_INVALID'),
            ('udt-bool-key','type T\n    bool x\na=array.from(T.new(true))\na.sort()','ARRAY_SORT_FIELD','LANGUAGE_INVALID'),
            ('udt-target-type','type T\n    int x\na=array.from(T.new(1))\na.binary_search("a")','ARRAY_SEARCH_TYPE','LANGUAGE_INVALID')]:add(v,'reject-'+name,body+'\nplot(1)',code=code,category=category)
    files['bars.json']=(ROOT/'conformance/functions/fixed.json').read_bytes()
    files['cases.json']=canonical({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','basis':'Fixed closes10/20/30/40; manually listed numeric order, original indices, duplicate ranks and endpoint positions. Ambiguous historical edges are explicit bounded interpretations, not external execution observations.','external_pine_execution':False},
        'rule_refs':[],'assertion_refs':[],'cases':cases})+b'\n'
    return files

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    files={'conformance/array-order/'+n:b for n,b in fixtures().items()};files.update({'examples/corpus/'+n:b for n,b in corpus_sources().items()})
    for name,raw in files.items():
        path=ROOT/name
        if a.check:assert path.read_bytes()==raw,name
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Owned array ordering fixtures and independent expectations verified.')
if __name__=='__main__':main()
