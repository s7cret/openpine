"""Installed ray and price behavior through historical and replay workers."""
from dataclasses import asdict
import time
import uuid

from packages.contracts.contexts import MarketContext
from tools.installed_lines_smoke import market_events
from tools.specification import ROOT


def verify(http, cli, work):
    fixtures = ROOT / 'conformance/line-extensions'
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

    line = {'id': 1, 'x1': 0, 'y1': 10., 'x2': 1, 'y2': 20., 'extend': 'both'}
    expected = [30., 40., 50., 60.]
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
        assert result['lines'] == [line] and result['outputs'][0]['values'] == expected, result
        assert http(f'/api/jobs/{job}/chart?start=2&limit=2')['lines'] == [line]
        events = http(f'/api/jobs/{job}/line_events')
        assert events['total'] == 4
        assert [r['event']['line']['extend'] for r in events['events']] == ['right', 'none', 'left', 'both']
        if version == 6:
            context = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
            replay = http('/api/replays', {'input_ref': compiled['input_ref'], 'contexts': [asdict(context)],
                'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120})['replay_id']
            route = f'/api/replays/{replay}'
            http(route + '/events', {'events': market_events(bars, context)})
            prefix = http(route + '/advance', {'max_events': 3, 'checkpoint_every': 1})
            assert prefix['lines'][0]['extend'] == 'right'
            assert prefix['preview']['lines'][0]['extend'] == 'left'
            assert prefix['preview']['outputs'][0]['value'] == 40.
            final = http(route + '/advance', {'max_events': 20, 'checkpoint_every': 2})
            assert final['lines'] == [line] and final['preview'] is None
            assert http(route + '/line_events') == events
            chart = http(route + '/chart?start=2&limit=2')
            assert chart['lines'] == [line] and [row[3] for row in chart['rows']] == expected[2:]
    return {'line_extension_versions': [4, 5, 6], 'line_extension_events': 4,
            'line_extension_projection': True, 'line_extension_viewport': True,
            'line_extension_pause_resume': True, 'line_extension_replay_preview': True}
