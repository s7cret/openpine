"""Installed loop examples through preparation, worker, CLI and research owners."""

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


def verify(http, cli, work):
    example = ROOT/'examples/loops'
    primary = http('/api/snapshots', {'provider':'upload','raw':(example/'bars.json').read_text(encoding='utf-8')})['sha256']
    higher = http('/api/snapshots', {'provider':'upload','raw':(example/'higher.json').read_text(encoding='utf-8')})['sha256']
    def finish(path, expected='succeeded'):
        deadline = time.monotonic()+180
        while time.monotonic()<deadline:
            value = http(path)
            if value['status']==expected:return value
            if value['status'] in ('failed','cancelled','timed_out','succeeded','paused','completed_with_errors','holdout_failed','no_eligible_candidate'):
                raise AssertionError(value)
            time.sleep(.1)
        raise TimeoutError(value)
    references = {}; jobs = []
    for version, family in ((2,'history-mean'), (5,'while-mean'), (6,'history-mean'), (6,'while-mean')):
        print(f'Installed loops: {family} v{version}, CLI/HTTP and joint resume', file=sys.stderr, flush=True)
        path = example/f'{family}-v{version}.pine'
        compiled = http('/api/compile', {'source':path.read_text(encoding='utf-8'),'source_name':path.as_posix()})
        ready = http('/api/launches', {'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,
            'input_ref':compiled['input_ref'], 'primary':{'kind':'snapshot','snapshot_id':primary},
            'contexts':[{'kind':'snapshot','snapshot_id':higher}], 'inputs':{'length':4},
            'timeout_seconds':120,'gap_policy':'fail','checkpoint_interval_bars':17,'pause_after_bars':17})
        if ready['status']!='ready':raise AssertionError(ready)
        job = http('/api/launches/'+ready['launch_id']+'/start', {})['job_id']; jobs.append(job)
        finish('/api/jobs/'+job,'paused'); http('/api/jobs/'+job+'/resume', {}); state=finish('/api/jobs/'+job)
        result = http('/api/jobs/'+job+'/result')['result']
        if state['resumed_from_bar']!=17 or result['simulation']['statistics']['trades']<1:raise AssertionError(state)
        override = work/f'loop-inputs-{version}-{family}.json'; override.write_text(json.dumps({'length':4}),encoding='utf-8')
        artifact = work/f'loop-ir-{version}-{family}.json'
        cli('compile','--source',path,'--profile','historical','--output',artifact)
        direct = cli('job','--artifact',artifact,'--bars',example/'bars.json','--contexts',example/'contexts.json',
            '--inputs',override,'--state-dir',work/f'loop-cli-{version}-{family}')
        if direct['response']['result']!=result:raise AssertionError('Saved .8 IR CLI differs from worker result.')
        chart = http(f'/api/jobs/{job}/chart?start=35&limit=40')
        if chart['outputs'][0]['values']!=result['outputs'][0]['values'][35:75]:raise AssertionError('Loop chart range differs.')
        saved=http('/api/jobs/'+job+'/configuration')
        if saved['request']['settings']!=saved['plan']['settings']:raise AssertionError('Frozen loop settings changed.')
        references[version,family] = ready['base']
    base=references[6,'history-mean']
    for route, filename, key in (('experiments','experiment.json','experiment_id'),('searches','search.json','search_id'),('walkforwards','walkforward.json','walkforward_id')):
        print('Installed loops: '+route, file=sys.stderr, flush=True)
        created=http('/api/'+route, {**json.loads((example/filename).read_text()), **base})
        path='/api/'+route+'/'+created[key]; finish(path)
        result=http(path+'/report')
        if result['sha256']!=digest(result['report']) or not result['report']['variants']:raise AssertionError('Loop research report differs.')
    return {'loop_cli_http_versions':[2,5,6], 'loop_saved_ir':True, 'loop_prepared_jobs':jobs,
        'loop_joint_resume':True, 'loop_bounded_chart':True, 'loop_frozen_settings':True,
        'loop_experiment':True, 'loop_search_holdout':True, 'loop_walkforward':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--expected-root',type=Path); args=parser.parse_args()
    if sys.version_info[:3]!=(3,14,8) or args.expected_root and args.expected_root.resolve()!=ROOT:raise AssertionError('Use the selected installed Python 3.14.8 root.')
    from apps.ui.server import Server
    bootstrap="import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('apps.openpine',run_name='__main__')"
    with tempfile.TemporaryDirectory(prefix='openpine-loops-') as temporary:
        work=Path(temporary)
        def cli(*arguments):
            p=subprocess.run([sys.executable,'-X','utf8','-c',bootstrap,str(ROOT),*map(str,arguments)],cwd=work,capture_output=True,text=True,encoding='utf-8',timeout=120)
            if p.returncode:raise AssertionError(p.stdout[-2000:]+' '+p.stderr[-2000:])
            return json.loads(p.stdout) if p.stdout.strip() else None
        server=Server(('127.0.0.1',0),work/'http'); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        base=f'http://127.0.0.1:{server.server_port}'; token=''
        def http(path,payload=None):return request_json(base,token,path,payload)
        try:token=http('/api/config')['token']; result=verify(http,cli,work)
        finally:server.shutdown(); thread.join(timeout=10); server.server_close()
    print(json.dumps({'status':'passed','python':sys.version.split()[0],**result},sort_keys=True))


if __name__=='__main__':main()
