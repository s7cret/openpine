"""Acceptance shared by installed-wheel verification and the source-tree gate."""
import json
import time
import uuid
from packages.compiler.semantic.specification import ROOT
from packages.contracts.wire import canonical
from tools import date_inputs_fixtures as dates


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
    return {'input_cli_versions':list(range(1,7)),'input_http_versions':[1,6],'input_cli_http_equal':True,'finite_experiments':True,'preset_revision':True,
            'date_input_worker_checkpoint':True}


def verify_dates(http, cli, work):
    source = ROOT / dates.EXAMPLE
    bars = ROOT / dates.DIRECTORY / 'bars.json'
    compiled = http('/api/compile', {'source': source.read_text(encoding='utf-8'), 'source_name': source.name})
    assert compiled['status'] == 'compiled', compiled
    definitions = compiled['inputs']['definitions']
    assert [definition['input_type'] for definition in definitions] == ['time', 'time']
    assert [definition['default'] for definition in definitions] == list(dates.DEFAULT_WINDOW)
    artifact = work / 'date-input.ir.json'
    artifact.write_bytes(canonical(http('/api/artifacts/' + compiled['artifact_ref'])))
    snapshot = http('/api/snapshots', {'provider': 'upload', 'raw': bars.read_text(encoding='utf-8')})['sha256']
    preset_request = {'input_ref': compiled['input_ref'], 'name': 'Date window',
                      'inputs': dates.inputs(dates.OVERRIDE_WINDOW), 'expected_revision': None}
    preset = http('/api/presets', preset_request)
    loaded, = http('/api/presets?input_ref=' + compiled['input_ref'])['presets']
    assert {key: loaded[key] for key in preset} == preset

    def launch(inputs, **options):
        prepared = http('/api/launches', {'schema_version': 'openpine.launch-request.1', 'request_id': uuid.uuid4().hex,
            'input_ref': compiled['input_ref'], 'primary': {'kind': 'snapshot', 'snapshot_id': snapshot}, 'contexts': [],
            'inputs': inputs, 'timeout_seconds': 120, 'gap_policy': 'fail', **options})
        assert prepared['status'] == 'ready', prepared
        return '/api/jobs/' + http(f"/api/launches/{prepared['launch_id']}/start", {})['job_id']

    def wait(route, expected):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = http(route)
            if state['status'] == expected:
                return state
            if state['status'] in ('failed', 'cancelled', 'timed_out', 'succeeded'):
                raise AssertionError(state)
            time.sleep(.05)
        raise TimeoutError(route)

    def result(route, orders, window):
        value = http(route + '/result')['result']
        events = http(route + '/ledger')['events']
        assert tuple(event['bar_index'] for event in events if event['kind'] == 'order') == orders
        assert tuple(value['outputs'][0]['values']) == tuple(int(index in orders) for index in range(6))
        assert value['settings']['values'] == {f'input:{k}': v for k, v in dates.inputs(window).items()}
        return value, events

    default = launch({})
    wait(default, 'succeeded')
    ordinary, _ = result(default, dates.DEFAULT_ORDERS, dates.DEFAULT_WINDOW)
    overridden = launch(preset['inputs'])
    wait(overridden, 'succeeded')
    baseline = result(overridden, dates.OVERRIDE_ORDERS, dates.OVERRIDE_WINDOW)
    assert baseline[0]['settings']['sha256'] != ordinary['settings']['sha256']

    paused = launch(preset['inputs'], checkpoint_interval_bars=1, pause_after_bars=3)
    wait(paused, 'paused')
    edited = http('/api/presets', {**preset_request, 'inputs': dates.inputs(dates.DEFAULT_WINDOW),
                                  'expected_revision': preset['revision']})
    assert edited['revision'] == preset['revision'] + 1
    http(paused + '/resume', {})
    restored = wait(paused, 'succeeded')
    assert restored['resumed_from_bar'] == 3
    assert result(paused, dates.OVERRIDE_ORDERS, dates.OVERRIDE_WINDOW) == baseline

    inputs = work / 'date-inputs.json'
    inputs.write_bytes(canonical(preset['inputs']))
    native = cli('job', '--artifact', artifact, '--bars', bars, '--inputs', inputs,
                 '--state-dir', work / 'date-input-cli')['response']['result']
    # The uploaded snapshot and CLI file have different provenance. Compare the
    # settings and execution observations common to those two entry points.
    assert native['settings'] == baseline[0]['settings'] and native['outputs'] == baseline[0]['outputs']
    assert native['simulation'] == baseline[0]['simulation']
