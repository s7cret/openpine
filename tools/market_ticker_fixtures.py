"""Versioned fixed ticker programs and independent expected output."""
import argparse

from packages.contracts.wire import canonical
from tools.specification import ROOT
from tools.prepared_requests_spec import datasets, context
from dataclasses import asdict, replace

DIRECTORY = 'conformance/market-tickers'
CASES = DIRECTORY + '/cases.json'
INDICATOR = 'examples/market-tickers/compare.pine'
STRATEGY = 'examples/market-tickers/children.pine'
CHILD_SUMS = (401.75, 414.234375)


def indicator(version):
    new = 'ticker.new' if version >= 5 else 'tickerid'
    request = 'request.security' if version >= 5 else 'security'
    decl = 'indicator' if version >= 5 else 'study'
    source = f'//@version={version}\n{decl}("Market identifiers")\nmarket = {new}("TEST","B")\n'
    source += f'plot({request}("TEST:B","1",close),"Literal")\nplot({request}(market,"1",close),"Market")\n'
    if version >= 5:
        source += ('ha = ticker.heikinashi(market)\n'
                   'plot(request.security(ticker.standard(ha),"1",close),"Standard")\n'
                   'plot(request.security(ticker.inherit(ticker.modify(ha),market),"2",open),"Higher HA open")\n')
    return source


STRATEGY_SOURCE = '''//@version=6
strategy("Child market signal", default_qty_type=strategy.fixed, initial_capital=1000)
market = ticker.new("TEST","B")
ha = ticker.heikinashi(market)
[opens, closes] = request.security_lower_tf(ha,"1",[open,close])
signal = array.sum(closes) > array.sum(opens)
if bar_index == 0 and signal
    strategy.entry("Child",strategy.long,qty=1)
plot(array.sum(opens),"Child HA opens")
'''


def fixtures():
    data = datasets()
    data['b4'] = replace(data['lower-parent'], context_id='b4', symbol='TEST:B')
    files = {INDICATOR: indicator(6).encode(), STRATEGY: STRATEGY_SOURCE.encode()}
    def save(name, value):
        files[f'{DIRECTORY}/{name}.json'] = canonical(value) + b'\n'
    for name in ('b1','b2','b4'):
        save(name, asdict(data[name]))
    for main, child in (('b1','b2'),('b4','b1')):
        save(main+'-contexts', dict(schema_version='openpine.context-inputs.1',primary_context=context(data[main]),
                                    contexts=[dict(context=context(data[child]),bars=child+'.json')]))
    cases = []
    for version in range(1,7):
        name=f'v{version}-market'
        files[f'{DIRECTORY}/{name}.pine']=indicator(version).encode()
        expected=dict(status='rejected',category='SPEC_UNRESOLVED',code='SIGNATURE_UNAVAILABLE')
        if version >= 2:
            rows=[dict(title=title,type='float',values=[float(n) for n in range(100,108)]) for title in
                  (('Literal','Market','Standard') if version>=5 else ('Literal','Market'))]
            if version>=5:
                rows.append(dict(title='Higher HA open',type='float',values=[None,100.,100.,100.,100.,150.,150.,225.]))
            expected=dict(status='executed',title='Market identifiers',shorttitle='',overlay=False,
                          observation_count=8,qualifiers=['series']*len(rows),outputs=rows)
        cases.append(dict(case_id=name,language_version=version,source=name+'.pine',bars='b1.json',
                          context_inputs='b1-contexts.json',expected=expected))
    for version in range(3, 7):
        name = f'v{version}-session-capability'
        fn = 'ticker.new' if version >= 5 else 'tickerid'
        decl = 'indicator' if version >= 5 else 'study'
        files[f'{DIRECTORY}/{name}.pine'] = (
            f'//@version={version}\n{decl}("Session capability")\ntarget = {fn}("TEST","B",session="regular")\nplot(close)\n'
        ).encode()
        cases.append(dict(case_id=name, language_version=version, source=name+'.pine', bars='b1.json',
                          expected=dict(status='rejected', category='PROFILE_UNCOVERED', code='TICKER_DATA_CAPABILITY')))
    for version in (5, 6):
        name = f'v{version}-child-arrays'
        files[f'{DIRECTORY}/{name}.pine'] = (
            f'//@version={version}\nindicator("Child arrays")\nha=ticker.heikinashi(ticker.new("TEST","B"))\n'
            '[opens,closes]=request.security_lower_tf(ha,"1",[open,close])\n'
            'plot(array.sum(opens),"Opens")\nplot(array.sum(closes),"Closes")\n'
        ).encode()
        cases.append(dict(case_id=name, language_version=version, source=name+'.pine', bars='b4.json',
                          context_inputs='b4-contexts.json',
                          expected=dict(status='executed', title='Child arrays', shorttitle='', overlay=False,
                                        observation_count=2, qualifiers=['series']*2, outputs=[
                                            dict(title='Opens',type='float',values=list(CHILD_SUMS)),
                                            dict(title='Closes',type='float',values=[406.,422.])])))
    save('cases',dict(schema_version='openpine.conformance.cases.1',implementation_profile_id='openpine.historical-bars.1',
                      rule_refs=[],assertion_refs=[],cases=cases,oracle=dict(kind='independent_manual_expectations',
                      external_pine_execution=False,basis='Market closes 100..107, recursive higher opens 100,100,150,225.')))
    return files


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--check',action='store_true')
    check=parser.parse_args().check
    for name,raw in fixtures().items():
        path=ROOT/name
        if check:
            assert path.read_bytes()==raw,name
        else:
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(raw)


if __name__=='__main__':
    main()
