"""Exercise point snapshots in the installed historical worker and replay."""
from dataclasses import asdict
import time
import uuid

from packages.contracts.contexts import MarketContext
from tools.chart_points_fixtures import BOX, FILL, FIRST, LABEL, SECOND, SERIES
from tools.installed_lines_smoke import market_events
from tools.specification import ROOT


def verify(http, cli, work):
    fixtures = ROOT / 'conformance/chart-points'
    bars = fixtures / 'bars.json'
    snapshot = http('/api/snapshots', {'provider': 'upload', 'raw': bars.read_text(encoding='utf-8')})['sha256']
    projected_label = {**LABEL, 'chart_x': 1.5}
    projected_second = {**SECOND, 'chart_x1': 1, 'chart_x2': 3}
    projected_box = {**BOX, 'chart_left': 1.5, 'chart_right': 4.}

    def compile_fixture(version):
        source = fixtures / f'v{version}-drawings.pine'
        compiled = http('/api/compile', {'source': source.read_text(encoding='utf-8'), 'source_name': source.name})
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

    def verify_chart(chart):
        assert chart['lines'] == [FIRST, projected_second] and chart['labels'] == [projected_label], chart
        assert chart['boxes'] == [projected_box] and chart['linefills'] == [FILL], chart

    references = {}
    for version in (5, 6):
        references[version] = compile_fixture(version)
        launch = http('/api/launches', {'schema_version': 'openpine.launch-request.1', 'request_id': uuid.uuid4().hex,
            'input_ref': references[version], 'primary': {'kind': 'snapshot', 'snapshot_id': snapshot},
            'contexts': [], 'inputs': {}, 'timeout_seconds': 120, 'gap_policy': 'allow_with_metadata',
            'checkpoint_interval_bars': 2, 'pause_after_bars': 2})
        assert launch['status'] == 'ready', launch
        job = http(f"/api/launches/{launch['launch_id']}/start", {})['job_id']
        finish(job, 'paused')
        route = f'/api/jobs/{job}'
        http(route + '/resume', {})
        assert finish(job, 'succeeded')['resumed_from_bar'] == 2
        result = http(route + '/result')['result']
        assert result['lines'] == [FIRST, SECOND] and result['labels'] == [LABEL] and result['boxes'] == [BOX], result
        assert [output['values'] for output in result['outputs']] == list(SERIES), result
        for start in (0, 1):
            verify_chart(http(f'{route}/chart?start={start}&limit=3'))
        events = http(route + '/line_events')['events']
        assert [row['event']['line']['y1'] for row in events] == [10., 16., 30., 30.], events
        for kind, expected in (('label', LABEL), ('box', BOX)):
            assert http(f'{route}/{kind}_events')['events'][-1]['event'][kind] == expected

    context = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
    replay = http('/api/replays', {'input_ref': references[6], 'contexts': [asdict(context)],
        'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120})['replay_id']
    route = f'/api/replays/{replay}'
    http(route + '/events', {'events': market_events(bars, context)})
    prefix = http(route + '/advance', {'max_events': 3, 'checkpoint_every': 1})
    assert prefix['lines'][0] == prefix['preview']['lines'][0] == FIRST
    assert prefix['labels'][0]['y'] == 16. and prefix['preview']['labels'][0]['y'] == 29.
    chart = http(route + '/chart?start=0&limit=3')
    assert chart['labels'][0]['chart_x'] == 0 and chart['preview']['labels'][0]['chart_x'] == 2.5, chart
    final = http(route + '/advance', {'max_events': 5, 'checkpoint_every': 1})
    assert final['labels'] == [LABEL] and final['lines'] == [FIRST, SECOND] and final['preview'] is None
    verify_chart(http(route + '/chart?start=1&limit=3'))
    return {'points_versions': [5, 6], 'points_pause_resume': True, 'points_snapshot_copy': True,
            'points_page_stable': True, 'points_raw_journal': True, 'points_replay_rollback': True}
