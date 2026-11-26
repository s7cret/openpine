"""Owned matrices programs with manually calculated finite expectations."""
import argparse
from tools.specification import ROOT
from packages.contracts.wire import canonical

def routing(v):
    header=f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Matrix routing")\n'
    setting='scale=input(2,"Scale")\n' if v<5 else 'scale=input.int(2,"Scale")\n'
    if v<5:return header+setting+'var total=0.0\ntotal:=total+close*scale\nplot(total)\nplot(close*scale)\nplot(bar_index+1)\n'
    return header+setting+'''var m=matrix.new<float>(1,2,0.)
m.set(0,0,m.get(0,0)+close*scale)
m.set(0,1,close*scale)
selected=0.0
for row in m
    selected:=row.get(0)
copy=m.transpose()
plot(selected)
plot(copy.get(1,0))
plot(bar_index+1)
'''

def corpus_sources():
    files={f'matrices-routing-v{v}.pine':routing(v).encode() for v in range(1,7)}
    for v in (5,6):
        selection=('m=matrix.new<float>(1,1,2.)\nqty=m.copy().get(0,0)\n' if v==5 else
            'type Order\n    float qty\nm=matrix.new<Order>(1,1,Order.new(1.))\nrow=m.row(0)\nrow.get(0).qty:=2.\nqty=m.get(0,0).qty\n')
        files[f'matrices-strategy-v{v}.pine']=(f'//@version={v}\n'
            'strategy("Matrix strategy",initial_capital=1000,default_qty_type=strategy.fixed,default_qty_value=2,commission_type=strategy.commission.percent,commission_value=1)\n'+selection+
            'if bar_index==0\n    strategy.entry("L",strategy.long,qty=qty)\nif bar_index==3\n    strategy.close("L")\nplot(strategy.equity)\n').encode()
    return files

def corpus_cases(record):
    from tools.semantic_core_fixtures import corpus_cases as previous
    result=previous(record)
    for c in result:
        c['id']=c['id'].replace('semantic-','matrices-');c['family']=c['family'].replace('semantic-','matrices-')
        c['files']['source']=record('examples/corpus/'+c['id']+'.pine')
        if c['family']=='matrices-routing':
            c['inputs']={'scale':2}
            c['oracle']={'reason':'Manual closes10/20/30/40 times scale2:20/40/60/80; cumulative20/60/120/200. A copied row reads total, transpose returns last. Scalar controls v1-v4.',
                         'outputs':[[20.,60.,120.,200.],[20.,40.,60.,80.],[1,2,3,4]]}
        else:c['oracle']['reason']='Matrix lookup selects qty2; v6 mutates a shallow UDT alias through row(). Entry2 at10, exit2 at10; .2 commission per side.'
    return result

NUMERIC_BODY = """var m=matrix.new<float>(2,2,2.)
if bar_index==0
    m.set(0,0,1.)
    m.set(1,1,9.)
if bar_index==2
    m.set(1,1,5.)
plot(matrix.min(id=m))
plot(m.max())
plot(matrix.avg(m))
plot(m.median())
plot(matrix.mode(m))
plot(m.trace())"""


POWER_PRODUCT_BODY = """var a=matrix.new<int>(2,2,1)
var square=matrix.new<int>()
var fifth=matrix.new<int>()
var blocks=matrix.new<float>(1,1,0.)
if bar_index==0
    a.set(1,1,0)
    square:=a.pow(2)
    fifth:=matrix.pow(power=5,id=a)
if bar_index==2
    b=matrix.new<float>(1,2,0.)
    b.set(0,1,0.5)
    blocks:=matrix.kron(id2=b,id1=fifth)
plot(square.get(0,0)*1000+square.get(0,1)*100+square.get(1,0)*10+square.get(1,1))
plot(fifth.get(0,0)*1000+fifth.get(0,1)*100+fifth.get(1,0)*10+fifth.get(1,1))
plot(blocks.rows()*10+blocks.columns())
plot(blocks.get(0,0))
plot(bar_index>=2 ? blocks.get(1,3) : 0.)"""


