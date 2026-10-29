"""Diagnostic reruns share normal plans, workers, controls and resource admission."""
import re,uuid,threading,time
from dataclasses import replace
from packages.contracts.trace import options
from packages.contracts.wire import digest
from packages.storage.traces import get,put,rows
from apps.openpine.launches import Launches
from apps.openpine.bound_passport import binding,validate

def identifier(key):
    if type(key) is not str or not re.fullmatch('[0-9a-f]{32}',key):raise ValueError('Invalid diagnostic identity.')
    return key

def binding_hash(value):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{64}',value):raise ValueError('Provide a 64-hex binding hash.')
    return value

class Traces:
    def __init__(self,manager,*,start=True):
        self.manager=manager;self.start=start
        if not hasattr(manager,'trace_coordinator'):manager.trace_coordinator={'threads':{},'stops':{},'active':set(),'lock':threading.RLock()}
        coordinator=manager.trace_coordinator;self.threads=coordinator['threads'];self.stops=coordinator['stops'];self.active=coordinator['active'];self.lock=coordinator['lock']
        from apps.openpine.replay import Replays
        self.replays=Replays(manager)
    def close(self):
        with self.lock:
            for event in self.stops.values():event.set()
            threads=list(self.threads.values())
        for thread in threads:thread.join(timeout=10)
    def create(self,payload):
        if type(payload) is dict and 'replay_id' in payload:return self.create_replay(payload)
        if type(payload) is not dict or payload.keys()!={'request_id','launch_id','binding_sha256','options'}:raise ValueError('Provide a saved launch, binding, request id and trace options.')
        key=identifier(payload['request_id']);source=identifier(payload['launch_id']);selected=options(payload['options'])
        from packages.storage.diagnostics import gone
        if gone(self.manager.db,'trace',key):raise ValueError('Deleted diagnostic request ids remain reserved.')
        binding_hash(payload['binding_sha256'])
        m=self.manager;service=Launches(m,None)
        with m.limiter.slot('prepare'),service.lock:
            pinned,prepared,plan=binding(service,source)
            if selected['end_bar']>m.market.manifest(plan.snapshot_id)['identity']['count']:raise ValueError('Selected trace bars exceed the pinned input.')
            if pinned['sha256']!=payload['binding_sha256']:raise ValueError('Selected preparation changed; check it again.')
            config={'schema_version':'openpine.trace-configuration.1','trace_id':key,'parent_launch_id':source,
                'binding':pinned,'options':selected,'request_sha256':digest(prepared['request']),'plan_sha256':digest(plan.payload())}
            saved=get(m.db,'trace:config:'+key)
            if saved is not None:
                if {k:v for k,v in saved.items() if k!='created_at'}!=config:raise ValueError('Diagnostic request id was reused with different inputs.')
                return self.status(key)
            checked=validate(service,source,pinned['sha256'])
            if checked['status']!='validated':raise ValueError(checked['issues'][0]['diagnostic']['message'])
            with m.lock,m.db.transaction():
                concurrent=get(m.db,'trace:config:'+key)
                if concurrent is not None:
                    if {k:v for k,v in concurrent.items() if k!='created_at'}!=config:raise ValueError('Diagnostic request id was reused with different inputs.')
                    return self.status(key)
                put(m.db,'trace:config:'+key,{**config,'created_at':time.time()})
                m.enqueue_prepared(prepared['request'],plan,job_id=key)
        return self.status(key)
    def status(self,key):
        identifier(key);m=self.manager
        from packages.storage.diagnostics import trace_config
        config=trace_config(m.db,key)
        if 'replay_id' in config:
            replay=self.replays.repository.status(config['replay_id']);ref=get(m.db,'trace:replay-result:'+config['replay_id'])
            report=m.objects.get(ref,max_bytes=256*1024) if ref else None
            state=get(m.db,'trace:replay-state:'+key,{'status':'queued'})
            if state['status']=='running' and key not in self.active and not (key in self.threads and self.threads[key].is_alive()):state={**state,'status':'interrupted'}
            return {'schema_version':'openpine.trace-status.1','trace_id':key,'configuration':config,'job':state,'replay':replay,
                'trace_ref':ref,'trace':{k:v for k,v in report.items() if k not in ('parts','configuration')} if report else None,
                'isolated_rerun':True,'scope':'frozen_replay_prefix','whole_program_execution_verified':False}
        state=m.repository.get(key);ref=get(m.db,'trace:result:'+key)
        saved=get(m.db,'trace:checkpoint:'+key)
        if saved and saved['checkpoint_id']==state.get('checkpoint_id'):
            checkpoint=m.objects.get(saved['trace_ref'],max_bytes=256*1024)
            final=m.objects.get(ref,max_bytes=256*1024) if ref else None
            if final is None or checkpoint['events']>final['events'] or checkpoint['attempt_id']==state.get('attempt_id') and final['attempt_id']!=state.get('attempt_id'):ref=saved['trace_ref']
        report=m.objects.get(ref,max_bytes=256*1024) if ref else None
        return {'schema_version':'openpine.trace-status.1','trace_id':key,'configuration':config,'job':state,
            'trace_ref':ref,'trace':{k:v for k,v in report.items() if k not in ('parts','configuration')} if report else None,
            'warning':get(m.db,'trace:warning:'+key),'isolated_rerun':True}
    def page(self,key,start=0,limit=200):
        with self.manager.limiter.slot('read'):
            status=self.status(key)
            if not status['trace_ref']:return {'events':[],'total':0,'next':0,'status':status}
            return {**rows(self.manager.objects,status['trace_ref'],start,limit),'status':status}
    def source(self,key):
        config=self.status(key)['configuration'];m=self.manager
        content=m.objects.get(config['binding']['input_ref'])
        if 'source_ref' in content:content=m.objects.get(content['source_ref'])
        if 'source' not in content:return {'source':None,'source_name':None}
        import hashlib
        return {'source':content['source'],'source_name':content['source_name'],'source_sha256':hashlib.sha256(content['source'].encode()).hexdigest()}
    def control(self,key,action):
        current=self.status(key)
        if action not in ('pause','resume','cancel'):raise ValueError('Unknown diagnostic control.')
        if 'replay' in current:
            if current['job']['status']=='completed':raise ValueError('Diagnostic prefix is already complete.')
            if action=='resume':self.launch(key)
            else:
                state={**current['job'],'desired':'cancelled' if action=='cancel' else 'paused'}
                put(self.manager.db,'trace:replay-state:'+key,state)
                if key in self.stops:self.stops[key].set()
                else:put(self.manager.db,'trace:replay-state:'+key,{**state,'status':state['desired']})
            return self.status(key)
        getattr(self.manager,action)(key)
        return self.status(key)

    def create_replay(self,payload):
        if payload.keys()!={'request_id','replay_id','binding_sha256','options'}:raise ValueError('Provide replay, binding, request id and trace options.')
        key=identifier(payload['request_id']);source=identifier(payload['replay_id']);selected=options(payload['options']);m=self.manager
        from packages.storage.diagnostics import gone
        if gone(m.db,'trace',key):raise ValueError('Deleted diagnostic request ids remain reserved.')
        binding_hash(payload['binding_sha256'])
        from apps.openpine.replay_passport import inspect
        with self.lock,m.limiter.slot('prepare'):
            existing=get(m.db,'trace:config:'+key)
            if existing:
                if existing.get('parent_replay_id')!=source or existing['binding']['sha256']!=payload['binding_sha256'] or existing['options']!=selected:raise ValueError('Diagnostic request id was reused.')
                return self.status(key)
            report=inspect(self.replays,source,expected=payload['binding_sha256'])['passport']
            if report['status']=='rejected':raise ValueError(report['replay_validation']['issues'][0]['diagnostic']['message'])
            bound=report['replay_binding']
            if not 1<=bound['accepted_events']<=10000000:raise ValueError('Diagnostic replay requires a frozen prefix of 1 to 10000000 events.')
            with m.db.transaction():
                clone=self.replays.repository.clone(source,(bound['config_ref'],bound['accepted_events'],bound['input_chain']))
                config={'schema_version':'openpine.trace-configuration.1','trace_id':key,'parent_replay_id':source,'replay_id':clone,'binding':bound,'options':selected,'created_at':time.time()}
                put(m.db,'trace:config:'+key,config);put(m.db,'trace:replay-config:'+clone,config)
                put(m.db,'trace:replay-state:'+key,{'status':'queued','desired':'running'})
        if self.start:self.launch(key)
        return self.status(key)

    def launch(self,key,*,background=True):
        with self.lock:
            for old in list(self.threads):
                if old not in self.active and not self.threads[old].is_alive():self.threads.pop(old);self.stops.pop(old,None)
            if key in self.threads and self.threads[key].is_alive():return
            if sum(t.is_alive() for t in self.threads.values())>=8:raise ValueError('Diagnostic replay queue is full.')
            self.stops[key]=threading.Event()
            if background:
                thread=threading.Thread(target=self.advance,args=(key,),daemon=True);self.threads[key]=thread;thread.start()
        if not background:self.advance(key)

    def advance(self,key):
        m=self.manager;config=get(m.db,'trace:config:'+key);event=self.stops.setdefault(key,threading.Event())
        self.active.add(key)
        try:
            put(m.db,'trace:replay-state:'+key,{'status':'running','desired':'running'})
            self.replays.advance(config['replay_id'],checkpoint_every=100,cancelled=event.is_set)
            if self.replays.status(config['replay_id'])['pending_events']:raise ValueError('Diagnostic prefix did not complete.')
            state={'status':'completed','desired':'completed'}
        except InterruptedError:
            desired=get(m.db,'trace:replay-state:'+key,{}).get('desired','paused')
            state={'status':desired if desired in ('paused','cancelled') else 'interrupted','desired':desired}
        except Exception as error:
            from packages.execution.limits import CoordinatorBusy
            state={'status':'waiting_resources' if isinstance(error,CoordinatorBusy) else 'failed','error':str(error)[:1024]}
        try:put(m.db,'trace:replay-state:'+key,state)
        finally:self.active.discard(key)
