"""Acceptance shared by installed-wheel verification and the source-tree gate."""
import json
import time
import uuid
from dataclasses import asdict
from packages.compiler.semantic.specification import ROOT
from packages.contracts.alerts import CONDITION_TEMPLATE_PROFILE
from packages.contracts.calendar import utc_datetime
from packages.contracts.contexts import MarketContext
from packages.contracts.live import RECEIVED_PROFILE
from packages.contracts.wire import canonical
from tools import date_inputs_fixtures as dates
from tools import execution_time_fixtures as execution_time
from tools import heikin_ashi_fixtures as heikin_ashi
from tools import trading_sessions_fixtures as sessions
from tools import higher_timeframes_fixtures as higher
from tools import time_offsets_fixtures as offsets
from tools import calendar_zones_fixtures as calendar_zones
from tools import chart_clock_fixtures as clock
from tools import session_flags_fixtures as session_flags
from tools import visible_range_fixtures as visible_range


def verify(http,cli,work):
    example=ROOT/'examples/inputs';expected={}
    for version in range(1,7):
        source=example/f'v{version}.pine'
        expected[version]=cli('run','--source',source,'--bars',example/'bars.json','--inputs',example/'overrides.json')['result']
        if expected[version]['settings']['values']['input:length']!=4:raise AssertionError('Installed CLI ignored inputs.')
    snapshot=http('/api/snapshots',{'provider':'upload','raw':(example/'bars.json').read_text(encoding='utf-8')})['sha256']
    exact=http('/api/compile',{'source':'//@version=6\nindicator("exact export")\nn=input.int(9007199254740993)\nplot(n)','source_name':'int64.pine'})
    if http('/api/artifacts/'+exact['artifact_ref'])!=exact['artifact']:raise AssertionError('Installed artifact download changed exact integer content.')
    for version in (1,6):
        source=example/f'v{version}.pine';compiled=http('/api/compile',{'source':source.read_text(encoding='utf-8'),'source_name':source.as_posix()})
        if len(compiled['inputs']['definitions'])!=5:raise AssertionError('Installed input metadata missing.')
        preset=http('/api/presets',{'input_ref':compiled['input_ref'],'name':f'v{version}','inputs':{'length':4,'scale':2,'src':'open'},'expected_revision':None})
        group=http('/api/experiments',{'name':f'Installed v{version}','input_ref':compiled['input_ref'],'snapshot_id':snapshot,'timeout_seconds':60,'inputs':preset['inputs'],'grid':{'scale':[2,3]}})
        identifier=group['experiment_id'];deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            group=http('/api/experiments/'+identifier)
            if group['status']!='running':break
            time.sleep(.04)
        if group['status']!='succeeded':raise AssertionError('Installed experiment failed: '+str(group))
        result=http('/api/jobs/'+group['variants'][0]['job_id']+'/result')['result']
        if result!=expected[version]:raise AssertionError('Installed configured CLI/HTTP results differ.')
    group=cli('experiment','run','--source',example/'v6.pine','--bars',example/'bars.json','--request',example/'experiment.json','--state-dir',work/'input-cli')
    if group['status']!='succeeded' or group['total']!=6:raise AssertionError('Installed experiment CLI did not finish six shared variants.')
    verify_dates(http, cli, work)
    verify_sessions(http, cli, work)
    verify_higher_timeframes(http, cli, work)
    verify_time_offsets(http, cli, work)
    verify_calendar_zones(http, cli, work)
    verify_chart_clock(http, cli, work)
    verify_session_flags(http, cli, work)
    verify_execution_time(http, cli, work)
    verify_visible_range(http, cli, work)
    verify_heikin_ashi(http, cli, work)
    verify_market_tickers(http, cli, work)
    from tools.installed_corporate_smoke import verify as verify_corporate
    verify_corporate(http,cli,work)
    return {'input_cli_versions':list(range(1,7)),'input_http_versions':[1,6],'input_cli_http_equal':True,'finite_experiments':True,'preset_revision':True,
            'date_input_worker_checkpoint':True,'session_input_worker_checkpoint':True,
            'higher_timeframe_worker_checkpoint':True, 'time_offset_worker_checkpoint':True,
            'calendar_zone_worker_checkpoint':True, 'chart_clock_worker_and_replay_checkpoint':True,
            'session_flags_worker_and_replay_checkpoint':True, 'execution_time_worker_and_replay_checkpoint':True,
            'visible_range_worker_checkpoint_and_recalculation':True,
            'heikin_ashi_worker_checkpoint_and_market_fill':True, 'market_ticker_worker_checkpoint':True, 'corporate_action_worker_checkpoint':True}


