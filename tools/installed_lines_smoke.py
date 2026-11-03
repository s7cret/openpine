"""Installed line lifecycle through historical and durable replay workers."""
from dataclasses import asdict
import time
import uuid

from packages.contracts.contexts import MarketContext
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import read_json
from tools.specification import ROOT


def market_events(path, context):
    events = []
    for bar in read_json(path)['bars']:
        t, p = bar['time'], bar['open']
        events.append(MarketEvent(len(events), context.context_id, 'open', t, t, 0, p, p, p, p, 0.).payload())
        events.append(MarketEvent(len(events), context.context_id, 'close', t, t + 60000, 1,
            p, bar['high'], bar['low'], bar['close'], bar['volume']).payload())
    return events


def verify(http, cli, work):
    fixtures = ROOT / 'conformance/lines'
    bars = fixtures / 'bars.json'
    snapshot = http('/api/snapshots', {'provider': 'upload', 'raw': bars.read_text(encoding='utf-8')})['sha256']

    def finish(job, wanted):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = http(f'/api/jobs/{job}')
            if state['status'] == wanted:
                return state
            if state['status'] in ('failed', 'cancelled', 'timed_out', 'succeeded'):
                raise AssertionError(state)
            time.sleep(.1)
        raise TimeoutError(state)

    line = {'id': 1, 'x1': 0, 'y1': 10., 'x2': 3, 'y2': 40.}
    tag = {'id': 1, 'x': 3, 'y': 40., 'text': 'moving'}
    for version in (4, 5, 6):
        path = fixtures / f'v{version}-moving.pine'
        compiled = http('/api/compile', {'source': path.read_text(encoding='utf-8'), 'source_name': path.name})
        assert compiled['status'] == 'compiled', compiled
        launch = http('/api/launches', {'schema_version': 'openpine.launch-request.1', 'request_id': uuid.uuid4().hex,
            'input_ref': compiled['input_ref'], 'primary': {'kind': 'snapshot', 'snapshot_id': snapshot}, 'contexts': [],
            'inputs': {}, 'timeout_seconds': 120, 'gap_policy': 'fail', 'checkpoint_interval_bars': 2, 'pause_after_bars': 2})
        assert launch['status'] == 'ready', launch
        job = http(f"/api/launches/{launch['launch_id']}/start", {})['job_id']
        finish(job, 'paused')
        http(f'/api/jobs/{job}/resume', {})
        assert finish(job, 'succeeded')['resumed_from_bar'] == 2
        result = http(f'/api/jobs/{job}/result')['result']
        assert result['lines'] == [line] and result['labels'] == [tag], result
        chart = http(f'/api/jobs/{job}/chart?start=1&limit=2')
        assert chart['lines'] == [line] and chart['labels'] == [], chart
        events = http(f'/api/jobs/{job}/line_events?limit=100')
        assert events['total'] == 11 and len(events['events']) == 11
        assert events['events'][6]['event'] == {'action': 'delete', 'id': 2, 'reason': 'explicit'}
        assert http(f'/api/jobs/{job}/label_events')['total'] == 5
        if version == 6:
            context = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
            replay = http('/api/replays', {'input_ref': compiled['input_ref'], 'contexts': [asdict(context)],
                'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120})['replay_id']
            route = f'/api/replays/{replay}'
            http(route + '/events', {'events': market_events(bars, context)})
            first = http(route + '/advance', {'max_events': 1, 'checkpoint_every': 1})
            assert first['confirmed_bars'] == 0 and first['lines'] == []
            assert first['preview']['lines'][0]['y2'] == 9. and first['preview']['lines'][0]['id'] == 1
            second = http(route + '/advance', {'max_events': 2, 'checkpoint_every': 1})
            assert second['confirmed_bars'] == 1 and second['lines'][0]['x2'] == 0
            assert second['preview']['lines'][0]['x2'] == 1
            final = http(route + '/advance', {'max_events': 20, 'checkpoint_every': 2})
            assert final['lines'] == [line] and final['labels'] == [tag] and final['preview'] is None
            assert http(route + '/line_events') == events
            assert http(route + '/chart?start=1&limit=2')['lines'] == [line]
    return {'line_versions': [4, 5, 6], 'line_resume_bar': 2, 'line_events': 11,
            'line_crossing_window': True, 'line_label_journals': True, 'line_durable_replay': True}
