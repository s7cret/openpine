"""Installed private dependency graph through CLI/HTTP and research workers."""
import json
from pathlib import Path
import sys
import time
import uuid
from tools.specification import ROOT
from tools.context_graph_oracle import expected_outputs,same
from packages.contracts.wire import digest

def verify(http,cli,work):
    root=ROOT/'examples/context-graph';jobs=[];base=None
    snapshots=[http('/api/snapshots',{'provider':'upload','raw':(root/name).read_text(encoding='utf-8')})['sha256'] for name in ('input.json','higher.json')]
    def finish(path,expected='succeeded'):
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            value=http(path)
            if value['status']==expected:return value
            if value['status'] in ('failed','cancelled','timed_out','succeeded','paused','completed_with_errors','holdout_failed','no_eligible_candidate'):raise AssertionError(value)
            time.sleep(.1)
        raise TimeoutError(value)
    for version in range(1,7):
        print(f'Installed context graph: v{version}',file=sys.stderr,flush=True)
        path=root/f'context-trend-v{version}.pine'
        compiled=http('/api/compile',{'source':path.read_text(),'source_name':path.as_posix()})
        ready=http('/api/launches',{'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,'input_ref':compiled['input_ref'],
            'primary':{'kind':'snapshot','snapshot_id':snapshots[0]},'contexts':[{'kind':'snapshot','snapshot_id':snapshots[1]}],
            'inputs':{'length':4},'timeout_seconds':120,'gap_policy':'fail','checkpoint_interval_bars':17,'pause_after_bars':17})
        if ready['status']!='ready':raise AssertionError(ready)
        key=http('/api/launches/'+ready['launch_id']+'/start',{})['job_id'];jobs.append(key)
        finish('/api/jobs/'+key,'paused');http('/api/jobs/'+key+'/resume',{});state=finish('/api/jobs/'+key)
        result=http('/api/jobs/'+key+'/result')['result']
        if state['resumed_from_bar']!=17 or not all(same(o['values'],e) for o,e in zip(result['outputs'][:4],expected_outputs(version),strict=True)):raise AssertionError('Private graph output differs from independent batch formulas.')
        artifact=work/f'context-graph-ir-{version}.json';override=work/f'context-graph-inputs-{version}.json';override.write_text('{"length":4}')
        cli('compile','--source',path,'--profile','historical','--output',artifact)
        actual=cli('job','--artifact',artifact,'--bars',root/'input.json','--contexts',root/'contexts.json','--inputs',override,'--state-dir',work/f'context-graph-cli-{version}')
        if actual['response']['result']!=result:raise AssertionError('Saved context graph CLI/HTTP differs.')
        if http(f'/api/jobs/{key}/chart?start=21&limit=9')['outputs'][1]['values']!=result['outputs'][1]['values'][21:30]:raise AssertionError('Private graph chart range differs.')
        config=http(f'/api/jobs/{key}/configuration')
        if config['plan']['settings']['values']['input:length']!=4:raise AssertionError('Private graph frozen settings differ.')
        if version==6:base=ready['base']
    for route,name,field in (('experiments','experiment','experiment_id'),('searches','search','search_id'),('walkforwards','walkforward','walkforward_id')):
        print('Installed context graph: '+route,file=sys.stderr,flush=True)
        made=http('/api/'+route,{**json.loads((root/(name+'.json')).read_text()),**base});path='/api/'+route+'/'+made[field]
        finish(path);report=http(path+'/report')
        if report['sha256']!=digest(report['report']) or not report['report']['variants']:raise AssertionError('Private graph research report differs.')
    phases=ROOT/'examples/environment';contexts=json.loads((phases/'replay-contexts.json').read_text());events=[json.loads(line) for line in (phases/'events.ndjson').read_text().splitlines()]
    for version in (1,6):
        path=root/f'phase-v{version}.pine';compiled=http('/api/compile',{'source':path.read_text(),'source_name':path.as_posix()})
        key=http('/api/replays',{'input_ref':compiled['input_ref'],**contexts,'broker_settings':None,'timeout_seconds':120})['replay_id']
        http(f'/api/replays/{key}/events',{'events':events});http(f'/api/replays/{key}/advance',{'max_events':3,'checkpoint_every':1})
        if [o['value'] for o in http(f'/api/replays/{key}')['preview']['outputs']]!=[40.,0]:raise AssertionError('Private graph preview committed state.')
        http(f'/api/replays/{key}/advance',{'max_events':100,'checkpoint_every':1});expected=http(f'/api/replays/{key}/chart')['rows']
        directory=work/f'context-graph-replay-{version}'
        made=cli('replay','create','--source',path,'--contexts',phases/'replay-contexts.json','--state-dir',directory,'--timeout','120')['replay_id']
        cli('replay','append','--id',made,'--events',phases/'events.ndjson','--state-dir',directory)
        cli('replay','advance','--id',made,'--max-events','3','--checkpoint-every','1','--state-dir',directory)
        cli('replay','advance','--id',made,'--state-dir',directory)
        if cli('replay','chart','--id',made,'--state-dir',directory)['rows']!=expected:raise AssertionError('Private graph recorded CLI/HTTP differs.')
    return {'context_graph_cli_http_versions':list(range(1,7)),'context_graph_prepared_jobs':jobs,'context_graph_saved_ir':True,
        'context_graph_independent_batch_oracle':True,'context_graph_joint_resume':True,'context_graph_bounded_chart':True,
        'context_graph_frozen_settings':True,'context_graph_experiment':True,'context_graph_search_holdout':True,
        'context_graph_walkforward':True,'context_graph_replay_cli_http_versions':[1,6]}
