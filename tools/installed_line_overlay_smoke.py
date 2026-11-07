"""Verify per-object line panes through installed job and replay workers."""
from dataclasses import asdict
import time
import uuid

from packages.contracts.contexts import MarketContext
from tools.installed_lines_smoke import market_events
from tools.line_styles_fixtures import OVERLAY_FILLS, OVERLAY_LINES, OVERLAY_POLYLINES
from tools.specification import ROOT


def verify(http, cli, work):
    fixtures = ROOT / 'conformance/line-styles'
    bars = fixtures / 'bars.json'
    snapshot = http('/api/snapshots', {'provider': 'upload', 'raw': bars.read_text(encoding='utf-8')})['sha256']

    def wait_job(route, wanted):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = http(route)
            if state['status'] == wanted:
                return state
            if state['status'] in ('failed', 'cancelled', 'timed_out', 'succeeded'):
                raise AssertionError(state)
            time.sleep(.1)
        raise TimeoutError(route)

    def check(state, *, width=3, color='#FF0000FF', path=True):
        lines = [{**OVERLAY_LINES[0], 'width': width}, *OVERLAY_LINES[1:]]
        fills = [*OVERLAY_FILLS[:2], {**OVERLAY_FILLS[2], 'color': color}]
        polylines = OVERLAY_POLYLINES if path else OVERLAY_POLYLINES[:1]
        assert state['lines'] == lines and state['linefills'] == fills and state['polylines'] == polylines, state

    def journals(route):
        line = http(route + '/line_events')
        polyline = http(route + '/polyline_events')
        assert line['total'] == 5 and polyline['total'] == 2
        assert [row['event']['line'] for row in line['events']] == [*OVERLAY_LINES, {**OVERLAY_LINES[0], 'width': 3}]
        assert [row['event']['polyline'] for row in polyline['events']] == OVERLAY_POLYLINES
        return line, polyline

    for version in (5, 6):
        source = fixtures / f'v{version}-force-overlay-mixed.pine'
        compiled = http('/api/compile', {'source': source.read_text(encoding='utf-8'), 'source_name': source.name})
        assert compiled['status'] == 'compiled', compiled
        launch = http('/api/launches', {'schema_version': 'openpine.launch-request.1', 'request_id': uuid.uuid4().hex,
            'input_ref': compiled['input_ref'], 'primary': {'kind': 'snapshot', 'snapshot_id': snapshot},
            'contexts': [], 'inputs': {}, 'timeout_seconds': 120, 'gap_policy': 'allow_with_metadata',
            'checkpoint_interval_bars': 2, 'pause_after_bars': 2})
        assert launch['status'] == 'ready', launch
        job = http(f"/api/launches/{launch['launch_id']}/start", {})['job_id']
        route = f'/api/jobs/{job}'
        wait_job(route, 'paused')
        http(route + '/resume', {})
        assert wait_job(route, 'succeeded')['resumed_from_bar'] == 2
        result = http(route + '/result')['result']
        assert result['declaration']['overlay'] is False and result['outputs'][0]['values'] == [30.] * 4
        check(result)
        check(http(route + '/chart?start=0&limit=4'))
        historical_journals = journals(route)

    context = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
    replay = http('/api/replays', {'input_ref': compiled['input_ref'], 'contexts': [asdict(context)],
        'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120})['replay_id']
    route = f'/api/replays/{replay}'
    http(route + '/events', {'events': market_events(bars, context)})
    opened = http(route + '/advance', {'max_events': 3, 'checkpoint_every': 1})
    check(opened, width=2, path=False)
    check(opened['preview'], width=4, color='#00FF00FF')
    assert http(route + '/line_events')['total'] == 4 and http(route + '/polyline_events')['total'] == 1
    window = http(route + '/chart?start=0&limit=4')
    assert window['line_overlay'] is False and window['polyline_overlay'] is False
    check(window, width=2, path=False)
    check(window['preview'], width=4, color='#00FF00FF')
    closed = http(route + '/advance', {'max_events': 1, 'checkpoint_every': 1})
    assert closed['preview'] is None
    check(closed)
    check(http(route + '/advance', {'max_events': 4, 'checkpoint_every': 1}))
    check(http(route + '/chart?start=0&limit=4'))
    assert journals(route) == historical_journals
    return {'line_overlay_versions': [5, 6], 'line_overlay_pause_resume': True,
            'line_overlay_journals': True, 'line_overlay_preview_rollback': True, 'line_overlay_fill_identity': True}
