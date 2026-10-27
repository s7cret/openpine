"""Installed saved-IR CLI, HTTP preparation, resume, chart and research."""
import json,time,uuid
from tools.specification import ROOT
from packages.contracts.wire import digest

def verify(http,cli,work):
    root=ROOT/'examples/lower-tf';expected=json.loads((root/'expected.json').read_text());jobs=[];base=None
    snapshots=[http('/api/snapshots',{'provider':'upload','raw':(root/n).read_text()})['sha256'] for n in ('parent.json','lower.json')]
    def finish(path,status='succeeded'):
        until=time.monotonic()+180
        while time.monotonic()<until:
            row=http(path)
            if row['status']==status:return row
            if row['status'] in ('failed','cancelled','timed_out','succeeded','paused','completed_with_errors','holdout_failed','no_eligible_candidate'):raise AssertionError(row)
            time.sleep(.05)
        raise TimeoutError(row)
    for v in (5,6):
        path=root/f'v{v}.pine';compiled=http('/api/compile',{'source':path.read_text(),'source_name':path.as_posix()})
        requirements=http('/api/launches/requirements?input_ref='+compiled['input_ref'])
        assert requirements['contexts'][0]['requests'][0]['kind']=='lower_tf'
        ready=http('/api/launches',{'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,
            'input_ref':compiled['input_ref'],'primary':{'kind':'snapshot','snapshot_id':snapshots[0]},
            'contexts':[{'kind':'snapshot','snapshot_id':snapshots[1]}],'inputs':{},'timeout_seconds':120,
            'gap_policy':'fail','checkpoint_interval_bars':1,'pause_after_bars':2})
        assert ready['status']=='ready',ready
        key=http('/api/launches/'+ready['launch_id']+'/start',{})['job_id'];jobs.append(key)
        finish('/api/jobs/'+key,'paused');http('/api/jobs/'+key+'/resume',{});state=finish('/api/jobs/'+key)
        result=http('/api/jobs/'+key+'/result')['result'];values=[o['values'] for o in result['outputs']]
        assert values==[expected[n] for n in ('prices_sum','children','sma_mean','condition','equity')],values
        artifact=work/f'lower-tf-{v}.json';cli('compile','--source',path,'--profile','historical','--output',artifact)
        other=cli('job','--artifact',artifact,'--bars',root/'parent.json','--contexts',root/'contexts.json','--state-dir',work/f'lower-cli-{v}')
        assert other['response']['result']==result
        assert state['resumed_from_bar']==2
        assert http(f'/api/jobs/{key}/chart?start=1&limit=2')['outputs'][0]['values']==[26.,42.]
        config=http('/api/jobs/'+key+'/configuration');assert config['plan']['contexts'][0]['snapshot_id']==snapshots[1]
        assert len(config['datasets'])==2
        base=ready['base']
    proposals=[('experiments','experiment_id',{'name':'Lower experiment','grid':{'length':[2,3]},'inputs':{},'timeout_seconds':120}),
        ('searches','search_id',{'name':'Lower search','method':'grid','budget':1,'seed':0,'space':{'length':[2]},'inputs':{},
            'timeout_seconds':120,'objective':{'direction':'maximize','metric':'net_profit'},
            'windows':{'selection':{'warmup_start_ms':0,'score_start_ms':240000,'end_ms':480000},
                'holdout':{'warmup_start_ms':240000,'score_start_ms':480000,'end_ms':960000}}}),
        ('walkforwards','walkforward_id',{'name':'Lower walkforward','method':'grid','budget':1,'seed':0,'space':{'length':[2]},'inputs':{},
            'timeout_seconds':120,'objective':{'direction':'maximize','metric':'net_profit'},
            'walkforward':{'folds':1,'max_jobs':2,'mode':'rolling','start_ms':240000,'step_ms':240000,
                'test_ms':240000,'train_ms':240000,'warmup_ms':240000}})]
    for route,field,payload in proposals:
        made=http('/api/'+route,{**payload,**base});url='/api/'+route+'/'+made[field];finish(url)
        report=http(url+'/report');assert report['sha256']==digest(report['report']) and report['report']['variants']
    return {'lower_tf_cli_http_versions':[5,6],'lower_tf_prepared_jobs':jobs,'lower_tf_saved_ir':True,
        'lower_tf_private_expressions':True,'lower_tf_resume':True,'lower_tf_chart_range':True,
        'lower_tf_experiment_search_walkforward':True}
