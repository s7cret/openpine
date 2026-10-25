"""Versioned descriptive metrics; these never change search selection scores."""
from decimal import Decimal, localcontext
from packages.contracts.optimization import decimal_value, decimal_text

SCHEMA = 'openpine.research-metrics.1'


def strategy_metrics(summary, events, *, cancelled=None):
    simulation = summary.get('simulation')
    if not simulation:
        return {'schema_version': SCHEMA, 'status': 'not_strategy', 'values': {}, 'undefined': {}}
    window = summary.get('evaluation_window')
    initial = decimal_value(simulation['policy']['initial_capital'])
    with localcontext() as context:
        context.prec = 700
        peak = initial
        drawdown = drawdown_percent = Decimal(0)
        gains = losses = Decimal(0)
        trades = wins = marks = 0
        last = None
        for index, event in enumerate(events):
            if index % 8192 == 0 and cancelled and cancelled():
                raise InterruptedError('Research report interrupted.')
            if window and not window['score_start_ms'] <= event['time'] < window['end_ms']:
                continue
            if event['kind'] == 'trade':
                profit = decimal_value(event['value']['net_profit'])
                trades += 1
                wins += profit > 0
                gains += max(profit, Decimal(0))
                losses += max(-profit, Decimal(0))
            elif event['kind'] == 'mark':
                last = event['value']
                equity = decimal_value(last['equity'])
                peak = max(peak, equity)
                drawdown = max(drawdown, peak - equity)
                if peak > 0:
                    drawdown_percent = max(drawdown_percent, (peak - equity) / peak * 100)
                marks += 1
        if not marks:
            return {'schema_version': SCHEMA, 'status': 'no_scored_marks', 'values': {}, 'undefined': {}}
        profit = decimal_value(last['equity']) - initial
        values = {'initial_capital': decimal_text(initial), 'ending_equity': decimal_text(last['equity']),
                  'net_profit': decimal_text(profit), 'max_drawdown': decimal_text(drawdown),
                  'max_drawdown_percent': decimal_text(drawdown_percent), 'closed_trades': str(trades),
                  'winning_trades': str(wins), 'gross_winning_net': decimal_text(gains),
                  'gross_losing_net': decimal_text(losses), 'commission': decimal_text(last['commission']),
                  'return_percent': decimal_text(profit / initial * 100) if initial > 0 else None,
                  'win_rate_percent': decimal_text(Decimal(wins) / trades * 100) if trades else None,
                  'profit_factor': decimal_text(gains / losses) if losses else None}
        undefined = {}
        if initial <= 0: undefined['return_percent'] = 'nonpositive_initial_capital'
        if not trades: undefined['win_rate_percent'] = 'no_closed_trades'
        if not losses: undefined['profit_factor'] = 'no_losing_closed_trades'
        if simulation['policy'].get('profile_id')=='openpine.performance-broker.1':
            performance=last['performance'];closed=decimal_value(performance['netprofit']);open_profit=profit-closed
            if (performance['archive']['count']!=trades or performance['wins']!=wins
                    or decimal_value(performance['grossprofit'])!=gains or decimal_value(performance['grossloss'])!=losses):
                raise ValueError('Trade observations differ from scored journal metrics.')
            values.update(closed_net_profit=decimal_text(closed),open_net_profit=decimal_text(open_profit))
        return {'schema_version': SCHEMA, 'status': 'available', 'values': values, 'undefined': undefined}
