"""Independent Pine alert cases with fixed historical output and definitions."""
import argparse
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical
from tools.specification import ROOT


INDICATOR = '''indicator("Breakout", overlay=true)
var line level = line.new(0, 10, 3, 10)
alertcondition(close > 10, "Above level", "Breakout")
if close > 10
    alert("open " + str.tostring(close))
    alert("sample", alert.freq_all)
    alert("confirmed", alert.freq_once_per_bar_close)
plot(close)
'''
STRATEGY = '''strategy("Breakout", default_qty_type=strategy.fixed, initial_capital=1000{ticks})
alertcondition(close > 10, "Indicator trigger", "Unused in strategy")
if barstate.isconfirmed and bar_index == 0
    strategy.entry("L", strategy.long)
if barstate.isconfirmed and bar_index == 2
    strategy.close("L")
{alerts}plot(strategy.equity)
'''
STRATEGY_ALERT = '''if close > 10
    alert("strategy " + str.tostring(close), alert.freq_all)
'''

CONDITION_TEMPLATE = ('{{exchange}}:{{ticker}}/{{interval}} {{open}} {{high}} {{low}} {{close}} {{volume}} '
                      '{{time}} {{timenow}} {{plot_0}} {{plot("Скрытая")}} {{plot_1}} {{custom}}')


def condition_template_source(version=6):
    declaration = 'study' if version == 4 else 'indicator'
    return f'''//@version={version}
{declaration}("Templates")
alertcondition(close > 10, "Observed", '{CONDITION_TEMPLATE}')
if close > 10
    alert("literal {{{{close}}}}", alert.freq_once_per_bar_close)
plot(close * 2, "Скрытая", display=display.none)
plot(close[1], "Previous")
'''

PROTECTIVE_FILLS = '''//@version=6
strategy("Protective fills", default_qty_type=strategy.fixed)
if bar_index == 0
    strategy.order("L", strategy.long, qty=2, limit=10, alert_message="Entry " + str.tostring(close))
    strategy.exit("X", "L", qty=1, limit=13, stop=8, alert_profit="Target " + str.tostring(close))
    strategy.exit("Y", "L", qty=1, limit=17, stop=7, alert_message="Quiet", disable_alert=close < 12)
if bar_index == 1 and close == 13
    alert("Script")
plot(strategy.equity)
'''


def protective_events(context_id):
    for index, quotes in enumerate(((10., 10.5, 11.), (12., 9., 13.), (14., 6., 15.))):
        opened = index * 60000
        for revision, (kind, price) in enumerate(zip(('open', 'update', 'close'), quotes, strict=True)):
            observed = quotes[:revision + 1]
            yield MarketEvent(index*3 + revision, context_id, kind, opened, opened + revision*30000,
                              revision, quotes[0], max(observed), min(observed), price, float(revision))


BINDING_OVERRIDES = {'prefix': 'Desk', 'scale': 3}
BINDING_MESSAGES = [
    (7, 'order_fill', 'Desk Entry 30 stop 9'),
    (8, 'order_fill', 'Desk Target 31.5 stop 9'),
    (8, 'alert', 'Script'),
]


def binding_fill_source(version=6, *, messages=True):
    convert = 'tostring' if version == 4 else 'str.tostring'
    prefix, scale = ('input', 'input') if version == 4 else ('input.string', 'input.int')
    exit_field = 'alert_message' if version == 4 else 'alert_profit'
    entry_message = ', alert_message=prefix + " Entry " + amount + " stop " + stopText' if messages else ''
    exit_message = f', {exit_field}=prefix + " Target " + amount + " stop " + stopText' if messages else ''
    return f'''//@version={version}
strategy("Bindings", default_qty_type=strategy.fixed)
prefix = {prefix}("Risk")
scale = {scale}(2)
var float stopPrice = 0.0
mid = (high + low) / 2
amount = {convert}(mid * scale)
stopText = {convert}(stopPrice)
if bar_index == 0
    stopPrice := low - 2
    strategy.entry("L", strategy.long, limit=8{entry_message})
    strategy.exit("X", "L", limit=15, stop=5{exit_message})
if bar_index == 1
    stopPrice := close - 4
if bar_index == 2
    stopPrice := close - 1
    alert("Script")
plot(strategy.equity)
'''


def strategy_source(*, every_tick=False, with_alerts=True):
    return '//@version=6\n' + STRATEGY.format(ticks=', calc_on_every_tick=true' if every_tick else '',
                                             alerts=STRATEGY_ALERT if with_alerts else '')


def fill_source(version=6, *, disabled=None):
    direction = 'true' if version == 1 else 'strategy.long'
    convert = 'tostring' if version == 4 else 'str.tostring'
    entry = f', alert_message="Entry " + {convert}(close)' if version >= 4 else ''
    close = f', alert_message="Close " + {convert}(open + close)' if version >= 4 else ''
    if disabled is not None:
        entry += ', disable_alert=' + disabled
    return f'''//@version={version}
strategy("Fill alerts", default_qty_type=strategy.fixed)
if bar_index == 0
    strategy.entry("L", {direction}{entry})
if bar_index == 1
    strategy.close("L"{close})
plot(strategy.equity)
'''


