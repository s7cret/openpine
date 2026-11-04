"""Installed worker, chart and replay proof for timestamp coordinates."""
from dataclasses import asdict
import time
import uuid

from packages.contracts.contexts import MarketContext
from tools.drawing_xloc_fixtures import BOX, FILL, FLOOR, LABEL, LINE
from tools.installed_lines_smoke import market_events
from tools.specification import ROOT


def verify(http, cli, work):
    fixtures = ROOT / 'conformance/drawing-xloc'
    bars = fixtures / 'bars.json'
    snapshot = http('/api/snapshots', {'provider': 'upload', 'raw': bars.read_text(encoding='utf-8')})['sha256']
    label = {**LABEL, 'chart_x': 1.5}
    line = {**LINE, 'chart_x1': 1, 'chart_x2': 3}
    box = {**BOX, 'chart_left': 1.5, 'chart_right': 4.}

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

    for version in (4, 5, 6):
        input_ref = compile_fixture(f'v{version}-drawings.pine')
        launch = http('/api/launches', {'schema_version': 'openpine.launch-request.1', 'request_id': uuid.uuid4().hex,
            'input_ref': input_ref, 'primary': {'kind': 'snapshot', 'snapshot_id': snapshot}, 'contexts': [],
            'inputs': {}, 'timeout_seconds': 120, 'gap_policy': 'allow_with_metadata', 'checkpoint_interval_bars': 2, 'pause_after_bars': 2})
        assert launch['status'] == 'ready', launch
        job = http(f"/api/launches/{launch['launch_id']}/start", {})['job_id']
        finish(job, 'paused')
        route = f'/api/jobs/{job}'
        http(route + '/resume', {})
        assert finish(job, 'succeeded')['resumed_from_bar'] == 2
        for endpoint in ('result', 'summary'):
            result = http(f'{route}/{endpoint}')['result']
            assert result['labels'] == [LABEL] and result['lines'] == [LINE] and result['boxes'] == [BOX], result
        for start in (0, 1):
            chart = http(f'{route}/chart?start={start}&limit=3')
            assert chart['labels'] == [label] and chart['lines'] == [line] and chart['boxes'] == [box], chart
        for kind, expected in (('label', LABEL), ('line', LINE), ('box', BOX)):
            events = http(f'{route}/{kind}_events')['events']
            assert events[-1]['event'][kind] == expected, events

    context = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
    replay = http('/api/replays', {'input_ref': compile_fixture('v6-mixed.pine'), 'contexts': [asdict(context)],
        'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120})['replay_id']
    route = f'/api/replays/{replay}'
    http(route + '/events', {'events': market_events(bars, context)})
    prefix = http(route + '/advance', {'max_events': 3, 'checkpoint_every': 1})
    assert prefix['labels'][0]['x'] == 0 and prefix['preview']['labels'][0]['x'] == 150000
    chart = http(route + '/chart?start=0&limit=3')
    assert chart['labels'][0]['chart_x'] == 0 and chart['preview']['labels'][0]['chart_x'] == 2.5, chart
    assert chart['preview']['linefills'][0]['color'] == '#FF000080'
    final = http(route + '/advance', {'max_events': 5, 'checkpoint_every': 1})
    assert final['labels'] == [LABEL] and final['lines'] == [LINE, FLOOR] and final['linefills'] == [FILL]
    chart = http(route + '/chart?start=1&limit=3')
    assert chart['labels'] == [label] and chart['lines'] == [line, FLOOR]
    assert chart['linefills'] == [FILL] and chart['boxes'] == [box] and chart['preview'] is None
    return {'xloc_versions': [4, 5, 6], 'xloc_pause_resume': True, 'xloc_page_stable': True,
            'xloc_raw_journal': True, 'xloc_mixed_channel': True, 'xloc_preview_clock': True}
