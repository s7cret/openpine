"""Installed linefill state through the historical and replay workers."""
from dataclasses import asdict
import time
import uuid

from packages.contracts.contexts import MarketContext
from tools.installed_lines_smoke import market_events
from tools.linefills_fixtures import FILL, INVENTORY_FINAL, LOWER, UPPER
from tools.specification import ROOT


def verify(http, cli, work):
    fixtures = ROOT / 'conformance/linefills'
    bars = fixtures / 'bars.json'
    snapshot = http('/api/snapshots', {'provider': 'upload', 'raw': bars.read_text(encoding='utf-8')})['sha256']

    def compile_fixture(name):
        path = fixtures / name
        compiled = http('/api/compile', {'source': path.read_text(encoding='utf-8'), 'source_name': path.name})
        assert compiled['status'] == 'compiled', compiled
        return compiled['input_ref']

    def finish(job, wanted):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = http(f'/api/jobs/{job}')
            if state['status'] == wanted:
                return state
            if state['status'] in ('failed', 'cancelled', 'timed_out', 'succeeded'):
                raise AssertionError(state)
            time.sleep(.1)
        raise TimeoutError(job)

    def historical_route(input_ref, cut):
        launch = http('/api/launches', {'schema_version': 'openpine.launch-request.1', 'request_id': uuid.uuid4().hex,
            'input_ref': input_ref, 'primary': {'kind': 'snapshot', 'snapshot_id': snapshot}, 'contexts': [],
            'inputs': {}, 'timeout_seconds': 120, 'gap_policy': 'fail', 'checkpoint_interval_bars': cut, 'pause_after_bars': cut})
        assert launch['status'] == 'ready', launch
        job = http(f"/api/launches/{launch['launch_id']}/start", {})['job_id']
        finish(job, 'paused')
        route = f'/api/jobs/{job}'
        http(route + '/resume', {})
        assert finish(job, 'succeeded')['resumed_from_bar'] == cut
        return route

    for version in (5, 6):
        route = historical_route(compile_fixture(f'v{version}-channel.pine'), 2)
        for endpoint in ('result', 'summary'):
            result = http(f'{route}/{endpoint}')['result']
            assert result['lines'] == [UPPER, LOWER] and result['linefills'] == [FILL], result
        chart = http(route + '/chart?start=1&limit=2')
        assert chart['linefills'] == [FILL] and chart['lines'] == [UPPER, LOWER], chart
        assert http(route + '/line_events')['total'] == 4

    context = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
    replay = http('/api/replays', {'input_ref': compile_fixture('v6-delete.pine'), 'contexts': [asdict(context)],
        'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120})['replay_id']
    route = f'/api/replays/{replay}'
    http(route + '/events', {'events': market_events(bars, context)})
    prefix = http(route + '/advance', {'max_events': 3, 'checkpoint_every': 1})
    assert prefix['linefills'] == [{**FILL, 'color': '#12345680'}] and prefix['lines'][0]['y2'] == 35.
    assert prefix['preview']['linefills'] == [{**FILL, 'color': '#FF000080'}] and prefix['preview']['lines'][0]['y2'] == 30.
    resumed = http(route + '/advance', {'max_events': 3, 'checkpoint_every': 1})
    assert resumed['linefills'] == [FILL] and resumed['lines'] == [UPPER, LOWER]
    assert http(route + '/chart?start=1&limit=2')['linefills'] == [FILL]
    final = http(route + '/advance', {'max_events': 2, 'checkpoint_every': 1})
    assert final['preview'] is None and final['linefills'] == [] and final['lines'] == [UPPER], final
    assert http(route + '/line_events')['total'] == 5

    input_ref = compile_fixture('v6-drawing-limits.pine')
    job_route = historical_route(input_ref, 1)
    result = http(job_route + '/result')['result']
    assert [line['id'] for line in result['lines']] == [2, 3] and result['linefills'] == []
    assert [label['id'] for label in result['labels']] == [4] and result['labels'][0]['y'] is None
    assert [box['id'] for box in result['boxes']] == [3, 4]
    assert [polyline['id'] for polyline in result['polylines']] == [4]
    chart = http(job_route + '/chart?start=1&limit=2')
    assert chart['lines'] == result['lines'] and chart['linefills'] == [], chart
    journal = http(job_route + '/line_events')
    assert journal['total'] == 5
    assert journal['events'][-2]['event'] == {'action': 'delete', 'id': 1, 'reason': 'capacity'}

    replay = http('/api/replays', {'input_ref': input_ref, 'contexts': [asdict(context)],
        'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120})['replay_id']
    route = f'/api/replays/{replay}'
    http(route + '/events', {'events': market_events(bars, context)})
    preview = http(route + '/advance', {'max_events': 3, 'checkpoint_every': 1})
    assert [line['id'] for line in preview['lines']] == [1, 2] and preview['linefills'] == [FILL]
    assert preview['preview']['lines'] == result['lines'] and preview['preview']['linefills'] == []
    assert http(route + '/line_events')['total'] == 2
    final = http(route + '/advance', {'max_events': 5, 'checkpoint_every': 1})
    assert final['preview'] is None and final['lines'] == result['lines'] and final['linefills'] == []
    assert http(route + '/line_events') == journal

    input_ref = compile_fixture('v6-drawing-inventory.pine')
    job_route = historical_route(input_ref, 1)
    result = http(job_route + '/result')['result']
    assert result['lines'] == [INVENTORY_FINAL] and result['linefills'] == [], result
    assert {row['title']: row['values'] for row in result['outputs']} == {
        'lines': [2, 2, 1, 1], 'oldest': [20., 25., 30., 30.], 'fills': [1, 0, 0, 0]}
    journal = http(job_route + '/line_events')
    assert journal['total'] == 8

    replay = http('/api/replays', {'input_ref': input_ref, 'contexts': [asdict(context)],
        'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120})['replay_id']
    route = f'/api/replays/{replay}'
    http(route + '/events', {'events': market_events(bars, context)})
    preview = http(route + '/advance', {'max_events': 3, 'checkpoint_every': 1})
    assert [line['id'] for line in preview['lines']] == [1, 2] and preview['linefills'] == [FILL]
    assert [line['id'] for line in preview['preview']['lines']] == [2, 3] and preview['preview']['linefills'] == []
    assert {row['title']: row['value'] for row in preview['preview']['outputs']} == {'lines': 2, 'oldest': 25., 'fills': 0}
    assert http(route + '/line_events')['total'] == 4
    final = http(route + '/advance', {'max_events': 5, 'checkpoint_every': 1})
    assert final['preview'] is None and final['lines'] == [INVENTORY_FINAL] and final['linefills'] == []
    chart = http(route + '/chart?start=1&limit=3')
    assert chart['lines'] == result['lines'] and chart['linefills'] == []
    assert http(route + '/line_events') == journal
    return {'linefill_versions': [5, 6], 'linefill_pause_resume': True, 'linefill_viewport': True,
            'linefill_replay_preview': True, 'linefill_boundary_cascade': True,
            'drawing_limits_checkpoint_and_ranges': True, 'drawing_inventory_checkpoint_and_replay': True}
