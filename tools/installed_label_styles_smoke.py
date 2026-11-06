"""Styled labels through the installed worker, chart, journal and replay."""
from dataclasses import asdict
import time
import uuid

from packages.contracts.contexts import MarketContext
from tools.installed_lines_smoke import market_events
from tools.label_styles_fixtures import CLOSED, POINT, PREVIEW, START, changed_size, final_label
from tools.specification import ROOT


def verify(http, cli, work):
    fixtures = ROOT / 'conformance/label-styles'
    bars = fixtures / 'bars.json'
    snapshot = http('/api/snapshots', {'provider': 'upload', 'raw': bars.read_text(encoding='utf-8')})['sha256']
    point_projection = {**POINT, 'chart_x': 1.5}

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

    def check_chart(route, version):
        main = {**final_label(version), 'chart_x': 1.5}
        body = http(route + '/chart?start=1&limit=1')['labels']
        assert body == [main] + ([point_projection] if version >= 5 else []), body
        assert http(route + '/chart?start=2&limit=2')['labels'] == []

    def check_events(route, version):
        rows = http(route + '/label_events')
        creates = [START] + ([POINT] if version >= 5 else [])
        closed = changed_size(CLOSED, version)
        recolored = {**START, 'color': CLOSED['color']}
        restyled = {**recolored, 'style': CLOSED['style']}
        retextcolored = {**restyled, 'textcolor': CLOSED['textcolor']}
        renamed = {**closed, 'text': 'moved'}
        moved = {**renamed, 'x': 1, 'y': 12.}
        updates = [recolored, restyled, retextcolored, closed, renamed, moved, final_label(version)]
        expected = [{'action': action, 'label': label} for action, group in (('create', creates), ('update', updates))
                    for label in group]
        assert rows['total'] == len(expected) and [row['event'] for row in rows['events']] == expected, rows
        return rows

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
        assert result['labels'] == [final_label(version)] + ([POINT] if version >= 5 else []), result
        check_chart(route, version)
        historical_events = check_events(route, version)

    context = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
    replay = http('/api/replays', {'input_ref': compiled['input_ref'], 'contexts': [asdict(context)],
        'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120})['replay_id']
    route = f'/api/replays/{replay}'
    http(route + '/events', {'events': market_events(bars, context)})
    opened = http(route + '/advance', {'max_events': 3, 'checkpoint_every': 1})
    preview = changed_size(PREVIEW, 6, preview=True)
    assert opened['labels'] == [START, POINT] and opened['preview']['labels'] == [preview, POINT], opened
    assert http(route + '/label_events')['total'] == 2
    window = http(route + '/chart?start=0&limit=2')
    assert window['labels'][0] == START and window['preview']['labels'][0] == preview, window
    closed = http(route + '/advance', {'max_events': 1, 'checkpoint_every': 1})
    assert closed['labels'] == [changed_size(CLOSED, 6), POINT] and closed['preview'] is None, closed
    final = http(route + '/advance', {'max_events': 4, 'checkpoint_every': 1})
    assert final['preview'] is None and final['labels'] == [final_label(6), POINT], final
    check_chart(route, 6)
    assert check_events(route, 6) == historical_events
    return {'label_style_versions': [4, 5, 6], 'label_style_point_overload': [5, 6],
            'label_style_integer_size_versions': [6], 'label_style_pause_resume': True,
            'label_style_time_window': True, 'label_style_journal': True, 'label_style_replay_rollback': True}