def verify_heikin_ashi(http, cli, work):
    bars = ROOT / heikin_ashi.DIRECTORY / 'bars.json'
    snapshot = http('/api/snapshots', {'provider': 'upload', 'raw': bars.read_text(encoding='utf-8')})['sha256']
    examples = ((heikin_ashi.EXAMPLE, heikin_ashi.VALUES), (heikin_ashi.STRATEGY, (heikin_ashi.VALUES[4],)))
    for example, expected in examples:
        source = ROOT / example
        compiled = http('/api/compile', {'source': source.read_text(encoding='utf-8'), 'source_name': source.name})
        assert compiled['status'] == 'compiled', compiled
        artifact = work / (source.stem + '-ha.ir.json')
        artifact.write_bytes(canonical(http('/api/artifacts/' + compiled['artifact_ref'])))
        launch = http('/api/launches', {'schema_version': 'openpine.launch-request.1', 'request_id': uuid.uuid4().hex,
            'input_ref': compiled['input_ref'], 'primary': {'kind': 'snapshot', 'snapshot_id': snapshot},
            'contexts': [], 'inputs': {}, 'timeout_seconds': 120, 'gap_policy': 'allow_with_metadata',
            'checkpoint_interval_bars': 2, 'pause_after_bars': 2})
        assert launch['status'] == 'ready', launch
        job = http(f"/api/launches/{launch['launch_id']}/start", {})['job_id']
        route = f'/api/jobs/{job}'
        wait_job(http, route, 'paused')
        plan = http(route + '/plan')
        assert plan['snapshot_id'] == snapshot and not plan.get('contexts'), plan
        http(route + '/resume', {})
        assert wait_job(http, route, 'succeeded')['resumed_from_bar'] == 2
        result = http(route + '/result')['result']
        saved = cli('run', '--artifact', artifact, '--bars', bars)['result']
        assert tuple(tuple(row['values']) for row in result['outputs']) == expected
        assert result['outputs'] == saved['outputs']
        if example == heikin_ashi.STRATEGY:
            ledger = http(route + '/ledger?limit=100')['events']
            fills = [row['value'] for row in ledger if row['kind'] == 'fill']
            assert [(fill['bar_index'], fill['price']) for fill in fills] == [(1, '16')], fills


