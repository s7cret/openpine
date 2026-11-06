"""Exercise mixed drawing panes across historical workers and replay."""
from dataclasses import asdict
import time
import uuid

from packages.contracts.contexts import MarketContext
from tools.box_text_fixtures import OVERLAY_BOXES, OVERLAY_LABELS, overlay_updates
from tools.installed_lines_smoke import market_events
from tools.specification import ROOT


def verify(http, cli, work):
    fixtures = ROOT / 'conformance/box-text'
    bars = fixtures / 'bars.json'
    snapshot = http('/api/snapshots', {'provider': 'upload', 'raw': bars.read_text(encoding='utf-8')})['sha256']
    label_updates, box_updates = overlay_updates()
    final = dict(labels=[OVERLAY_LABELS[0], label_updates[-1]], boxes=[OVERLAY_BOXES[0], box_updates[-1]])

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

    def check_drawings(result, expected):
        assert {kind: result[kind] for kind in expected} == expected, result

    def check_journals(route):
        journals = {}
        for kind, initial, updates in (('label', OVERLAY_LABELS, label_updates), ('box', OVERLAY_BOXES, box_updates)):
            journal = http(f'{route}/{kind}_events')
            expected = [{'action': action, kind: row}
                        for action, records in (('create', initial), ('update', updates)) for row in records]
            assert journal['total'] == len(expected) and [row['event'] for row in journal['events']] == expected, journal
            journals[kind] = journal
        return journals

    for version in (5, 6):
        file = fixtures / f'v{version}-force-overlay-mixed.pine'
        compiled = http('/api/compile', {'source': file.read_text(encoding='utf-8'), 'source_name': file.name})
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
        assert result['declaration']['overlay'] is False
        check_drawings(result, final)
        check_drawings(http(route + '/chart?start=0&limit=4'), final)
        historical_journals = check_journals(route)

    context = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
    replay = http('/api/replays', {'input_ref': compiled['input_ref'], 'contexts': [asdict(context)],
        'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120})['replay_id']
    route = f'/api/replays/{replay}'
    http(route + '/events', {'events': market_events(bars, context)})
    opened = http(route + '/advance', {'max_events': 3, 'checkpoint_every': 1})
    initial = dict(labels=list(OVERLAY_LABELS), boxes=list(OVERLAY_BOXES))
    open_labels, open_boxes = overlay_updates(preview=True)
    preview = dict(labels=[OVERLAY_LABELS[0], open_labels[0]], boxes=[OVERLAY_BOXES[0], open_boxes[0]])
    check_drawings(opened, initial)
    check_drawings(opened['preview'], preview)
    assert http(route + '/label_events')['total'] == http(route + '/box_events')['total'] == 2
    window = http(route + '/chart?start=0&limit=4')
    assert window['label_overlay'] is False and window['box_overlay'] is False
    check_drawings(window, dict(initial, labels=[OVERLAY_LABELS[0]]))
    check_drawings(window['preview'], preview)
    closed = http(route + '/advance', {'max_events': 1, 'checkpoint_every': 1})
    assert closed['preview'] is None
    check_drawings(closed, dict(labels=[OVERLAY_LABELS[0], label_updates[0]], boxes=[OVERLAY_BOXES[0], box_updates[0]]))
    end = http(route + '/advance', {'max_events': 4, 'checkpoint_every': 1})
    check_drawings(end, final)
    check_drawings(http(route + '/chart?start=0&limit=4'), final)
    assert check_journals(route) == historical_journals
    return {'drawing_overlay_versions': [5, 6], 'drawing_overlay_pause_resume': True,
            'drawing_overlay_journals': True, 'drawing_overlay_preview_rollback': True}
