"""Keep the composite installed workflows and route their input graph through preparation."""
import uuid
from tools.installed_composite_smoke import verify as composite_verify


def verify(http,cli,work):
    prepared_jobs=0;prepared_research=0;job_keys=[]
    def bridge(path,payload=None):
        nonlocal prepared_jobs,prepared_research
        if path in ('/api/jobs','/api/experiments','/api/searches','/api/walkforwards') and payload is not None:
            config={'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,
                'input_ref':payload['input_ref'],'primary':{'kind':'snapshot','snapshot_id':payload['snapshot_id']},
                'contexts':[{'kind':'snapshot','snapshot_id':b['snapshot_id']} for b in payload.get('contexts',[])],
                'inputs':payload.get('inputs',{}),'timeout_seconds':payload['timeout_seconds'],'gap_policy':'fail'}
            for key in ('checkpoint_interval_bars','pause_after_bars','broker_settings'):
                if key in payload:config[key]=payload[key]
            ready=http('/api/launches',config)
            if ready['status']!='ready' or len(ready['base']['contexts'])!=1:raise AssertionError(ready)
            if path=='/api/jobs':
                prepared_jobs+=1
                first=http('/api/launches/'+ready['launch_id']+'/start',{})
                job_keys.append(first['job_id'])
                if http('/api/launches/'+ready['launch_id']+'/start',{})['job_id']!=first['job_id']:raise AssertionError('Duplicate dispatch.')
                return first
            prepared_research+=1
            payload={**payload,**ready['base']}
        return http(path,payload) if payload is not None else http(path)
    result=composite_verify(bridge,cli,work)
    if prepared_jobs!=4 or prepared_research!=3:raise AssertionError((prepared_jobs,prepared_research))
    for key in job_keys:
        saved=http('/api/jobs/'+key+'/configuration')
        if (saved['job_id']!=key or not saved['source']['source'] or len(saved['datasets'])!=2
                or saved['request']['contexts']!=saved['plan']['contexts']):raise AssertionError(saved)
    return {**result,'prepared_launch_cli_http':True,'prepared_launch_idempotent_dispatch':True,
        'prepared_launch_research_contexts':True,'prepared_launch_saved_configuration':True}
