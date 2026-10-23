"""Composite strategies through installed CLI/HTTP, charts and research owners."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from packages.compiler.semantic.specification import ROOT
from packages.contracts.wire import digest
from tools.acceptance_http import request_json


def verify(http,cli,work):
    example=ROOT/'examples/composite'
    primary=http('/api/snapshots',{'provider':'upload','raw':(example/'bars.json').read_text(encoding='utf-8')})['sha256']
    higher=http('/api/snapshots',{'provider':'upload','raw':(example/'higher.json').read_text(encoding='utf-8')})['sha256']
    raw=json.loads((example/'contexts.json').read_text(encoding='utf-8'))
    contexts={'primary_context':raw['primary_context'],'contexts':[{'context':raw['contexts'][0]['context'],'snapshot_id':higher}]}
    def finish(path,expected='succeeded'):
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            state=http(path)
            if state['status']==expected:return state
            if state['status'] in ('failed','cancelled','timed_out','succeeded','paused','completed_with_errors','holdout_failed','no_eligible_candidate'):raise AssertionError(state)
            time.sleep(.1)
        raise TimeoutError(state)
    def report(path):
        value=http(path+'/report')
        if value['sha256']!=digest(value['report']) or not value['report']['variants']:raise AssertionError('Composite research report is incomplete.')
        if any(len(v['plan']['contexts'])!=1 for v in value['report']['variants']):raise AssertionError('Composite research lost its context binding.')
        return value['report']
    inputs={}
    for version in (1,6):
        for family,overrides in (('macd-cross',{'atrLength':6,'signalLength':2}),('bb-reversion',{'atrLength':6,'length':10,'mult':1.0})):
            print(f'Installed composite: {family} v{version}, requested tuple and joint resume',file=sys.stderr,flush=True)
            path=example/f'{family}-v{version}.pine'
            compiled=http('/api/compile',{'source':path.read_text(encoding='utf-8'),'source_name':path.name});inputs[version,family]=compiled['input_ref']
            job=http('/api/jobs',{'schema_version':'openpine.job-request.6','input_ref':compiled['input_ref'],
                'snapshot_id':primary,'timeout_seconds':120,'inputs':overrides,**contexts,'checkpoint_interval_bars':17,'pause_after_bars':17})['job_id']
            finish('/api/jobs/'+job,'paused');http('/api/jobs/'+job+'/resume',{});state=finish('/api/jobs/'+job)
            result=http('/api/jobs/'+job+'/result')['result']
            if state['resumed_from_bar']!=17 or result['simulation']['statistics']['trades']<1:raise AssertionError('Composite strategy did not resume and close trades.')
            if any(result['settings']['values']['input:'+name]!=value for name,value in overrides.items()):raise AssertionError('Composite input overrides were lost.')
            override=work/f'composite-{family}-{version}.json';override.write_text(json.dumps(overrides),encoding='utf-8')
            direct=cli('job','--source',path,'--bars',example/'bars.json','--contexts',example/'contexts.json',
                '--inputs',override,'--state-dir',work/f'composite-cli-{family}-{version}')
            if direct['response']['result']!=result:raise AssertionError('Composite CLI/HTTP results differ.')
            chart=http(f'/api/jobs/{job}/chart?start=35&limit=40');ledger=http(f'/api/jobs/{job}/ledger?start=0&limit=1000')
            if chart['outputs'][0]['values']!=result['outputs'][0]['values'][35:75]:raise AssertionError('Composite chart range differs.')
            if ledger['events']!=result['simulation']['events'][:1000]:raise AssertionError('Composite ledger differs.')
            if not all(r['historical_future']==(version<3) for r in result['requests']):raise AssertionError('Composite lookahead disclosure differs.')
    print('Installed composite: MACD experiment, Bollinger holdout and requested tuple walk-forward',file=sys.stderr,flush=True)
    group=http('/api/experiments',{**json.loads((example/'macd-experiment.json').read_text()),'input_ref':inputs[6,'macd-cross'],'snapshot_id':primary,**contexts})
    finish('/api/experiments/'+group['experiment_id']);report('/api/experiments/'+group['experiment_id'])
    search=http('/api/searches',{**json.loads((example/'bb-search.json').read_text()),'input_ref':inputs[6,'bb-reversion'],'snapshot_id':primary,**contexts})
    search=finish('/api/searches/'+search['search_id'])
    if search['holdout_job']['job_id'] in {v['job_id'] for v in search['variants']}:raise AssertionError('Composite holdout is not separate.')
    report('/api/searches/'+search['search_id'])
    study=http('/api/walkforwards',{**json.loads((example/'macd-walkforward.json').read_text()),'input_ref':inputs[6,'macd-cross'],'snapshot_id':primary,**contexts})
    finish('/api/walkforwards/'+study['walkforward_id']);report('/api/walkforwards/'+study['walkforward_id'])
    return {'composite_cli_http_versions':[1,6],'composite_families':['macd-cross','bb-reversion'],
        'composite_joint_resume':True,'composite_input_overrides':True,'composite_chart_ledger':True,
        'composite_version_disclosures':True,'composite_experiment':True,'composite_separate_holdout':True,
        'composite_requested_walkforward':True,'composite_report_hashes':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--expected-root',type=Path);args=parser.parse_args()
    if sys.version_info[:3]!=(3,14,8) or args.expected_root and args.expected_root.resolve()!=ROOT:raise AssertionError('Use the selected installed Python 3.14.8 root.')
    from apps.ui.server import Server
    bootstrap="import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('apps.openpine',run_name='__main__')"
    with tempfile.TemporaryDirectory(prefix='openpine-composite-') as temporary:
        work=Path(temporary)
        def cli(*arguments):
            p=subprocess.run([sys.executable,'-X','utf8','-c',bootstrap,str(ROOT),*map(str,arguments)],cwd=work,capture_output=True,text=True,encoding='utf-8',timeout=120)
            if p.returncode:raise AssertionError(p.stdout[-2000:]+' '+p.stderr[-2000:])
            return json.loads(p.stdout)
        server=Server(('127.0.0.1',0),work/'http');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}';token=''
        def http(path,payload=None):return request_json(base,token,path,payload)
        try:token=http('/api/config')['token'];result=verify(http,cli,work)
        finally:server.shutdown();thread.join(timeout=10);server.server_close()
    print(json.dumps({'status':'passed','python':sys.version.split()[0],**result},sort_keys=True))


if __name__=='__main__':main()
