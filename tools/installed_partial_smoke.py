"""Installed partial fills through prepared jobs, CLI/HTTP, chart and research."""
import json
import sys
import time
import uuid
from tools.specification import ROOT
from tools.partial_exit_oracle import expected
from packages.contracts.wire import digest

def verify(http,cli,work):
    root=ROOT/'examples/partial-exits';jobs=[];base=None
    snapshot=http('/api/snapshots',{'provider':'upload','raw':(root/'bars.json').read_text()})['sha256']
    def finish(path,target='succeeded'):
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            state=http(path)
            if state['status']==target:return state
            if state['status'] in ('failed','cancelled','timed_out','succeeded','paused','completed_with_errors','holdout_failed','no_eligible_candidate'):raise AssertionError(state)
            time.sleep(.1)
        raise TimeoutError(state)
    for version in range(1,7):
        print(f'Installed partial exits: v{version}',file=sys.stderr,flush=True)
        path=root/f'partial-v{version}.pine';compiled=http('/api/compile',{'source':path.read_text(),'source_name':path.as_posix()})
        ready=http('/api/launches',{'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,'input_ref':compiled['input_ref'],
            'primary':{'kind':'snapshot','snapshot_id':snapshot},'contexts':[],'inputs':{'part':4},'timeout_seconds':120,
            'gap_policy':'fail','checkpoint_interval_bars':2,'pause_after_bars':2})
        if ready['status']!='ready':raise AssertionError(ready)
        key=http('/api/launches/'+ready['launch_id']+'/start',{})['job_id'];jobs.append(key)
        finish('/api/jobs/'+key,'paused');http('/api/jobs/'+key+'/resume',{});state=finish('/api/jobs/'+key)
        result=http('/api/jobs/'+key+'/result')['result'];reference=expected(4)
        if state['resumed_from_bar']!=2 or [o['values'] for o in result['outputs']]!=list(reference):raise AssertionError('Partial outputs differ from closed-form independent oracle.')
        stats=result['simulation']['statistics']
        if (stats['equity'],stats['trades'],stats['closed_positions'])!=('91',180,90):raise AssertionError(stats)
        artifact=work/f'partial-ir-{version}.json';override=work/f'partial-inputs-{version}.json';override.write_text('{"part":4}')
        cli('compile','--source',path,'--profile','historical','--output',artifact)
        direct=cli('job','--artifact',artifact,'--bars',root/'bars.json','--contexts',root/'contexts.json','--inputs',override,'--state-dir',work/f'partial-cli-{version}')
        if direct['response']['result']!=result:raise AssertionError('Partial saved CLI/HTTP differs.')
        chart=http(f'/api/jobs/{key}/chart?start=1&limit=2')
        if chart['schema_version']!='openpine.chart-window.3' or not any(e['kind']=='trade' and e['value']['remaining_quantity']=='6' for e in chart['protection']['events']):raise AssertionError('Partial chart omits quantity/residue.')
        if http(f'/api/jobs/{key}/configuration')['plan']['settings']['values']['input:part']!=4:raise AssertionError('Partial frozen settings differ.')
        if version==6:base=ready['base']
    for route,name,field in (('experiments','experiment','experiment_id'),('searches','search','search_id'),('walkforwards','walkforward','walkforward_id')):
        print('Installed partial exits: '+route,file=sys.stderr,flush=True)
        made=http('/api/'+route,{**json.loads((root/(name+'.json')).read_text()),**base});path='/api/'+route+'/'+made[field]
        finish(path);report=http(path+'/report')
        if report['sha256']!=digest(report['report']) or not report['report']['variants']:raise AssertionError('Partial research report differs.')
    return {'partial_cli_http_versions':list(range(1,7)),'partial_prepared_jobs':jobs,'partial_saved_ir':True,
        'partial_independent_quantity_fee_oracle':True,'partial_joint_resume':True,'partial_chart_quantities':True,
        'partial_frozen_settings':True,'partial_experiment':True,'partial_search_holdout':True,'partial_walkforward':True}
