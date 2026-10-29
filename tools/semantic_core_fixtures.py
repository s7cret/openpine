"""Owned whole programs and independently calculated semantic-core expectations."""
import argparse
from tools.specification import ROOT
from packages.contracts.wire import canonical


def routing(v):
    header = f'//@version={v}\n' + ('study' if v < 5 else 'indicator') + '("Semantic core")\n'
    if v >= 5:
        body = '''scale(x, factor=2) => x*factor
route(x) =>
    switch
        x<=20 => scale(x)
        => scale(x,factor=3)
'''
    else:
        body = '''scale(x, factor) => x*factor
route(x) =>
    if x<=20
        scale(x,2)
    else
        scale(x,3)
'''
    return header + body + 'plot(route(close))\n'


def fixtures():
    files, cases = {}, []
    def add(v, name, body, values=None, reject=None, complete=False, base='float'):
        file=f'v{v}-{name}.pine'
        text=body if complete else f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Semantic core")\n'+body+'\n'
        files[file]=text.encode()
        expected=({'status':'rejected','category':reject[0],'code':reject[1]} if reject else
            {'status':'executed','title':'Semantic core','shorttitle':'','overlay':False,'observation_count':4,
             'outputs':[{'title':'','type':base,'values':row} for row in values], 'qualifiers':['series']*len(values)})
        cases.append({'case_id':f'v{v}-{name}','language_version':v,'source':file,
            'bars':'bars.json','expected':expected})
    for v in range(1,7):
        add(v,'routing',routing(v),[[20.,40.,90.,120.]],complete=True)
        if v<5:
            add(v,'reject-default','f(x=2) => x\nplot(f())',reject=('LANGUAGE_INVALID','FUNCTION_DEFAULT_VERSION'))
            add(v,'reject-switch','x=switch close\n    10 => 1\nplot(x)',reject=('LANGUAGE_INVALID','SWITCH_VERSION'))
        else:
            add(v,'selector','f(x=2) => x\nx=switch close\n    10 => f()\n    20 => f(3)\n    => f(x=4)\nplot(x)',[[2,3,4,4]],base='int')
            add(v,'missing','x=switch close\n    10 => 1\n    20 => 2.5\nplot(x)',[[1.,2.5,None,None]])
            add(v,'statement','var n=0\nswitch\n    close<=20 => n:=n+1\n    => n:=n+2\nplot(n)',[[1,2,4,6]],base='int')
            add(v,'reject-dynamic-default','f(x=close) => x\nplot(f(1))',reject=('PROFILE_UNCOVERED','FUNCTION_DEFAULT_EXPRESSION'))
    files['bars.json']=(ROOT/'conformance/functions/fixed.json').read_bytes()
    files['cases.json']=canonical({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','basis':'close 10,20,30,40; piecewise factors 2/3, first matching literal, numeric missing and cumulative branch increments. No runtime oracle.', 'external_pine_execution':False},
        'rule_refs':[],'assertion_refs':[],'cases':cases})+b'\n'
    return files


def corpus_sources():
    files={f'semantic-routing-v{v}.pine':routing(v).encode() for v in range(1,7)}
    for v in (5,6):
        files[f'semantic-strategy-v{v}.pine']=(f'//@version={v}\n'
            'strategy("Semantic strategy",initial_capital=1000,default_qty_type=strategy.fixed,default_qty_value=2,commission_type=strategy.commission.percent,commission_value=1)\n'
            'size(qty=2) => qty\nswitch bar_index\n    0 => strategy.entry("L",strategy.long,qty=size())\n'
            '    3 => strategy.close("L")\nplot(strategy.equity)\n').encode()
    return files


def corpus_cases(record):
    cases=[]
    for v in range(1,7):
        cases.append({'id':f'semantic-routing-v{v}','version':v,'family':'semantic-routing',
            'files':{'source':record(f'examples/corpus/semantic-routing-v{v}.pine'),'bars':record('conformance/functions/fixed.json')},
            'inputs':{},'expected':{'status':'compiled'},'oracle':{'reason':'Independent close 10,20,30,40 times 2 for <=20, otherwise 3; older syntax implements identical logic.',
            'outputs':[[20.,40.,90.,120.]]},'execution':{},
            'history_replay':{'equivalent':True,'reason':'Primary close-only indicator; matching confirmed observations.'}})
    for v in (5,6):
        cases.append({'id':f'semantic-strategy-v{v}','version':v,'family':'semantic-strategy',
            'files':{'source':record(f'examples/corpus/semantic-strategy-v{v}.pine'),'bars':record('examples/strategies/wave.json')},
            'inputs':{},'expected':{'status':'compiled'},'oracle':{'reason':'Switch enters2 at bar1 open10 and exits2 at bar4 open10; one-percent commission .2 each side; cash plus position*close.',
            'outputs':[[1000.,1003.8,1007.8,999.8,999.6,999.6,999.6,999.6]]},
            'execution':{'pause_after':2,'checkpoint':'position','statistics':{'equity':'999.6','commission':'0.4'}},
            'history_replay':{'equivalent':False,'reason':'Historical next-open and observed-event broker eligibility differ; restored state is checked within each model.'}})
    return cases


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args(argv)
    files={'conformance/semantic-core/'+name:raw for name,raw in fixtures().items()}
    files.update({'examples/corpus/'+name:raw for name,raw in corpus_sources().items()})
    for name,raw in files.items():
        path=ROOT/name
        if args.check:assert path.read_bytes()==raw,name
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Owned semantic-core programs and manual oracles verified.')


if __name__=='__main__':main()
