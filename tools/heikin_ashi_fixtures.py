"""Generate historical Heikin Ashi cases with hand-calculated expectations."""
import argparse
from dataclasses import asdict

from packages.contracts.contexts import MarketContext
from packages.contracts.wire import canonical
from tools.specification import ROOT

DIRECTORY = 'conformance/heikin-ashi'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/heikin-ashi/compare.pine'
STRATEGY = 'examples/heikin-ashi/entry.pine'
TITLE = 'Compare market and Heikin Ashi candles'
NAMES = ('Market close', 'HA open', 'HA high', 'HA low', 'HA close', 'HA volume')
VALUES = ((12., 14., 24., 8., 7., 18.), (11., 11.5, 13., 17.5, 12.25, 9.125),
          (18., 18., 25., 17.5, 12.25, 21.), (8., 10., 13., 5., 4., 9.125),
          (12., 14.5, 22., 7., 6., 17.), (1., 2., 3., 4., 5., 6.))


def source(version, *, higher=False):
    declaration = 'study' if version < 5 else 'indicator'
    symbol = 'tickerid' if version < 4 else 'syminfo.tickerid'
    period = 'period' if version < 4 else 'timeframe.period'
    constructor = 'heikinashi' if version < 5 else 'ticker.heikinashi'
    security = 'security' if version < 5 else 'request.security'
    head = f'//@version={version}\n{declaration}("{TITLE}")\nha = {constructor}({symbol})\n'
    if higher:
        flag = 'true' if version < 3 else 'gaps=barmerge.gaps_on'
        return head + (f'plot({security}({symbol}, "2", close), "Market close")\n'
                       f'plot({security}(ha, "2", open), "HA open")\n'
                       f'plot({security}(ha, "2", close), "HA close")\n'
                       f'plot({security}(ha, "2", close, {flag}), "HA gaps")\n')
    return head + (f'plot({security}({symbol}, {period}, close), "Market close")\n'
                   f'plot({security}(ha, {period}, open), "HA open")\n'
                   f'plot({security}(ha, {period}, high), "HA high")\n'
                   f'plot({security}(ha, {period}, low), "HA low")\n'
                   f'plot({security}({constructor}({symbol}), {period}, close), "HA close")\n'
                   f'plot({security}(ha, {period}, volume), "HA volume")\n')


STRATEGY_BODY = '''//@version=6
strategy("Market fill from a Heikin Ashi signal", default_qty_type=strategy.fixed, initial_capital=1000)
ha = ticker.heikinashi(syminfo.tickerid)
haClose = request.security(ha, timeframe.period, close)
if bar_index == 0 and haClose > 11
    strategy.entry("Long", strategy.long, qty=1)
plot(haClose, "HA close")
'''


def dataset(context, interval, rows):
    bars = [dict(time=minute * 60_000, open=o, high=h, low=l, close=c, volume=v)
            for minute, o, h, l, c, v in rows]
    return dict(schema_version='openpine.historical-bars.2', context_id=context,
                timeframe=interval, symbol='TEST:HA', bars=bars)


def fixtures():
    market = dataset('market-minute', '1', ((0, 10, 18, 8, 12, 1), (1, 16, 18, 10, 14, 2),
        (2, 20, 25, 19, 24, 3), (3, 6, 9, 5, 8, 4), (4, 5, 8, 4, 7, 5), (5, 15, 21, 14, 18, 6)))
    higher = dataset('market-two-minute', '2', ((0, 10, 20, 8, 18, 3), (4, 28, 40, 24, 36, 5)))

    def context(data):
        return asdict(MarketContext(data['context_id'], data['symbol'], data['timeframe'], 'uploaded-file.1'))

    contexts = dict(schema_version='openpine.context-inputs.1', primary_context=context(market),
                    contexts=[{'context': context(higher), 'bars': 'higher.json'}])
    files = {EXAMPLE: source(6).encode(), STRATEGY: STRATEGY_BODY.encode()}
    files.update({f'{DIRECTORY}/{name}.json': canonical(row) + b'\n' for name, row in
                  (('bars', market), ('higher', higher), ('contexts', contexts))})
    cases = []
    for version in range(1, 7):
        for is_higher in ((False,) if version == 1 else (False, True)):
            label = 'higher' if is_higher else 'same'
            name = f'v{version}-{label}'
            files[f'{DIRECTORY}/{name}.pine'] = source(version, higher=is_higher).encode()
            case = dict(case_id=name, source=f'{name}.pine', bars='bars.json', language_version=version)
            if version == 1:
                case['expected'] = dict(status='rejected', category='LANGUAGE_INVALID', code='SIGNATURE_UNAVAILABLE')
            else:
                names, values = NAMES, VALUES
                if is_higher:
                    case['context_inputs'] = 'contexts.json'
                    names = ('Market close', 'HA open', 'HA close', 'HA gaps')
                    values = ((18., 18., 18., 18., 36., 36.), (14.,) * 6, (14., 14., 14., 14., 32., 32.),
                              (14., None, None, None, 32., None)) if version == 2 else (
                              (None, 18., 18., 18., 18., 36.), (None, 14., 14., 14., 14., 14.),
                              (None, 14., 14., 14., 14., 32.), (None, 14., None, None, None, 32.))
                case['expected'] = dict(status='executed', title=TITLE, shorttitle='', overlay=False,
                    observation_count=6, qualifiers=['series'] * len(names), outputs=[
                    dict(title=title, type='float', values=row) for title, row in zip(names, values, strict=True)])
            cases.append(case)
    files[CASES] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Manual HA recurrence, raw OHLCV alongside the transform and sparse two-minute visibility.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    for name, content in fixtures().items():
        path = ROOT / name
        if check:
            assert path.read_bytes() == content, name
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    print('Heikin Ashi fixtures verified.')


if __name__ == '__main__':
    main()