def row_transform_body(v):
    body = """var m=matrix.new<int>()
var part=matrix.new<int>()
if bar_index==0
    m.add_row(array_id=array.from(2,20))
    m.add_row(array_id=array.from(1,10))
    m.add_row(array_id=array.from(2,21))
    part:=m.submatrix(0,1)
    part.set(0,1,22)
    appended=matrix.concat(id2=part,id1=m)
    appended.sort()
if bar_index==2
    m.reverse()
    matrix.sort(m,column=1,order=order.descending)
plot(m.get(0,0))
plot(m.get(0,1))
plot(m.get(1,1))
plot(m.get(2,1))
plot(m.get(3,1))
plot(part.get(0,1))"""
    if v==6:
        body += """
type Item
    int id
    string key
records=matrix.new<Item>(2,1,Item.new(1,"b"))
records.set(1,0,Item.new(2,"a"))
records.sort(sort_field="key")
plot(records.get(0,0).id)"""
    return body


PREDICATE_BODY = """var a=matrix.new<int>(2,2,0)
var gram=matrix.new<int>()
if bar_index==0
    a.set(0,1,1)
    a.set(1,0,1)
    gram:=a.mult(a.transpose())
if bar_index==2
    gram.set(0,1,2)
plot(matrix.is_square(id=gram) ? 1 : 0)
plot(gram.is_symmetric() ? 1 : 0)
plot(gram.is_identity() ? 1 : 0)
plot(gram.is_diagonal() ? 1 : 0)
plot(gram.is_binary() ? 1 : 0)
plot(gram.is_stochastic() ? 1 : 0)
plot(gram.is_triangular() ? 1 : 0)
plot(a.is_antidiagonal() ? 1 : 0)
plot(gram.is_antisymmetric() ? 1 : 0)
plot(gram.is_zero() ? 1 : 0)"""


ARITHMETIC_BODY = """var a=matrix.new<int>(2,3,1)
if bar_index==0
    a.set(0,1,2)
    a.set(0,2,3)
    a.set(1,0,4)
    a.set(1,1,5)
    a.set(1,2,6)
if bar_index==2
    a.set(1,2,8)
b=matrix.new<float>(2,3,0.5)
c=matrix.new<int>(3,1,0)
c.set(0,0,1)
c.set(2,0,-1)
v=array.from(1,0,-1)
p=matrix.mult(id2=c,id1=a)
q=a.mult(v)
s=a.sum(b)
s.set(0,0,10.)
plot(p.get(0,0))
plot(q.get(1))
plot(matrix.diff(id2=b,id1=a).get(1,2))
plot(a.sum(0.5).get(1,2))
plot(a.mult(2).get(0,0))
plot(a.diff(1).get(1,2))
plot(s.get(1,2))"""


