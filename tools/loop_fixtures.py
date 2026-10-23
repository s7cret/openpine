"""Author fixed loop fixtures from arithmetic expectations, without running Pine."""

import argparse
from tools.specification import ROOT, encode, read_json, sha


def source(version, body, title='loops'):
    return f'//@version={version}\n' + ('study' if version<5 else 'indicator') + f'("{title}")\n{body}\n'


def prepare():
    files = {}
    cases = []
    def case(version, name, body, outputs=None, *, code=None, category='LANGUAGE_INVALID'):
        name = f'v{version}-{name}.pine'
        files['conformance/loops/'+name] = source(version, body).encode()
        if code:expected = {'status':'rejected', 'category':category, 'code':code}
        else:
            expected = {'status':'executed', 'title':'loops', 'shorttitle':'', 'overlay':False,
                'observation_count':4, 'qualifiers':['series']*len(outputs),
                'outputs':[{'title':'', 'type':typ, 'values':values} for typ,values in outputs]}
        cases.append({'case_id':name.removesuffix('.pine'), 'language_version':version,
            'source':name, 'bars':'bars.json', 'expected':expected})
    files['conformance/loops/bars.json'] = encode({'schema_version':'openpine.historical-bars.1',
        'context_id':'loop.corpus', 'symbol':'TEST:LOOPS', 'timeframe':'1',
        'bars':[{'time':i*60000,'close':float(10*(i+1))} for i in range(4)]})
    for v in range(1,7):
        case(v,'baseline','plot(close)',[('float',[10.,20.,30.,40.])])
        for action in ('break','continue'):
            case(v,action+'-outside',action+'\nplot(1)',code='LOOP_CONTROL_SCOPE')
        if v<5:case(v,'while-unavailable','while false\n    x = 1\nplot(1)',code='LOOP_VERSION')
        if v==1:
            case(v,'for-unavailable','for i = 0 to 1\n    x = i\nplot(1)',code='LOOP_VERSION')
            continue
        case(v,'bounds','''sum = 0
for i = 5 to 0 by 2
    sum := sum * 10 + i
plot(sum)''',[('int',[531]*4)])
        case(v,'dynamic','''end = 4
sum = 0
for i = 0 to end
    end := 1
    sum := sum + 1
plot(sum)''',[('int',[2 if v==6 else 5]*4)])
        case(v,'control-value','''value = for i = 1 to 6
    if i == 2
        continue
    if i == 4
        break
    i * 10
plot(value)''',[('int',[30]*4)])
        case(v,'history','''sum = 0.0
for i = 0 to 2
    sum := sum + nz(close[i])
plot(sum)''',[('float',[10.,30.,60.,90.])])
        case(v,'var','''value = for i = 0 to 2
    var n = 0
    n := n + 1
    n
plot(value)''',[('int',[3,6,9,12])])
        case(v,'udf','''sum_to(n) =>
    sum = 0
    for i = 1 to n
        sum := sum + i
plot(sum_to(4))''',[('int',[10]*4)])
        case(v,'tuple','''[a,b] = for i = 1 to 3
    [close+i,i*2]
plot(a)
plot(b)''',[('float',[13.,23.,33.,43.]),('int',[6]*4)])
        case(v,'missing','''value = for i = na to 3
    i
plot(value)''',[('int',[None]*4)])
        prefix='ta.' if v>=5 else ''
        case(v,'ta',f'''value = for i = 0 to 2
    {prefix}sma(close+i,2)
plot(value)''',[('float',[11.5,21.5,31.5,41.5])])
        case(v,'counter-write','for i = 0 to 2\n    i := 5\nplot(1)',code='LOOP_COUNTER_WRITE',category='PROFILE_UNCOVERED')
        case(v,'bound-type','for i = 0.5 to 2\n    x = i\nplot(1)',code='LOOP_BOUND_TYPE')
        case(v,'scope','for i = 0 to 2\n    x = i\nplot(x)',code='UNDEFINED_VARIABLE')
        case(v,'string-value','value = for i = 0 to 2\n    "x"\nplot(1)',code='LOOP_RESULT_TYPE',category='PROFILE_UNCOVERED')
        if v>=5:
            case(v,'while','''i = 0
sum = 0
while i < 5
    i := i + 1
    if i == 2
        continue
    if i == 4
        break
    sum := sum + i
plot(sum)''',[('int',[4]*4)])
            case(v,'while-missing','value = while false\n    close\nplot(value)',[('float',[None]*4)])
    files['conformance/loops/cases.json'] = encode({'schema_version':'openpine.conformance.cases.1',
        'implementation_profile_id':'openpine.historical-bars.1', 'rule_refs':[], 'assertion_refs':[],
        'oracle':{'kind':'independent_manual_expectations', 'external_pine_execution':False,
            'basis':'Fixed integer arithmetic and manually enumerated histories/iteration samples; no OpenPine output used as oracle.'},
        'cases':cases})
    base = ROOT/'examples/composite'
    for name in ('bars.json','higher.json','contexts.json'):files['examples/loops/'+name]=(base/name).read_bytes()
    for v in range(2,7):
        for family in (('history-mean','while-mean') if v>=5 else ('history-mean',)):
            api='request.security' if v>=5 else 'security'
            length='input.int(6,title="Window",minval=1,maxval=40)' if v>=5 else 'input(6,title="Window",minval=1,maxval=40)'
            body = ('    for i = 0 to n - 1\n        if i > bar_index\n            break\n'
                '        if na(src[i])\n            continue\n        total := total + src[i]\n        count := count + 1')
            if family=='while-mean':
                body = ('    i = 0\n    while i < n\n        offset = i\n        i := i + 1\n'
                    '        if offset > bar_index\n            break\n        if na(src[offset])\n            continue\n'
                    '        total := total + src[offset]\n        count := count + 1')
            text = f'''//@version={v}
strategy("{family} v{v}",default_qty_type=strategy.fixed,default_qty_value=1,initial_capital=1000,commission_value=0.1)
// Bounded artifact-defined model; no external equivalence claim.
length={length}
mean(src,n) =>
    total = 0.0
    count = 0
{body}
        total / count
basis = mean(close,length)
higher = {api}("TEST:TECHNICAL","4",close)
if strategy.position_size == 0 and close > basis and close >= higher
    strategy.entry("L",strategy.long)
if strategy.position_size > 0 and (close < basis or bar_index % 16 == 15)
    strategy.close("L")
plot(basis,title="Loop mean")
plot(higher,title="Higher close")
plot(strategy.equity,title="Equity")
'''
            files[f'examples/loops/{family}-v{v}.pine']=text.encode()
    for name,original in (('experiment.json','macd-experiment.json'),('search.json','bb-search.json'),('walkforward.json','macd-walkforward.json')):
        data=read_json(base/original);data['name']='Loop mean '+name.removesuffix('.json');data['inputs']={}
        data['grid' if name=='experiment.json' else 'space']={'length':[4,8]}
        files['examples/loops/'+name]=encode(data)
    files['examples/loops/fixtures.json']=encode({'schema_version':'openpine.loop-fixtures.1',
        'meaning':'For in v2-v6, while in v5/v6. V1 has explicit rejection fixtures; external comparison is a separate stage.',
        'files':{k.removeprefix('examples/loops/'):sha(v) for k,v in files.items() if k.startswith('examples/loops/')}})
    return files


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args()
    for name,raw in prepare().items():
        path=ROOT/name
        if args.check:
            if not path.is_file() or path.read_bytes()!=raw:raise ValueError('Reviewed loop fixture differs: '+name)
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Independent loop fixtures verified.')


if __name__=='__main__':main()
