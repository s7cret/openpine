"""Independently expected results for str.format and its replay consumer."""

import argparse
import json

from packages.contracts.contexts import MarketContext
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical
from tools.specification import ROOT
from tools.tick_price_fixtures import bars, instrument, market_input


DIRECTORY = 'conformance/string-format'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/string-format/price-alert.pine'
TITLE = 'String format'
PRIMARY = MarketContext('format-primary', 'TEST:FORMAT', '1', 'recorded.1')
ENVIRONMENTS = {PRIMARY.context_id: {'instrument': instrument('0.25'), 'history_last_ms': None}}
CLOSES = (1.25, 2.5, 3.75)
PREVIEW = 'Цена 100.75 | риск 13%'
ALERT = (5, 'Цена 101.00 | риск 13%')


def tape():
    rows = (('open', 0, 0, 0, 100., 100.),
            ('close', 0, 60_000, 1, 100., 100.),
            ('open', 60_000, 60_000, 0, 100., 100.),
            ('update', 60_000, 80_000, 1, 100., 100.625),
            ('update', 60_000, 100_000, 2, 100., 100.625),
            ('close', 60_000, 120_000, 3, 100., 100.875),
            ('open', 120_000, 120_000, 0, 102., 102.),
            ('close', 120_000, 180_000, 1, 102., 102.))
    return tuple(MarketEvent(seq, PRIMARY.context_id, kind, time, at, revision, opening, close, opening, close, 1.)
                 for seq, (kind, time, at, revision, opening, close) in enumerate(rows))


def example_input():
    candles = tuple((event.open, event.high, event.low, event.close) for event in tape() if event.kind == 'close')
    return market_input(bars(PRIMARY, candles), '0.25')


def source(version, body):
    header = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{header}("{TITLE}")\n{body}\n'


def fixtures():
    files, cases = {}, []

    def add(version, name, body, expected):
        identifier = f'v{version}-{name}'
        path = identifier + '.pine'
        files[path] = source(version, body).encode()
        cases.append(dict(case_id=identifier, language_version=version, source=path, bars='bars.json', expected=expected))

    def plot(call, expected):
        return f'plot({call} == {json.dumps(expected, ensure_ascii=False)} ? 1.0 : 0.0)'

    def executed(*values, qualifier='simple'):
        outputs = [dict(title='', type='float', values=value) for value in values]
        return dict(status='executed', title=TITLE, shorttitle='', overlay=False,
                    observation_count=len(CLOSES), qualifiers=[qualifier] * len(outputs), outputs=outputs)

    def rejected(code, category='LANGUAGE_INVALID'):
        return dict(status='rejected', category=category, code=code)

    for version in (1, 2, 3):
        add(version, 'unavailable', plot('str.format("{0}",1)', '1'), rejected('SIGNATURE_UNAVAILABLE'))

    yes = (1.,) * len(CLOSES)
    for version in (4, 5, 6):
        add(version, 'substitutions', '\n'.join((
            plot('str.format("{1} | {0} | {1} | \'{2}\' | \'\' | {9}", true, "тест")',
                 "тест | true | тест | {2} | ' | {9}"),
            plot('str.format("literal {0}")', 'literal {0}'),
            plot('str.format("{0}", na)', 'NaN'),
        )), executed(yes, yes, yes))
        add(version, 'number-formats', '\n'.join((
            plot('str.format("{0} | {1,number,integer}", 1234.56789, 1234.6)', '1,234.568 | 1,235'),
            plot('str.format("{0,number,currency} | {1,number,percent}", -1250.5, -0.125)', '-$1,250.50 | -13%'),
            plot('str.format("{0,number,0.00} | {1,number,000.0#}", 2.5, 7.125)', '2.50 | 007.13'),
            plot('''str.format("{0,number,'$',###.0000}", 1234.56)''', '$1,234.5600'),
        )), executed(yes, yes, yes, yes))
        dynamic = 'fmt = bar_index == 0 ? "C {0,number,0.00}" : "O {0,number,0.00}"\n'
        add(version, 'series-template', dynamic + plot('str.format(fmt, close)', 'O 2.50'),
            executed((0., 1., 0.), qualifier='series'))
        add(version, 'unbalanced', plot('str.format("price {0", close)', ''),
            rejected('STRING_FORMAT', 'RUNTIME_ERROR'))
        add(version, 'wrong-modifier-type', plot('str.format("{0,number,0.00}", true)', ''),
            rejected('STRING_FORMAT', 'RUNTIME_ERROR'))
        add(version, 'date-modifier', plot('str.format("{0,date}", 0)', ''),
            rejected('STRING_FORMAT', 'RUNTIME_ERROR'))
        add(version, 'wrong-template-type', plot('str.format(1, 2)', ''), rejected('ARGUMENT_TYPE'))
        add(version, 'arity', plot('str.format("{0}",' + ','.join('1' for _ in range(33)) + ')', ''),
            rejected('STRING_FORMAT_ARITY', 'PROFILE_UNCOVERED'))

    candles = tuple((value,) * 4 for value in CLOSES)
    files['bars.json'] = canonical(bars(PRIMARY, candles)) + b'\n'
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Documented positional replacement and number formats with hand-computed examples.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    directory = ROOT / DIRECTORY
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, data in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == data, name
        else:
            path.write_bytes(data)
    print('String format fixtures verified.')


if __name__ == '__main__':
    main()
