"""Acceptance shared by installed-wheel verification and the source-tree gate."""
import json
import time
import uuid
from packages.compiler.semantic.specification import ROOT
from packages.contracts.wire import canonical
from tools import date_inputs_fixtures as dates
from tools import trading_sessions_fixtures as sessions
from tools import higher_timeframes_fixtures as higher
from tools import time_offsets_fixtures as offsets


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
    return {'input_cli_versions':list(range(1,7)),'input_http_versions':[1,6],'input_cli_http_equal':True,'finite_experiments':True,'preset_revision':True,
            'date_input_worker_checkpoint':True,'session_input_worker_checkpoint':True,
            'higher_timeframe_worker_checkpoint':True, 'time_offset_worker_checkpoint':True}


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
