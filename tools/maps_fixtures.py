"""Owned maps programs and finite independently calculated expectations."""
import argparse
from tools.specification import ROOT
from packages.contracts.wire import canonical

def routing(v):
    header=f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Map routing")\n'
    setting='scale=input(2,"Scale")\n' if v<5 else 'scale=input.int(2,"Scale")\n'
    if v<5:return header+setting+'var total=0.0\ntotal:=total+close*scale\nplot(total)\nplot(close*scale)\nplot(bar_index+1)\n'
    return header+setting+'''var m=map.new<string,float>()
m.put("total",nz(m.get("total"),0)+close*scale)
m.put("last",close*scale)
selected=0.0
for [key,value] in m
    if key=="total"
        selected:=value
copy=m.copy()
plot(selected)
plot(copy.get("last"))
plot(bar_index+1)
'''

def corpus_sources():
    files={f'maps-routing-v{v}.pine':routing(v).encode() for v in range(1,7)}
    for v in (5,6):
        selection=('m=map.new<string,float>()\nm.put("qty",2.)\nqty=m.copy().get("qty")\n' if v==5 else
            'type Order\n    float qty\nm=map.new<string,Order>()\nm.put("qty",Order.new(1.))\nvalues=m.values()\nvalues.get(0).qty:=2.\nqty=m.get("qty").qty\n')
        files[f'maps-strategy-v{v}.pine']=(f'//@version={v}\n'
            'strategy("Map strategy",initial_capital=1000,default_qty_type=strategy.fixed,default_qty_value=2,commission_type=strategy.commission.percent,commission_value=1)\n'+selection+
            'if bar_index==0\n    strategy.entry("L",strategy.long,qty=qty)\nif bar_index==3\n    strategy.close("L")\nplot(strategy.equity)\n').encode()
    return files

def corpus_cases(record):
    from tools.semantic_core_fixtures import corpus_cases as previous
    result=previous(record)
    for c in result:
        c['id']=c['id'].replace('semantic-','maps-');c['family']=c['family'].replace('semantic-','maps-')
        c['files']['source']=record('examples/corpus/'+c['id']+'.pine')
        if c['family']=='maps-routing':
            c['inputs']={'scale':2}
            c['oracle']={'reason':'Closes10/20/30/40 times scale2 give20/40/60/80; cumulative totals20/60/120/200. Snapshot iteration selects total; map copy retains last. Scalar controls v1-v4.',
                         'outputs':[[20.,60.,120.,200.],[20.,40.,60.,80.],[1,2,3,4]]}
        else:c['oracle']['reason']='Map lookup selects qty2; v6 changes a shared flat UDT through map.values(). Entry2 at10, exit2 at10; .2 commission each side.'
    return result

