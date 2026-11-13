"""Exercise alert metadata, replay checkpoint and paged journal via HTTP."""
from dataclasses import asdict
from packages.contracts.alerts import CONDITION_TEMPLATE_PROFILE
from packages.contracts.contexts import MarketContext
from packages.contracts.realtime import MarketEvent
from tools.alerts_fixtures import (
    BINDING_MESSAGES, BINDING_OVERRIDES, CONDITION_TEMPLATE, INDICATOR, PROTECTIVE_FILLS,
    binding_fill_source, condition_template_source, fill_source, protective_events,
)
from tools.specification import ROOT
from tools.window_indicators_fixtures import EXAMPLE
from tools.channel_indicators_fixtures import EXAMPLE as CHANNEL_EXAMPLE, squeeze_tape
from tools.trend_indicators_fixtures import EXAMPLE as TREND_EXAMPLE, reversal_tape
from tools.momentum_fixtures import EXAMPLE as MOMENTUM_EXAMPLE, crossing_tape


def verify(http, cli, work):
    source = '//@version=6\n' + INDICATOR
    compiled = http('/api/compile', {'source': source, 'source_name': 'alerts.pine'})
    assert compiled['status'] == 'compiled', compiled
    condition, = compiled['alert_conditions']
    assert (condition['title'], condition['message']) == ('Above level', 'Breakout')
    context = MarketContext('primary', 'TEST:ALERTS', '1', 'recorded.1')
    identifier = http('/api/replays', {'input_ref': compiled['input_ref'], 'contexts': [asdict(context)],
        'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120})['replay_id']
    path = f'/api/replays/{identifier}'
    events = [MarketEvent(seq, 'primary', kind, 0, at, seq, 10., close, 10., close, float(seq)).payload()
              for seq, (kind, at, close) in enumerate((('open', 0, 10.), ('update', 30000, 10.5), ('close', 60000, 11.)))]
    http(path + '/events', {'events': events})
    state = http(path + '/advance', {'max_events': 2, 'checkpoint_every': 1})
    assert state['confirmed_bars'] == 0 and state['alerts']['records'] == 2
    state = http(path + '/advance', {'max_events': 1, 'checkpoint_every': 1})
    assert state['confirmed_bars'] == 1 and state['alerts']['records'] == 5
    prefix = http(path + '/alert_events?limit=2')
    tail = http(path + '/alert_events?start=2&limit=3')
    assert [row['event']['message'] for row in prefix['events']] == ['open 10.5', 'sample']
    assert [row['event']['message'] for row in tail['events']] == ['Breakout', 'sample', 'confirmed']
    assert tail['total'] == tail['next'] == 5
    assert http(path + '/chart')['lines'][0]['y1'] == 10.
    verify_fills(http, context)
    verify_protective_fills(http, context)
    verify_fill_replay(http, context, binding_fill_source(), 'fill-bindings.pine',
                       splits=((7, 0), (1, 1), (1, 3)), messages=BINDING_MESSAGES, quantities=['1', '1'],
                       inputs=BINDING_OVERRIDES)
    verify_condition_templates(http, context)
    verify_stochastic_template(http, context)
    verify_squeeze_template(http, context)
    verify_trend_template(http, context)
    verify_momentum_template(http, context)
    return {'alert_replay_checkpoint': True, 'order_fill_alert_replay_checkpoint': True,
            'protective_fill_alert_replay_checkpoint': True, 'fill_alert_bindings_replay_checkpoint': True,
            'alert_condition_template_replay_checkpoint': True, 'alert_fill_ledger_lookup': True,
            'window_indicators_replay_checkpoint': True, 'channel_indicators_replay_checkpoint': True,
            'trend_indicators_replay_checkpoint': True, 'momentum_replay_checkpoint': True}


def verify_momentum_template(http, context):
    example = ROOT / MOMENTUM_EXAMPLE
    compiled = http('/api/compile', {'source': example.read_text(encoding='utf-8'), 'source_name': example.name})
    assert compiled['status'] == 'compiled', compiled
    settings = {'input_ref': compiled['input_ref'], 'contexts': [asdict(context)], 'primary_id': context.context_id,
                'broker_settings': None, 'timeout_seconds': 120,
                'alert_condition_profile': CONDITION_TEMPLATE_PROFILE}
    events = [event.payload() for event in crossing_tape(context.context_id)]

    def replay():
        identifier = http('/api/replays', settings)['replay_id']
        path = f'/api/replays/{identifier}'
        http(path + '/events', {'events': events})
        return path

    baseline = replay()
    http(baseline + '/advance', {'max_events': len(events), 'checkpoint_every': len(events)})
    restored = replay()
    partial = http(restored + '/advance', {'max_events': 17, 'checkpoint_every': 1})
    assert (partial['sequence'], partial['confirmed_bars'], partial['alerts']['records']) == (16, 5, 0)
    final = http(restored + '/advance', {'max_events': len(events) - 17, 'checkpoint_every': 1})
    assert (final['sequence'], final['confirmed_bars'], final['alerts']['records']) == (26, 9, 2)
    events = http(restored + '/alert_events')
    assert events == http(baseline + '/alert_events')
    assert [(row['sequence'], row['event']['message']) for row in events['events']] == [
        (seq, 'CCI crossed 80 at 100') for seq in (17, 26)]


def verify_trend_template(http, context):
    example = ROOT / TREND_EXAMPLE
    compiled = http('/api/compile', {'source': example.read_text(encoding='utf-8'), 'source_name': example.name})
    assert compiled['status'] == 'compiled', compiled
    identifier = http('/api/replays', {'input_ref': compiled['input_ref'], 'contexts': [asdict(context)],
        'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120,
        'alert_condition_profile': CONDITION_TEMPLATE_PROFILE})['replay_id']
    path = f'/api/replays/{identifier}'
    http(path + '/events', {'events': [e.payload() for e in reversal_tape(context.context_id)]})
    partial = http(path + '/advance', {'max_events': 8, 'checkpoint_every': 1})
    assert (partial['sequence'], partial['confirmed_bars'], partial['alerts']['records']) == (7, 2, 0)
    final = http(path + '/advance', {'max_events': 13, 'checkpoint_every': 1})
    assert (final['sequence'], final['confirmed_bars'], final['alerts']['records']) == (20, 7, 3)
    events = http(path + '/alert_events')['events']
    assert [(event['sequence'], event['event']['message']) for event in events] == [
        (seq, 'Trend reversed, ADX 100') for seq in (8, 14, 20)]


def verify_squeeze_template(http, context):
    example = ROOT / CHANNEL_EXAMPLE
    compiled = http('/api/compile', {'source': example.read_text(encoding='utf-8'), 'source_name': example.name})
    assert compiled['status'] == 'compiled', compiled
    identifier = http('/api/replays', {'input_ref': compiled['input_ref'], 'contexts': [asdict(context)],
        'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120,
        'alert_condition_profile': CONDITION_TEMPLATE_PROFILE})['replay_id']
    path = f'/api/replays/{identifier}'
    http(path + '/events', {'events': [e.payload() for e in squeeze_tape(context.context_id)]})
    partial = http(path + '/advance', {'max_events': 8, 'checkpoint_every': 1})
    assert (partial['sequence'], partial['confirmed_bars'], partial['alerts']['records']) == (7, 2, 0)
    full = http(path + '/advance', {'max_events': 7, 'checkpoint_every': 1})
    assert (full['sequence'], full['confirmed_bars'], full['alerts']['records']) == (14, 5, 1)
    event, = http(path + '/alert_events')['events']
    assert (event['sequence'], event['event']['message']) == (8, 'BB width 2')


def verify_stochastic_template(http, context):
    example = ROOT / EXAMPLE
    compiled = http('/api/compile', {'source': example.read_text(encoding='utf-8'), 'source_name': example.name})
    assert compiled['status'] == 'compiled', compiled
    identifier = http('/api/replays', {'input_ref': compiled['input_ref'], 'contexts': [asdict(context)],
        'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120,
        'alert_condition_profile': CONDITION_TEMPLATE_PROFILE})['replay_id']
    path = f'/api/replays/{identifier}'
    http(path + '/events', {'events': [e.payload() for e in protective_events(context.context_id)]})
    partial = http(path + '/advance', {'max_events': 8, 'checkpoint_every': 1})
    assert (partial['sequence'], partial['confirmed_bars'], partial['alerts']['records']) == (7, 2, 2)
    full = http(path + '/advance', {'max_events': 1, 'checkpoint_every': 1})
    assert (full['sequence'], full['confirmed_bars'], full['alerts']['records']) == (8, 3, 3)
    journal = http(path + '/alert_events')
    assert [(row['sequence'], row['event']['message']) for row in journal['events']] == [
        (2, 'Stochastic 100'), (5, 'Stochastic 100'), (8, 'Stochastic 100')]


def verify_condition_templates(http, context):
    compiled = http('/api/compile', {'source': condition_template_source(), 'source_name': 'condition-templates.pine'})
    assert compiled['status'] == 'compiled', compiled
    assert compiled['alert_conditions'][0]['message'] == CONDITION_TEMPLATE
    config = {'input_ref': compiled['input_ref'], 'contexts': [asdict(context)], 'primary_id': context.context_id,
              'broker_settings': None, 'timeout_seconds': 120, 'alert_condition_profile': CONDITION_TEMPLATE_PROFILE}
    events = [event.payload() for event in protective_events(context.context_id) if event.sequence < 6]

    def create():
        identifier = http('/api/replays', config)['replay_id']
        path = f'/api/replays/{identifier}'
        http(path + '/events', {'events': events})
        return path

    full = create()
    http(full + '/advance', {'max_events': 6, 'checkpoint_every': 6})
    replay = create()
    state = http(replay + '/advance', {'max_events': 5, 'checkpoint_every': 1})
    assert (state['sequence'], state['alerts']['records']) == (4, 2)
    http(replay + '/advance', {'max_events': 1, 'checkpoint_every': 1})
    journal = http(replay + '/alert_events')
    assert journal == http(full + '/alert_events') and journal['total'] == 4
    assert [(row['sequence'], row['event']['kind'], row['event']['message']) for row in journal['events']] == [
        (2, 'alertcondition', 'TEST:ALERTS/1 10 11 10 11 2 '
         '1970-01-01T00:00:00Z 1970-01-01T00:01:00Z 22 22 na {{custom}}'),
        (2, 'alert', 'literal {{close}}'),
        (5, 'alertcondition', 'TEST:ALERTS/1 12 13 9 13 2 '
         '1970-01-01T00:01:00Z 1970-01-01T00:02:00Z 26 26 11 {{custom}}'),
        (5, 'alert', 'literal {{close}}'),
    ]
    passport = http(replay + '/passport')['passport']
    assert passport['selection']['qualification']['alert_condition_execution']['profile_id'] == CONDITION_TEMPLATE_PROFILE
    assert passport['replay_execution']['applied_events'] == 6


def verify_fills(http, context):
    compiled = http('/api/compile', {'source': fill_source(disabled='close < 12'), 'source_name': 'fills.pine'})
    assert compiled['status'] == 'compiled', compiled
    identifier = http('/api/replays', {'input_ref': compiled['input_ref'], 'contexts': [asdict(context)],
        'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120})['replay_id']
    path = f'/api/replays/{identifier}'
    events = []
    for index in range(3):
        opened, price = index * 60000, 10. + index * 2
        for revision, kind, delta in ((0, 'open', 0), (1, 'close', 1)):
            events.append(MarketEvent(len(events), context.context_id, kind, opened, opened + delta * 60000,
                                      revision, price, price + delta, price, price + delta, float(delta)).payload())
    http(path + '/events', {'events': events})
    state = http(path + '/advance', {'max_events': 2, 'checkpoint_every': 1})
    assert (state['sequence'], state['alerts']['records']) == (1, 0)
    state = http(path + '/advance', {'max_events': 3, 'checkpoint_every': 1})
    assert (state['sequence'], state['alerts']['records']) == (4, 1)
    event = http(path + '/alert_events?limit=1')['events'][0]['event']
    fill_path = path + f"/fill?bar_index={event['bar_index']}&fill_id={event['fill_id']}"
    assert http(fill_path) == {'event': None, 'pending': True}
    state = http(path + '/advance', {'max_events': 1, 'checkpoint_every': 1})
    assert (state['sequence'], state['alerts']['records']) == (5, 1)
    journal = http(path + '/alert_events?limit=1')
    assert journal['total'] == journal['next'] == 1
    row, = journal['events']
    event = row['event']
    assert (row['sequence'], event['phase'], event['kind'], event['message']) == (4, 'open', 'order_fill', 'Close 28')
    fills = [record['value'] for record in http(path + '/ledger?limit=100')['events'] if record['kind'] == 'fill']
    assert len(fills) == 2 and (event['order_id'], event['fill_id']) == (fills[-1]['order_id'], fills[-1]['fill_id'])
    lookup = http(fill_path)
    assert lookup['pending'] is False and lookup['event']['value'] == fills[-1]


def verify_protective_fills(http, context):
    verify_fill_replay(http, context, PROTECTIVE_FILLS, 'protective-fills.pine',
                       splits=((3, 0), (3, 3), (3, 3)), quantities=['2', '1', '1'],
                       messages=[(4, 'order_fill', 'Entry 9'), (5, 'order_fill', 'Target 13'), (5, 'alert', 'Script')])


def verify_fill_replay(http, context, source, filename, *, splits, messages, quantities, inputs=None):
    compiled = http('/api/compile', {'source': source, 'source_name': filename})
    assert compiled['status'] == 'compiled', compiled
    settings = {'input_ref': compiled['input_ref'], 'contexts': [asdict(context)], 'primary_id': context.context_id,
                'broker_settings': None, 'broker_profile': 'openpine.observed-broker.1', 'timeout_seconds': 120}
    if inputs is not None:
        settings['inputs'] = inputs
    events = [event.payload() for event in protective_events(context.context_id)]

    def replay():
        identifier = http('/api/replays', settings)['replay_id']
        path = f'/api/replays/{identifier}'
        http(path + '/events', {'events': events})
        return path

    baseline = replay()
    http(baseline + '/advance', {'max_events': len(events), 'checkpoint_every': len(events)})
    path = replay()
    sequence = -1
    for count, expected in splits:
        sequence += count
        state = http(path + '/advance', {'max_events': count, 'checkpoint_every': 1})
        assert (state['sequence'], state['alerts']['records']) == (sequence, expected)
    journal = http(path + '/alert_events?limit=100')
    assert journal == http(baseline + '/alert_events?limit=100')
    ledger = http(path + '/ledger?limit=100')
    assert ledger == http(baseline + '/ledger?limit=100')
    fills = [row['value'] for row in ledger['events'] if row['kind'] == 'fill']
    assert [fill['quantity'] for fill in fills] == quantities
    assert [(row['sequence'], row['event']['kind'], row['event']['message']) for row in journal['events']] == messages
    fill_alerts = [row['event'] for row in journal['events'] if row['event']['kind'] == 'order_fill']
    assert [(row['order_id'], row['fill_id']) for row in fill_alerts] == [
        (fill['order_id'], fill['fill_id']) for fill in fills[:len(fill_alerts)]]
    for event in fill_alerts:
        lookup = http(path + f"/fill?bar_index={event['bar_index']}&fill_id={event['fill_id']}")
        expected, = (row for row in ledger['events'] if row['kind'] == 'fill'
                     and row['value']['fill_id'] == event['fill_id'])
        assert lookup == {'event': expected, 'pending': False}
