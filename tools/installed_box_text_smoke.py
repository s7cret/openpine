"""Box text through installed historical and replay workers."""
from dataclasses import asdict
import time
import uuid

from packages.contracts.contexts import MarketContext
from tools.box_text_fixtures import START, changes, final_updates, point
from tools.installed_lines_smoke import market_events
from tools.specification import ROOT


def verify(http, cli, work):
    fixtures = ROOT / 'conformance/box-text'
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

    def projected_point(version):
        return dict(point(version), chart_left=1, chart_right=2)

    def check_chart(route, version):
        expected = [final_updates(version)[-1], projected_point(version)]
        window = http(route + '/chart?start=1&limit=1')
        assert window['boxes'] == expected, window
        extension = http(route + '/chart?start=3&limit=1')
        assert extension['boxes'] == [projected_point(version)], extension

    def check_events(route, version):
        expected = [{'action': action, 'box': box} for action, group in
                    (('create', [START, point(version)]), ('update', [*changes(version), *final_updates(version)]))
                    for box in group]
        journal = http(route + '/box_events')
        assert journal['total'] == len(expected) and [row['event'] for row in journal['events']] == expected, journal
        return journal

    for version in (5, 6):
        file = fixtures / f'v{version}-text-attributes.pine'
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
        assert result['boxes'] == [final_updates(version)[-1], point(version)], result
        check_chart(route, version)
        historical_events = check_events(route, version)

    context = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
    replay = http('/api/replays', {'input_ref': compiled['input_ref'], 'contexts': [asdict(context)],
        'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120})['replay_id']
    route = f'/api/replays/{replay}'
    http(route + '/events', {'events': market_events(bars, context)})
    opened = http(route + '/advance', {'max_events': 3, 'checkpoint_every': 1})
    preview = changes(6, preview=True)[-1]
    assert opened['boxes'] == [START, point(6)] and opened['preview']['boxes'] == [preview, point(6)], opened
    assert http(route + '/box_events')['total'] == 2
    window = http(route + '/chart?start=0&limit=2')
    # The confirmed clock has one bar. Preview adds the open bar at 60000,
    # so the future anchor at 240000 projects four one-minute slots from zero.
    assert window['total'] == 1 and window['boxes'] == [START], window
    assert window['preview']['boxes'] == [preview, dict(projected_point(6), chart_right=4.)], window
    closed = http(route + '/advance', {'max_events': 1, 'checkpoint_every': 1})
    assert closed['boxes'] == [changes(6)[-1], point(6)] and closed['preview'] is None, closed
    final = http(route + '/advance', {'max_events': 4, 'checkpoint_every': 1})
    assert final['preview'] is None and final['boxes'] == [final_updates(6)[-1], point(6)], final
    check_chart(route, 6)
    assert check_events(route, 6) == historical_events
    return {'box_text_versions': [5, 6], 'box_text_numeric_format_versions': [6],
            'box_text_pause_resume': True, 'box_text_journal': True, 'box_text_replay_rollback': True}
