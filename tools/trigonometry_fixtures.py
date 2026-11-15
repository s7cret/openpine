"""Known angles, versioned binding and a requested-context replay example."""

import argparse

from packages.contracts.contexts import MarketContext
from packages.contracts.wire import canonical
from tools.numeric_aggregates_fixtures import two_context_tape
from tools.specification import ROOT


DIRECTORY = 'conformance/trigonometry'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/trigonometry/price-angle.pine'
TITLE = 'Angle functions'
CLOSES = (1., 2., 3., 4.)
PRIMARY = MarketContext('angle-primary', 'TEST:ANGLES', '1', 'recorded.1')
HIGHER = MarketContext('angle-higher', PRIMARY.symbol, '2', PRIMARY.source)
ANGLE = 26.565051
HISTORICAL = (None, -ANGLE, -ANGLE, -ANGLE, -ANGLE, ANGLE, ANGLE, ANGLE)
REPLAY = (-ANGLE, -ANGLE, 0., -ANGLE, ANGLE, ANGLE, ANGLE, ANGLE)
ALERT = (22, 'Angle turned positive: 26.565051')


def source(version, body):
    header = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{header}("{TITLE}")\n{body}\n'


def tape():
    primary = ((20., 21., 19., 20.),) * len(REPLAY)
    higher = ((8., 8., 4., 4.), (8., 12., 4., 4.), (8., 12., 8., 12.), (8., 12., 8., 12.))
    return two_context_tape(PRIMARY, HIGHER, primary, higher, preview_higher_index=1)


def fixtures():
    files, cases = {}, []

    def add(version, name, body, expected):
        identifier = f'v{version}-{name}'
        path = identifier + '.pine'
        files[path] = source(version, body).encode()
        cases.append(dict(case_id=identifier, language_version=version, source=path, bars='bars.json', expected=expected))

    def executed(*values, qualifier='const'):
        outputs = [dict(title='', type='float', values=[value] * len(CLOSES)) for value in values]
        return dict(status='executed', title=TITLE, shorttitle='', overlay=False,
                    observation_count=len(CLOSES), qualifiers=[qualifier] * len(outputs), outputs=outputs)

    def rejected(code, category='LANGUAGE_INVALID'):
        return dict(status='rejected', category=category, code=code)

    def plot(expression):
        return f'plot({expression})'

    for version in range(1, 7):
        prefix = 'math.' if version >= 5 else ''

        def use(name, arg):
            return f'{prefix}{name}({arg})'

        direct = ('sin', 'cos', 'tan', 'asin', 'acos', 'atan')
        identity_args = ('0', '0', '0', '0', '1', '0')
        body = [plot(use(name, arg)) for name, arg in zip(direct, identity_args, strict=True)]
        body.append(plot(use('round', use('atan', '1') + ', 12')))
        add(version, 'identities', '\n'.join(body), executed(0., 1., 0., 0., 0., 0., 0.785398163397))
        add(version, 'domains', '\n'.join(plot(use(name, arg)) for name, arg in (
            ('acos', '2'), ('asin', '-2'), ('sin', 'na'), ('atan', 'na'))), executed(None, None, None, None))
        name = 'angle' if version >= 5 else 'x'
        named_call = plot(use('acos', f'{name}=1'))
        add(version, 'named', named_call, executed(0.) if version >= 3 else
            rejected('NAMED_ARGUMENT_UNVERIFIED', 'PROFILE_UNCOVERED'))
        wrong_prefix = '' if version >= 5 else 'math.'
        add(version, 'namespace', f'plot({wrong_prefix}sin(0))', rejected('SIGNATURE_UNAVAILABLE'))
        if version < 4:
            add(version, 'conversion-unavailable', 'plot(toradians(180))', rejected('SIGNATURE_UNAVAILABLE'))
            add(version, 'constant-unavailable', 'plot(math.pi)', rejected('UNDEFINED_VARIABLE'))
            continue
        constants = ('math.pi', 'math.e', 'math.phi', 'math.rphi')
        add(version, 'constants', '\n'.join(map(plot, constants)),
            executed(3.141592653589793, 2.718281828459045, 1.618033988749895, 0.6180339887498948))
        conversions = (use('todegrees', 'radians=math.pi'), use('toradians', 'degrees=180'), use('toradians', 'na'))
        add(version, 'conversion', '\n'.join(map(plot, conversions)),
            executed(180., 3.141592653589793, None, qualifier='series'))
        add(version, 'conversion-overflow', plot(use('todegrees', '1e308')),
            rejected('NUMERIC_OVERFLOW', 'RUNTIME_ERROR'))
        if version >= 5:
            add(version, 'migration-parameter', plot(use('acos', 'number=1')), rejected('UNKNOWN_ARGUMENT'))

    bars = [dict(time=index * 60_000, open=value, high=value, low=value, close=value, volume=2.)
            for index, value in enumerate(CLOSES)]
    files['bars.json'] = canonical(dict(schema_version='openpine.historical-bars.2', context_id=PRIMARY.context_id,
                                       symbol=PRIMARY.symbol, timeframe='1', bars=bars)) + b'\n'
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Known trigonometric identities, published constants, domains and versioned signatures.'))) + b'\n'
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
    print('Trigonometry fixtures verified.')


if __name__ == '__main__':
    main()
