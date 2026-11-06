"""Vertical labels through installed historical and replay workers."""
from dataclasses import asdict
import time
import uuid

from packages.contracts.contexts import MarketContext
from tools.installed_lines_smoke import market_events
from tools.label_yloc_fixtures import BELOW, CLOSED, FINAL, POINT, START, labels
from tools.specification import ROOT


def replay_events(path, context):
    events = market_events(path, context)
    update = dict(events[2], kind='update', event_time=90000, revision=1, high=21., low=18., close=19.5, volume=.5)
    events[3] = dict(events[3], revision=2)
    events.insert(3, update)
    return [dict(event, sequence=index) for index, event in enumerate(events)]


def verify(http, cli, work):
    fixtures = ROOT / 'conformance/label-yloc'
    bars = fixtures / 'bars.json'
    snapshot = http('/api/snapshots', {'provider': 'upload', 'raw': bars.read_text(encoding='utf-8')})['sha256']

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
        expected = [dict(FINAL, chart_x=1), BELOW] + ([dict(POINT, chart_x=1)] if version >= 5 else [])
        actual = http(route + '/chart?start=1&limit=1')['labels']
        assert actual == expected, actual
        assert http(route + '/chart?start=2&limit=2')['labels'] == []

    def check_events(route, version):
        changed = dict(START, yloc='abovebar')
        updates = [changed, dict(changed, y=25.), CLOSED, dict(CLOSED, yloc='price'), FINAL]
        expected = [{'action': action, 'label': row}
                    for action, rows in (('create', [START, *labels(version)]), ('update', updates)) for row in rows]
        journal = http(route + '/label_events')
        assert journal['total'] == len(expected) and [row['event'] for row in journal['events']] == expected, journal
        return journal

    for version in (4, 5, 6):
        file = fixtures / f'v{version}-bar-anchored.pine'
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
        assert result['labels'] == [FINAL, *labels(version)], result
        check_chart(route, version)
        historical_events = check_events(route, version)

    context = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
    replay = http('/api/replays', {'input_ref': compiled['input_ref'], 'contexts': [asdict(context)],
        'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120})['replay_id']
    route = f'/api/replays/{replay}'
    http(route + '/events', {'events': replay_events(bars, context)})
    opened = http(route + '/advance', {'max_events': 3, 'checkpoint_every': 1})
    assert opened['labels'] == [START, *labels(6)], opened
    preview = opened['preview']
    assert preview['labels'] == [dict(CLOSED, y=24., yloc='belowbar'), *labels(6)], preview
    window = http(route + '/chart?start=0&limit=2')
    assert window['labels'] == [START], window
    assert [row.get('chart_x') for row in window['preview']['labels']] == [1, None, None, 1], window
    updated = http(route + '/advance', {'max_events': 1, 'checkpoint_every': 1})
    assert updated['labels'] == opened['labels'] and updated['preview']['revision'] == 1, updated
    assert updated['preview']['labels'] == [dict(CLOSED, y=24.5, yloc='belowbar'), *labels(6)], updated
    assert http(route + '/label_events')['total'] == 4
    closed = http(route + '/advance', {'max_events': 1, 'checkpoint_every': 1})
    assert closed['labels'] == [CLOSED, *labels(6)] and closed['preview'] is None, closed
    final = http(route + '/advance', {'max_events': 4, 'checkpoint_every': 1})
    assert final['preview'] is None and final['labels'] == [FINAL, *labels(6)], final
    check_chart(route, 6)
    assert check_events(route, 6) == historical_events
    return {'label_yloc_versions': [4, 5, 6], 'label_yloc_point_overload': [5, 6], 'label_yloc_pause_resume': True,
            'label_yloc_exact_bar_time': True, 'label_yloc_journal': True, 'label_yloc_replay_rollback': True}
