"""Finite experiment composition and presets over the existing local job scheduler."""
from dataclasses import replace
import itertools
import re
import threading

from packages.contracts.application import TERMINAL_STATUSES
from packages.contracts.inputs import input_schema,settings_for,admitted_settings,transport_values
from packages.contracts.wire import digest
from packages.execution.artifacts import import_program
from packages.execution.identity import plan_identity
from packages.execution.session import Session
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.storage.experiments import ExperimentRepository

MAX_VARIANTS=256


class Experiments:
    def __init__(self,manager,*,start=True):
        self.manager=manager;self.repository=ExperimentRepository(manager.db)
        self.stopping=threading.Event();self.thread=None;self.fatal=None;self.cursor=0
        self.session=None
        if start:
            self.thread=threading.Thread(target=self._loop,name='openpine-experiments',daemon=True);self.thread.start()

    def _program(self,input_ref):
        content=self.manager.objects.get(input_ref)
        if 'artifact_ref' not in content:raise ValueError('Experiments and presets require successfully compiled input.')
        program=import_program(self.manager.objects.get(content['artifact_ref']))
        self.session=self.session or Session(profile_id=HISTORICAL_PROFILE_ID)
        self.session.validate_program(program)
        if not input_schema(program)['definitions']:raise ValueError('This program declares no configurable inputs.')
        return program

    def presets(self,input_ref):
        with self.manager.limiter.slot('read'):
            program=self._program(input_ref)
            rows=self.repository.presets(input_schema(program)['sha256'])
            for row in rows:row['inputs']=transport_values(row['settings']['values'])
            return {'input_schema':input_schema(program),'presets':rows}

    def save_preset(self,payload):
        if type(payload) is not dict or payload.keys()!={'input_ref','name','inputs','expected_revision'}:raise ValueError('Invalid preset fields.')
        name=payload['name'];revision=payload['expected_revision']
        if type(name) is not str or not name.strip() or len(name)>128 or revision is not None and (type(revision) is not int or revision<1):raise ValueError('Invalid preset name/revision.')
        with self.manager.limiter.slot('prepare'):
            program=self._program(payload['input_ref']);settings=settings_for(program,payload['inputs'])
            row=self.repository.save_preset(settings['input_schema_sha256'],name.strip(),payload['input_ref'],settings,revision)
            return {**row,'inputs':transport_values(settings['values'])}

    def create(self,payload,*,request_id=None):
        from .research_operations import Creation
        creation=Creation(self.manager,'experiment',payload,request_id)
        if existing:=creation.existing():return existing
        with self.manager.limiter.slot('prepare'),self.manager.lock:
            if existing:=creation.existing():return existing
            manifest,prepared=self.prepare(payload)
            with self.manager.db.transaction():
                identifier=self.repository.create(manifest,prepared,transaction=False)
                creation.publish(identifier)
        return creation.result(self,identifier)

    def prepare(self,payload):
        required={'name','input_ref','snapshot_id','timeout_seconds'}
        optional={'variants','grid','inputs','primary_context','contexts','broker_settings','max_attempts','checkpoint_interval_bars','evaluation_window','magnifier'}
        if type(payload) is not dict or not required<=payload.keys()<=required|optional or ('variants' in payload)==('grid' in payload):raise ValueError('Experiment requires variants or grid and a fixed compiled input/dataset.')
        if type(payload['name']) is not str or not payload['name'].strip() or len(payload['name'])>128:raise ValueError('Experiment name requires 1..128 characters.')
        attempts=payload.get('max_attempts',2);interval=payload.get('checkpoint_interval_bars',1000)
        if type(attempts) is not int or not 1<=attempts<=3 or type(interval) is not int or not 1<=interval<=10000000:raise ValueError('Invalid experiment retry/checkpoint policy.')
        with self.manager.lock,self.manager.limiter.slot('prepare'):
            if len(self.repository.groups(active=True,limit=16))>=16:raise ValueError('At most 16 unfinished running experiments.')
            program=self._program(payload['input_ref']);schema=input_schema(program)
            if 'grid' in payload:
                grid=payload['grid']
                if type(grid) is not dict or not 1<=len(grid)<=64 or any(type(k) is not str for k in grid):raise ValueError('Grid requires named input axes.')
                count=1
                for values in grid.values():
                    if type(values) is not list or not values:raise ValueError('Every grid axis requires a nonempty array.')
                    count*=len(values)
                    if count>MAX_VARIANTS:raise ValueError('Grid exceeds 256 variants.')
                keys=sorted(grid);variants=[dict(zip(keys,values,strict=True)) for values in itertools.product(*(grid[k] for k in keys))]
            else:variants=payload['variants']
            if type(variants) is not list or not 1<=len(variants)<=MAX_VARIANTS or any(type(v) is not dict for v in variants):raise ValueError('Use 1..256 explicit input override objects.')
            base_request={'schema_version':'openpine.job-request.6',**{k:payload[k] for k in ('input_ref','snapshot_id','timeout_seconds')},
                'inputs':payload.get('inputs',{}),**{k:payload[k] for k in ('primary_context','contexts','broker_settings') if k in payload}}
            if 'evaluation_window' in payload:base_request.update(schema_version='openpine.job-request.7',evaluation_window=payload['evaluation_window'])
            if payload.get('magnifier') is not None:base_request.update(schema_version='openpine.job-request.8',magnifier=payload['magnifier'])
            base_settings=settings_for(program,payload.get('inputs',{}));aliases={d['name']:d['input_id'] for d in schema['definitions']}
            known=set(aliases.values());prepared=[];seen=set()
            for variant in variants:
                values=dict(base_settings['values']);supplied=set()
                for key,value in variant.items():
                    identifier=key if key in known else aliases.get(key)
                    if identifier is None or identifier in supplied:raise ValueError('Unknown/duplicate input axis: '+str(key))
                    supplied.add(identifier);values[identifier]=value
                settings=settings_for(program,values)
                if settings['sha256'] in seen:raise ValueError('Experiment variants normalize to duplicate settings.')
                seen.add(settings['sha256']);configured={**base_request,'settings':settings};configured.pop('inputs',None)
                request=self.manager.preparation.freeze(configured)
                plan=replace(self.manager.preparation.build_plan(request,self.manager.policy),max_attempts=attempts,checkpoint_interval_bars=interval)
                prepared.append((request,plan))
            base=prepared[0][0]
            from packages.contracts.prepared_requests import uses_prepared,graph
            manifest={'schema_version':'openpine.experiment.1','name':payload['name'].strip(),'input_ref':base['input_ref'],
                'snapshot_id':base['snapshot_id'],'input_schema_sha256':schema['sha256'],
                'variants':[{'request_sha256':digest(request),'plan_sha256':digest(plan.payload())} for request,plan in prepared],
                **({'context_graphs':[graph(program,r['settings']) for r,_ in prepared]} if uses_prepared(program) else {})}
            return manifest,prepared

    def status(self,identifier,*,start=0,limit=50):
        if type(identifier) is not str or not re.fullmatch('[0-9a-f]{32}',identifier):raise ValueError('Invalid experiment identity.')
        if type(start) is not int or start<0 or type(limit) is not int or not 1<=limit<=100:raise ValueError('Experiment page requires start>=0, limit 1..100.')
        with self.manager.limiter.slot('read'):
            manifest,sha=self.repository.manifest(identifier);group=self.repository.group(identifier);counts=self.repository.counts(identifier)
            total=sum(counts.values());done=sum(counts.get(status,0) for status in TERMINAL_STATUSES)
            status='cancelled' if group['desired']=='cancelled' else ('pausing' if counts.get('running',0) else 'paused') if group['desired']=='paused' else 'succeeded' if counts.get('succeeded',0)==total else 'completed_with_errors' if done==total else 'running'
            rows=[]
            for variant in self.repository.variants(identifier,start=start,limit=limit):
                ordinal=variant['ordinal']
                if manifest['variants'][ordinal]!={'request_sha256':digest(variant['request']),'plan_sha256':digest(variant['plan'].payload())}:raise ValueError('Variant differs from the immutable experiment manifest.')
                state=self.manager.get(variant['job_id']) if variant['job_id'] else None;metrics={}
                if state and state['result_id']:
                    response=self.manager.objects.get(state['result_id'],max_bytes=32*1024*1024)
                    result=response.get('result') or {};simulation=result.get('simulation')
                    if simulation:metrics=simulation.get('statistics',{})
                    else:metrics={'outputs':[{'title':o['title'],'last':o.get('last')} for o in result.get('outputs',[])[:32]]}
                rows.append({'ordinal':ordinal,'job_id':variant['job_id'],'status':state['status'] if state else 'skipped' if group['desired']=='cancelled' else 'planned',
                    **({'context_graph':manifest['context_graphs'][ordinal]} if 'context_graphs' in manifest else {}),
                    'settings':variant['plan'].settings,'inputs':transport_values(variant['plan'].settings['values']),'progress':{'completed':state['completed'],'total':state['total']} if state else None,
                    'metrics':metrics,'error':state.get('error') if state else None})
            return {'experiment_id':identifier,'name':manifest['name'],'manifest_sha256':sha,'status':status,'desired':group['desired'],
                'error':group['error'] or self.fatal,'input_ref':manifest['input_ref'],'snapshot_id':manifest['snapshot_id'],
                'counts':counts,'total':total,'start':start,'next':start+len(rows),'variants':rows}

    def list(self):
        return [{k:v for k,v in self.status(group['experiment_id'],limit=1).items() if k!='variants'} for group in self.repository.groups(limit=50)]

    def control(self,identifier,action,*,search_owner=False,include_status=True):
        if action not in ('pause','resume','cancel'):raise ValueError('Unknown experiment control.')
        with self.manager.lock:
            if not search_owner and self.manager.db.execute('SELECT 1 FROM searches WHERE selection_id=? OR holdout_id=?',(identifier,identifier)):
                raise ValueError('Control this experiment through its owning search.')
            group=self.repository.group(identifier)
            counts=self.repository.counts(identifier)
            if sum(counts.values())==sum(counts.get(status,0) for status in TERMINAL_STATUSES):
                return self.status(identifier) if include_status else None
            if group['desired']=='cancelled' and action!='cancel':raise ValueError('An explicitly cancelled experiment cannot resume; create a new group.')
            if action=='resume' and group['desired']!='running' and len(self.repository.groups(active=True,limit=16))>=16:raise ValueError('At most 16 unfinished running experiments.')
            desired={'pause':'paused','resume':'running','cancel':'cancelled'}[action]
            self.repository.control(identifier,desired)
            if action in ('pause','cancel'):
                for job in self.repository.owned_jobs(identifier):getattr(self.manager,action)(job)
        return self.status(identifier) if include_status else None

    def tick(self):
        with self.manager.lock:
            if self.manager.stopping.is_set() or self.manager.fatal or self.stopping.is_set():return
            groups=self.repository.groups(active=True,limit=16)
            if not groups:return
            offset=self.cursor%len(groups);groups=groups[offset:]+groups[:offset];self.cursor+=1
            for group in groups:
                if self.repository.owned_active()>=self.manager.policy.workers:return
                if len(self.manager.repository.active())>=self.manager.capacity:return
                identifier=group['experiment_id']
                try:
                    paused=self.repository.owned_jobs(identifier,('paused',))
                    if paused:self.manager.resume(paused[0]);continue
                    variant=self.repository.pending(identifier)
                    if variant is None:continue
                    if (variant['plan'].execution_sha256,variant['plan'].spec_lock_sha256)!=plan_identity():raise ValueError('Experiment requires another execution/specification version; its frozen plans cannot be silently replaced.')
                    admitted_settings(self._program(variant['request']['input_ref']),variant['plan'].settings)
                    self.manager.enqueue_prepared(variant['request'],variant['plan'],job_id=variant['candidate_id'],experiment_variant=(identifier,variant['ordinal']))
                except OpenPineError as error:
                    if error.diagnostic.code in ('COORDINATOR_BUSY','JOB_QUEUE_FULL'):continue
                    self.repository.control(identifier,'paused',error=str(error))
                except (ValueError,KeyError,TypeError) as error:self.repository.control(identifier,'paused',error=str(error))

    def _loop(self):
        while not self.stopping.wait(.1):
            try:self.tick()
            except Exception as error:self.fatal=str(error);return

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=15)
            if self.thread.is_alive():raise RuntimeError('Experiment dispatcher did not stop.')
