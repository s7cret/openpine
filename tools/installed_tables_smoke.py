"""Installed pane table state through historical and replay workers."""
from dataclasses import asdict
import time
import uuid

from packages.contracts.contexts import MarketContext
from packages.contracts.drawings import KINDS
from tools.installed_lines_smoke import market_events
from tools.specification import ROOT
from tools.tables_fixtures import ZONE, drawing_methods_table, layout_table, merge_table, status_table, styled_table


def verify(http, cli, work):
    fixtures = ROOT / 'conformance/tables'
    bars = fixtures / 'bars.json'
    snapshot = http('/api/snapshots', {'provider': 'upload', 'raw': bars.read_text(encoding='utf-8')})['sha256']
    expected = [status_table()]

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

    def execute(path):
        compiled = http('/api/compile', {'source': path.read_text(encoding='utf-8'), 'source_name': path.name})
        assert compiled['status'] == 'compiled', compiled
        launch = http('/api/launches', {'schema_version': 'openpine.launch-request.1', 'request_id': uuid.uuid4().hex,
            'input_ref': compiled['input_ref'], 'primary': {'kind': 'snapshot', 'snapshot_id': snapshot}, 'contexts': [],
            'inputs': {}, 'timeout_seconds': 120, 'gap_policy': 'fail', 'checkpoint_interval_bars': 2, 'pause_after_bars': 2})
        assert launch['status'] == 'ready', launch
        job = http(f"/api/launches/{launch['launch_id']}/start", {})['job_id']
        finish(job, 'paused')
        http(f'/api/jobs/{job}/resume', {})
        assert finish(job, 'succeeded')['resumed_from_bar'] == 2
        return compiled, f'/api/jobs/{job}'

    def verify_replay(compiled, overlay, confirmed, preview, final_tables, boxes=None):
        context = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
        replay = http('/api/replays', {'input_ref': compiled['input_ref'], 'contexts': [asdict(context)],
            'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120})['replay_id']
        route = f'/api/replays/{replay}'
        http(route + '/events', {'events': market_events(bars, context)})
        prefix = http(route + '/advance', {'max_events': 3, 'checkpoint_every': 1})
        assert prefix['table_overlay'] is overlay
        assert prefix['tables'] == confirmed and prefix['preview']['tables'] == preview, prefix
        chart = http(route + '/chart?start=0&limit=1')
        assert chart['tables'] == confirmed and chart['preview']['tables'] == preview, chart
        final = http(route + '/advance', {'max_events': 20, 'checkpoint_every': 2})
        assert final['preview'] is None and final['tables'] == final_tables, final
        if boxes is not None:
            assert final['boxes'] == boxes, final
        return route

    for version in (4, 5, 6):
        compiled, route = execute(fixtures / f'v{version}-status.pine')
        for name in ('result', 'summary'):
            result = http(f'{route}/{name}')['result']
            assert result['tables'] == expected and result['boxes'] == [ZONE], result
        for start in (0, 3):
            chart = http(f'{route}/chart?start={start}&limit=1')
            assert chart['tables'] == expected, chart
        if version == 6:
            verify_replay(compiled, True, [status_table('10', '#AA2222FF')],
                          [status_table('19', '#AA2222FF')], expected, [ZONE])
    for version in (4, 5, 6):
        compiled, route = execute(fixtures / f'v{version}-style.pine')
        expected_style = [styled_table(version, 'final')]
        for name in ('result', 'summary'):
            result = http(f'{route}/{name}')['result']
            assert result['tables'] == expected_style and result['declaration']['overlay'] is False, result
        assert http(f'{route}/chart?start=3&limit=1')['tables'] == expected_style
        if version == 6:
            verify_replay(compiled, False, [styled_table(6, 'first')], [styled_table(6, 'preview')], expected_style)
    for version in (4, 5, 6):
        compiled, route = execute(fixtures / f'v{version}-cell-layout.pine')
        expected_layout = [layout_table(version, 'final')]
        for name in ('result', 'summary'):
            result = http(f'{route}/{name}')['result']
            assert result['tables'] == expected_layout and result['declaration']['overlay'] is False, result
        for start in (0, 3):
            chart = http(f'{route}/chart?start={start}&limit=1')
            assert chart['tables'] == expected_layout, chart
        if version == 6:
            verify_replay(compiled, False, [layout_table(6, 'first')], [layout_table(6, 'preview')], expected_layout)
    for version in (5, 6):
        compiled, route = execute(fixtures / f'v{version}-merge.pine')
        expected_merge = [merge_table('final')]
        for name in ('result', 'summary'):
            result = http(f'{route}/{name}')['result']
            assert result['tables'] == expected_merge and result['declaration']['overlay'] is False, result
        for start in (0, 3):
            chart = http(f'{route}/chart?start={start}&limit=1')
            assert chart['tables'] == expected_merge, chart
        if version == 6:
            verify_replay(compiled, False, [merge_table('first')], [merge_table('preview')], expected_merge)
    _, qualified_route = execute(fixtures / 'v6-drawing-namespace.pine')
    compiled, instance_route = execute(fixtures / 'v6-drawing-methods.pine')
    for name in ('result', 'summary', 'chart?start=0&limit=4'):
        qualified, instance = [http(f'{route}/{name}') for route in (qualified_route, instance_route)]
        if name in ('result', 'summary'):
            qualified, instance = qualified['result'], instance['result']
        assert {kind: qualified[kind] for kind in KINDS} == {kind: instance[kind] for kind in KINDS}
        assert instance['tables'] == [drawing_methods_table()], instance
    spare = dict(id=2, position='bottom_left', columns=1, rows=1, bgcolor=None, cells=[])
    replay_route = verify_replay(compiled, True, [drawing_methods_table('Header', transient=True), spare],
                                 [drawing_methods_table('Open')], [drawing_methods_table()])
    for kind in ('label', 'line', 'box', 'polyline'):
        journal = http(f'{qualified_route}/{kind}_events')
        assert journal['total'] > 0 and http(f'{instance_route}/{kind}_events') == journal
        assert http(f'{replay_route}/{kind}_events') == journal
    return {'table_versions': [4, 5, 6], 'table_pause_resume': True, 'table_window_independent': True,
            'table_replay_preview': True, 'table_mixed_drawings': True,
            'table_style_checkpoint_and_replay': True, 'table_cell_layout_checkpoint_and_replay': True,
            'table_merge_versions': [5, 6], 'table_merge_checkpoint_and_replay': True,
            'drawing_methods_checkpoint_and_replay': True, 'drawing_methods_journal_parity': True}