def verify_market_tickers(http, cli, work):
    from tools import market_ticker_fixtures as fixtures

    root = ROOT / fixtures.DIRECTORY
    snapshots = {key: http('/api/snapshots', {'provider': 'upload', 'raw': (root / f'{key}.json').read_text()})['sha256']
                 for key in ('b1', 'b2', 'b4')}
    closes = [float(n) for n in range(100, 108)]
    higher = [None, 100., 100., 100., 100., 150., 150., 225.]
    for example, parent, child, expected in (
            (fixtures.INDICATOR, 'b1', 'b2', [closes, closes, closes, higher]),
            (fixtures.STRATEGY, 'b4', 'b1', [list(fixtures.CHILD_SUMS)])):
        source = ROOT / example
        compiled = http('/api/compile', {'source': source.read_text(), 'source_name': source.name})
        assert compiled['status'] == 'compiled', compiled
        artifact = work / (source.stem + '-market.ir.json')
        artifact.write_bytes(canonical(http('/api/artifacts/' + compiled['artifact_ref'])))
        launch = http('/api/launches', {
            'schema_version': 'openpine.launch-request.1', 'request_id': uuid.uuid4().hex,
            'input_ref': compiled['input_ref'], 'primary': {'kind': 'snapshot', 'snapshot_id': snapshots[parent]},
            'contexts': [{'kind': 'snapshot', 'snapshot_id': snapshots[child]}], 'inputs': {},
            'timeout_seconds': 120, 'gap_policy': 'allow_with_metadata', 'checkpoint_interval_bars': 1, 'pause_after_bars': 1})
        assert launch['status'] == 'ready', launch
        job = http(f"/api/launches/{launch['launch_id']}/start", {})['job_id']
        route = f'/api/jobs/{job}'
        wait_job(http, route, 'paused')
        plan = http(route + '/plan')
        assert [row['snapshot_id'] for row in plan['contexts']] == [snapshots[child]], plan
        http(route + '/resume', {})
        assert wait_job(http, route, 'succeeded')['resumed_from_bar'] == 1
        result = http(route + '/result')['result']
        direct = cli('job', '--artifact', artifact, '--bars', root / f'{parent}.json',
                     '--contexts', root / f'{parent}-contexts.json', '--state-dir', work / ('market-' + parent))
        assert [row['values'] for row in result['outputs']] == expected, result['outputs']
        assert direct['response']['result']['outputs'] == result['outputs']
        if example == fixtures.STRATEGY:
            events = http(route + '/ledger?limit=100')['events']
            fills = [row['value'] for row in events if row['kind'] == 'fill']
            assert [(fill['bar_index'], fill['price']) for fill in fills] == [(1, '1')], fills


def verify_dates(http, cli, work):
    verify_window(http, cli, work, 'Date window', ROOT / dates.EXAMPLE, ROOT / dates.DIRECTORY / 'bars.json',
                  ('time', 'time'), dates.inputs(dates.DEFAULT_WINDOW), dates.inputs(dates.OVERRIDE_WINDOW),
                  dates.DEFAULT_ORDERS, dates.OVERRIDE_ORDERS)


def verify_sessions(http, cli, work):
    defaults = dict(zip(('start', 'end'), sessions.DATE_RANGE, strict=True), window=sessions.DEFAULT)
    verify_window(http, cli, work, 'Session window', ROOT / sessions.EXAMPLE, ROOT / sessions.DIRECTORY / 'strategy.json',
                  ('time', 'time', 'session'), defaults, {'window': sessions.OVERRIDE},
                  sessions.DEFAULT_ORDERS, sessions.OVERRIDE_ORDERS, invalid_overrides={'window': '0930-1130:0'})


def verify_higher_timeframes(http, cli, work):
    defaults = {'start': higher.START, 'end': higher.END, 'window': higher.WINDOW, 'tf': higher.DEFAULT}
    verify_window(http, cli, work, 'Higher timeframe', ROOT / higher.EXAMPLE, ROOT / higher.DIRECTORY / 'strategy.json',
                  ('time', 'time', 'session', 'timeframe'), defaults, {'tf': higher.OVERRIDE},
                  higher.DEFAULT_ORDERS, higher.OVERRIDE_ORDERS, pause_after=higher.PAUSE_AFTER)


def wait_job(http, route, expected):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        state = http(route)
        if state['status'] == expected:
            return state
        if state['status'] in ('failed', 'cancelled', 'timed_out', 'succeeded'):
            raise AssertionError(state)
        time.sleep(.05)
    raise TimeoutError(route)


