"""Manual finite overload expectations; fixture generation never evaluates Pine."""
import argparse
from tools.specification import ROOT
from packages.contracts.wire import canonical


def routing(v):
    header=f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Overload routing")\n'
    if v>=5:
        body='scale(int x)=>x+1\nscale(float x,int factor=2)=>x*factor\nplot(scale(close)+scale(2))\n'
    else:
        body='scale_int(int x)=>x+1\nscale_float(float x,int factor)=>x*factor\nplot(scale_float(close,2)+scale_int(2))\n'
    return header+body


def fixtures():
    files={};cases=[]
    def add(v,name,body,values=None,*,base='int',qualifier='const',reject=None,complete=False):
        file=f'v{v}-{name}.pine'
        text=body if complete else f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Overloads")\n'+body+'\n'
        files[file]=text.encode()
        expected=({'status':'rejected','category':'LANGUAGE_INVALID','code':reject} if reject else
            {'status':'executed','title':'Overload routing' if complete else 'Overloads','shorttitle':'','overlay':False,'observation_count':4,
             'outputs':[{'title':'','type':base,'values':values}],'qualifiers':[qualifier]})
        cases.append({'case_id':f'v{v}-{name}','language_version':v,'source':file,'bars':'bars.json','expected':expected})
    for v in range(1,7):
        add(v,'routing',routing(v),[23.,43.,63.,83.],base='float',qualifier='series',complete=True)
        if v<5:
            add(v,'reject-types','f(int x)=>x\nf(float x)=>x\nplot(1)',reject='FUNCTION_OVERLOAD_VERSION')
            add(v,'reject-arity','f(x)=>x\nf(x,y)=>x+y\nplot(1)',reject='FUNCTION_OVERLOAD_VERSION')
        else:
            add(v,'exact','f(int x)=>x+1\nf(float x)=>x+0.5\nplot(f(2))',[3]*4)
            add(v,'widen','f(float x)=>x+0.5\nf(string x)=>x\nplot(f(2))',[2.5]*4,base='float')
            add(v,'arity','f(x)=>x\nf(x,y)=>x+y\nplot(f(2,4))',[6]*4)
            add(v,'named-default','f(int x,int y=2)=>x+y\nf(float x,int y=3)=>x*y\nplot(f(x=close))',[30.,60.,90.,120.],base='float',qualifier='series')
            add(v,'earlier','f(int x)=>x+1\nf(float x)=>f(2)+x\nplot(f(close))',[13.,23.,33.,43.],base='float',qualifier='series')
            add(v,'series','f(simple int x)=>1\nf(series int x)=>2\nplot(f(bar_index))',[2]*4,qualifier='series')
            add(v,'state','f(int x)=>\n    var n=0\n    n:=n+x\n    n\nf(float x)=>x\nplot(f(2))',[2,4,6,8],qualifier='series')
            for name,body,code in [
                ('reject-duplicate','f(int x)=>x\nf(int y)=>2.5\nplot(1)','FUNCTION_OVERLOAD_DUPLICATE'),
                ('reject-untyped','f(x)=>x\nf(int x)=>x\nplot(1)','FUNCTION_OVERLOAD_TYPE'),
                ('reject-ambiguity','f(int x,float y)=>x+y\nf(float x,int y)=>x+y\nplot(f(1,2))','FUNCTION_OVERLOAD_AMBIGUOUS'),
                ('reject-no-match','f(int x)=>x\nf(float x)=>x\nplot(f(true))','FUNCTION_OVERLOAD_NO_MATCH'),
                ('reject-recursion','f(int x)=>f(x)\nf(float x)=>x\nplot(1)','FUNCTION_RECURSION')]:add(v,name,body,reject=code)
            optional='f(int x)=>x\nf(int x,int y=2)=>x+y\n'
            add(v,'optional-signatures',optional+'plot(f(1))',reject='FUNCTION_OVERLOAD_AMBIGUOUS' if v==5 else 'FUNCTION_OVERLOAD_DUPLICATE')
    files['bars.json']=(ROOT/'conformance/functions/fixed.json').read_bytes()
    files['cases.json']=canonical({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','basis':'close 10,20,30,40; exact scalar headers, arity, literal defaults, counters and explicit version rejections. No runtime oracle.','external_pine_execution':False},
        'rule_refs':[],'assertion_refs':[],'cases':cases})+b'\n'
    return files


def corpus_sources():
    files={f'overload-routing-v{v}.pine':routing(v).encode() for v in range(1,7)}
    for v in (5,6):
        files[f'overload-strategy-v{v}.pine']=(f'//@version={v}\n'
            'strategy("Overload strategy",initial_capital=1000,default_qty_type=strategy.fixed,default_qty_value=2,commission_type=strategy.commission.percent,commission_value=1)\n'
            'size(int qty)=>qty\nsize(float qty)=>qty\nif bar_index==0\n    strategy.entry("L",strategy.long,qty=size(2))\n'
            'if bar_index==3\n    strategy.close("L")\nplot(strategy.equity)\n').encode()
    return files


def corpus_cases(record):
    from tools.semantic_core_fixtures import corpus_cases as previous
    cases=previous(record)
    for c in cases:
        c['id']=c['id'].replace('semantic-','overload-');c['family']=c['family'].replace('semantic-','overload-')
        c['files']['source']=record('examples/corpus/'+c['id']+'.pine')
        if c['family']=='overload-routing':
            c['oracle']={'reason':'close 10,20,30,40 times2 plus integer branch 2+1. v1-v4 use distinct names; v5/v6 resolve overload headers.','outputs':[[23.,43.,63.,83.]]}
        else:c['oracle']['reason']='Integer overload selects quantity2. Enter2 at open10 and exit2 at open10; commissions .2 each side; close mark-to-market.'
    return cases


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    files={'conformance/function-overloads/'+name:raw for name,raw in fixtures().items()}
    files.update({'examples/corpus/'+name:raw for name,raw in corpus_sources().items()})
    for name,raw in files.items():
        path=ROOT/name
        if a.check:assert path.read_bytes()==raw,name
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Owned overload programs and manual expectations verified.')


if __name__=='__main__':main()
