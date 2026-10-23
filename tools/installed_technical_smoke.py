"""Practical TA through the installed CLI, HTTP, chart and research services."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from packages.compiler.semantic.specification import ROOT
from packages.contracts.protection import PROFILE
from packages.contracts.wire import digest
from tools.acceptance_http import request_json


def verify(http, cli, work):
    example = ROOT/'examples/technical'
    primary = http('/api/snapshots', {'provider':'upload', 'raw':(example/'bars.json').read_text(encoding='utf-8')})['sha256']
    higher = http('/api/snapshots', {'provider':'upload', 'raw':(example/'higher.json').read_text(encoding='utf-8')})['sha256']
    contexts = json.loads((example/'contexts.json').read_text(encoding='utf-8'))
    context_payload = {'primary_context':contexts['primary_context'],
        'contexts':[{'context':contexts['contexts'][0]['context'], 'snapshot_id':higher}]}
    compiled_inputs = {}
    direct = {}

    def finish(path, expected='succeeded', timeout=180):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            state = http(path)
            if state['status'] == expected:
                return state
            if state['status'] in ('failed','cancelled','succeeded','completed_with_errors',
                                  'holdout_failed','no_eligible_candidate','paused'):
                raise AssertionError(f'{path}: {state}')
            time.sleep(.1)
        raise TimeoutError(f'{path}: {state}')

    def report(path):
        value = http(path+'/report')
        if value['sha256'] != digest(value['report']):
            raise AssertionError('Installed technical report hash differs.')
        for variant in value['report']['variants']:
            if variant['plan']['simulation_policy']['profile_id'] != PROFILE:
                raise AssertionError('Installed report lost the protective execution policy.')
        return value['report']

    for version in (1, 6):
        for family, overrides in (
            ('atr-cross', {'atrLength':6, 'risk':1.75}),
            ('rsi-trend', {'atrLength':6, 'rsiLength':6}),
            ('channel-filter', {'atrLength':6, 'channelLength':6, 'higherLength':3}),
        ):
            print(f'Installed technical: {family} v{version}, inputs and joint resume',file=sys.stderr,flush=True)
            source = example/f'{family}-v{version}.pine'
            compiled = http('/api/compile', {'source':source.read_text(encoding='utf-8'), 'source_name':source.name})
            compiled_inputs[version, family] = compiled['input_ref']
            context = context_payload if family == 'channel-filter' else {}
            job = http('/api/jobs', {'schema_version':'openpine.job-request.6',
                'input_ref':compiled['input_ref'], 'snapshot_id':primary, 'timeout_seconds':120,
                'inputs':overrides, **context, 'checkpoint_interval_bars':17, 'pause_after_bars':17})['job_id']
            finish('/api/jobs/'+job, 'paused')
            http(f'/api/jobs/{job}/resume', {})
            state = finish('/api/jobs/'+job)
            result = http(f'/api/jobs/{job}/result')['result']
            if state['resumed_from_bar'] != 17 or result['simulation']['statistics']['trades'] < 1:
                raise AssertionError('Installed technical strategy did not resume and close trades.')
            if result['simulation']['policy']['profile_id'] != PROFILE:
                raise AssertionError('Installed technical strategy lost its protective broker.')
            for name, value in overrides.items():
                if result['settings']['values']['input:'+name] != value:
                    raise AssertionError('Installed technical input override was lost.')
            overrides_file = work/f'{family}-v{version}-inputs.json'
            overrides_file.write_text(json.dumps(overrides), encoding='utf-8')
            extra = ('--contexts', example/'contexts.json') if context else ()
            local = cli('job', '--source', source, '--bars', example/'bars.json', '--inputs', overrides_file,
                '--state-dir', work/f'technical-{family}-{version}', *extra)
            if local['response']['result'] != result:
                raise AssertionError('Installed technical CLI/HTTP results differ.')
            direct[version, family] = result
            chart = http(f'/api/jobs/{job}/chart?start=35&limit=40')
            ledger = http(f'/api/jobs/{job}/ledger?start=0&limit=1000')
            if chart['schema_version'] != 'openpine.chart-window.2' or chart['outputs'][0]['values'] != result['outputs'][0]['values'][35:75]:
                raise AssertionError('Installed technical chart does not preserve the requested series range.')
            if ledger['events'] != result['simulation']['events'][:1000] or not any(e['kind']=='fill' for e in ledger['events']):
                raise AssertionError('Installed technical ledger lost fills or event order.')
            if context and not all(request['historical_future'] == (version < 3) for request in result['requests']):
                raise AssertionError('Installed technical request lost versioned future-read disclosure.')

    # A shared compiled RSI program must keep variants independent and reproduce
    # the directly executed variant with exactly the same input settings.
    print('Installed technical: RSI experiment',file=sys.stderr,flush=True)
    group = http('/api/experiments', {**json.loads((example/'rsi-experiment.json').read_text(encoding='utf-8')),
        'input_ref':compiled_inputs[6,'rsi-trend'], 'snapshot_id':primary})
    group = finish('/api/experiments/'+group['experiment_id'])
    result = http('/api/jobs/'+group['variants'][1]['job_id']+'/result')['result']
    if result != direct[6,'rsi-trend']:
        raise AssertionError('Installed RSI experiment differs from its direct configured run.')

    print('Installed technical: ATR search and separate holdout',file=sys.stderr,flush=True)
    search = http('/api/searches', {**json.loads((example/'atr-search.json').read_text(encoding='utf-8')),
        'input_ref':compiled_inputs[6,'atr-cross'], 'snapshot_id':primary})
    search = finish('/api/searches/'+search['search_id'])
    if search['decision']['winner_ordinal'] is None or search['holdout_job']['job_id'] in {v['job_id'] for v in search['variants']}:
        raise AssertionError('Installed ATR search did not create a separate winner holdout.')
    report('/api/searches/'+search['search_id'])

    print('Installed technical: requested RMA walk-forward',file=sys.stderr,flush=True)
    study = http('/api/walkforwards', {**json.loads((example/'channel-walkforward.json').read_text(encoding='utf-8')),
        'input_ref':compiled_inputs[6,'channel-filter'], 'snapshot_id':primary, **context_payload})
    study = finish('/api/walkforwards/'+study['walkforward_id'])
    value = report('/api/walkforwards/'+study['walkforward_id'])
    if not value['variants'] or any(len(v['plan']['contexts']) != 1 for v in value['variants']):
        raise AssertionError('Installed walk-forward lost the requested context snapshot.')
    return {'technical_cli_http_versions':[1,6], 'technical_strategy_families':['atr-cross','rsi-trend','channel-filter'],
        'technical_input_overrides':True, 'technical_joint_resume':True, 'technical_range_chart_ledger':True,
        'technical_legacy_lookahead_disclosed':True, 'technical_rsi_experiment':True,
        'technical_atr_search_holdout':True, 'technical_requested_rma_walkforward':True,
        'technical_research_report_hashes':True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-root', type=Path)
    args = parser.parse_args(argv)
    if sys.version_info[:3] != (3,14,8) or args.expected_root and args.expected_root.resolve() != ROOT:
        raise AssertionError('Acceptance requires Python 3.14.8 and the selected installed root.')
    from apps.ui.server import Server
    bootstrap = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('apps.openpine',run_name='__main__')"
    with tempfile.TemporaryDirectory(prefix='openpine-technical-') as directory:
        work = Path(directory)
        def cli(*arguments):
            process = subprocess.run([sys.executable,'-X','utf8','-c',bootstrap,str(ROOT),*map(str,arguments)],
                cwd=work,capture_output=True,text=True,encoding='utf-8',timeout=120)
            if process.returncode:
                raise AssertionError(process.stdout[-2000:]+' '+process.stderr[-2000:])
            return json.loads(process.stdout)
        server = Server(('127.0.0.1',0),work/'http')
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        token = ''
        def http(path,payload=None):
            return request_json(base, token, path, payload)
        try:
            token = http('/api/config')['token']
            result = verify(http,cli,work)
        finally:
            server.shutdown()
            thread.join(timeout=10)
            server.server_close()
    print(json.dumps({'status':'passed','python':sys.version.split()[0],**result},sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