def verify_time_offsets(http, cli, work):
    source = ROOT / offsets.EXAMPLE
    bars = ROOT / offsets.DIRECTORY / 'sparse.json'
    compiled = http('/api/compile', {'source': source.read_text(encoding='utf-8'), 'source_name': source.name})
    assert compiled['status'] == 'compiled', compiled
    artifact = work / 'time-offsets.ir.json'
    artifact.write_bytes(canonical(http('/api/artifacts/' + compiled['artifact_ref'])))
    snapshot = http('/api/snapshots', {'provider': 'upload', 'raw': bars.read_text(encoding='utf-8')})['sha256']
    launch = http('/api/launches', {'schema_version': 'openpine.launch-request.1', 'request_id': uuid.uuid4().hex,
        'input_ref': compiled['input_ref'], 'primary': {'kind': 'snapshot', 'snapshot_id': snapshot},
        'contexts': [], 'inputs': {}, 'timeout_seconds': 120, 'gap_policy': 'allow_with_metadata',
        'checkpoint_interval_bars': offsets.PAUSE_AFTER, 'pause_after_bars': offsets.PAUSE_AFTER})
    assert launch['status'] == 'ready', launch
    job = http(f"/api/launches/{launch['launch_id']}/start", {})['job_id']
    route = f'/api/jobs/{job}'
    wait_job(http, route, 'paused')
    http(route + '/resume', {})
    assert wait_job(http, route, 'succeeded')['resumed_from_bar'] == offsets.PAUSE_AFTER
    result = http(route + '/result')['result']
    saved = cli('run', '--artifact', artifact, '--bars', bars)['result']
    expected = [('bar_time', stamp, stamp) for stamp in offsets.dates(*offsets.MARKERS)]
    assert [(line['xloc'], line['x1'], line['x2']) for line in result['lines']] == expected
    assert saved['lines'] == result['lines']
    events = http(route + '/line_events')
    assert events['total'] == len(expected)


def verify_calendar_zones(http, cli, work):
    source = ROOT / calendar_zones.EXAMPLE
    bars = ROOT / calendar_zones.DIRECTORY / 'fall.json'
    compiled = http('/api/compile', {'source': source.read_text(encoding='utf-8'), 'source_name': source.name})
    assert compiled['status'] == 'compiled', compiled
    artifact = work / 'calendar-zones.ir.json'
    artifact.write_bytes(canonical(http('/api/artifacts/' + compiled['artifact_ref'])))
    snapshot = http('/api/snapshots', {'provider': 'upload', 'raw': bars.read_text(encoding='utf-8')})['sha256']
    cut = calendar_zones.PAUSE_AFTER
    launch = http('/api/launches', {'schema_version': 'openpine.launch-request.1', 'request_id': uuid.uuid4().hex,
        'input_ref': compiled['input_ref'], 'primary': {'kind': 'snapshot', 'snapshot_id': snapshot},
        'contexts': [], 'inputs': {}, 'timeout_seconds': 120, 'gap_policy': 'allow_with_metadata',
        'checkpoint_interval_bars': cut, 'pause_after_bars': cut})
    assert launch['status'] == 'ready', launch
    job = http(f"/api/launches/{launch['launch_id']}/start", {})['job_id']
    route = f'/api/jobs/{job}'
    wait_job(http, route, 'paused')
    http(route + '/resume', {})
    assert wait_job(http, route, 'succeeded')['resumed_from_bar'] == cut
    result = http(route + '/result')['result']
    saved = cli('run', '--artifact', artifact, '--bars', bars)['result']
    assert tuple(tuple(row['values']) for row in result['outputs']) == calendar_zones.EXAMPLE_OUTPUTS
    assert saved['outputs'] == result['outputs']


