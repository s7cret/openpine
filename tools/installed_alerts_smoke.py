"""Exercise alert metadata, replay checkpoint and paged journal via HTTP."""
from dataclasses import asdict
from packages.contracts.contexts import MarketContext
from packages.contracts.realtime import MarketEvent
from tools.alerts_fixtures import (
    BINDING_MESSAGES, BINDING_OVERRIDES, INDICATOR, PROTECTIVE_FILLS,
    binding_fill_source, fill_source, protective_events,
)


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
    return {'alert_replay_checkpoint': True, 'order_fill_alert_replay_checkpoint': True,
            'protective_fill_alert_replay_checkpoint': True, 'fill_alert_bindings_replay_checkpoint': True}


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
    state = http(path + '/advance', {'max_events': 1, 'checkpoint_every': 1})
    assert (state['sequence'], state['alerts']['records']) == (5, 1)
    journal = http(path + '/alert_events?limit=1')
    assert journal['total'] == journal['next'] == 1
    row, = journal['events']
    event = row['event']
    assert (row['sequence'], event['phase'], event['kind'], event['message']) == (4, 'open', 'order_fill', 'Close 28')
    fills = [record['value'] for record in http(path + '/ledger?limit=100')['events'] if record['kind'] == 'fill']
    assert len(fills) == 2 and (event['order_id'], event['fill_id']) == (fills[-1]['order_id'], fills[-1]['fill_id'])


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
