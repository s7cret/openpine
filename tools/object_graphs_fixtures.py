"""Finite reference-field programs with independently calculated expectations."""
import argparse
from tools.specification import ROOT
from packages.contracts.wire import canonical

def routing(v):
    header=f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Graph routing")\n'
    setting='scale=input(2,"Scale")\n' if v<5 else 'scale=input.int(2,"Scale")\n'
    if v<5:return header+setting+'var total=0.0\ntotal:=total+close*scale\nplot(total)\nplot(close*scale)\nplot(bar_index+1)\n'
    if v==5:return header+setting+'''type Node
    float value
    Node previous
type State
    Node head
    float total=0.
    int count=0
var state=State.new()
state.head:=Node.new(close*scale,state.head)
state.total:=state.total+state.head.value
state.count:=state.count+1
copy=state.copy()
plot(copy.total)
plot(copy.head.value)
plot(copy.count)
'''
    return header+setting+'''type Node
    float value
    Node previous
type State
    map<string,Node> nodes
    matrix<float> panel
    array<float> values
var state=State.new(map.new<string,Node>(),matrix.new<float>(1,2,0.),array.new_float())
state.values.push(close*scale)
node=Node.new(close*scale,state.nodes.get("head"))
state.nodes.put("head",node)
state.panel.set(0,0,state.panel.get(0,0)+node.value)
copy=state.copy()
copy.panel.set(0,1,copy.nodes.get("head").value)
plot(state.panel.get(0,0))
plot(state.panel.get(0,1))
plot(state.values.size())
'''

def corpus_sources():
    result={f'object-graphs-routing-v{v}.pine':routing(v).encode() for v in range(1,7)}
    for v in (5,6):
        body=('type Order\n    float qty\n    Order next\ntype Book\n    array<Order> queue\nvar book=Book.new(array.new<Order>())\n'
              'if bar_index==0\n    order=Order.new(2.)\n    order.next:=order\n    book.queue.push(order)\n    strategy.entry("L",strategy.long,qty=book.queue.get(0).next.qty)\n' if v==5 else
              'type Order\n    float qty\n    Order next\ntype Book\n    map<string,Order> active\n    matrix<float> panel\nvar book=Book.new(map.new<string,Order>(),matrix.new<float>(1,1,2.))\n'
              'if bar_index==0\n    order=Order.new(book.panel.get(0,0))\n    order.next:=order\n    book.active.put("L",order)\n    strategy.entry("L",strategy.long,qty=book.active.get("L").next.qty)\n')
        result[f'object-graphs-strategy-v{v}.pine']=(f'//@version={v}\nstrategy("Graph strategy",initial_capital=1000,default_qty_type=strategy.fixed,default_qty_value=2,commission_type=strategy.commission.percent,commission_value=1)\n'+body+'if bar_index==3\n    strategy.close("L")\nplot(strategy.equity)\n').encode()
    return result

def corpus_cases(record):
    from tools.semantic_core_fixtures import corpus_cases as previous
    result=previous(record)
    for c in result:
        c['id']=c['id'].replace('semantic-','object-graphs-');c['family']=c['family'].replace('semantic-','object-graphs-')
        c['files']['source']=record('examples/corpus/'+c['id']+'.pine')
        if c['family']=='object-graphs-routing':
            c['inputs']={'scale':2};c['oracle']={'reason':'Four closes10/20/30/40 times scale2 give20/40/60/80, cumulative20/60/120/200 and counts1/2/3/4. V5 linked nodes; v6 UDT collection fields and shallow-copy aliases. Scalar controls v1-v4.',
                'outputs':[[20.,60.,120.,200.],[20.,40.,60.,80.],[1,2,3,4]]}
        else:c['oracle']['reason']='A cyclic Order reached through Book collection fields supplies quantity2. Entry2 fills at10, exit2 at10, commission .2 each side; original independent broker oracle retained.'
    return result

