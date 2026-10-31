"""Owned shared-window programs and manually authored finite expectations."""
import argparse
from tools.specification import ROOT
from packages.contracts.wire import canonical

def routing(v):
    header=f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Array windows")\n'
    setting='scale=input(2,"Scale")\n' if v<5 else 'scale=input.int(2,"Scale")\n'
    if v<4:return header+setting+'total=(close+nz(close[1],0))*scale\nplot(total)\nplot(close*scale)\nplot(bar_index+1)\n'
    return header+setting+'''var values=array.new_float(0)
array.push(values,close*scale)
n=array.size(values)
window=array.slice(values,n>1 ? n-2 : 0,n)
total=array.sum(window)
array.reverse(window)
latest=array.get(values,n>1 ? n-2 : 0)
array.reverse(window)
plot(total)
plot(latest)
plot(array.size(values))
'''

def corpus_sources():
    result={f'array-slice-routing-v{v}.pine':routing(v).encode() for v in range(1,7)}
    for v in (5,6):
        selection=('units=array.from(999.,1.,2.)\nwindow=units.slice(1,3)\nwindow.sort(order.descending)\nqty=units.get(1)\n' if v==5 else
            'type Signal\n    float units\nunits=array.from(Signal.new(999.),Signal.new(1.),Signal.new(2.))\nwindow=units.slice(1,3)\nwindow.sort(order.descending,sort_field="units")\nqty=units.get(1).units\n')
        result[f'array-slice-strategy-v{v}.pine']=(f'//@version={v}\n'
            'strategy("Array window strategy",initial_capital=1000,default_qty_type=strategy.fixed,default_qty_value=2,commission_type=strategy.commission.percent,commission_value=1)\n'+selection+
            'if bar_index==0\n    strategy.entry("L",strategy.long,qty=qty)\nif bar_index==3\n    strategy.close("L")\nplot(strategy.equity)\n').encode()
    return result

def corpus_cases(record):
    from tools.semantic_core_fixtures import corpus_cases as previous
    result=previous(record)
    for c in result:
        c['id']=c['id'].replace('semantic-','array-slice-');c['family']=c['family'].replace('semantic-','array-slice-')
        c['files']['source']=record('examples/corpus/'+c['id']+'.pine')
        if c['family']=='array-slice-routing':
            c['inputs']={'scale':2}
            c['oracle']={'reason':'Scaled closes20/40/60/80. Last two sum20/60/100/140. Reversing their shared window moves the latest to the first window position in the root; reverse again restores chronology. Scalar control v1-v3.',
                'outputs':[[20.,60.,100.,140.],[20.,40.,60.,80.],[1,2,3,4]]}
        else:c['oracle']['reason']='Sort only slots1..2 through a shared window, leaving sentinel999 outside. Root slot1 selects qty2. V6 uses flat UDT references. Entry bar1 open10, exit bar4 open10; .2 commission each side.'
    return result