def fixtures():
    files={};cases=[]
    def add(v,name,body,values=None,types=None,code=None,category='LANGUAGE_INVALID',complete=False):
        file=f'v{v}-{name}.pine';files[file]=(body if complete else f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Maps")\n'+body+'\n').encode()
        expected={'status':'rejected','category':category,'code':code} if code else {
            'status':'executed','title':'Map routing' if complete else 'Maps','shorttitle':'','overlay':False,'observation_count':4,
            'outputs':[{'title':'','type':t,'values':row} for t,row in zip(types or ['int']*len(values),values,strict=True)],
            'qualifiers':['series']*len(values)}
        case={'case_id':f'v{v}-{name}','language_version':v,'source':file,'bars':'bars.json','expected':expected}
        if complete:case['inputs']={'scale':2}
        cases.append(case)
    for v in range(1,7):
        add(v,'routing',routing(v),[[20.,60.,120.,200.],[20.,40.,60.,80.],[1,2,3,4]],['float','float','int'],complete=True)
        if v<5:
            for name,body in [('constructor','m=map.new<string,int>()\nplot(1)'),('annotation','map<string,int> m=na\nplot(1)'),('namespace','map.get(na,"x")\nplot(1)')]:
                add(v,'reject-'+name,body,code='MAP_VERSION')
            continue
        rows=[
            ('new-size','m=map.new<string,int>()\nplot(m.size())',[[0]*4]),
            ('previous','m=map.new<string,int>()\nplot(m.put("a",2))\nplot(m.put("a",3))\nplot(m.get("a"))',[[None]*4,[2]*4,[3]*4]),
            ('contains-remove','m=map.new<int,int>()\nm.put(2,3)\nplot(m.contains(2) ? 1 : 0)\nplot(m.remove(2))\nplot(m.contains(2) ? 1 : 0)\nplot(m.remove(2))',[[1]*4,[3]*4,[0]*4,[None]*4]),
            ('clear','m=map.new<int,int>()\nm.put(1,2)\nm.clear()\nplot(m.size())',[[0]*4]),
            ('overwrite-order','m=map.new<int,int>()\nm.put(2,20)\nm.put(1,10)\nm.put(2,21)\nplot(m.keys().get(0))\nplot(m.values().get(0))',[[2]*4,[21]*4]),
            ('reinsert','m=map.new<int,int>()\nm.put(2,20)\nm.put(1,10)\nm.remove(2)\nm.put(2,22)\nplot(m.keys().get(1))',[[2]*4]),
            ('copy-alias','a=map.new<int,int>()\na.put(1,2)\nb=a\nb.put(1,3)\nc=a.copy()\nc.put(1,4)\nplot(a.get(1)*10+c.get(1))',[[34]*4]),
            ('independent-arrays','m=map.new<int,int>()\nm.put(1,2)\nk=m.keys()\nk.set(0,8)\na=m.values()\na.set(0,9)\nplot(m.get(1))\nplot(m.contains(8) ? 1 : 0)',[[2]*4,[0]*4]),
            ('put-all','a=map.new<int,int>()\na.put(2,20)\na.put(1,10)\nb=map.new<int,int>()\nb.put(1,11)\nb.put(3,30)\na.put_all(b)\na.put_all(a)\nplot(a.size())\nplot(a.get(1))\nplot(a.keys().get(2))',[[3]*4,[11]*4,[3]*4]),
            ('missing-int','m=map.new<string,int>()\nplot(m.get("x"))\nplot(m.remove("x"))',[[None]*4]*2),
            ('bool-value','m=map.new<string,bool>()\nplot(m.get("x") ? 1 : 0)\nm.put("x",true)\nplot(m.get("x") ? 1 : 0)',[[0]*4,[1]*4]),
            ('missing-string','m=map.new<string,string>()\nplot(na(m.get("x")) ? 1 : 0)\nm.put("x",na)\nplot(na(m.get("x")) ? 1 : 0)',[[1]*4]*2),
            ('unicode','m=map.new<string,string>()\nm.put("ключ","да")\nplot(m.get("ключ")=="да" ? 1 : 0)\nplot(m.size())',[[1]*4,[1]*4]),
            ('signed-zero','m=map.new<float,int>()\nm.put(-0.,1)\nm.put(0.,2)\nplot(m.size())\nplot(m.get(-0.))',[[1]*4,[2]*4]),
            ('missing-key','m=map.new<float,int>()\nm.put(na,2)\nm.put(float(na),3)\nplot(m.size())\nplot(m.get(na))',[[1]*4,[3]*4]),
            ('bool-key','m=map.new<bool,int>()\nm.put(false,2)\nm.put(true,3)\nplot(m.get(false)*10+m.get(true))',[[23]*4]),
            ('enum-key','enum Side\n    up\n    down\nm=map.new<Side,int>()\nm.put(Side.up,7)\nplot(m.get(Side.up))\nplot(m.keys().get(0)==Side.up ? 1 : 0)',[[7]*4,[1]*4]),
            ('enum-value','enum Side\n    up\n    down\nm=map.new<int,Side>()\nm.put(1,Side.down)\nplot(m.values().get(0)==Side.down ? 1 : 0)',[[1]*4]),
            ('object-alias','type Box\n    int n\nm=map.new<string,Box>()\nx=Box.new(2)\nm.put("x",x)\nc=m.copy()\na=m.values()\na.get(0).n:=7\nplot(c.get("x").n)\nm.clear()\nplot(c.get("x").n)',[[7]*4]*2),
            ('for-in','m=map.new<int,int>()\nm.put(2,20)\nm.put(1,10)\ntotal=0\nfor [k,v] in m\n    total:=total*100+k*10+v\nplot(total)',[[4020]*4]),
            ('for-in-mutation','m=map.new<int,int>()\nm.put(1,10)\nm.put(2,20)\ntotal=0\nfor [k,v] in m\n    total:=total+nz(v,0)\n    m.remove(2)\n    m.put(3,30)\nplot(total)',[[10]*4]),
            ('udf','read(map<string,int> m,string key) => m.get(key)\nm=map.new<string,int>()\nm.put("x",8)\nplot(read(m,"x"))',[[8]*4]),
            ('method','method add(map<string,int> m,int n) =>\n    m.put("x",n)\n    m.get("x")\nm=map.new<string,int>()\nplot(m.add(6))',[[6]*4]),
            ('named-order','mark(array<int> a,int x) =>\n    a.push(x)\n    x\nm=map.new<int,int>()\na=array.new<int>()\nmap.put(value=mark(a,2),id=m,key=mark(a,1))\nplot(a.get(0)*10+a.get(1))',[[21]*4]),
            ('history','m=map.new<string,int>()\nm.put("x",bar_index)\np=m[1]\nplot(na(p) ? -1 : p.get("x"))',[[-1,0,1,2]]),
        ]
        for name,body,values in rows:add(v,name,body,values)
        add(v,'float-values','m=map.new<int,float>()\nm.put(1,2)\nplot(m.get(1))',[[2.0]*4],['float'])
        for name,body,code,category in [
            ('key-type','m=map.new<int,int>()\nmap.put(m,true,1)','MAP_ARGUMENT_TYPE','LANGUAGE_INVALID'),
            ('value-type','m=map.new<int,int>()\nmap.put(m,1,1.5)','MAP_ARGUMENT_TYPE','LANGUAGE_INVALID'),
            ('map-type','map.get(array.new<int>(),1)','MAP_TYPE','LANGUAGE_INVALID'),
            ('missing-argument','m=map.new<int,int>()\nmap.put(m,1)','MISSING_ARGUMENT','LANGUAGE_INVALID'),
            ('unknown-argument','m=map.new<int,int>()\nmap.put(m,wrong=1,value=2)','UNKNOWN_ARGUMENT','LANGUAGE_INVALID'),
            ('duplicate-argument','m=map.new<int,int>()\nmap.put(m,key=1,key=2,value=3)','DUPLICATE_ARGUMENT','LANGUAGE_INVALID'),
            ('different-map','a=map.new<int,int>()\nmap.put_all(a,map.new<string,int>())','MAP_ARGUMENT_TYPE','LANGUAGE_INVALID'),
            ('enum-type','enum A\n    x\nenum B\n    x\nm=map.new<A,int>()\nmap.put(m,B.x,1)','MAP_ARGUMENT_TYPE','LANGUAGE_INVALID'),
            ('reference-key','type A\n    int x\nm=map.new<A,int>()','MAP_TYPE','LANGUAGE_INVALID'),
            ('single-iterator','m=map.new<int,int>()\nfor x in m\n    continue','MAP_ITERATOR','LANGUAGE_INVALID'),
            ('missing-reference','map<string,int> m=na\nm.put("x",1)','MAP_NA','RUNTIME_ERROR'),
            ('operator','m=map.new<string,int>()\nplot(m==m)','MAP_OPERATOR','PROFILE_UNCOVERED'),
        ]:add(v,'reject-'+name,body+'\nplot(1)',code=code,category=category)
        add(v,'enum-missing','enum Side\n    up\n    down\nm=map.new<Side,Side>()\nm.put(Side.up,na)\nm.put(na,Side.down)\nplot(m.size())\nplot(m.get(na)==Side.down ? 1 : 0)\nplot(na(m.get(Side.up)) ? 1 : 0)',[[2]*4,[1]*4,[1]*4])
        for name,body in [('request-expression','m=map.new<string,float>()\nm.put("x",close)\nr=request.security("TEST:X","D",m.get("x"))\nplot(r)'),('nested-request','m=map.new<string,float>()\nm.put("x",request.security("TEST:X","D",close))\nplot(m.get("x"))')]:
            add(v,'reject-'+name,body,code='REQUEST_SCOPE',category='PROFILE_UNCOVERED')
        if v==6:add(v,'reject-bool-na','m=map.new<bool,int>()\nmap.put(m,na,1)\nplot(1)',code='MAP_ARGUMENT_TYPE')
    files['bars.json']=(ROOT/'conformance/functions/fixed.json').read_bytes()
    files['cases.json']=canonical({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','basis':'Manual ordered key/value contents, shallow aliases and fixed closes10/20/30/40. Traversal mutation/missing key edges are bounded interpretations.','external_pine_execution':False},
        'rule_refs':[],'assertion_refs':[],'cases':cases})+b'\n'
    return files

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    files={'conformance/maps/'+n:b for n,b in fixtures().items()}
    files.update({'examples/corpus/'+n:b for n,b in corpus_sources().items()})
    for name,raw in files.items():
        path=ROOT/name
        if a.check:assert path.read_bytes()==raw,name
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Owned maps fixtures and independent expectations verified.')
if __name__=='__main__':main()
