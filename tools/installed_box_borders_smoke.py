"""Box borders through the installed worker, chart, journal and replay."""
from dataclasses import asdict
import time
import uuid

from packages.contracts.contexts import MarketContext
from tools.box_borders_fixtures import CLOSED, FINAL_INDEX, FINAL_TIME, POINTS, PREVIEW, START
from tools.installed_lines_smoke import market_events
from tools.specification import ROOT


def verify(http, cli, work):
    fixtures = ROOT / 'conformance/box-borders'
    bars = fixtures / 'bars.json'
    snapshot = http('/api/snapshots', {'provider': 'upload', 'raw': bars.read_text(encoding='utf-8')})['sha256']
    point_projection = {**POINTS, 'chart_left': 0, 'chart_right': 1}
    time_projection = {**FINAL_TIME, 'chart_left': 0, 'chart_right': 1}

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
        main = time_projection if version >= 6 else FINAL_INDEX
        body = http(route + '/chart?start=0&limit=2')['boxes']
        assert body == [main] + ([point_projection] if version >= 5 else []), body
        extension = http(route + '/chart?start=2&limit=2')['boxes']
        assert extension == [main], extension

    def check_events(route, version):
        rows = http(route + '/box_events')
        creates = [START] + ([POINTS] if version >= 5 else [])
        updates = [{**START, 'border_width': 3}, {**CLOSED, 'extend': 'none'}, CLOSED, FINAL_INDEX]
        if version >= 6:
            updates.append(FINAL_TIME)
        expected = [{'action': action, 'box': box} for action, group in (('create', creates), ('update', updates))
                    for box in group]
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
        boxes = [FINAL_TIME if version >= 6 else FINAL_INDEX] + ([POINTS] if version >= 5 else [])
        assert result['boxes'] == boxes, result
        check_chart(route, version)
        historical_events = check_events(route, version)

    context = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
    replay = http('/api/replays', {'input_ref': compiled['input_ref'], 'contexts': [asdict(context)],
        'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120})['replay_id']
    route = f'/api/replays/{replay}'
    http(route + '/events', {'events': market_events(bars, context)})
    opened = http(route + '/advance', {'max_events': 3, 'checkpoint_every': 1})
    assert opened['boxes'] == [START, POINTS] and opened['preview']['boxes'] == [PREVIEW, POINTS], opened
    assert http(route + '/box_events')['total'] == 2
    window = http(route + '/chart?start=0&limit=2')
    assert window['boxes'][0] == START and window['preview']['boxes'][0] == PREVIEW, window
    closed = http(route + '/advance', {'max_events': 1, 'checkpoint_every': 1})
    assert closed['boxes'] == [CLOSED, POINTS] and closed['preview'] is None, closed
    final = http(route + '/advance', {'max_events': 4, 'checkpoint_every': 1})
    assert final['preview'] is None and final['boxes'] == [FINAL_TIME, POINTS], final
    check_chart(route, 6)
    assert check_events(route, 6) == historical_events
    return {'box_border_versions': [4, 5, 6], 'box_border_point_overload': [5, 6],
            'box_border_pause_resume': True, 'box_border_extension_window': True,
            'box_border_journal': True, 'box_border_replay_rollback': True}