def fixtures():
    cases, files = [], {}

    def add(version, name, body, expected, *, strategy=False):
        identifier = f'v{version}-{name}'
        path = identifier + '.pine'
        declaration = ('strategy("Alerts", default_qty_type=strategy.fixed)' if strategy else
                       'study("Alerts")' if version < 5 else 'indicator("Alerts")')
        files[path] = (f'//@version={version}\n{declaration}\n' + body + '\n').encode()
        cases.append(dict(case_id=identifier, language_version=version, source=path, bars='bars.json', expected=expected))

    def rejected(code, category='LANGUAGE_INVALID'):
        return dict(status='rejected', category=category, code=code)

    def executed():
        return dict(status='executed', title='Alerts', shorttitle='', overlay=False,
                    observation_count=4, qualifiers=['series'],
                    outputs=[dict(title='', type='float', values=[10., 20., 30., 40.])])

    add(1, 'condition-version', 'alertcondition(close > 10)', rejected('ALERT_VERSION'))
    for version in (2, 3):
        add(version, 'call-version', 'alert("message")', rejected('ALERT_VERSION'))
    for version in range(2, 7):
        add(version, 'condition', 'alertcondition(close > 10, title="Breakout", message="Above level")\nplot(close)',
            {**executed(), 'alert_conditions': [dict(title='Breakout', message='Above level')]})
    for version in range(2, 7):
        add(version, 'condition-template-metadata', 'alertcondition(close > 10, "Price", "Close {{close}}")\nplot(close)',
            {**executed(), 'alert_conditions': [dict(title='Price', message='Close {{close}}')]})
    for version in range(4, 7):
        add(version, 'call', 'if close > 10\n    alert("Sample", alert.freq_all)\nplot(close)', executed())
    add(6, 'condition-scope', 'if close > 10\n    alertcondition(close > 10)', rejected('ALERTCONDITION_SCOPE'))
    add(6, 'call-frequency', 'alert("sample", "invalid")', rejected('ALERT_FREQUENCY'))
    for version in range(1, 4):
        direction = 'true' if version == 1 else 'strategy.long'
        add(version, 'fill-message-version', f'strategy.entry("L", {direction}, alert_message="Fill")',
            rejected('FILL_ALERT_VERSION'), strategy=True)
    add(4, 'disable-fill-version', 'strategy.close("L", disable_alert=true)',
        rejected('FILL_ALERT_VERSION'), strategy=True)
    add(6, 'fill-message-history', 'strategy.close("L", alert_message=str.tostring(close[1]))',
        rejected('FILL_ALERT_EXPRESSION', 'PROFILE_UNCOVERED'), strategy=True)
    add(3, 'order-fill-message-version', 'strategy.order("L", strategy.long, alert_message="Fill")',
        rejected('FILL_ALERT_VERSION'), strategy=True)
    add(4, 'close-all-disable-version', 'strategy.close_all(disable_alert=true)',
        rejected('FILL_ALERT_VERSION'), strategy=True)
    add(4, 'exit-role-version', 'strategy.exit("X", "L", stop=8, alert_loss="Stop")',
        rejected('FILL_ALERT_VERSION'), strategy=True)
    add(5, 'exit-role-history', 'strategy.exit("X", "L", stop=8, alert_loss=str.tostring(close[1]))',
        rejected('FILL_ALERT_EXPRESSION', 'PROFILE_UNCOVERED'), strategy=True)
    add(4, 'fill-local-binding', 'if close > 0\n    text = tostring(close)\n'
        '    strategy.entry("L", strategy.long, alert_message=text)',
        rejected('FILL_ALERT_EXPRESSION', 'PROFILE_UNCOVERED'), strategy=True)
    add(5, 'fill-aliased-history', 'price = close[1]\nstrategy.close("L", alert_message=str.tostring(price))',
        rejected('FILL_ALERT_EXPRESSION', 'PROFILE_UNCOVERED'), strategy=True)
    add(6, 'fill-mutable-ordinary', 'price = close\nprice := low\n'
        'strategy.exit("X", "L", stop=8, alert_loss=str.tostring(price))',
        rejected('FILL_ALERT_EXPRESSION', 'PROFILE_UNCOVERED'), strategy=True)
    files['bars.json'] = (ROOT / 'conformance/lines/bars.json').read_bytes()
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Explicit version and deferred expression boundaries, unchanged history and condition metadata.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    directory = ROOT / 'conformance/alerts'
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, raw in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == raw, name
        else:
            path.write_bytes(raw)
    print('Alert fixtures verified.')


if __name__ == '__main__':
    main()
