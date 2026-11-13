"""Small independent expectations for Pine volatility channels."""

import argparse
from fractions import Fraction as F

from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical
from tools.specification import ROOT

DIRECTORY = 'conformance/channel-indicators'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/channel-indicators/volatility-squeeze.pine'


def source(version, body):
    declaration = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{declaration}("Channels")\n{body}\n'


def squeeze_tape(context_id):
    for index, close in enumerate((10., 10., 30., 30., 30.)):
        time = index * 60_000
        for revision, (kind, price) in enumerate((('open', close), ('update', close + 1), ('close', close))):
            yield MarketEvent(index * 3 + revision, context_id, kind, time, time + revision * 30_000,
                              revision, close, close + (revision > 0), close - (revision > 0), price, float(revision))


def fixtures():
    files = {}
    cases = []

    def add(v, name, body, expected):
        case_id = f'v{v}-{name}'
        path = case_id + '.pine'
        files[path] = source(v, body).encode()
        cases.append(dict(case_id=case_id, language_version=v, source=path, bars='bars.json', expected=expected))

    def executed(*outputs):
        return dict(status='executed', title='Channels', shorttitle='', overlay=False, observation_count=4,
                    qualifiers=['series'] * len(outputs), outputs=[
                        dict(title='', type='float', values=[None if x is None else float(x) for x in values])
                        for values in outputs])

    def rejected(code):
        return dict(status='rejected', category='LANGUAGE_INVALID', code=code)

    for v in range(1, 7):
        prefix = 'ta.' if v >= 5 else ''
        add(v, 'fixed-bollinger', f'[m,u,l] = {prefix}bb(close,2,1)\nplot(m)\nplot(u)\nplot(l)',
            executed([None, 15, 25, 35], [None, 20, 30, 40], [None, 10, 20, 30]))
        if v < 4:
            for name in ('bbw', 'kc', 'kcw'):
                code = f'{name}(close,2,1)'
                body = f'[m,u,l] = {code}\nplot(m)' if name == 'kc' else f'plot({code})'
                add(v, 'version-' + name, body, rejected('SIGNATURE_UNAVAILABLE'))
            add(v, 'series-bb', 'n = bar_index % 2 + 1\n[m,u,l] = bb(close,n,1)\nplot(m)',
                rejected('ARGUMENT_QUALIFIER'))
            continue
        add(v, 'changing-bollinger',
            f'n = bar_index % 2 + 1\n[m,u,l] = {prefix}bb(mult=1,length=n,series=close)\n'
            f'plot(m)\nplot(u)\nplot(l)\nplot({prefix}bbw(close,n,1))\n'
            f'plot({prefix}bbw(close - (bar_index + 1) * 10,n,1))',
            executed([10, 15, 30, 35], [10, 20, 30, 40], [10, 10, 30, 30],
                     [0, F(2, 3), 0, F(2, 7)], [None] * 4))
        basis = [None, 15, 25, 35] if v == 4 else [10, F(50, 3), F(230, 9), F(950, 27)]
        raw_width = [None, F(8, 15), F(8, 25), F(8, 35)] if v == 4 else [F(8, x) for x in basis]
        upper = [None if i < (2 if v == 4 else 1) else b + 12 for i, b in enumerate(basis)]
        width = [None if u is None else F(24, b) for u, b in zip(upper, basis, strict=True)]
        # The documented EMA runs in float. Compare nine decimals against an
        # independent rational calculation while keeping the Pine inputs exact.
        scale = 1_000_000_000
        values = ('m', 'u', 'eu', f'{prefix}kcw(close,2,1,true)',
                  f'{prefix}kcw(close,2,1)', f'{prefix}kcw(close,2,1,false)')
        plots = '\n'.join(f'plot(round(({x}) * {scale}) / {scale}.0)' if v == 4 else
                          f'plot(math.round(({x}) * {scale}) / {scale}.0)' for x in values)
        expected = [[None if x is None else round(x * scale) / scale for x in values]
                    for values in (basis, upper, upper, width, width, raw_width)]
        add(v, 'keltner-ranges',
            f'[m,u,l] = {prefix}kc(close,2,1)\n'
            f'[explicit,eu,el] = {prefix}kc(useTrueRange=true,mult=1,series=close,length=2)\n' + plots,
            executed(*expected))
        qualifier_error = 'NO_MATCHING_OVERLOAD' if v == 5 else 'ARGUMENT_QUALIFIER'
        add(v, 'keltner-series-length', f'plot({prefix}kcw(close,bar_index + 1,1))',
            rejected(qualifier_error))
        add(v, 'keltner-series-flag', f'plot({prefix}kcw(close,2,1,close > 0))',
            rejected(qualifier_error))
    files['bars.json'] = (ROOT / 'conformance/lines/bars.json').read_bytes()
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Analytic values for equal steps, constant high-low and true range, plus version gates.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    directory = ROOT / DIRECTORY
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, raw in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == raw, name
        else:
            path.write_bytes(raw)
    print('Channel indicator fixtures verified.')


if __name__ == '__main__':
    main()