def fixtures():
    files={};cases=[]
    def add(v,name,body,values=None,types=None,code=None,category='LANGUAGE_INVALID',complete=False):
        filename=f'v{v}-{name}.pine';files[filename]=(body if complete else f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Array slice")\n'+body+'\n').encode()
        expected={'status':'rejected','category':category,'code':code} if code else {'status':'executed','title':'Array windows' if complete else 'Array slice','shorttitle':'','overlay':False,'observation_count':4,
            'outputs':[{'title':'','type':t,'values':row} for t,row in zip(types or ['int']*len(values),values,strict=True)],'qualifiers':['series']*len(values)}
        cases.append({'case_id':f'v{v}-{name}','language_version':v,'source':filename,'bars':'bars.json','expected':expected})
    for v in range(1,7):
        add(v,'routing',routing(v),[[20.,60.,100.,140.],[20.,40.,60.,80.],[1,2,3,4]],['float','float','int'],complete=True)
        if v<4:
            add(v,'reject-version','array.slice(na,0,1)\nplot(1)',code='ARRAY_VERSION');continue
        for name,body,values,types in [
            ('set','a=array.from(1,2,3,4)\nb=array.slice(a,1,3)\narray.set(b,0,8)\narray.set(a,2,9)\nplot(array.get(a,1)*10+array.get(b,1))',[[89]*4],None),
            ('push-pop','a=array.from(1,2,3,4)\nb=array.slice(a,1,3)\narray.push(b,8)\nplot(array.size(a)*100+array.size(b)*10+array.get(a,3))\nplot(array.pop(b))\nplot(array.get(a,3))',[[538]*4,[8]*4,[4]*4],None),
            ('unshift-shift','a=array.from(1,2,3,4)\nb=array.slice(a,1,3)\narray.unshift(b,9)\nplot(array.shift(b))\nplot(array.get(a,1))',[[9]*4,[2]*4],None),
            ('clear-refill','a=array.from(1,2,3,4)\nb=array.slice(a,1,3)\narray.clear(b)\nplot(array.size(a)*10+array.size(b))\narray.push(b,9)\nplot(array.get(a,1)*10+array.get(a,2))',[[20]*4,[94]*4],None),
            ('positional','a=array.from(1,2,3,4)\nb=array.slice(a,1,3)\narray.unshift(a,0)\nplot(array.get(b,0)*10+array.get(b,1))',[[12]*4],None),
            ('nested','a=array.from(0,1,2,3,4)\nb=array.slice(a,1,4)\nc=array.slice(b,1,3)\narray.push(c,9)\nplot(array.size(b)*100+array.size(c)*10+array.get(a,4))\nplot(array.get(b,2))',[[339]*4,[3]*4],None),
            ('overlap','a=array.from(0,1,2,3)\nb=array.slice(a,1,3)\nc=array.slice(a,2,4)\narray.push(b,9)\nplot(array.get(c,0)*10+array.get(c,1))',[[29]*4],None),
            ('copy','a=array.from(1,2,3)\nb=array.slice(a,1,3)\nc=array.copy(b)\narray.set(c,0,8)\nplot(array.get(b,0)*10+array.get(c,0))',[[28]*4],None),
            ('editing','a=array.from(0,1,2,3,4)\nb=array.slice(a,1,4)\narray.insert(b,1,8)\nplot(array.remove(b,2))\narray.reverse(b)\narray.fill(b,9,1,3)\nplot(array.get(a,0)*10000+array.get(a,1)*1000+array.get(a,2)*100+array.get(a,3)*10+array.get(a,4))',[[2]*4,[3994]*4],None),
            ('self-concat','a=array.from(0,1,2,3)\nb=array.slice(a,1,3)\narray.concat(b,b)\nplot(array.size(a)*100+array.size(b)*10+array.get(a,4))',[[642]*4],None),
            ('concat-parent','a=array.from(0,1,2,3)\nb=array.slice(a,1,3)\narray.concat(a,b)\nplot(array.get(a,4)*10+array.get(a,5))',[[12]*4],None),
            ('concat-overlap','a=array.from(0,1,2,3,4)\nb=array.slice(a,1,3)\nc=array.slice(a,2,5)\narray.concat(b,c)\nplot(array.size(a)*100+array.size(b)*10+array.get(a,5))',[[854]*4],None),
            ('sort','a=array.from(9,3,1,2,8)\nb=array.slice(a,1,4)\narray.sort(b)\nplot(array.get(a,0)*10000+array.get(a,1)*1000+array.get(a,2)*100+array.get(a,3)*10+array.get(a,4))',[[91238]*4],None),
            ('search-aggregate','a=array.from(9,2,3,2,8)\nb=array.slice(a,1,4)\nplot(array.indexof(b,2))\nplot(array.lastindexof(b,2))\nplot(array.includes(b,8) ? 1 : 0)\nplot(array.sum(b))\nplot(array.avg(b))',[[0]*4,[2]*4,[0]*4,[7]*4,[7/3]*4],['int','int','int','int','float']),
            ('growth-revives','a=array.from(1,2,3)\nb=array.slice(a,1,3)\narray.clear(a)\narray.push(a,4)\narray.push(a,5)\narray.push(a,6)\nplot(array.get(b,0)*10+array.get(b,1))',[[56]*4],None),
            ('bool','a=array.from(false,true,false)\nb=array.slice(a,1,3)\narray.set(b,0,false)\nplot(array.get(a,1) ? 0 : 1)',[[1]*4],None),
            ('string','a=array.from("x","ёж","猫")\nb=array.slice(a,1,3)\narray.fill(b,"a")\nplot(array.get(a,2)=="a" ? array.size(b) : -1)',[[2]*4],None),
            ('float','a=array.from(1.,2.,3.)\nb=array.slice(a,1,3)\nplot(array.avg(b))',[[2.5]*4],['float']),
        ]:add(v,name,body,values,types)
        for name,start,end in [('negative',-1,2),('empty',1,1),('reversed',2,1),('beyond',0,4)]:
            add(v,'reject-'+name,f'a=array.from(1,2,3)\nb=array.slice(a,{start},{end})\nplot(array.size(b))',code='ARRAY_RANGE',category='RUNTIME_ERROR')
        for name,body,code,category in [
            ('missing-bound','a=array.from(1)\narray.slice(a,na,1)','ARRAY_RANGE','RUNTIME_ERROR'),
            ('missing-id','int[] a=na\narray.slice(a,0,1)','ARRAY_NA','RUNTIME_ERROR'),
            ('shrunk','a=array.from(1,2,3)\nb=array.slice(a,1,3)\narray.pop(a)\narray.size(b)','ARRAY_SLICE_BOUNDS','RUNTIME_ERROR'),
            ('missing-argument','a=array.from(1)\narray.slice(a,0)','MISSING_ARGUMENT','LANGUAGE_INVALID'),
            ('wrong-bound-type','a=array.from(1)\narray.slice(a,0.,1)','ARRAY_INDEX_TYPE','LANGUAGE_INVALID'),
            ('unknown-name','a=array.from(1)\narray.slice(a,0,1,other=0)','UNKNOWN_ARGUMENT','LANGUAGE_INVALID'),
            ('repeated','a=array.from(1)\narray.slice(a,0,index_from=0,index_to=1)','DUPLICATE_ARGUMENT','LANGUAGE_INVALID'),
            ('storage','a=array.new_int(100000,1)\nb=array.slice(a,0,1)\narray.push(b,2)','ARRAY_STORAGE','RUNTIME_ERROR')]:
            add(v,'reject-'+name,body+'\nplot(1)',code=code,category=category)
        if v>=5:
            add(v,'methods-loop','a=array.from(0,1,2,3)\nb=a.slice(1,3)\nb.push(4)\ntotal=0\nfor x in b\n    total:=total+x\nplot(total)\nplot(b.binary_search(2))\np=b.sort_indices(order.descending)\nplot(p.get(0))',[[7]*4,[1]*4,[2]*4])
            add(v,'udt','type T\n    int x\nx=T.new(1)\na=array.from(T.new(0),x,T.new(2))\nb=a.slice(1,3)\nc=b.copy()\nb.get(0).x:=9\nplot(c.get(0).x)\nb.remove(0)\nplot(x.x)\nplot(a.get(1).x)',[[9]*4,[9]*4,[2]*4])
            add(v,'enum','enum E\n    a\n    b\na=array.from(E.a,E.b)\nb=a.slice(0,1)\nb.set(0,E.b)\nplot(a.get(0)==E.b ? 1 : 0)',[[1]*4])
    files['bars.json']=(ROOT/'conformance/functions/fixed.json').read_bytes()
    files['cases.json']=canonical({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','basis':'Manually listed positional root/window contents, splice results and fixed closes10/20/30/40. Source conflicts and unspecified nested-window behavior are bounded interpretations.','external_pine_execution':False},
        'rule_refs':[],'assertion_refs':[],'cases':cases})+b'\n'
    return files

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    files={'conformance/array-slice/'+n:b for n,b in fixtures().items()};files.update({'examples/corpus/'+n:b for n,b in corpus_sources().items()})
    for name,raw in files.items():
        path=ROOT/name
        if a.check:assert path.read_bytes()==raw,name
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Owned array slice fixtures and independent expectations verified.')
if __name__=='__main__':main()
