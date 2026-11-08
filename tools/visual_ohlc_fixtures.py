"""Finite independent examples for calculated bars and candles."""

import argparse

from packages.contracts.wire import canonical
from tools.specification import ROOT


def fixtures():
    files = {}
    cases = []
    closes = (10., 20., 30., 40.)
    blue = '#2196F3FF'

    def add(version, name, body, outputs=None, code=None, category='LANGUAGE_INVALID'):
        case_id = f'v{version}-{name}'
        filename = case_id + '.pine'
        declaration = 'study' if version < 5 else 'indicator'
        files[filename] = f'//@version={version}\n{declaration}("Visual OHLC")\n{body}\n'.encode()
        expected = (dict(status='rejected', category=category, code=code) if code else
                    dict(status='executed', title='Visual OHLC', shorttitle='', overlay=False,
                         outputs=outputs, observation_count=4, qualifiers=['series'] * len(outputs)))
        cases.append(dict(case_id=case_id, language_version=version, source=filename, bars='bars.json', expected=expected))

    def sample(close, color, wick=None, border=None):
        return dict(open=close-1, high=close+2, low=close-2, close=close,
                    color=color, wickcolor=wick, bordercolor=border)

    def layer(kind, title, bars, forced=False):
        output = dict(kind=kind, title=title, type='float', values=[None]*4, bars=bars)
        if forced:
            output['force_overlay'] = True
        return output

    for version in range(1, 7):
        candle_colors = ', #12345680, bordercolor=na' if version >= 3 else ''
        force = ', force_overlay=true' if version >= 5 else ''
        candles = [None, None, *[sample(c+100, '#FF0000FF' if c == 30 else '#00FF00FF',
                                        '#12345680' if version >= 3 else blue,
                                        None if version >= 3 else blue) for c in closes[2:]]]
        bars = [sample(c, '#663399FF') for c in closes]
        add(version, 'layers',
            'plotcandle(open+100, low+100, high+100, close>20 ? close+100 : na, '
            '"Candles", close>30 ? #00FF00 : #FF0000' + candle_colors + ')\n'
            'plotbar(open, high, low, close, "Bars", #663399' + force + ')',
            [layer('plotcandle', 'Candles', candles), layer('plotbar', 'Bars', bars, version >= 5)])
        add(version, 'missing-components',
            'float missing = na\n' + '\n'.join(
                'plotbar(' + ', '.join('missing' if i == j else 'close' for i in range(4)) + ')'
                for j in range(4)),
            [layer('plotbar', '', [None]*4) for _ in range(4)])
        add(version, 'local-call', 'if close>20\n    plotbar(open,high,low,close)\nplot(close)', code='GLOBAL_ONLY_CALL')
        if version<4:
            add(version,'candle-display-version','plotcandle(open,high,low,close,display=display.none)',
                code='VISUAL_DISPLAY_VERSION')
        else:
            add(version,'ohlc-display',
                f'plotcandle(open,high,low,close,show_last=1,display=display.none{force})\n'
                'plotbar(open,high,low,close,display=display.all)',
                [dict(layer('plotcandle','',[sample(c,blue,blue,blue) for c in closes],bool(force)),
                      show_last=1,display='none'),
                 dict(layer('plotbar','',[sample(c,blue) for c in closes]),display='all')])
        if version<3:
            add(version,'window-version','plotbar(open,high,low,close,show_last=1)',code='SIGNAL_WINDOW_VERSION')
        else:
            setting = '' if version==3 else 'n=input(1)\n'
            last = '1' if version==3 else 'n'
            add(version,'windows',setting+f'plotcandle(open,high,low,close,show_last={last}{force})\n'
                'plotbar(open,high,low,close,show_last=0)',
                [dict(layer('plotcandle','',[sample(c,blue,blue,blue) for c in closes],bool(force)),show_last=1),
                 dict(layer('plotbar','',[sample(c,blue) for c in closes]),show_last=0)])
        if version==6:
            add(version,'window-negative','plotbar(open,high,low,close,show_last=-1)',
                code='VISUAL_OHLC',category='RUNTIME_ERROR')
        if version < 5:
            add(version, 'overlay-version', 'plotbar(open,high,low,close,force_overlay=true)', code='VISUAL_OVERLAY_VERSION')
        else:
            add(version, 'overlay-qualifier', 'plotbar(open,high,low,close,force_overlay=close>20)', code='ARGUMENT_QUALIFIER')
            add(version, 'overlay-false', 'plotcandle(open,high,low,close,force_overlay=false)',
                [layer('plotcandle', '', [sample(c, blue, blue, blue) for c in closes])])
        if version >= 3:
            add(version, 'positional-editable', 'plotcandle(open,high,low,close,"",#000000,#000000,false)',
                code='OHLC_POSITIONAL_PROFILE', category='PROFILE_UNCOVERED')
            add(version, 'bar-wick-rejected', 'plotbar(open,high,low,close,wickcolor=#000000)', code='UNKNOWN_ARGUMENT')

    files['bars.json'] = (ROOT / 'conformance/visual-signals/bars.json').read_bytes()
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[],
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Manual closes 10/20/30/40; normalized extrema, missing bars, colors and panes.'),
        cases=cases)) + b'\n'
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    directory = ROOT / 'conformance/visual-ohlc'
    if not args.check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, raw in fixtures().items():
        path = directory / name
        if args.check:
            assert path.read_bytes() == raw, name
        else:
            path.write_bytes(raw)
    print('Visual OHLC fixtures verified.')


if __name__ == '__main__':
    main()
