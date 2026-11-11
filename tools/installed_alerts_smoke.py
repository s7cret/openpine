"""Exercise alert metadata, replay checkpoint and paged journal via HTTP."""
from dataclasses import asdict
from packages.contracts.contexts import MarketContext
from packages.contracts.realtime import MarketEvent
from tools.alerts_fixtures import INDICATOR


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
    return {'alert_replay_checkpoint': True}
