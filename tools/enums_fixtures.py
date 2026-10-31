"""Owned enum workflows with independent finite expectations."""
import argparse
from tools.specification import ROOT
from packages.contracts.wire import canonical

HEAD='enum Mode\n    direct = "Close"\n    doubled = "Double"\n'

def routing(v):
    header=f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Enum routing")\n'
    if v<5:return header+'mode=input(1,"Mode")\nplot(mode==1 ? close : close*2)\n'
    return header+HEAD+'''mode=input.enum(Mode.direct,"Mode")
type Sample
    Mode mode=Mode.direct
    float price=0.
method value(Sample self)=>
    switch self.mode
        Mode.direct=>self.price
        =>self.price*2
var modes=array.new<Mode>()
modes.push(mode)
if modes.size()>2
    modes.shift()
point=Sample.new(modes.get(0),close)
plot(point.value())
'''

def corpus_sources():
    result={f'enum-routing-v{v}.pine':routing(v).encode() for v in range(1,7)}
    for v in (5,6):
        result[f'enum-strategy-v{v}.pine']=(f'//@version={v}\n'
            'strategy("Enum strategy",initial_capital=1000,default_qty_type=strategy.fixed,default_qty_value=2,commission_type=strategy.commission.percent,commission_value=1)\n'
            'enum Phase\n    waiting\n    entering\n    exiting\n'
            'initial=input.enum(Phase.entering,"Initial phase")\n'
            'type Decision\n    Phase phase=Phase.waiting\n    float units=2.\n'
            'var signal=Decision.new()\nif bar_index==0\n    signal.phase:=initial\n'
            'if signal.phase==Phase.entering\n    strategy.entry("L",strategy.long,qty=signal.units)\n    signal.phase:=Phase.waiting\n'
            'if bar_index==3\n    signal.phase:=Phase.exiting\n'
            'if signal.phase==Phase.exiting\n    strategy.close("L")\n    signal.phase:=Phase.waiting\nplot(strategy.equity)\n').encode()
    return result

def corpus_cases(record):
    from tools.semantic_core_fixtures import corpus_cases as previous
    result=previous(record)
    for c in result:
        c['id']=c['id'].replace('semantic-','enum-');c['family']=c['family'].replace('semantic-','enum-')
        c['files']['source']=record('examples/corpus/'+c['id']+'.pine')
        if c['family']=='enum-routing':
            c['inputs']={'mode':2 if c['version']<5 else 'enum:3:1:2:1'}
            c['oracle']={'reason':'Frozen doubled mode: close 10,20,30,40 times2; integer choices v1-v4, enum input, scalar enum array, UDT field and method v5/v6.','outputs':[[20.,40.,60.,80.]]}
        else:c['oracle']['reason']='Enum phase submits quantity2 once at bar0; entry fills bar1 open10 and exit at bar4 open10, commission .2 each side.'
    return result

