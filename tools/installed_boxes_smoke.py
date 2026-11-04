"""Installed box, line and label lifecycle through both worker routes."""
from dataclasses import asdict
import time
import uuid

from packages.contracts.contexts import MarketContext
from tools.installed_lines_smoke import market_events
from tools.specification import ROOT


def verify(http, cli, work):
    fixtures = ROOT / 'conformance/boxes'
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

    box = {'id': 1, 'left': 0, 'top': 50., 'right': 4, 'bottom': 5.,
           'border_color': '#112233FF', 'bgcolor': '#33669980'}
    line = {'id': 1, 'x1': 0, 'y1': 10., 'x2': 3, 'y2': 40.}
    label = {'id': 1, 'x': 3, 'y': 40., 'text': 'zone'}
    expected = {'boxes': [box], 'lines': [line], 'labels': [label]}
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
        assert {kind: result[kind] for kind in expected} == expected
        chart = http(f'/api/jobs/{job}/chart?start=1&limit=2')
        assert chart['boxes'] == [box] and chart['lines'] == [line] and chart['labels'] == []
        events = http(f'/api/jobs/{job}/box_events')
        assert events['total'] == 12 and events['events'][5]['event']['box']['bgcolor'] is None
        assert http(f'/api/jobs/{job}/line_events')['total'] == 1
        assert http(f'/api/jobs/{job}/label_events')['total'] == 5
        if version == 6:
            context = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
            replay = http('/api/replays', {'input_ref': compiled['input_ref'], 'contexts': [asdict(context)],
                'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120})['replay_id']
            route = f'/api/replays/{replay}'
            http(route + '/events', {'events': market_events(bars, context)})
            prefix = http(route + '/advance', {'max_events': 3, 'checkpoint_every': 1})
            assert prefix['boxes'][0]['bgcolor'] == '#12345680'
            assert prefix['preview']['boxes'][0]['bgcolor'] == '#FF000080'
            final = http(route + '/advance', {'max_events': 20, 'checkpoint_every': 2})
            assert final['preview'] is None and {kind: final[kind] for kind in expected} == expected
            assert http(route + '/box_events') == events
            assert http(route + '/chart?start=1&limit=2')['boxes'] == [box]
    return {'box_versions': [4, 5, 6], 'box_events': 12, 'box_pause_resume': True,
            'box_replay_preview': True, 'box_crossing_window': True, 'box_mixed_drawings': True}
