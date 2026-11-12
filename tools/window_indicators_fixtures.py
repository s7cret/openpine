"""Independent small Pine programs for the bounded window indicator profile."""
import argparse

from packages.contracts.wire import canonical
from tools.specification import ROOT

DIRECTORY = 'conformance/window-indicators'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/window-indicators/stochastic-alert.pine'


def source(version, body):
    declaration = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{declaration}("Window indicators")\n{body}\n'


def fixtures():
    files = {}
    cases = []

    def add(v, name, body, expected):
        ident = f'v{v}-{name}'
        path = ident + '.pine'
        files[path] = source(v, body).encode()
        cases.append(dict(case_id=ident, language_version=v, source=path, bars='bars.json', expected=expected))

    def executed(*outputs):
        return dict(status='executed', title='Window indicators', shorttitle='', overlay=False,
                    observation_count=4, qualifiers=['series'] * len(outputs),
                    outputs=[dict(title='', type='float', values=values) for values in outputs])

    def rejected(code):
        return dict(status='rejected', category='LANGUAGE_INVALID', code=code)

    for v in range(1, 7):
        p = 'ta.' if v >= 5 else ''
        add(v, 'fixed', f'plot({p}swma(close))\nplot({p}stoch(close,high,low,3))\nplot({p}linreg(close,3,-1))',
            executed([None, None, None, 25.], [50., 600 / 7, 275 / 3, 275 / 3], [None, None, 40., 50.]))
        if v < 4:
            for name, args in [('stoch', 'close,high,low,bar_index + 1'), ('linreg', 'close,bar_index + 1,0')]:
                add(v, 'series-length-' + name, f'plot({p}{name}({args}))', rejected('ARGUMENT_QUALIFIER'))
        else:
            add(v, 'changing', 'n = bar_index == 3 ? 4 : bar_index % 2 + 1\n'
                f'plot({p}stoch(length=n, low=low, high=high, source=close))\n'
                f'plot({p}linreg(offset=0, source=close[1], length=n))',
                executed([50., 600 / 7, 50., 1600 / 17], [None, None, 20., None]))
            add(v, 'series-offset', f'plot({p}linreg(close,3,bar_index))', rejected('ARGUMENT_QUALIFIER'))
    files['bars.json'] = (ROOT / 'conformance/lines/bars.json').read_bytes()
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Manual weighted averages, extrema and regression on a straight line; versioned qualifier boundaries.'))) + b'\n'
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
    print('Window indicator fixtures verified.')


if __name__ == '__main__':
    main()
