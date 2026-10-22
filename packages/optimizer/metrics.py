"""Decimal scoring of immutable outputs; holdout results never enter selection."""
from decimal import Decimal, localcontext

from packages.contracts.optimization import METRICS_SCHEMA, OUTPUT_METRICS, metric_key, decimal_value, decimal_text


def evaluate(summary, window, objective, constraints, *, events=(), rows=(),cancelled=None):
    requested = [objective, *constraints]; values = {}; start, end = window['score_start_ms'], window['end_ms']
    with localcontext() as context:
        context.prec = 700
        simulation = summary.get('simulation')
        if simulation:
            initial = decimal_value(simulation['policy']['initial_capital'])
            peak = initial; drawdown = Decimal(0); count = 0; last = None
            for index,event in enumerate(events):
                if index%8192==0 and cancelled and cancelled():raise InterruptedError('Research evaluation interrupted.')
                if event['kind'] != 'mark' or not start <= event['time'] < end:
                    continue
                last = event['value']; equity = decimal_value(last['equity'])
                peak = max(peak, equity); drawdown = max(drawdown, peak-equity); count += 1
            if not count:
                raise ValueError('No scored broker marks.')
            values.update(net_profit=decimal_text(decimal_value(last['equity'])-initial),
                          max_drawdown=decimal_text(drawdown), trades=decimal_text(last['trades']),
                          commission=decimal_text(last['commission']))
        output_specs = [s for s in requested if s['metric'] in OUTPUT_METRICS]
        if output_specs:
            outputs = summary['outputs']; wanted = {s['output_index'] for s in output_specs}
            if any(index >= len(outputs) for index in wanted):
                raise ValueError('Requested output_index does not exist.')
            slots = {outputs[i]['output_id']: i for i in wanted}; accumulators = {}
            for cursor,(_, timestamp, output_id, value) in enumerate(rows):
                if cursor%8192==0 and cancelled and cancelled():raise InterruptedError('Research evaluation interrupted.')
                if output_id not in slots or not start <= timestamp < end or value is None:
                    continue
                number = decimal_value(value); index = slots[output_id]
                state = accumulators.setdefault(index, [0, Decimal(0), number, number, number])
                state[0] += 1; state[1] += number; state[2] = min(state[2], number); state[3] = max(state[3], number); state[4] = number
            for spec in output_specs:
                state = accumulators.get(spec['output_index'])
                if state:
                    number = {'output_last': state[4], 'output_mean': state[1]/state[0], 'output_min': state[2], 'output_max': state[3]}[spec['metric']]
                    values[metric_key(spec)] = decimal_text(number)
        key = metric_key(objective)
        if key not in values:
            return {'schema_version': METRICS_SCHEMA, 'status': 'missing_metric', 'metrics': values, 'score': None}
        for spec in constraints:
            value = values.get(metric_key(spec))
            if value is None:
                return {'schema_version': METRICS_SCHEMA, 'status': 'missing_metric', 'metrics': values, 'score': None}
            actual, expected = decimal_value(value), decimal_value(spec['value'])
            if spec['operator'] == 'gte' and actual < expected or spec['operator'] == 'lte' and actual > expected:
                return {'schema_version': METRICS_SCHEMA, 'status': 'constraint_failed', 'metrics': values, 'score': None}
        return {'schema_version': METRICS_SCHEMA, 'status': 'eligible', 'metrics': values, 'score': values[key]}


def rank(rows, direction):
    eligible = [row for row in rows if row['assessment']['status'] == 'eligible']
    def key(row):
        score=decimal_value(row['assessment']['score'])
        return (score.copy_negate() if direction=='maximize' else score,row['ordinal'])
    return sorted(eligible,key=key)