def fixtures():
    files={};cases=[]
    def add(v,name,body,values=None,base='int',code=None,category='LANGUAGE_INVALID',q='series',complete=False):
        filename=f'v{v}-{name}.pine';files[filename]=(body if complete else f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Enums")\n'+body+'\n').encode()
        expected={'status':'rejected','category':category,'code':code} if code else {'status':'executed','title':'Enum routing' if complete else 'Enums','shorttitle':'','overlay':False,'observation_count':4,
            'outputs':[{'title':'','type':base,'values':row} for row in values],'qualifiers':[q]*len(values)}
        cases.append({'case_id':f'v{v}-{name}','language_version':v,'source':filename,'bars':'bars.json','expected':expected})
    for v in range(1,7):
        add(v,'routing',routing(v),[[10.,20.,30.,40.]],base='float',complete=True)
        if v<5:
            add(v,'reject-version',HEAD+'plot(1)',code='ENUM_VERSION');continue
        def positive(name,body,values):add(v,name,HEAD+body,values)
        positive('literal-identity','plot((Mode.direct==Mode.direct ? 1 : 0)+bar_index*0)',[[1]*4])
        positive('inequality','plot((Mode.direct!=Mode.doubled ? 1 : 0)+bar_index*0)',[[1]*4])
        positive('titles','plot((str.tostring(Mode.doubled)=="Double" ? 1 : 0)+bar_index*0)',[[1]*4])
        positive('missing','Mode m=na\nplot((na(m) ? 1 : 0)+bar_index*0)',[[1]*4])
        positive('assignment','var Mode m=Mode.direct\nm:=bar_index%2==0 ? Mode.direct : Mode.doubled\nplot(m==Mode.direct ? 1 : 0)',[[1,0,1,0]])
        positive('history','Mode m=bar_index%2==0 ? Mode.direct : Mode.doubled\nplot(na(m[1]) ? -1 : m[1]==Mode.direct ? 1 : 0)',[[-1,1,0,1]])
        positive('switch','m=switch bar_index\n    0=>Mode.direct\n    =>Mode.doubled\nplot(m==Mode.direct ? 1 : 0)',[[1,0,0,0]])
        positive('missing-switch','m=switch bar_index\n    0=>Mode.direct\nplot(na(m) ? -1 : 1)',[[1,-1,-1,-1]])
        positive('udf-default','f(Mode m=Mode.doubled)=>m\nplot((f()==Mode.doubled ? 1 : 0)+bar_index*0)',[[1]*4])
        positive('overloads','enum Other\n    direct\nf(Mode m)=>1\nf(Other m)=>2\nplot(f(Mode.direct)+f(Other.direct)+bar_index*0)',[[3]*4])
        positive('method','method code(Mode self)=>self==Mode.direct ? 1 : 2\nplot((Mode.doubled).code()+bar_index*0)',[[2]*4])
        positive('input','m=input.enum(Mode.doubled,"Mode",options=[Mode.direct,Mode.doubled])\nplot((m==Mode.doubled ? 1 : 0)+bar_index*0)',[[1]*4])
        positive('fields','type T\n    Mode m=Mode.direct\nvar a=T.new()\na.m:=bar_index%2==0 ? Mode.direct : Mode.doubled\nplot(a.m==Mode.direct ? 1 : 0)',[[1,0,1,0]])
        positive('array-from','a=array.from(Mode.direct,na,Mode.doubled)\nplot(a.size())\nplot(na(a.get(1)) ? 1 : 0)',[[3]*4,[1]*4])
        positive('array-default','a=array.new<Mode>(2)\nplot(na(a.get(0)) ? 1 : 0)',[[1]*4])
        positive('array-deque','a=array.new<Mode>()\na.push(Mode.direct)\na.unshift(Mode.doubled)\na.set(1,Mode.doubled)\nfirst=a.shift()\nlast=a.pop()\nplot(first==last ? 1 : 0)',[[1]*4])
        positive('array-copy','a=array.from(Mode.direct)\nb=a.copy()\nb.set(0,Mode.doubled)\nplot(a.get(0)==Mode.direct ? 1 : 0)',[[1]*4])
        positive('iteration','a=array.from(Mode.direct,Mode.doubled,Mode.direct)\nn=0\nfor m in a\n    n:=n+(m==Mode.direct ? 1 : 0)\nplot(n)',[[2]*4])
        negatives=[
            ('foreign-assignment','enum Other\n    direct\nMode m=Other.direct\nplot(1)','ASSIGNMENT_TYPE','LANGUAGE_INVALID'),
            ('string-assignment','Mode m="Close"\nplot(1)','ASSIGNMENT_TYPE','LANGUAGE_INVALID'),
            ('number-assignment','Mode m=1\nplot(1)','ASSIGNMENT_TYPE','LANGUAGE_INVALID'),
            ('ordering','plot(Mode.direct<Mode.doubled ? 1 : 0)','ORDERING_TYPE','LANGUAGE_INVALID'),
            ('arithmetic','plot(Mode.direct+1)','EXPRESSION_TYPE','LANGUAGE_INVALID'),
            ('unknown-member','m=Mode.missing\nplot(1)','ENUM_UNKNOWN_MEMBER','LANGUAGE_INVALID'),
            ('field-type','type T\n    Mode m\na=T.new(1)\nplot(1)','UDT_FIELD_TYPE','LANGUAGE_INVALID'),
            ('array-type','a=array.new<Mode>()\narray.push(a,"Close")\nplot(1)','ARRAY_ELEMENT_TYPE','LANGUAGE_INVALID'),
            ('numeric-aggregate','a=array.from(Mode.direct)\nplot(a.sum())','ARRAY_NUMERIC','LANGUAGE_INVALID'),
            ('mixed-array','a=array.from(Mode.direct,1)\nplot(1)','ARRAY_ELEMENT_TYPE','LANGUAGE_INVALID'),
            ('input-foreign','enum Other\n    direct\nm=input.enum(Mode.direct,options=[Other.direct])\nplot(1)','ENUM_INPUT_TYPE','LANGUAGE_INVALID'),
            ('input-default-options','m=input.enum(Mode.direct,options=[Mode.doubled])\nplot(1)','ENUM_INPUT_DEFINITION','LANGUAGE_INVALID'),
            ('empty-pop','a=array.new<Mode>()\nm=a.pop()\nplot(1)','ARRAY_EMPTY','RUNTIME_ERROR'),
            ('missing-array','array<Mode> a=na\nplot(a.size())','ARRAY_NA','RUNTIME_ERROR')]
        for name,body,code,category in negatives:add(v,'reject-'+name,HEAD+body,code=code,category=category)
        add(v,'reject-duplicate','enum T\n    one\n    one\nplot(1)',code='ENUM_DUPLICATE_MEMBER')
        add(v,'reject-duplicate-title','enum T\n    one="same"\n    two="same"\nplot(1)',code='ENUM_DUPLICATE_TITLE')
        add(v,'reject-dynamic-title','enum T\n    one=str.tostring(close)\nplot(1)',code='ENUM_TITLE',category='PROFILE_UNCOVERED')
        if v==6:add(v,'reject-namespace','enum ta\n    value\nplot(1)',code='ENUM_NAME')
        else:add(v,'namespace-shadow','enum ta\n    value\nplot((ta.value==ta.value ? 1 : 0)+bar_index*0)',[[1]*4])
    files['bars.json']=(ROOT/'conformance/functions/fixed.json').read_bytes()
    files['cases.json']=canonical({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','basis':'Close 10,20,30,40; nominal members, alternating parity, literal titles and enum scalar array structure. No runtime-derived expectations.','external_pine_execution':False},
        'rule_refs':[],'assertion_refs':[],'cases':cases})+b'\n'
    return files

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args(argv)
    files={'conformance/enums/'+n:b for n,b in fixtures().items()}
    files.update({'examples/corpus/'+n:b for n,b in corpus_sources().items()})
    for name,raw in files.items():
        path=ROOT/name
        if args.check:assert path.read_bytes()==raw,name
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Owned enum fixtures and independent expectations verified.')

if __name__=='__main__':main()