def fixtures():
    files={};cases=[]
    def add(v,name,body,values=None,types=None,code=None,category='LANGUAGE_INVALID',complete=False,qualifiers=None):
        path=f'v{v}-{name}.pine';files[path]=(body if complete else f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Object graphs")\n'+body+'\n').encode()
        expected={'status':'rejected','category':category,'code':code} if code else {
            'status':'executed','title':'Graph routing' if complete else 'Object graphs','shorttitle':'','overlay':False,'observation_count':4,
            'outputs':[{'title':'','type':t,'values':row} for t,row in zip(types or ['int']*len(values),values,strict=True)],'qualifiers':qualifiers or ['series']*len(values)}
        case={'case_id':f'v{v}-{name}','language_version':v,'source':path,'bars':'bars.json','expected':expected}
        if complete:case['inputs']={'scale':2}
        cases.append(case)
    for v in range(1,7):
        add(v,'routing',routing(v),[[20.,60.,120.,200.],[20.,40.,60.,80.],[1,2,3,4]],['float','float','int'],complete=True)
        if v<5:
            for name,typ in [('self','N'),('array','array<int>'),('map','map<string,int>'),('matrix','matrix<int>')]:
                add(v,'reject-'+name,'type N\n    '+typ+' data\nplot(1)',code='UDT_VERSION')
            continue
        for name,body,values,types in [
            ('missing','type N\n    N next\n    array<int> a\n    map<string,int> m\n    matrix<int> x\nn=N.new()\nplot(na(n.next) and na(n.a) and na(n.m) and na(n.x) ? 1 : 0)',[[1]*4],None),
            ('self','type N\n    int value\n    N next\nn=N.new(7)\nn.next:=n\nplot(n.next.next.value)',[[7]*4],None),
            ('cycle','type N\n    int value\n    N next\na=N.new(3)\nb=N.new(7,a)\na.next:=b\nplot(a.next.value*10+b.next.value)',[[73]*4],None),
            ('nested','type Child\n    float value\ntype Parent\n    Child child\np=Parent.new(Child.new(close))\np.child.value:=p.child.value+1\nplot(p.child.value)',[[11.,21.,31.,41.]],['float']),
            ('array','type Box\n    array<int> data\nb=Box.new(array.from(1,2))\nb.data.push(3)\nplot(b.data.size()*10+b.data.get(2))',[[33]*4],None),
            ('map','type Box\n    map<string,int> data\nb=Box.new(map.new<string,int>())\nb.data.put("x",7)\nplot(b.data.get("x"))',[[7]*4],None),
            ('matrix','type Box\n    matrix<int> data\nb=Box.new(matrix.new<int>(1,2,3))\nb.data.set(0,1,7)\nplot(b.data.get(0,0)*10+b.data.get(0,1))',[[37]*4],None),
            ('indirect-array-cycle','type Box\n    int value\n    array<Box> children\nb=Box.new(7,array.new<Box>())\nb.children.push(b)\nplot(b.children.get(0).value)',[[7]*4],None),
            ('indirect-map-cycle','type Box\n    int value\n    map<string,Box> children\nb=Box.new(7,map.new<string,Box>())\nb.children.put("self",b)\nplot(b.children.get("self").value)',[[7]*4],None),
            ('indirect-matrix-cycle','type Box\n    int value\n    matrix<Box> children\nb=Box.new(7,matrix.new<Box>(1,1))\nb.children.set(0,0,b)\nplot(b.children.get(0,0).value)',[[7]*4],None),
            ('copy','type N\n    int x\ntype Box\n    N child\na=Box.new(N.new(1))\nb=a.copy()\nb.child.x:=7\nplot(a.child.x)\nb.child:=N.new(9)\nplot(a.child.x*10+b.child.x)',[[7]*4,[79]*4],None),
            ('copy-array','type Box\n    array<int> data\na=Box.new(array.from(1))\nb=Box.copy(a)\nb.data.set(0,7)\nb.data:=array.from(9)\nplot(a.data.get(0)*10+b.data.get(0))',[[79]*4],None),
            ('copy-map','type Box\n    map<string,int> data\na=Box.new(map.new<string,int>())\nb=a.copy()\nb.data.put("x",7)\nplot(a.data.get("x"))',[[7]*4],None),
            ('copy-matrix','type Box\n    matrix<int> data\na=Box.new(matrix.new<int>(1,1,1))\nb=a.copy()\nb.data.set(0,0,7)\nplot(a.data.get(0,0))',[[7]*4],None),
            ('list','type N\n    int value\n    N next\nvar N head=na\nhead:=N.new(bar_index,head)\nplot(na(head.next) ? -1 : head.next.value)',[[-1,0,1,2]],None),
            ('history','type N\n    int value\n    N next\na=N.new(bar_index)\na.next:=a\nplot(bar_index>0 ? (a[1]).next.value : -1)',[[-1,0,1,2]],None),
            ('udf','type N\n    int value\n    N next\nread(N node)=>node.next.value\na=N.new(7)\na.next:=a\nplot(read(a))',[[7]*4],None),
            ('method','type N\n    int value\n    N next\nmethod bump(N node)=>\n    node.value:=node.value+1\n    node\na=N.new(7)\na.next:=a\nplot(a.next.bump().value)',[[8]*4],None),
            ('marked-history','type N\n    varip int ticks=0\n    varip N next\nvar a=N.new()\na.next:=a\na.ticks:=a.ticks+1\nplot(a.next.ticks)',[[1,2,3,4]],None),
            ('enum','enum Side\n    up\n    down\ntype Box\n    array<Side> values\nb=Box.new(array.new<Side>(1,Side.up))\nb.values.set(0,Side.down)\nplot(b.values.get(0)==Side.down ? 1 : 0)',[[1]*4],None),
            ('order','type N\n    int value\ntype Box\n    N left\n    N right\nvar counter=array.new_int(1,0)\nmake()=>\n    counter.set(0,counter.get(0)+1)\n    N.new(counter.get(0))\nb=Box.new(right=make(),left=make())\nplot(b.left.value*10+b.right.value)',[[21,43,65,87]],None),
        ]:add(v,name,body,values,types)
        for name,body,code,category in [
            ('default-call','type N\n    array<int> data=array.new_int()\nplot(1)','UDT_FIELD_DEFAULT','PROFILE_UNCOVERED'),
            ('default-number','type N\n    N next=1\nplot(1)','UDT_FIELD_TYPE','LANGUAGE_INVALID'),
            ('foreign-nominal','type A\n    int x\ntype B\n    int x\ntype C\n    A child\nc=C.new(B.new())\nplot(1)','UDT_FIELD_TYPE','LANGUAGE_INVALID'),
            ('wrong-family','type N\n    array<int> data\na=N.new(matrix.new<int>())\nplot(1)','UDT_FIELD_TYPE','LANGUAGE_INVALID'),
            ('wrong-element','type N\n    array<int> data\na=N.new(array.new_float())\nplot(1)','UDT_FIELD_TYPE','LANGUAGE_INVALID'),
            ('missing-object','type N\n    N next\na=N.new()\nplot(na(a.next.next) ? 1 : 0)','UDT_NA','RUNTIME_ERROR'),
            ('missing-array','type N\n    array<int> data\na=N.new()\nplot(a.data.size())','ARRAY_NA','RUNTIME_ERROR'),
            ('missing-map','type N\n    map<string,int> data\na=N.new()\nplot(a.data.size())','MAP_NA','RUNTIME_ERROR'),
            ('missing-matrix','type N\n    matrix<int> data\na=N.new()\nplot(a.data.rows())','MATRIX_NA','RUNTIME_ERROR'),
            ('forward-type','type A\n    B child\ntype B\n    int x\nplot(1)','UDT_FIELD_TYPE','PROFILE_UNCOVERED'),
            ('direct-nesting','a=array.new<array<int>>()\nplot(1)','ARRAY_ELEMENT','PROFILE_UNCOVERED'),
            ('varip-array','type N\n    N next\nvarip a=array.new<N>()\nplot(1)','VARIP_GRAPH_TYPE','PROFILE_UNCOVERED'),
            ('varip-map','type N\n    N next\nvarip a=map.new<string,N>()\nplot(1)','VARIP_GRAPH_TYPE','PROFILE_UNCOVERED'),
            ('varip-field','type N\n    N next\n    varip array<N> data\nplot(1)','VARIP_GRAPH_TYPE','PROFILE_UNCOVERED'),
            ('requests','type N\n    array<float> data\na=N.new(array.from(close))\nplot(request.security("TEST:X","D",a.data.get(0)))','UDT_REQUEST','PROFILE_UNCOVERED'),
        ]:add(v,'reject-'+name,body,code=code,category=category)
    files['bars.json']=(ROOT/'conformance/functions/fixed.json').read_bytes()
    files['cases.json']=canonical({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','basis':'Fixed closes10/20/30/40, hand-calculated linked values, reference aliases, copy replacement, coordinates and source-order counters. External execution is separate.','external_pine_execution':False},
        'rule_refs':[],'assertion_refs':[],'cases':cases})+b'\n'
    return files

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    files={'conformance/object-graphs/'+n:b for n,b in fixtures().items()};files.update({'examples/corpus/'+n:b for n,b in corpus_sources().items()})
    for name,raw in files.items():
        path=ROOT/name
        if a.check:assert path.read_bytes()==raw,name
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Owned reference-field fixtures and manual expectations verified.')
if __name__=='__main__':main()
