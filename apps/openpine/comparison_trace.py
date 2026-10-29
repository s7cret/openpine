"""Reproduce a pinned difference through existing workers, with explicit compatibility gates."""
import time
from packages.contracts.trace import options
from packages.contracts.storage import RunPlan
from packages.contracts.wire import digest
from packages.execution.identity import implementation_hash
from packages.storage.traces import get,put
from packages.storage.diagnostics import gone
from apps.openpine.comparison_sources import read

def create(service,key):
    m=service.manager;config=service.repository.get(key);state=service.repository.state(key);found=state.get('first_difference')
    if not found or type(found.get('bar')) is not int:raise ValueError('This difference has no primary bar for automatic tracing.')
    prior=get(m.db,'comparison:traces:'+key)
    if prior:return prior
    bar=found['bar'];results=[]
    from apps.openpine.traces import Traces
    traces=Traces(m)
    for ordinal,descriptor in enumerate(config['sources']):
        side=read(m,descriptor);trace_id=digest({'comparison':key,'side':ordinal})[:32]
        if side['identity']['implementation_sha256']!=implementation_hash():
            results.append({'status':'unavailable','reason':'COMPUTATION_INCOMPATIBLE','detail':'Saved data remains comparable; reproducing this old computation requires its matching build.'});continue
        end=min(side['bars'],bar+2)
        if end<=max(0,bar-1):results.append({'status':'unavailable','reason':'BAR_OUTSIDE_PINNED_SIDE'});continue
        selected=options({'start_bar':max(0,bar-1),'end_bar':end})
        with traces.lock,m.lock,m.db.transaction():
            if gone(m.db,'trace',trace_id):results.append({'status':'unavailable','reason':'TRACE_DELETED'});continue
            existing=get(m.db,'trace:config:'+trace_id)
            if existing is None:
                binding={'input_ref':side['input_ref'],'program_sha256':side['program_sha256'],'sha256':descriptor['binding_sha256']}
                trace={'schema_version':'openpine.trace-configuration.1','trace_id':trace_id,'parent_comparison_id':key,'binding':binding,'options':selected,'created_at':time.time()}
                if side['kind']=='job':
                    plan=RunPlan.from_payload(side['plan']);trace.update(parent_job_id=side['id'],request_sha256=digest(side['request']),plan_sha256=digest(side['plan']))
                    binding['plan_sha256']=trace['plan_sha256']
                    put(m.db,'trace:config:'+trace_id,trace);m.enqueue_prepared(side['request'],plan,job_id=trace_id)
                else:
                    from packages.storage.comparison_inputs import clone
                    clone_id=clone(traces.replays.repository,side['tape_ref']);tape=m.objects.get(side['tape_ref'],max_bytes=1024*1024)
                    trace.update(parent_replay_id=side['id'],replay_id=clone_id)
                    binding.update(config_ref=side['config_ref'],accepted_events=tape['events'],input_chain=tape['input_chain'])
                    put(m.db,'trace:config:'+trace_id,trace);put(m.db,'trace:replay-config:'+clone_id,trace);put(m.db,'trace:replay-state:'+trace_id,{'status':'queued','desired':'running'})
            results.append({'status':'created','trace_id':trace_id})
        if side['kind']=='replay' and traces.start and traces.status(trace_id)['job']['status']!='completed':traces.launch(trace_id)
    value={'comparison_id':key,'bar':bar,'sides':results,'basis':'Actual evaluator/broker observations; no second interpreter.'}
    put(m.db,'comparison:traces:'+key,value);return value

def explanation(service,key,side_index=0,start=0,limit=100):
    if type(side_index) is not int or side_index not in (0,1):raise ValueError('Select side 0 or 1.')
    m=service.manager;config=service.repository.get(key);side=read(m,config['sources'][side_index]);traces=get(m.db,'comparison:traces:'+key)
    value={'comparison_id':key,'side':side_index,'first_difference':service.repository.state(key).get('first_difference'),
        'source_locations':side['locations'],'source':None,'evidence':'No trace has been requested.','trace':None,'events':[]}
    content=m.objects.get(side['input_ref'])
    if content.get('source_ref'):content=m.objects.get(content['source_ref'])
    if 'source' in content:value['source']={'source':content['source'],'source_name':content['source_name']}
    if traces:
        selected=traces['sides'][side_index]
        if selected['status']=='created':
            from apps.openpine.traces import Traces
            if gone(m.db,'trace',selected['trace_id']):
                value['evidence']={'status':'unavailable','reason':'TRACE_DELETED'};return value
            page=Traces(m,start=False).page(selected['trace_id'],start,limit)
            value.update(trace=page['status'],events=page['events'],next=page['next'],total=page['total'],evidence='Recorded trace events; causal attribution is limited to their explicit fields.')
        else:value['evidence']=selected
    return value
