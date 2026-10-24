"""Author array cases from fixed arithmetic expectations, without running Pine."""

import argparse
from tools.specification import ROOT, encode, read_json, sha


def source(version, body):
    return f'//@version={version}\n'+('study' if version<5 else 'indicator')+'("arrays")\n'+body+'\n'


def prepare():
    files, cases = {}, []
    def case(version,name,body,outputs=None,code=None,category='LANGUAGE_INVALID'):
        name=f'v{version}-{name}.pine';files['conformance/arrays/'+name]=source(version,body).encode()
        expected={'status':'rejected','category':category,'code':code} if code else {
            'status':'executed','title':'arrays','shorttitle':'','overlay':False,'observation_count':4,
            'qualifiers':['series']*len(outputs),'outputs':[{'title':'','type':typ,'values':values} for typ,values in outputs]}
        cases.append({'case_id':name[:-5],'language_version':version,'source':name,'bars':'bars.json','expected':expected})
    files['conformance/arrays/bars.json']=encode({'schema_version':'openpine.historical-bars.1',
        'context_id':'arrays.corpus','symbol':'TEST:ARRAYS','timeframe':'1',
        'bars':[{'time':i*60000,'close':float((i+1)*10)} for i in range(4)]})
    for v in range(1,7):
        case(v,'baseline','plot(close)',[('float',[10.,20.,30.,40.])])
        if v<4:
            for label,body in [('constructor','a = array.new_int(1)'),('annotation','int[] a = na'),
                    ('unused-function','unused() =>\n    array.new_int(1)')]:
                case(v,label,body+'\nplot(1)',code='ARRAY_VERSION')
            continue
        case(v,'alias-copy','''var a = array.new_int(1, 0)
b = a
array.set(b, 0, array.get(a, 0) + 1)
c = array.copy(a)
array.set(c, 0, 99)
plot(array.get(a, 0))
plot(array.get(c, 0))''',[('int',[1,2,3,4]),('int',[99]*4)])
        case(v,'queue-stack-clear','''a = array.from(2, 3)
array.unshift(a, 1)
array.push(a, 4)
first = array.shift(a)
last = array.pop(a)
plot(first * 100 + last * 10 + array.sum(a))
array.clear(a)
plot(array.size(a))''',[('int',[145]*4),('int',[0]*4)])
        case(v,'aggregates','''a = array.from(2, na, 4)
plot(array.sum(a))
plot(array.avg(a))
plot(array.min(a))
plot(array.max(a))
b = array.new_float(0)
plot(array.sum(b))''',[('int',[6]*4),('float',[3.]*4),('int',[2]*4),('int',[4]*4),('float',[None]*4)])
        case(v,'fresh-history','''a = array.from(close)
p = a[1]
plot(na(p) ? na : array.get(p, 0))''',[('float',[None,10.,20.,30.])])
        case(v,'persistent-history','''var a = array.new_float(1)
array.set(a, 0, close)
p = a[1]
plot(na(p) ? na : array.get(p, 0))''',[('float',[None,20.,30.,40.])])
        case(v,'primitive-elements','''a = array.new_bool(1)
b = array.new_string(1, "hello")
plot(array.get(a, 0) == false ? 1 : 0)
plot(array.get(b, 0) == "hello" ? 1 : 0)''',[('int',[1 if v==6 else 0]*4),('int',[1]*4)])
        annotation='float[]' if v==4 else 'array<float>'
        case(v,'udf',f'''var a = array.new_float(0)
append({annotation} values) =>
    array.push(values, close)
    values
b = append(a)
plot(array.size(b))
plot(array.avg(a))''',[('int',[1,2,3,4]),('float',[10.,15.,20.,25.])])
        case(v,'conditional-reference',f'''{annotation} a = na
if close > 20
    a := array.from(close)
plot(na(a) ? 0.0 : array.get(a, 0))''',[('float',[0.,0.,30.,40.])])
        case(v,'promote','a = array.from(1, 2.5)\nplot(array.get(a, 0))\nplot(array.sum(a))',[('float',[1.]*4),('float',[3.5]*4)])
        for label,body,code,category in [
            ('mixed','a = array.from(1, "x")','ARRAY_ELEMENT_TYPE','LANGUAGE_INVALID'),
            ('empty-from','a = array.from()','ARRAY_ARITY','LANGUAGE_INVALID'),
            ('nested','a = array.from(array.new_int(1))','ARRAY_ELEMENT_TYPE','LANGUAGE_INVALID'),
            ('wrong-write','a = array.from(1)\narray.push(a, 1.5)','ARRAY_ELEMENT_TYPE','LANGUAGE_INVALID'),
            ('bool-statistics','a = array.from(true)\nx = array.sum(a)','ARRAY_NUMERIC','LANGUAGE_INVALID'),
            ('uncovered-sort','a = array.from(1)\narray.sort(a)','ARRAY_OPERATION','PROFILE_UNCOVERED'),
            ('bad-index','a = array.from(1)\nx = array.get(a, 1)','ARRAY_INDEX','RUNTIME_ERROR'),
            ('empty-pop','a = array.new_int(0)\nx = array.pop(a)','ARRAY_EMPTY','RUNTIME_ERROR'),
            ('negative-size','a = array.new_int(-1)','ARRAY_SIZE','RUNTIME_ERROR'),
            ('too-large','a = array.new_int(100001)','ARRAY_STORAGE','RUNTIME_ERROR'),
            ('na-reference',f'{annotation} a = na\nx = array.size(a)','ARRAY_NA','RUNTIME_ERROR'),
            ('write-parameter','f(a) =>\n    a := array.from(1)\n    1\na = array.from(1)\nx = f(a)','FUNCTION_WRITE','LANGUAGE_INVALID')]:
            case(v,label,body+'\nplot(1)',code=code,category=category)
        negative='a = array.from(1, 2)\narray.set(a, -1, 7)\nplot(array.get(a, -2))\nplot(array.sum(a))'
        if v==6:case(v,'negative-index',negative,[('int',[1]*4),('int',[8]*4)])
        else:case(v,'negative-index',negative,code='ARRAY_INDEX',category='RUNTIME_ERROR')
        if v==4:
            case(v,'generic','a = array.new<int>()\nplot(1)',code='ARRAY_SYNTAX_VERSION')
            case(v,'for-in','a = array.new_int(0)\nfor x in a\n    y = x\nplot(1)',code='FOR_IN_VERSION')
            continue
        case(v,'generic','array<int> a = array.new<int>()\nplot(array.size(a))',[('int',[0]*4)])
        case(v,'for-in','''a = array.from(1, 2, 3, 4)
sum = 0
value = for [index, item] in a
    if item == 2
        continue
    if item == 4
        break
    sum := sum + index * item
    item * 10
plot(sum)
plot(value)''',[('int',[6]*4),('int',[30]*4)])
        case(v,'live-size','''a = array.from(1, 2)
sum = 0
for item in a
    sum := sum + item
    if array.size(a) < 4
        array.push(a, 3)
plot(sum)''',[('int',[9]*4)])
        case(v,'counter-write','a = array.from(1)\nfor item in a\n    item := 2\nplot(1)',code='LOOP_COUNTER_WRITE',category='PROFILE_UNCOVERED')
    files['conformance/arrays/cases.json']=encode({'schema_version':'openpine.conformance.cases.1',
        'implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','external_pine_execution':False,
            'basis':'Manually authored primitive-array/reference expectations; no runtime-generated expected values.'},
        'rule_refs':[],'assertion_refs':[],'cases':cases})
    base = ROOT/'examples/loops'
    for name in ('bars.json','higher.json','contexts.json'):
        files['examples/arrays/'+name]=(base/name).read_bytes()
    for version in (4,5,6):
        for family in ('queue-mean','weighted-mean'):
            api='request.security' if version>=5 else 'security'
            inp='input.int' if version>=5 else 'input'
            if family=='queue-mean':calculation='basis = array.avg(window)'
            else:
                header='for [index, value] in window' if version>=5 else 'for index = 0 to array.size(window) - 1'
                read='' if version>=5 else '    value = array.get(window, index)\n'
                calculation='total = 0.0\nweights = 0\n'+header+'\n'+read+'    total := total + value * (index + 1)\n    weights := weights + index + 1\nbasis = total / weights'
            text=f'''//@version={version}
strategy("Array {family}",default_qty_type=strategy.fixed,initial_capital=1000)
length = {inp}(6, title="Window", minval=1, maxval=1000)
var window = array.new_float(0)
alias = window
array.push(alias, close)
if array.size(window) > length
    array.shift(window)
{calculation}
higher = {api}("TEST:TECHNICAL", "4", close)
if strategy.position_size == 0 and close > basis and close >= higher
    strategy.entry("L", strategy.long)
if strategy.position_size > 0 and (close < basis or bar_index % 16 == 15)
    strategy.close("L")
plot(basis, title="Array filter")
plot(higher, title="Higher close")
plot(strategy.equity, title="Equity")
'''
            files[f'examples/arrays/{family}-v{version}.pine']=text.encode()
    for name in ('experiment.json','search.json','walkforward.json'):
        data=read_json(base/name);data['name']='Array '+name.removesuffix('.json')
        files['examples/arrays/'+name]=encode(data)
    files['examples/arrays/fixtures.json']=encode({'schema_version':'openpine.array-fixtures.1',
        'meaning':'Primitive arrays v4-v6, for-in v5/v6; external execution verification is a separate stage.',
        'files':{name.removeprefix('examples/arrays/'):sha(raw) for name,raw in files.items() if name.startswith('examples/arrays/')}})
    return files


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args()
    files=prepare()
    for name,raw in files.items():
        path=ROOT/name
        if args.check:
            if not path.exists() or path.read_bytes()!=raw:raise ValueError('Array fixture differs: '+name)
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Independent array fixtures verified.')


if __name__=='__main__':main()
