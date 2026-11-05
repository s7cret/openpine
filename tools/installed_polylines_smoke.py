"""Exercise raw and projected polylines through the installed worker and replay."""
from dataclasses import asdict
from math import isclose
import time
import uuid

from packages.contracts.contexts import MarketContext
from tools.installed_lines_smoke import market_events
from tools.polylines_fixtures import FIRST, POLYGON, SECOND, SERIES
from tools.specification import ROOT


def verify(http, cli, work):
    fixtures = ROOT / 'conformance/polylines'
    bars = fixtures / 'bars.json'
    snapshot = http('/api/snapshots', {'provider': 'upload', 'raw': bars.read_text(encoding='utf-8')})['sha256']
    projected = {**POLYGON, 'chart_vertices': [[0, 12.], [1.5, 42.], [3, 15.]]}

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

    refs = {}
    for version in (5, 6):
        source = fixtures / f'v{version}-drawings.pine'
        compiled = http('/api/compile', {'source': source.read_text(encoding='utf-8'), 'source_name': source.name})
        assert compiled['status'] == 'compiled', compiled
        refs[version] = compiled['input_ref']
        launch = http('/api/launches', {'schema_version': 'openpine.launch-request.1', 'request_id': uuid.uuid4().hex,
            'input_ref': refs[version], 'primary': {'kind': 'snapshot', 'snapshot_id': snapshot},
            'contexts': [], 'inputs': {}, 'timeout_seconds': 120, 'gap_policy': 'allow_with_metadata',
            'checkpoint_interval_bars': 2, 'pause_after_bars': 2})
        assert launch['status'] == 'ready', launch
        job = http(f"/api/launches/{launch['launch_id']}/start", {})['job_id']
        finish(job, 'paused')
        route = f'/api/jobs/{job}'
        http(route + '/resume', {})
        assert finish(job, 'succeeded')['resumed_from_bar'] == 2
        result = http(route + '/result')['result']
        assert result['polylines'] == [FIRST, POLYGON] and [row['values'] for row in result['outputs']] == list(SERIES)
        for start in (0, 1):
            assert http(f'{route}/chart?start={start}&limit=2')['polylines'] == [FIRST, projected]
        journal = http(route + '/polyline_events?limit=2')
        assert journal['total'] == 4 and journal['next'] == 2
        assert [row['event']['polyline'] for row in journal['events']] == [FIRST, SECOND]
        tail = http(route + '/polyline_events?start=2&limit=2')
        assert [row['event'] for row in tail['events']] == [
            {'action': 'delete', 'id': 2, 'reason': 'explicit'}, {'action': 'create', 'polyline': POLYGON}]

    context = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
    replay = http('/api/replays', {'input_ref': refs[6], 'contexts': [asdict(context)],
        'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120})['replay_id']
    route = f'/api/replays/{replay}'
    http(route + '/events', {'events': market_events(bars, context)})
    prefix = http(route + '/advance', {'max_events': 3, 'checkpoint_every': 1})
    preview = {**SECOND, 'vertices': [[0, 29.], [3, 45.]]}
    assert prefix['polylines'] == [FIRST] and prefix['preview']['polylines'] == [FIRST, preview]
    assert http(route + '/polyline_events')['total'] == 1
    confirmed = http(route + '/advance', {'max_events': 1, 'checkpoint_every': 1})
    assert confirmed['polylines'] == [FIRST, SECOND] and confirmed['preview'] is None
    opened = http(route + '/advance', {'max_events': 1, 'checkpoint_every': 1})
    assert opened['polylines'] == [FIRST, SECOND] and opened['preview']['polylines'] == [FIRST, POLYGON]
    preview_chart = http(route + '/chart?start=1&limit=2')
    vertices = preview_chart['preview']['polylines'][1]['chart_vertices']
    assert len(vertices) == 3 and vertices[:2] == [[0, 12.], [1.5, 42.]], vertices
    x, price = vertices[2]
    assert isclose(x, 7 / 3, rel_tol=0, abs_tol=1e-12) and price == 15., vertices
    final = http(route + '/advance', {'max_events': 3, 'checkpoint_every': 1})
    assert final['polylines'] == [FIRST, POLYGON]
    assert http(route + '/chart?start=1&limit=2')['polylines'] == [FIRST, projected]
    assert http(route + '/polyline_events?start=2&limit=2') == tail
    return {'polyline_versions': [5, 6], 'polyline_pause_resume': True, 'polyline_snapshots': True,
            'polyline_page_stable': True, 'polyline_journal': True, 'polyline_replay_preview': True}
