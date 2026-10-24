"""Installed signal profile through saved IR, CLI/HTTP, workers and research."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from tools.specification import ROOT
from tools.signal_fixtures import oracle
from packages.contracts.wire import digest
from tools.acceptance_http import request_json


def verify(http,cli,work):
    example=ROOT/'examples/signals';jobs=[];base=None
    snapshots=[http('/api/snapshots',{'provider':'upload','raw':(example/n).read_text(encoding='utf-8')})['sha256'] for n in ('input.json','higher.json')]
    closes=[b['close'] for b in json.loads((example/'bars.json').read_text())['bars']]
    def finish(path,expected='succeeded'):
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            r=http(path)
            if r['status']==expected:return r
            if r['status'] in ('failed','cancelled','timed_out','succeeded','paused','completed_with_errors','holdout_failed','no_eligible_candidate'):raise AssertionError(r)
            time.sleep(.1)
        raise TimeoutError(r)
    for v in range(1,7):
        print(f'Installed signals: v{v}, contexts and resume',file=sys.stderr,flush=True)
        path=example/f'event-trend-v{v}.pine';compiled=http('/api/compile',{'source':path.read_text(encoding='utf-8'),'source_name':path.as_posix()})
        ready=http('/api/launches',{'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,'input_ref':compiled['input_ref'],
            'primary':{'kind':'snapshot','snapshot_id':snapshots[0]},'contexts':[{'kind':'snapshot','snapshot_id':snapshots[1]}],'inputs':{'length':4},'timeout_seconds':120,'gap_policy':'fail','checkpoint_interval_bars':17,'pause_after_bars':17})
        if ready['status']!='ready':raise AssertionError(ready)
        job=http('/api/launches/'+ready['launch_id']+'/start',{})['job_id'];jobs.append(job)
        finish('/api/jobs/'+job,'paused');http('/api/jobs/'+job+'/resume',{});state=finish('/api/jobs/'+job);result=http('/api/jobs/'+job+'/result')['result']
        if state['resumed_from_bar']!=17 or result['outputs'][0]['values']!=oracle('wma',closes,length=4) or result['outputs'][1]['values']!=oracle('vwma',closes,[100.]*360,length=4):raise AssertionError('Signal weighted oracle differs.')
        if result['contexts'][0]['dataset']['count']!=90 or result['simulation']['statistics']['trades']<1:raise AssertionError('Requested event/strategy result differs.')
        artifact=work/f'signal-ir-{v}.json';override=work/f'signal-inputs-{v}.json';override.write_text('{"length":4}',encoding='utf-8')
        cli('compile','--source',path,'--profile','historical','--output',artifact)
        if json.loads(artifact.read_text())['ir']['schema_version']!='openpine.typed-ir.10':raise AssertionError('Mixed environment/signal IR differs.')
        direct=cli('job','--artifact',artifact,'--bars',example/'input.json','--contexts',example/'contexts.json','--inputs',override,'--state-dir',work/f'signal-cli-{v}')
        if direct['response']['result']!=result:raise AssertionError('Saved signal CLI/HTTP differs.')
        if http(f'/api/jobs/{job}/chart?start=19&limit=7')['outputs'][0]['values']!=result['outputs'][0]['values'][19:26]:raise AssertionError('Signal chart range differs.')
        saved=http('/api/jobs/'+job+'/configuration')
        if saved['plan']['settings']['values']['input:length']!=4 or saved['request']['settings']!=saved['plan']['settings']:raise AssertionError('Frozen signal input differs.')
        if v==6:base=ready['base']
    for route,name,key in (('experiments','experiment','experiment_id'),('searches','search','search_id'),('walkforwards','walkforward','walkforward_id')):
        print('Installed signals: '+route,file=sys.stderr,flush=True)
        made=http('/api/'+route,{**json.loads((example/(name+'.json')).read_text()),**base});path='/api/'+route+'/'+made[key];finish(path);report=http(path+'/report')
        if report['sha256']!=digest(report['report']) or not report['report']['variants']:raise AssertionError('Signal research report differs.')
    phases=ROOT/'examples/environment';contexts=json.loads((phases/'replay-contexts.json').read_text());events=[json.loads(line) for line in (phases/'events.ndjson').read_text().splitlines()]
    for v in (1,6):
        path=example/f'phase-v{v}.pine';compiled=http('/api/compile',{'source':path.read_text(),'source_name':path.as_posix()})
        key=http('/api/replays',{'input_ref':compiled['input_ref'],**contexts,'broker_settings':None,'timeout_seconds':120})['replay_id']
        http(f'/api/replays/{key}/events',{'events':events});http(f'/api/replays/{key}/advance',{'max_events':3,'checkpoint_every':1})
        if [r['value'] for r in http(f'/api/replays/{key}')['preview']['outputs']]!=[2.,10.,10.,0,0]:raise AssertionError('Signal preview leaked state.')
        http(f'/api/replays/{key}/advance',{'max_events':100,'checkpoint_every':1});expected=http(f'/api/replays/{key}/chart')['rows']
        state=work/f'signal-replay-{v}';made=cli('replay','create','--source',path,'--contexts',phases/'replay-contexts.json','--state-dir',state,'--timeout','120')['replay_id']
        cli('replay','append','--id',made,'--events',phases/'events.ndjson','--state-dir',state);cli('replay','advance','--id',made,'--max-events','3','--checkpoint-every','1','--state-dir',state);cli('replay','advance','--id',made,'--state-dir',state)
        if cli('replay','chart','--id',made,'--state-dir',state)['rows']!=expected:raise AssertionError('Signal recorded CLI/HTTP differs.')
    return {'signal_cli_http_versions':list(range(1,7)),'signal_saved_ir':True,'signal_prepared_jobs':jobs,'signal_context_predicates':True,'signal_joint_resume':True,'signal_bounded_chart':True,'signal_frozen_settings':True,'signal_experiment':True,'signal_search_holdout':True,'signal_walkforward':True,'signal_replay_cli_http_versions':[1,6]}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--expected-root',type=Path);args=p.parse_args()
    if sys.version_info[:3]!=(3,14,8) or args.expected_root and args.expected_root.resolve()!=ROOT:raise AssertionError('Use installed Python 3.14.8.')
    from apps.ui.server import Server
    bootstrap="import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('apps.openpine',run_name='__main__')"
    with tempfile.TemporaryDirectory(prefix='openpine-signals-') as directory:
        work=Path(directory)
        def cli(*args):
            r=subprocess.run([sys.executable,'-X','utf8','-c',bootstrap,str(ROOT),*map(str,args)],cwd=work,capture_output=True,text=True,encoding='utf-8',timeout=180)
            if r.returncode:raise AssertionError(r.stdout[-2000:]+' '+r.stderr[-2000:])
            return json.loads(r.stdout) if r.stdout.strip() else None
        server=Server(('127.0.0.1',0),work/'http');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();url=f'http://127.0.0.1:{server.server_port}';token=''
        def http(path,payload=None):return request_json(url,token,path,payload)
        try:token=http('/api/config')['token'];result=verify(http,cli,work)
        finally:server.shutdown();thread.join(timeout=10);server.server_close()
    print(json.dumps({'status':'passed',**result},sort_keys=True))


if __name__=='__main__':main()
