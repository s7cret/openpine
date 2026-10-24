"""Installed UTC environment through CLI, HTTP, workers, replay and research."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from packages.compiler.semantic.specification import ROOT
from packages.contracts.wire import digest
from tools.acceptance_http import request_json


def verify(http,cli,work):
    example=ROOT/'examples/environment';jobs=[];base=None
    snapshots=[http('/api/snapshots',{'provider':'upload','raw':(example/name).read_text(encoding='utf-8')})['sha256'] for name in ('input.json','higher.json')]
    def finish(path,expected='succeeded'):
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            value=http(path)
            if value['status']==expected:return value
            if value['status'] in ('failed','cancelled','timed_out','succeeded','paused','completed_with_errors','holdout_failed','no_eligible_candidate'):raise AssertionError(value)
            time.sleep(.1)
        raise TimeoutError(value)
    for version in range(1,7):
        print(f'Installed environment: calendar/ticks v{version}, frozen metadata and resume',file=sys.stderr,flush=True)
        path=example/f'calendar-ticks-v{version}.pine'
        compiled=http('/api/compile',{'source':path.read_text(encoding='utf-8'),'source_name':path.as_posix()})
        ready=http('/api/launches',{'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,'input_ref':compiled['input_ref'],
            'primary':{'kind':'snapshot','snapshot_id':snapshots[0]},'contexts':[{'kind':'snapshot','snapshot_id':snapshots[1]}],
            'inputs':{'ticks':3},'timeout_seconds':120,'gap_policy':'fail','checkpoint_interval_bars':17,'pause_after_bars':17})
        if ready['status']!='ready':raise AssertionError(ready)
        job=http('/api/launches/'+ready['launch_id']+'/start',{})['job_id'];jobs.append(job)
        finish('/api/jobs/'+job,'paused');http('/api/jobs/'+job+'/resume',{});state=finish('/api/jobs/'+job)
        result=http('/api/jobs/'+job+'/result')['result']
        if state['resumed_from_bar']!=17 or result['outputs'][3]['values']!=[.25]*360 or result['simulation']['statistics']['trades']<1:raise AssertionError('Environment worker state/output differs.')
        if result['outputs'][1]['values']!=[1709157600000+i*60000 for i in range(360)]:raise AssertionError('UTC output differs from its source.')
        artifact=work/f'environment-ir-{version}.json';override=work/f'environment-inputs-{version}.json';override.write_text(json.dumps({'ticks':3}),encoding='utf-8')
        cli('compile','--source',path,'--profile','historical','--output',artifact)
        direct=cli('job','--artifact',artifact,'--bars',example/'input.json','--contexts',example/'contexts.json','--inputs',override,'--state-dir',work/f'environment-cli-{version}')
        if direct['response']['result']!=result:raise AssertionError('Saved environment IR differs from HTTP result.')
        chart=http(f'/api/jobs/{job}/chart?start=118&limit=7')
        if chart['outputs'][1]['values']!=result['outputs'][1]['values'][118:125]:raise AssertionError('Environment chart differs.')
        saved=http('/api/jobs/'+job+'/configuration')
        if saved['plan']['simulation_policy']['settings']['tick_size']!='0.25' or saved['request']['settings']!=saved['plan']['settings']:raise AssertionError('Environment/broker/settings pin differs.')
        if version==6:base=ready['base']
    for route,filename,key in (('experiments','experiment.json','experiment_id'),('searches','search.json','search_id'),('walkforwards','walkforward.json','walkforward_id')):
        print('Installed environment: '+route,file=sys.stderr,flush=True)
        created=http('/api/'+route,{**json.loads((example/filename).read_text(encoding='utf-8')),**base})
        path='/api/'+route+'/'+created[key];finish(path);report=http(path+'/report')
        if report['sha256']!=digest(report['report']) or not report['report']['variants']:raise AssertionError('Environment research report differs.')
    contexts=json.loads((example/'replay-contexts.json').read_text(encoding='utf-8'))
    events=[json.loads(line) for line in (example/'events.ndjson').read_text(encoding='utf-8').splitlines()]
    for version in (1,6):
        print(f'Installed environment: recorded phases v{version}, CLI/HTTP',file=sys.stderr,flush=True)
        path=example/f'phase-v{version}.pine';compiled=http('/api/compile',{'source':path.read_text(encoding='utf-8'),'source_name':path.as_posix()})
        identifier=http('/api/replays',{'input_ref':compiled['input_ref'],**contexts,'broker_settings':None,'timeout_seconds':120})['replay_id']
        http(f'/api/replays/{identifier}/events',{'events':events})
        http(f'/api/replays/{identifier}/advance',{'max_events':3,'checkpoint_every':1})
        preview=http(f'/api/replays/{identifier}')['preview']
        expected=[120000,.25] if version==1 else [120000,.25,1,0,0]
        if [row['value'] for row in preview['outputs']]!=expected:raise AssertionError('Recorded open phase differs.')
        http(f'/api/replays/{identifier}/advance',{'max_events':100,'checkpoint_every':1})
        expected_rows=http(f'/api/replays/{identifier}/chart')['rows']
        state=work/f'environment-replay-{version}'
        made=cli('replay','create','--source',path,'--contexts',example/'replay-contexts.json','--state-dir',state,'--timeout','120')['replay_id']
        cli('replay','append','--id',made,'--events',example/'events.ndjson','--state-dir',state)
        cli('replay','advance','--id',made,'--max-events','3','--checkpoint-every','1','--state-dir',state)
        cli('replay','advance','--id',made,'--state-dir',state)
        if cli('replay','chart','--id',made,'--state-dir',state)['rows']!=expected_rows:raise AssertionError('Recorded environment CLI/HTTP differ.')
    return {'environment_cli_http_versions':list(range(1,7)),'environment_saved_ir_10':True,'environment_prepared_jobs':jobs,
        'environment_frozen_metadata':True,'environment_broker_tick':True,'environment_joint_resume':True,'environment_bounded_chart':True,
        'environment_experiment':True,'environment_search_holdout':True,'environment_walkforward':True,'environment_replay_cli_http_versions':[1,6]}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--expected-root',type=Path);args=p.parse_args()
    if sys.version_info[:3]!=(3,14,8) or args.expected_root and args.expected_root.resolve()!=ROOT:raise AssertionError('Use installed Python 3.14.8.')
    from apps.ui.server import Server
    bootstrap="import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('apps.openpine',run_name='__main__')"
    with tempfile.TemporaryDirectory(prefix='openpine-environment-') as temporary:
        work=Path(temporary)
        def cli(*arguments):
            result=subprocess.run([sys.executable,'-X','utf8','-c',bootstrap,str(ROOT),*map(str,arguments)],cwd=work,capture_output=True,text=True,encoding='utf-8',timeout=180)
            if result.returncode:raise AssertionError(result.stdout[-2000:]+' '+result.stderr[-2000:])
            return json.loads(result.stdout) if result.stdout.strip() else None
        server=Server(('127.0.0.1',0),work/'http');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{server.server_port}';token=''
        def http(path,payload=None):return request_json(url,token,path,payload)
        try:token=http('/api/config')['token'];result=verify(http,cli,work)
        finally:server.shutdown();thread.join(timeout=10);server.server_close()
    print(json.dumps({'status':'passed',**result},sort_keys=True))


if __name__=='__main__':main()