def verify_chart_clock(http, cli, work):
    source = ROOT / clock.EXAMPLE
    bars = ROOT / clock.DIRECTORY / 'sparse.json'
    compiled = http('/api/compile', {'source': source.read_text(encoding='utf-8'), 'source_name': source.name})
    assert compiled['status'] == 'compiled', compiled
    artifact = work / 'chart-clock.ir.json'
    artifact.write_bytes(canonical(http('/api/artifacts/' + compiled['artifact_ref'])))
    snapshot = http('/api/snapshots', {'provider': 'upload', 'raw': bars.read_text(encoding='utf-8')})['sha256']
    cut = clock.PAUSE_AFTER
    launch = http('/api/launches', {'schema_version': 'openpine.launch-request.1', 'request_id': uuid.uuid4().hex,
        'input_ref': compiled['input_ref'], 'primary': {'kind': 'snapshot', 'snapshot_id': snapshot},
        'contexts': [], 'inputs': {}, 'timeout_seconds': 120, 'gap_policy': 'allow_with_metadata',
        'checkpoint_interval_bars': cut, 'pause_after_bars': cut})
    assert launch['status'] == 'ready', launch
    job = http(f"/api/launches/{launch['launch_id']}/start", {})['job_id']
    route = f'/api/jobs/{job}'
    wait_job(http, route, 'paused')
    http(route + '/resume', {})
    assert wait_job(http, route, 'succeeded')['resumed_from_bar'] == cut
    result = http(route + '/result')['result']
    saved = cli('run', '--artifact', artifact, '--bars', bars)['result']
    assert tuple(tuple(row['values']) for row in result['outputs']) == clock.HISTORICAL
    assert saved['outputs'] == result['outputs'] and saved['simulation'] == result['simulation']
    assert [event['bar_index'] for event in http(route + '/ledger')['events'] if event['kind'] == 'order'] == [3]

    context = MarketContext(clock.SPARSE_ID, clock.SYMBOL, '1', 'recorded.1')
    last = dict(instrument=None, history_last_ms=clock.OPENINGS[clock.HISTORY_INDEX],
                history_last_index=clock.HISTORY_INDEX)
    replay = http('/api/replays', {'input_ref': compiled['input_ref'], 'contexts': [asdict(context)],
        'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120,
        'environments': {context.context_id: last}})['replay_id']
    route = f'/api/replays/{replay}'
    http(route + '/events', {'events': [item.payload() for item in clock.tape()]})
    first = http(route + '/advance', {'max_events': 4, 'checkpoint_every': 1})
    assert (first['sequence'], first['confirmed_bars']) == (3, 3), first
    resumed = http(route + '/advance', {'max_events': 20, 'checkpoint_every': 1})
    rows = http(route + '/chart')['rows']
    assert (resumed['sequence'], resumed['confirmed_bars']) == (6, 5), resumed
    values = tuple(tuple(row[3] for row in rows if row[2] == output['output_id'])
                   for output in resumed['outputs'])
    assert values == clock.REPLAY, values


