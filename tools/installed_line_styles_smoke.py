"""Styled lines through the installed worker, chart, journal and replay."""
from dataclasses import asdict
import time
import uuid

from packages.contracts.contexts import MarketContext
from tools.installed_lines_smoke import market_events
from tools.line_styles_fixtures import CLOSED, FILL, FINAL, FLOOR, POINTS, PREVIEW, PRICES, START
from tools.specification import ROOT


def verify(http, cli, work):
    fixtures = ROOT / 'conformance/line-styles'
    bars = fixtures / 'bars.json'
    snapshot = http('/api/snapshots', {'provider': 'upload', 'raw': bars.read_text(encoding='utf-8')})['sha256']
    final_projected = {**FINAL, 'chart_x1': 1.5, 'chart_x2': 3}
    points_projected = {**POINTS, 'chart_x1': 0, 'chart_x2': 3}

    def finish(route, wanted):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = http(route)
            if state['status'] == wanted:
                return state
            if state['status'] in ('failed', 'cancelled', 'timed_out', 'succeeded'):
                raise AssertionError(state)
            time.sleep(.1)
        raise TimeoutError(route)

    def expected_lines(first, version, *, projected=False):
        return [first, FLOOR] + ([points_projected if projected else POINTS] if version >= 5 else [])

    def chart(route, version):
        for start in (0, 1):
            state = http(f'{route}/chart?start={start}&limit=3')
            assert state['lines'] == expected_lines(final_projected, version, projected=True), state
            if version >= 5:
                assert state['linefills'] == [FILL], state

    def check_events(route, version):
        response = http(route + '/line_events')
        creates = [{'action': 'create', 'line': line} for line in expected_lines(START, version)]
        updates = [{'action': 'update', 'line': line} for line in (
            {**START, 'color': None}, {**START, 'color': None, 'style': 'arrow_left'}, CLOSED,
            {**FINAL, 'extend': 'none'}, FINAL)]
        expected = creates + updates
        assert response['total'] == len(expected), response
        assert [row['event'] for row in response['events']] == expected, response
        return response

    for version in (4, 5, 6):
        file = fixtures / f'v{version}-appearance.pine'
        compiled = http('/api/compile', {'source': file.read_text(encoding='utf-8'), 'source_name': file.name})
        assert compiled['status'] == 'compiled', compiled
        launch = http('/api/launches', {'schema_version': 'openpine.launch-request.1', 'request_id': uuid.uuid4().hex,
            'input_ref': compiled['input_ref'], 'primary': {'kind': 'snapshot', 'snapshot_id': snapshot},
            'contexts': [], 'inputs': {}, 'timeout_seconds': 120, 'gap_policy': 'allow_with_metadata',
            'checkpoint_interval_bars': 2, 'pause_after_bars': 2})
        assert launch['status'] == 'ready', launch
        job = http(f"/api/launches/{launch['launch_id']}/start", {})['job_id']
        route = f'/api/jobs/{job}'
        finish(route, 'paused')
        http(route + '/resume', {})
        assert finish(route, 'succeeded')['resumed_from_bar'] == 2
        result = http(route + '/result')['result']
        assert result['lines'] == expected_lines(FINAL, version) and result['outputs'][0]['values'] == PRICES, result
        chart(route, version)
        historical_events = check_events(route, version)

    context = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
    replay = http('/api/replays', {'input_ref': compiled['input_ref'], 'contexts': [asdict(context)],
        'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120})['replay_id']
    route = f'/api/replays/{replay}'
    http(route + '/events', {'events': market_events(bars, context)})
    opened = http(route + '/advance', {'max_events': 3, 'checkpoint_every': 1})
    assert opened['lines'] == expected_lines(START, 6) and opened['preview']['lines'] == expected_lines(PREVIEW, 6), opened
    assert http(route + '/line_events')['total'] == 3
    window = http(route + '/chart?start=0&limit=3')
    assert window['lines'][0] == START and window['preview']['lines'][0] == PREVIEW, window
    assert window['linefills'] == window['preview']['linefills'] == [FILL], window
    closed = http(route + '/advance', {'max_events': 1, 'checkpoint_every': 1})
    assert closed['lines'] == expected_lines(CLOSED, 6) and closed['preview'] is None, closed
    final = http(route + '/advance', {'max_events': 4, 'checkpoint_every': 1})
    assert final['preview'] is None and final['lines'] == expected_lines(FINAL, 6), final
    chart(route, 6)
    assert check_events(route, 6) == historical_events
    return {'line_style_versions': [4, 5, 6], 'line_style_point_overload': [5, 6],
            'line_style_pause_resume': True, 'line_style_chart_and_price': True,
            'line_style_journal': True, 'line_style_replay_rollback': True}
