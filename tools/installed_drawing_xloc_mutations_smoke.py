"""Exercise coordinate mode changes through installed historical and replay workers."""
from dataclasses import asdict
import time
import uuid

from packages.contracts.contexts import MarketContext
from tools.drawing_xloc_mutations_fixtures import (
    FILL, FLOOR, INDEX_BOX, INDEX_LABEL, INDEX_LINE, TIME_BOX, TIME_LABEL, TIME_LINE, series,
)
from tools.installed_lines_smoke import market_events
from tools.specification import ROOT


def verify(http, cli, work):
    fixtures = ROOT / 'conformance/drawing-xloc-mutations'
    bars = fixtures / 'bars.json'
    snapshot = http('/api/snapshots', {'provider': 'upload', 'raw': bars.read_text(encoding='utf-8')})['sha256']
    projected_line = {**TIME_LINE, 'chart_x1': 1.5, 'chart_x2': 3}
    projected_label = {**TIME_LABEL, 'chart_x': 1.5}
    projected_box = {**TIME_BOX, 'chart_left': 1.5, 'chart_right': 3}

    def compile_fixture(version, extra=''):
        path = fixtures / f'v{version}-switch.pine'
        compiled = http('/api/compile', {'source': path.read_text(encoding='utf-8') + extra, 'source_name': path.name})
        assert compiled['status'] == 'compiled', compiled
        return compiled['input_ref']

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

    def check_chart(chart, version):
        lines = [projected_line] + ([FLOOR] if version >= 5 else [])
        assert chart['lines'] == lines and chart['labels'] == [projected_label], chart
        assert chart['boxes'] == [projected_box if version >= 6 else INDEX_BOX], chart
        if version >= 5:
            assert chart['linefills'] == [FILL], chart

    def check_journals(route, version):
        for kind, raw, updated in (('line', INDEX_LINE, TIME_LINE), ('label', INDEX_LABEL, TIME_LABEL),
                                   ('box', INDEX_BOX, TIME_BOX if version >= 6 else None)):
            initial_count = 2 if kind == 'line' and version >= 5 else 1
            events = http(f'{route}/{kind}_events')
            assert events['total'] == initial_count + bool(updated), events
            assert events['events'][0]['event'] == {'action': 'create', kind: raw}, events
            if updated:
                assert events['events'][-1]['event'] == {'action': 'update', kind: updated}, events

    refs = {}
    for version in (4, 5, 6):
        refs[version] = compile_fixture(version)
        launch = http('/api/launches', {'schema_version': 'openpine.launch-request.1', 'request_id': uuid.uuid4().hex,
            'input_ref': refs[version], 'primary': {'kind': 'snapshot', 'snapshot_id': snapshot}, 'contexts': [],
            'inputs': {}, 'timeout_seconds': 120, 'gap_policy': 'allow_with_metadata',
            'checkpoint_interval_bars': 2, 'pause_after_bars': 2})
        assert launch['status'] == 'ready', launch
        job = http(f"/api/launches/{launch['launch_id']}/start", {})['job_id']
        route = f'/api/jobs/{job}'
        finish(route, 'paused')
        http(route + '/resume', {})
        assert finish(route, 'succeeded')['resumed_from_bar'] == 2
        result = http(route + '/result')['result']
        assert result['lines'] == [TIME_LINE] + ([FLOOR] if version >= 5 else []), result
        assert result['labels'] == [TIME_LABEL] and result['boxes'] == [TIME_BOX if version >= 6 else INDEX_BOX], result
        assert tuple(output['values'] for output in result['outputs']) == series(version), result
        assert all(output['value_type']['base'] == 'int' for output in result['outputs']), result
        for start in (0, 1):
            check_chart(http(f'{route}/chart?start={start}&limit=3'), version)
        check_journals(route, version)

    def check_replay(input_ref):
        context = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
        replay = http('/api/replays', {'input_ref': input_ref, 'contexts': [asdict(context)],
            'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120})['replay_id']
        route = f'/api/replays/{replay}'
        http(route + '/events', {'events': market_events(bars, context)})
        prefix = http(route + '/advance', {'max_events': 3, 'checkpoint_every': 1})
        assert prefix['lines'] == [INDEX_LINE, FLOOR] and prefix['boxes'] == [INDEX_BOX], prefix
        assert prefix['preview']['lines'] == [{**TIME_LINE, 'x2': 240000}, FLOOR], prefix
        assert prefix['preview']['boxes'] == [{**TIME_BOX, 'right': 240000}], prefix
        assert http(route + '/line_events')['total'] == 2
        chart = http(route + '/chart?start=0&limit=3')
        assert chart['labels'] == [INDEX_LABEL] and chart['preview']['labels'] == [{**TIME_LABEL, 'chart_x': 2.5}], chart
        assert chart['preview']['linefills'] == [FILL], chart

        opened = http(route + '/advance', {'max_events': 2, 'checkpoint_every': 1})
        assert opened['lines'] == [TIME_LINE, FLOOR] and opened['labels'] == [TIME_LABEL], opened
        assert opened['preview']['lines'][0] == {**INDEX_LINE, 'x1': -1, 'xloc': 'bar_index', 'extend': 'none'}, opened
        assert opened['preview']['labels'] == [{**INDEX_LABEL, 'x': -1, 'xloc': 'bar_index'}], opened
        assert opened['preview']['boxes'] == [{**INDEX_BOX, 'left': -1, 'xloc': 'bar_index'}], opened
        assert opened['preview']['linefills'] == [FILL], opened
        check_journals(route, 6)
        final = http(route + '/advance', {'max_events': 3, 'checkpoint_every': 1})
        assert final['preview'] is None and final['lines'] == [TIME_LINE, FLOOR] and final['linefills'] == [FILL], final
        check_chart(http(route + '/chart?start=1&limit=3'), 6)
        check_journals(route, 6)

    check_replay(refs[6])
    # Exercise replay clock admission with point IR but only coordinate setters introducing timestamps.
    check_replay(compile_fixture(6, 'chart.point anchor = chart.point.from_index(0, 25)\nplot(anchor.index, "anchor")\n'))
    return {'xloc_mutation_versions': [4, 5, 6], 'xloc_mutation_pause_resume': True,
            'xloc_mutation_raw_getters': True, 'xloc_mutation_atomic_journal': True,
            'xloc_mutation_replay_rollback': True, 'xloc_mutation_point_ir_clock': True}