def verify_session_flags(http, cli, work):
    source = ROOT / session_flags.EXAMPLE
    bars = ROOT / session_flags.DIRECTORY / 'sparse.json'
    compiled = http('/api/compile', {'source': source.read_text(encoding='utf-8'), 'source_name': source.name})
    assert compiled['status'] == 'compiled', compiled
    artifact = work / 'session-flags.ir.json'
    artifact.write_bytes(canonical(http('/api/artifacts/' + compiled['artifact_ref'])))
    snapshot = http('/api/snapshots', {'provider': 'upload', 'raw': bars.read_text(encoding='utf-8')})['sha256']
    cut = session_flags.PAUSE_AFTER
    launch = http('/api/launches', {'schema_version': 'openpine.launch-request.1', 'request_id': uuid.uuid4().hex,
        'input_ref': compiled['input_ref'], 'primary': {'kind': 'snapshot', 'snapshot_id': snapshot},
        'contexts': [], 'inputs': {}, 'timeout_seconds': 120, 'gap_policy': 'allow_with_metadata',
        'checkpoint_interval_bars': cut, 'pause_after_bars': cut})
    assert launch['status'] == 'ready', launch
    job = http(f"/api/launches/{launch['launch_id']}/start", {})['job_id']
    route = f'/api/jobs/{job}'
    wait_job(http, route, 'paused')
    http(route + '/resume', {})
    assert wait_job(http, route, 'succeeded')['resumed_from_bar'] == cut
    result = http(route + '/result')['result']
    saved = cli('run', '--artifact', artifact, '--bars', bars)['result']
    expected = {title: values for title, _, values in session_flags.OUTPUTS}
    assert {row['title']: tuple(row['values']) for row in result['outputs']} == expected
    assert saved['outputs'] == result['outputs']

    context = MarketContext(session_flags.CONTEXT_ID, session_flags.SYMBOL, '1', 'recorded.1')
    replay = http('/api/replays', {'input_ref': compiled['input_ref'], 'contexts': [asdict(context)],
        'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120})['replay_id']
    route = f'/api/replays/{replay}'
    tape = session_flags.tape()
    http(route + '/events', {'events': [item.payload() for item in tape]})
    for end, start in ((session_flags.OPEN_CUT, 0), (session_flags.UPDATE_CUT, session_flags.OPEN_CUT)):
        status = http(route + '/advance', {'max_events': end - start, 'checkpoint_every': 1})
        assert (status['sequence'], status['confirmed_bars']) == (end - 1, 1), status
        preview = status['preview']
        assert preview['revision'] == end - session_flags.OPEN_CUT
        assert [row['value'] for row in preview['outputs']] == [0, 1, 0, 1, None]
    resumed = http(route + '/advance', {'max_events': len(tape), 'checkpoint_every': 1})
    assert (resumed['sequence'], resumed['confirmed_bars']) == (len(tape) - 1, len(session_flags.OPENINGS)), resumed
    rows = http(route + '/chart')['rows']
    values = {output['title']: tuple(row[3] for row in rows if row[2] == output['output_id'])
              for output in resumed['outputs']}
    assert values == expected, values


def verify_execution_time(http, cli, work):
    source = ROOT / execution_time.EXAMPLE
    bars = ROOT / execution_time.DIRECTORY / 'bars.json'
    compiled = http('/api/compile', {'source': source.read_text(encoding='utf-8'), 'source_name': source.name})
    assert compiled['status'] == 'compiled', compiled
    artifact = work / 'execution-time.ir.json'
    artifact.write_bytes(canonical(http('/api/artifacts/' + compiled['artifact_ref'])))
    snapshot = http('/api/snapshots', {'provider': 'upload', 'raw': bars.read_text(encoding='utf-8')})['sha256']
    stamp = execution_time.EXECUTION_TIME
    launch = http('/api/launches', {'schema_version': 'openpine.launch-request.1', 'request_id': uuid.uuid4().hex,
        'input_ref': compiled['input_ref'], 'primary': {'kind': 'snapshot', 'snapshot_id': snapshot},
        'contexts': [], 'inputs': {}, 'timeout_seconds': 120, 'gap_policy': 'fail',
        'execution_time_ms': stamp, 'checkpoint_interval_bars': 1, 'pause_after_bars': 1})
    assert launch['status'] == 'ready', launch
    job = http(f"/api/launches/{launch['launch_id']}/start", {})['job_id']
    route = f'/api/jobs/{job}'
    wait_job(http, route, 'paused')
    assert http(route + '/plan')['execution_time_ms'] == stamp
    http(route + '/resume', {})
    assert wait_job(http, route, 'succeeded')['resumed_from_bar'] == 1
    result = http(route + '/result')['result']
    saved = cli('run', '--artifact', artifact, '--bars', bars, '--execution-time-ms', stamp)['result']
    assert tuple(tuple(row['values']) for row in result['outputs']) == execution_time.HISTORICAL_OUTPUTS
    assert saved['outputs'] == result['outputs']

    context = MarketContext(execution_time.CONTEXT_ID, execution_time.SYMBOL, '1', 'recorded.1')
    replay = http('/api/replays', {'input_ref': compiled['input_ref'], 'contexts': [asdict(context)],
        'primary_id': context.context_id, 'broker_settings': None, 'timeout_seconds': 120,
        'event_profile': RECEIVED_PROFILE, 'alert_condition_profile': CONDITION_TEMPLATE_PROFILE})['replay_id']
    route = f'/api/replays/{replay}'
    tape = execution_time.tape()
    http(route + '/events', {'events': [item.payload() for item in tape]})
    for end, start, gap in ((execution_time.OPEN_CUT, 0, 0), (execution_time.UPDATE_CUT, execution_time.OPEN_CUT, 10_000)):
        status = http(route + '/advance', {'max_events': end - start, 'checkpoint_every': 1})
        assert (status['sequence'], status['confirmed_bars']) == (end - 1, 1), status
        assert [row['value'] for row in status['preview']['outputs']] == [tape[end - 1].event_time, gap]
    resumed = http(route + '/advance', {'max_events': len(tape), 'checkpoint_every': 1})
    assert (resumed['sequence'], resumed['confirmed_bars']) == (len(tape) - 1, len(execution_time.OPENINGS)), resumed
    rows = http(route + '/chart')['rows']
    values = tuple(tuple(row[3] for row in rows if row[2] == output['output_id'])
                   for output in resumed['outputs'])
    assert values == execution_time.REPLAY_OUTPUTS, values
    messages = http(route + '/alert_events')['events']
    assert [row['event']['message'] for row in messages] == [
        utc_datetime(ts).isoformat(timespec='seconds').replace('+00:00', 'Z') + '|' + str(ts)
        for ts in execution_time.CONFIRMED_TIMES]


def verify_visible_range(http, cli, work):
    source = ROOT / visible_range.EXAMPLE
    bars = ROOT / visible_range.DIRECTORY / 'bars.json'
    compiled = http('/api/compile', {'source': source.read_text(encoding='utf-8'), 'source_name': source.name})
    assert compiled['status'] == 'compiled', compiled
    artifact = work / 'visible-range.ir.json'
    artifact.write_bytes(canonical(http('/api/artifacts/' + compiled['artifact_ref'])))
    snapshot = http('/api/snapshots', {'provider': 'upload', 'raw': bars.read_text(encoding='utf-8')})['sha256']
    launch = http('/api/launches', {'schema_version': 'openpine.launch-request.1', 'request_id': uuid.uuid4().hex,
        'input_ref': compiled['input_ref'], 'primary': {'kind': 'snapshot', 'snapshot_id': snapshot},
        'contexts': [], 'inputs': {'scale': 2}, 'timeout_seconds': 120, 'gap_policy': 'allow_with_metadata',
        'visible_range': visible_range.FIRST, 'checkpoint_interval_bars': 2, 'pause_after_bars': 2})
    assert launch['status'] == 'ready', launch
    job = http(f"/api/launches/{launch['launch_id']}/start", {})['job_id']
    route = f'/api/jobs/{job}'
    wait_job(http, route, 'paused')
    assert http(route + '/plan')['visible_range'] == visible_range.FIRST
    http(route + '/resume', {})
    assert wait_job(http, route, 'succeeded')['resumed_from_bar'] == 2
    original = http(route + '/plan')
    overrides = work / 'visible-range-inputs.json'
    overrides.write_bytes(canonical({'scale': 2}))
    saved = cli('run', '--artifact', artifact, '--bars', bars, '--inputs', overrides,
                '--visible-range', visible_range.FIRST['left_ms'], visible_range.FIRST['right_ms'])['result']

    def outputs(selected, bounds):
        result = http(selected + '/result')['result']
        assert result['observation_count'] == len(visible_range.OPENINGS)
        assert tuple(tuple(row['values']) for row in result['outputs']) == visible_range.values(bounds, 2)
        return result['outputs']

    assert outputs(route, visible_range.FIRST) == saved['outputs']
    try:
        http(route + '/recalculate-view', visible_range.GAP)
    except AssertionError as error:
        assert 'VISIBLE_RANGE_CANDLE' in str(error), error
    else:
        raise AssertionError('A missing boundary candle was accepted.')
    seen = {job}
    for bounds in (visible_range.SECOND, visible_range.SINGLE):
        derived = http(route + '/recalculate-view', bounds)['job_id']
        assert derived not in seen
        seen.add(derived)
        selected = '/api/jobs/' + derived
        wait_job(http, selected, 'succeeded')
        assert http(selected + '/plan') == {**original, 'visible_range': bounds}
        outputs(selected, bounds)
    assert outputs(route, visible_range.FIRST) == saved['outputs'] and http(route + '/plan') == original


def verify_window(http, cli, work, title, source, bars, kinds, defaults, overrides, default_orders, override_orders,
                  *, invalid_overrides=None, pause_after=3):
    compiled = http('/api/compile', {'source': source.read_text(encoding='utf-8'), 'source_name': source.name})
    assert compiled['status'] == 'compiled', compiled
    definitions = compiled['inputs']['definitions']
    assert tuple(definition['input_type'] for definition in definitions) == kinds
    assert {definition['name']: definition['default'] for definition in definitions} == defaults
    prefix = title.lower().replace(' ', '-')
    artifact = work / f'{prefix}.ir.json'
    artifact.write_bytes(canonical(http('/api/artifacts/' + compiled['artifact_ref'])))
    raw = bars.read_text(encoding='utf-8')
    count = len(json.loads(raw)['bars'])
    snapshot = http('/api/snapshots', {'provider': 'upload', 'raw': raw})['sha256']
    preset_request = {'input_ref': compiled['input_ref'], 'name': title,
                      'inputs': overrides, 'expected_revision': None}
    if invalid_overrides is not None:
        try:
            http('/api/presets', {**preset_request, 'inputs': invalid_overrides})
        except AssertionError as error:
            assert 'INPUT_SETTINGS' in str(error), error
        else:
            raise AssertionError('Invalid session override was stored.')
    preset = http('/api/presets', preset_request)
    default_preset = http('/api/presets', {**preset_request, 'name': title + ' defaults', 'inputs': defaults})
    saved = {row['preset_id']: row for row in http('/api/presets?input_ref=' + compiled['input_ref'])['presets']}
    assert set(saved) == {preset['preset_id'], default_preset['preset_id']}
    for expected in (preset, default_preset):
        assert {key: saved[expected['preset_id']][key] for key in expected} == expected

    def launch(inputs, **options):
        prepared = http('/api/launches', {'schema_version': 'openpine.launch-request.1', 'request_id': uuid.uuid4().hex,
            'input_ref': compiled['input_ref'], 'primary': {'kind': 'snapshot', 'snapshot_id': snapshot}, 'contexts': [],
            'inputs': inputs, 'timeout_seconds': 120, 'gap_policy': 'fail', **options})
        assert prepared['status'] == 'ready', prepared
        return '/api/jobs/' + http(f"/api/launches/{prepared['launch_id']}/start", {})['job_id']

    def result(route, orders, settings):
        value = http(route + '/result')['result']
        events = http(route + '/ledger')['events']
        assert tuple(event['bar_index'] for event in events if event['kind'] == 'order') == orders
        assert tuple(value['outputs'][0]['values']) == tuple(int(index in orders) for index in range(count))
        assert value['settings']['values'] == {f'input:{k}': v for k, v in settings.items()}
        return value, events

    default = launch(default_preset['inputs'])
    wait_job(http, default, 'succeeded')
    ordinary, _ = result(default, default_orders, defaults)
    overridden = launch(preset['inputs'])
    wait_job(http, overridden, 'succeeded')
    selected = defaults | overrides
    baseline = result(overridden, override_orders, selected)
    assert baseline[0]['settings']['sha256'] != ordinary['settings']['sha256']

    paused = launch(preset['inputs'], checkpoint_interval_bars=1, pause_after_bars=pause_after)
    wait_job(http, paused, 'paused')
    edited = http('/api/presets', {**preset_request, 'inputs': defaults,
                                  'expected_revision': preset['revision']})
    assert edited['revision'] == preset['revision'] + 1
    http(paused + '/resume', {})
    restored = wait_job(http, paused, 'succeeded')
    assert restored['resumed_from_bar'] == pause_after
    assert result(paused, override_orders, selected) == baseline

    inputs = work / f'{prefix}.json'
    inputs.write_bytes(canonical(preset['inputs']))
    native = cli('job', '--artifact', artifact, '--bars', bars, '--inputs', inputs,
                 '--state-dir', work / f'{prefix}-cli')['response']['result']
    # The uploaded snapshot and CLI file have different provenance. Compare the
    # settings and execution observations common to those two entry points.
    assert native['settings'] == baseline[0]['settings'] and native['outputs'] == baseline[0]['outputs']
    assert native['simulation'] == baseline[0]['simulation']