def fixtures():
    files={};cases=[]
    def add(v,name,body,values=None,types=None,code=None,category='LANGUAGE_INVALID',complete=False):
        file=f'v{v}-{name}.pine';files[file]=(body if complete else f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Matrices")\n'+body+'\n').encode()
        expected={'status':'rejected','category':category,'code':code} if code else {
            'status':'executed','title':'Matrix routing' if complete else 'Matrices','shorttitle':'','overlay':False,'observation_count':4,
            'outputs':[{'title':'','type':t,'values':row} for t,row in zip(types or ['int']*len(values),values,strict=True)],'qualifiers':['series']*len(values)}
        case={'case_id':f'v{v}-{name}','language_version':v,'source':file,'bars':'bars.json','expected':expected}
        if complete:case['inputs']={'scale':2}
        cases.append(case)
    for v in range(1,7):
        add(v,'routing',routing(v),[[20.,60.,120.,200.],[20.,40.,60.,80.],[1,2,3,4]],['float','float','int'],complete=True)
        if v<5:
            for name,body in [('constructor','m=matrix.new<int>()'),('annotation','matrix<int> m=na'),('namespace','matrix.get(na,0,0)')]:add(v,'reject-'+name,body+'\nplot(1)',code='MATRIX_VERSION')
            continue
        add(v,'power-product',POWER_PRODUCT_BODY,[[2111]*4,[8553]*4,[11,11,24,24],[0.]*4,[0.,0.,1.5,1.5]],['int','int','int','float','float'])
        add(v,'row-transforms',row_transform_body(v),
            [[1,1,22,22],[10,10,2,2],[20,20,2,2],[21,21,2,2],[22,22,1,1],[22]*4]+([[2]*4] if v==6 else []))
        add(v,'predicates',PREDICATE_BODY,[[1]*4]+[[1,1,0,0]]*5+[[1]*4,[1]*4,[0]*4,[0]*4])
        add(v,'arithmetic',ARITHMETIC_BODY,
            [[-2]*4,[-2,-2,-4,-4],[5.5,5.5,7.5,7.5],[6.5,6.5,8.5,8.5],[2]*4,[5,5,7,7],[6.5,6.5,8.5,8.5]],
            ['int','int','float','float','int','int','float'])
        add(v,'numeric-statistics',NUMERIC_BODY,
            [[1.]*4,[9.,9.,5.,5.],[3.5,3.5,2.5,2.5],[2.]*4,[2.]*4,[10.,10.,6.,6.]],['float']*6)
        rows=[
            ('empty','m=matrix.new<int>()\nplot(m.rows()+m.columns()+m.elements_count())',[[0]*4]),
            ('dimensions','m=matrix.new<int>(2,3,7)\nplot(m.rows()*100+m.columns()*10+m.elements_count())\nplot(m.get(1,2))',[[236]*4,[7]*4]),
            ('missing-int','m=matrix.new<int>(1,1)\nplot(m.get(0,0))',[[None]*4]),
            ('string-missing','m=matrix.new<string>(1,1)\nplot(na(m.get(0,0)) ? 1 : 0)',[[1]*4]),
            ('bool-default','m=matrix.new<bool>(1,1)\nplot('+('na(m.get(0,0))' if v==5 else 'not m.get(0,0)')+' ? 1 : 0)',[[1]*4]),
            ('set','m=matrix.new<int>(1,2,1)\nm.set(0,1,8)\nplot(m.get(0,0)*10+m.get(0,1))',[[18]*4]),
            ('fill','m=matrix.new<int>(2,3,1)\nm.fill(7,from_row=1,to_column=2)\nplot(m.get(1,1)*10+m.get(1,2))',[[71]*4]),
            ('fill-all','m=matrix.new<int>(2,2,1)\nm.fill(7)\nm.fill(9,1,1,0,2)\nplot(m.get(1,1))',[[7]*4]),
            ('alias-copy','m=matrix.new<int>(1,1,1)\na=m\na.set(0,0,2)\nb=m.copy()\nb.set(0,0,3)\nplot(m.get(0,0)*10+b.get(0,0))',[[23]*4]),
            ('row-col','m=matrix.new<int>(2,2,1)\nm.set(1,0,3)\na=m.row(1)\nb=m.col(0)\na.set(0,9)\nb.set(1,8)\nplot(m.get(1,0))',[[3]*4]),
            ('transpose','m=matrix.new<int>(2,3,1)\nm.set(1,2,7)\nt=m.transpose()\nm.set(1,2,9)\nplot(t.get(2,1))\nplot(t.rows()*10+t.columns())',[[7]*4,[32]*4]),
            ('reshape','m=matrix.new<int>(2,2,0)\nm.set(1,0,3)\nm.reshape(1,4)\nplot(m.get(0,2))',[[3]*4]),
            ('add-row','m=matrix.new<int>()\na=array.from(1,2)\nm.add_row(array_id=a)\na.set(0,9)\nm.add_row(0,array.from(3,4))\nplot(m.get(1,0)*10+m.get(0,1))',[[14]*4]),
            ('add-col','m=matrix.new<int>()\nm.add_col(array_id=array.from(1,2))\nm.add_col(0,array.from(3,4))\nplot(m.get(1,0)*10+m.get(0,1))',[[41]*4]),
            ('remove-row','m=matrix.new<int>(2,2,1)\nm.set(1,0,3)\na=m.remove_row()\na.set(0,9)\nplot(m.rows())\nplot(m.get(0,0))',[[1]*4,[1]*4]),
            ('remove-col','m=matrix.new<int>(2,2,1)\nm.set(1,1,3)\na=m.remove_col()\nplot(a.get(1))\nplot(m.columns())',[[3]*4,[1]*4]),
            ('swap','m=matrix.new<int>(2,2,0)\nm.set(1,1,7)\nm.swap_rows(0,1)\nm.swap_columns(0,1)\nplot(m.get(0,0))',[[7]*4]),
            ('zero-rows','m=matrix.new<int>(0,3)\nm.add_row()\na=m.remove_row()\nplot(m.rows()*10+m.columns())\nplot(a.size())',[[3]*4,[3]*4]),
            ('zero-cols','m=matrix.new<int>(2,0)\nm.add_col(array_id=array.from(1,2))\nplot(m.get(1,0))',[[2]*4]),
            ('for-in','m=matrix.new<int>(2,2,1)\ntotal=0\nfor [i,row] in m\n    total:=total+row.get(0)+i\n    row.set(0,9)\n    m.set(1,0,5)\nplot(total)\nplot(m.get(0,0))',[[7]*4,[1]*4]),
            ('udf','read(matrix<int> m) => m.get(0,0)\nm=matrix.new<int>(1,1,8)\nplot(read(m))',[[8]*4]),
            ('override','method get(matrix<int> m,int alias) => m.rows()+alias\nm=matrix.new<int>(1,1,3)\nplot(m.get(alias=7))',[[8]*4]),
            ('enum','enum Side\n    up\n    down\nm=matrix.new<Side>(1,1,Side.up)\nm.set(0,0,Side.down)\nplot(m.get(0,0)==Side.down ? 1 : 0)',[[1]*4]),
            ('udt','type Box\n    int n\nm=matrix.new<Box>(1,1,Box.new(1))\na=m.row(0)\nc=m.copy()\na.get(0).n:=7\nplot(c.get(0,0).n)',[[7]*4]),
            ('history','m=matrix.new<int>(1,1,bar_index)\np=m[1]\nplot(na(p) ? -1 : p.get(0,0))',[[-1,0,1,2]]),
            ('missing-ref','matrix<int> m=na\nplot(na(m) ? 1 : 0)\nm:=matrix.new<int>()\nplot(na(m) ? 1 : 0)',[[1]*4,[0]*4]),
        ]
        for name,body,values in rows:add(v,name,body,values)
        add(v,'float','m=matrix.new<float>(1,1,2)\nplot(m.get(0,0))',[[2.0]*4],['float'])
        add(v,'string','m=matrix.new<string>(1,1,"ключ")\nm.fill(na)\nplot(na(m.get(0,0)) ? 1 : 0)',[[1]*4])
        for name,body,code,category in [
            ('shape','m=matrix.new<int>(-1,2)','MATRIX_SHAPE','RUNTIME_ERROR'),
            ('product','m=matrix.new<int>(1000,1000)','MATRIX_SHAPE','RUNTIME_ERROR'),
            ('dimension','m=matrix.new<int>(100001,0)','MATRIX_SHAPE','RUNTIME_ERROR'),
            ('index','m=matrix.new<int>(1,1)\nm.get(-1,0)','MATRIX_INDEX','RUNTIME_ERROR'),
            ('reshape','m=matrix.new<int>(1,1)\nm.reshape(1,2)','MATRIX_SHAPE','RUNTIME_ERROR'),
            ('vector','m=matrix.new<int>(2,2)\nm.add_row(array_id=array.from(1))','MATRIX_ARRAY_SIZE','RUNTIME_ERROR'),
            ('empty-remove','m=matrix.new<int>()\nm.remove_row()','MATRIX_INDEX','RUNTIME_ERROR'),
            ('shape-iteration','m=matrix.new<int>(2,2,1)\nfor row in m\n    m.reshape(1,4)','MATRIX_ITERATION_SHAPE','RUNTIME_ERROR'),
            ('missing','matrix<int> m=na\nm.rows()','MATRIX_NA','RUNTIME_ERROR'),
            ('receiver','matrix.get(array.new<int>(),0,0)','MATRIX_TYPE','LANGUAGE_INVALID'),
            ('type','m=matrix.new<int>(1,1)\nmatrix.set(m,0,0,true)','MATRIX_ARGUMENT_TYPE','LANGUAGE_INVALID'),
            ('argument','m=matrix.new<int>()\nmatrix.get(m,0)','MISSING_ARGUMENT','LANGUAGE_INVALID'),
            ('named','matrix.new<int>(wrong=1)','UNKNOWN_ARGUMENT','LANGUAGE_INVALID'),
            ('operator','m=matrix.new<int>()\nplot(m==m)','MATRIX_OPERATOR','PROFILE_UNCOVERED'),
        ]:add(v,'reject-'+name,body+'\nplot(1)',code=code,category=category)
    files['bars.json']=(ROOT/'conformance/functions/fixed.json').read_bytes()
    files['cases.json']=canonical({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','basis':'Manual row-major coordinates, shallow UDT aliases and fixed closes10/20/30/40; iteration and sparse defaults are explicit bounded interpretations.','external_pine_execution':False},
        'rule_refs':[],'assertion_refs':[],'cases':cases})+b'\n'
    return files

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    files={'conformance/matrices/'+n:b for n,b in fixtures().items()};files.update({'examples/corpus/'+n:b for n,b in corpus_sources().items()})
    for name,raw in files.items():
        path=ROOT/name
        if a.check:assert path.read_bytes()==raw,name
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Owned matrices fixtures and independent expectations verified.')
if __name__=='__main__':main()
