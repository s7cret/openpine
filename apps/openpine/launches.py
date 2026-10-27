"""Durable preparation over the existing history service, snapshots and job scheduler."""
from dataclasses import asdict,replace
import threading

from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.contracts.launch import validate_launch,hex_id
from packages.contracts.contexts import MarketContext
from packages.contracts.context_ir import request_nodes,request_flags
from packages.contracts.calendar import requestable
from packages.contracts.inputs import settings_for,transport_values
from packages.contracts.market import HistoryRequest
from packages.contracts.marketdata import DataQuery
from packages.contracts.wire import digest,loads
from packages.execution.artifacts import import_program
from packages.execution.session import Session
from packages.contracts.calendar import floor_open,next_open
from packages.marketdata.coverage import coverage_report
from packages.storage.launch import LaunchRepository


class Launches:
    def __init__(self,manager,history):
        self.manager,self.history=manager,history
        self.repository=LaunchRepository(manager.db)
        self.lock=threading.RLock();self.session=None

    def program(self,input_ref):
        hex_id(input_ref);frozen=self.manager.objects.get(input_ref)
        if 'artifact_ref' not in frozen:raise ValueError('Prepare a successfully compiled program first.')
        program=import_program(self.manager.objects.get(frozen['artifact_ref']))
        self.session=self.session or Session(profile_id=HISTORICAL_PROFILE_ID)
        self.session.validate_program(program)
        return program

    def requirements(self,input_ref,inputs=None,*,settings=None,primary_context=None):
        return requirements_for(self.program(input_ref),input_ref,inputs,settings=settings,primary_context=primary_context)

    def history_spec(self,payload):
        names={'input_ref','snapshot_id','symbol','timeframe','window','gap_policy','source'}
        if type(payload) is not dict or not names<=payload.keys()<=names|{'inputs'}:raise ValueError('Invalid context acquisition proposal.')
        primary=self.primary_metadata({'kind':'snapshot','snapshot_id':payload['snapshot_id']})
        requirements=self.requirements(payload['input_ref'],payload.get('inputs'),primary_context=primary)
        required={(r['symbol'],r['timeframe']) for r in requirements['contexts']}
        if (payload['symbol'],payload['timeframe']) not in required:raise ValueError('Context does not belong to this program.')
        primary=self.manager.market.manifest(payload['snapshot_id'])['identity'];window=payload['window']
        if window is not None:
            from packages.contracts.optimization import evaluation_window
            evaluation_window(window)
        start=window['warmup_start_ms'] if window else primary['first_ms']
        end=window['end_ms'] if window else next_open(primary['last_ms'],primary['timeframe'])
        from packages.contracts.market import InstrumentKey
        row=next(r for r in requirements['contexts'] if (r['symbol'],r['timeframe'])==(payload['symbol'],payload['timeframe']))
        stop=next_open(end-1,payload['timeframe']) if any(r['historical_future'] for r in row['requests']) else floor_open(end,payload['timeframe'])
        if floor_open(start,payload['timeframe'])>=stop:raise ValueError('Select enough warmup history to include a closed requested bar, or bind an existing snapshot.')
        request=HistoryRequest(InstrumentKey.from_pine(payload['symbol']),payload['timeframe'],
            floor_open(start,payload['timeframe']),stop,
            payload['gap_policy'],source=payload['source'])
        from packages.marketdata.timeframes import check
        check(request.timeframe)
        return {'kind':'history','request':request.payload()}

    def _source_pair(self,spec):
        if spec['kind']=='snapshot':
            identity=self.manager.market.manifest(spec['snapshot_id'])['identity']
            return identity['symbol'],identity['timeframe']
        request=HistoryRequest.from_payload(spec['request'])
        return request.instrument.pine_symbol,request.timeframe

    def primary_metadata(self,spec):
        symbol,tf=self._source_pair(spec)
        return MarketContext('primary',symbol,tf,'launch-metadata.1')

    def _check_graph(self,config):
        from packages.marketdata.timeframes import check
        for spec in [config['primary'],*config['contexts']]:
            if spec['kind']=='history':
                history=HistoryRequest.from_payload(spec['request']);check(history.timeframe)
                if (history.instrument.exchange,history.instrument.market,history.instrument.price_kind)!=('binance','spot','trade'):
                    raise ValueError('The configured provider serves Binance Spot trade candles.')
        requirements=self.requirements(config['input_ref'],config['inputs'],primary_context=self.primary_metadata(config['primary']));primary=self._source_pair(config['primary'])
        pairs=[primary,*[self._source_pair(spec) for spec in config['contexts']]]
        if len(set(pairs))!=len(pairs):raise ValueError('Ambiguous symbol/timeframe sources.')
        required={(r['symbol'],r['timeframe']) for r in requirements['contexts']}
        if not required<=set(pairs):raise ValueError('Missing contexts: '+', '.join(f'{s} / {tf}' for s,tf in sorted(required-set(pairs))))
        if set(pairs[1:])-required:raise ValueError('Additional sources must belong to a written request.')
        from packages.contracts.lower_tf import requestable as lower_requestable
        for row in requirements['contexts']:
            for request in row['requests']:
                if request.get('kind')=='lower_tf':
                    if not lower_requestable(primary[1],row['timeframe']) and not request['ignore_invalid_timeframe']:
                        raise ValueError('Lower requests require a smaller divisible UTC minute interval.')
                elif not requestable(primary[1],row['timeframe']):raise ValueError('security requires a supported same/higher context interval.')
        settings_for(self.program(config['input_ref']),config['inputs'])
        return requirements

    def create(self,config):
        validate_launch(config)
        with self.lock,self.manager.lock,self.manager.limiter.slot('prepare'):
            self._check_graph(config);self.repository.create(config)
        return self.status(config['request_id'])

    def _prepare(self,config,state):
        manager=self.manager;ids=[s['snapshot_id'] for s in state['sources']]
        window=config.get('window')
        if window:
            ids[0]=manager.market.slice(ids[0],window['warmup_start_ms'],window['end_ms'])
        primary=manager.market.manifest(ids[0]);p=primary['identity']
        start=window['warmup_start_ms'] if window else p['first_ms']
        end=window['end_ms'] if window else next_open(p['last_ms'],p['timeframe'])
        if window and (not p['first_ms']<=window['score_start_ms']<=p['last_ms'] or next_open(p['last_ms'],p['timeframe'])>end):
            raise ValueError('The primary snapshot has no bars in its evaluation interval.')
        reqs=self.requirements(config['input_ref'],config['inputs'],primary_context=self.primary_metadata({'kind':'snapshot','snapshot_id':ids[0]}));modes={(r['symbol'],r['timeframe']):{n['lookahead'] for n in r['requests']} for r in reqs['contexts']}
        reports=[];contexts=[]
        for index,sha in enumerate(ids):
            manifest=manager.market.manifest(sha);identity=manifest['identity'];tf=identity['timeframe']
            a=start if index==0 else floor_open(start,tf)
            b=end if index==0 or 'on' not in modes.get((identity['symbol'],tf),()) else next_open(end-1,tf)
            if index:b=floor_open(b,tf)
            if window and index:
                # Keep the explicitly selected warmup range; never resample candles.
                ids[index]=manager.market.slice(sha,floor_open(window['warmup_start_ms'],tf),next_open(window['end_ms']-1,tf))
                manifest=manager.market.manifest(ids[index]);identity=manifest['identity']
            context=MarketContext(identity['context_id'],identity['symbol'],tf,manifest['provenance']['provider'])
            if index:contexts.append({'context':asdict(context),'snapshot_id':ids[index]})
            else:primary_context=asdict(context)
            report=None
            if a<b:
                query=DataQuery(identity['symbol'],tf,a,b)
                report=coverage_report(query,manager.market.source(ids[index]).rows(a,b),as_of=b)
                if not report['requested_complete'] and config['gap_policy']=='fail':
                    raise ValueError(f'Incomplete data for {identity["symbol"]} / {tf}: {report["counts"]}. Choose an explicit gap policy.')
            reports.append({'context':asdict(context),'snapshot_id':ids[index],'identity':identity,'coverage':report,
                'loaded_start_ms':identity['first_ms'],'loaded_end_ms':next_open(identity['last_ms'],tf),
                'required_start_ms':a,'required_end_ms':b})
        if len({r['context']['context_id'] for r in reports})!=len(reports):raise ValueError('Context ids must be unique across snapshots.')
        request={'schema_version':'openpine.job-request.6','input_ref':config['input_ref'],'snapshot_id':ids[0],
            'timeout_seconds':config['timeout_seconds'],'inputs':config['inputs'],'primary_context':primary_context,'contexts':contexts}
        if config.get('broker_settings') is not None:request['broker_settings']=config['broker_settings']
        if window and window['score_start_ms']>window['warmup_start_ms']:
            request.update(schema_version='openpine.job-request.7',evaluation_window=window)
        if config.get('magnifier') is not None:request.update(schema_version='openpine.job-request.8',magnifier=config['magnifier'])
        frozen=manager.preparation.freeze(request)
        plan=replace(manager.preparation.build_plan(frozen,manager.policy),checkpoint_interval_bars=config.get('checkpoint_interval_bars',100000))
        pause=config.get('pause_after_bars')
        if pause is not None and pause>p['count']:raise ValueError('Pause boundary exceeds the prepared input.')
        return {'schema_version':'openpine.prepared-launch.1','request':frozen,'plan':plan.payload(),
            'requirements':reqs,'datasets':reports,'window':window,'gap_policy':config['gap_policy']}

    def status(self,key):
        with self.lock,self.manager.lock:
            row=self.repository.get(key);config,state=row['config'],row['state']
            if state['status']=='preparing':
                try:
                    for spec,selected in zip([config['primary'],*config['contexts']],state['sources'],strict=True):
                        if selected['snapshot_id']:continue
                        if selected['task_id'] is None:
                            selected['task_id']=self.history.submit(spec['request'])['task_id'];self.repository.save(state)
                        task=self.history.status(selected['task_id'])
                        if task['status'] in ('failed','cancelled','empty'):raise ValueError('History preparation stopped: '+str(task.get('error') or task['status']))
                        if task['status']=='interrupted':self.history.resume(selected['task_id'])
                        if task['status']=='succeeded':selected['snapshot_id']=task['snapshot_id'];self.repository.save(state)
                    if all(s['snapshot_id'] for s in state['sources']):
                        with self.manager.limiter.slot('prepare'):prepared=self._prepare(config,state)
                        state.update(prepared_ref=self.manager.objects.put(prepared),status='ready',error=None)
                        self.repository.save(state)
                except (ValueError,KeyError) as error:
                    state.update(status='failed',error=str(error));self.repository.save(state)
                except OpenPineError as error:
                    if error.diagnostic.code!='COORDINATOR_BUSY':
                        state.update(status='failed',error=error.diagnostic.message);self.repository.save(state)
            result={**state,'config':config}
            if state['prepared_ref']:
                prepared=self.manager.objects.get(state['prepared_ref'])
                result.update(prepared=prepared,base=self._base(prepared))
            return result

    @staticmethod
    def _base(prepared):
        request=prepared['request'];settings=request.get('settings')
        base={k:request[k] for k in ('input_ref','snapshot_id','timeout_seconds','primary_context','contexts','broker_settings','magnifier') if k in request}
        base['inputs']=transport_values(settings['values']) if settings else {}
        return base

    def research_base(self,key,snapshots):
        if type(snapshots) is not list or len(snapshots)>7 or any(type(s) is not str for s in snapshots) or len(set(snapshots))!=len(snapshots):raise ValueError('Choose at most seven distinct catalogue snapshots.')
        state=self.repository.get(key)['state']
        if state['status'] not in ('ready','submitted') or not state['prepared_ref']:raise ValueError('Prepare the launch before choosing research datasets.')
        base=self._base(self.manager.objects.get(state['prepared_ref']));contexts=list(base.get('contexts',[]));primary=base['primary_context']
        known={(primary['symbol'],primary['timeframe']),*((b['context']['symbol'],b['context']['timeframe']) for b in contexts)}
        selected={b['snapshot_id'] for b in contexts}|{base['snapshot_id']}
        for sha in snapshots:
            hex_id(sha)
            if sha in selected:continue
            manifest=self.manager.market.manifest(sha);identity=manifest['identity'];pair=identity['symbol'],identity['timeframe']
            if pair in known:raise ValueError('Research catalogue contains multiple revisions of one market context.')
            context=MarketContext(identity['context_id'],*pair,manifest['provenance']['provider'])
            contexts.append({'context':asdict(context),'snapshot_id':sha});known.add(pair);selected.add(sha)
        if len(contexts)>7 or len({primary['context_id'],*(b['context']['context_id'] for b in contexts)})!=1+len(contexts):raise ValueError('Research catalogue exceeds the context budget or repeats context ids.')
        return {**base,'contexts':contexts}

    def start(self,key):
        with self.lock,self.manager.lock:
            state=self.status(key)
            if state['status'] not in ('ready','submitted'):raise ValueError('Preparation is not ready.')
            prepared=state['prepared'];request=prepared['request']
            self.program(request['input_ref'])
            from packages.contracts.storage import RunPlan
            plan=RunPlan.from_payload(prepared['plan'])
            # A stable job id recovers a crash after INSERT and before linking the UI preparation.
            try:job=self.manager.repository.get(key)
            except KeyError:
                if len(self.manager.repository.active())>=self.manager.capacity:raise ValueError('Job queue is full.')
                job=self.manager.enqueue_prepared(request,plan,job_id=key,pause_after=state['config'].get('pause_after_bars'))
            else:
                if self.manager.request(key)!=request or self.manager.plan(key)!=plan:raise ValueError('Preparation job identity conflict.')
            row=self.repository.get(key)['state'];row.update(status='submitted',job_id=key);self.repository.save(row)
            return job

    def cancel(self,key):
        with self.lock,self.manager.lock:
            row=self.repository.get(key);state=row['state']
            if state['status']=='submitted':return self.manager.cancel(key)
            # History tasks are shared; stopping one preparation must not cancel another consumer.
            state.update(status='cancelled');self.repository.save(state)
        return self.status(key)

    def resume(self,key):
        with self.lock,self.manager.lock:
            row=self.repository.get(key);state=row['state']
            if state['status'] not in ('cancelled','failed'):raise ValueError('Preparation is not resumable.')
            for selected in state['sources']:
                if selected['task_id'] and not selected['snapshot_id']:
                    task=self.history.status(selected['task_id'])
                    if task['status'] in ('failed','cancelled','interrupted'):self.history.resume(selected['task_id'])
            state.update(status='ready' if state['prepared_ref'] else 'preparing',error=None);self.repository.save(state)
        return self.status(key)

    def list(self):return [r['state'] for r in self.repository.list()]

    def source(self,key):
        config=self.repository.get(key)['config'];content=self.manager.objects.get(config['input_ref'])
        return {'source':self.manager.objects.get(content['source_ref']) if 'source_ref' in content else None}

    def sources(self,key):
        config=self.repository.get(key)['config']
        return {'sources':[{'symbol':pair[0],'timeframe':pair[1]} for pair in
            (self._source_pair(spec) for spec in [config['primary'],*config['contexts']])]}

    def configuration(self,job_id):
        request=self.manager.request(job_id);content=self.manager.objects.get(request['input_ref'])
        source=self.manager.objects.get(content['source_ref']) if 'source_ref' in content else content if 'source' in content else None
        settings=request.get('settings')
        ids=[request['snapshot_id'],*(b['snapshot_id'] for b in request.get('contexts',[])),*([request['magnifier']['snapshot_id']] if request.get('magnifier') else [])]
        graph_fields={}
        if 'artifact_ref' in content:
            program=import_program(self.manager.objects.get(content['artifact_ref']))
            from packages.contracts.prepared_requests import uses_prepared,graph
            if uses_prepared(program):graph_fields={'context_graph':graph(program,settings,primary=request.get('primary_context'))}
        return {'schema_version':'openpine.saved-launch.1','job_id':job_id,'source':source,**graph_fields,
            'input_ref':request['input_ref'],'inputs':transport_values(settings['values']) if settings else {},
            'request':request,'plan':self.manager.plan(job_id).payload(),
            'state':self.manager.get(job_id),
            'launch_config':self.repository.get(job_id)['config'] if self.manager.db.execute('SELECT 1 FROM launches WHERE launch_id=?',(job_id,)) else None,
            'datasets':[self.manager.market.manifest(sha)['identity'] for sha in ids]}

    def snapshots(self,offset=0,limit=50):
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=50:raise ValueError('Invalid snapshot page.')
        rows=self.manager.db.execute('SELECT snapshot_id FROM datasets ORDER BY created_at DESC,snapshot_id LIMIT ? OFFSET ?',(limit+1,offset))
        return {'items':[{'snapshot_id':r[0],'identity':self.manager.market.manifest(r[0])['identity']} for r in rows[:limit]],
            'next_offset':offset+limit if len(rows)>limit else None}


def requirements_for(program,input_ref,inputs=None,*,settings=None,primary_context=None):
    rows={}
    from packages.contracts.inputs import admitted_settings
    from packages.contracts.prepared_requests import resolved_requests,uses_prepared,graph
    selected=admitted_settings(program,settings) if settings is not None else settings_for(program,inputs)
    from packages.contracts.primary_targets import uses_primary,PrimaryRequired,shape,metadata
    relative=uses_primary(program)
    if primary_context is not None:metadata(primary_context)
    try:
        selected_graph=graph(program,selected,primary=primary_context) if uses_prepared(program) else None
        requests=resolved_requests(program,selected,primary=primary_context)
    except PrimaryRequired as error:
        return {'schema_version':'openpine.launch-requirements.3','status':'needs_primary','needs_primary':True,
            'required_primary_fields':list(error.fields),'input_ref':input_ref,'language_version':program.identity.language_version,
            'contexts':[],'context_graph':None,'compatibility_notes':list(program.identity.compatibility_notes),
            'target_templates':[{'call_site_id':n.call_site_id,'dependencies':{name:list(shape(program,n.arguments[i].expression,allow_functions=n.overload_id.endswith('.function.primary.prepared'),allow_scalars=n.overload_id.endswith('.scalar.function.primary.prepared'),allow_numeric=n.overload_id.endswith('.numeric.scalar.function.primary.prepared'))) for i,name in enumerate(('symbol','timeframe'))}}
                for n in request_nodes(program) if n.overload_id.endswith('.primary.prepared')]}
    for node in requests:
        symbol,tf=(node.arguments[i].expression.value for i in (0,1))
        gaps,lookahead=request_flags(node,program.identity.language_version)
        row=rows.setdefault((symbol,tf),{'symbol':symbol,'timeframe':tf,'requests':[]})
        row['requests'].append({'call_site_id':node.call_site_id,'gaps':gaps,'lookahead':lookahead,'historical_future':lookahead=='on'})
        if node.operation=='context.lower_tf':
            from packages.contracts.lower_tf import options
            ignore,count=options(node)
            row['requests'][-1].update(kind='lower_tf',ignore_invalid_timeframe=ignore,calc_bars_count=count)
    return {'schema_version':'openpine.launch-requirements.3' if relative else 'openpine.launch-requirements.2' if uses_prepared(program) else 'openpine.launch-requirements.1','input_ref':input_ref,
        'language_version':program.identity.language_version,'contexts':list(rows.values()),
        'compatibility_notes':list(program.identity.compatibility_notes),
        **({'context_graph':selected_graph} if uses_prepared(program) else {}),
        **({'status':'resolved','needs_primary':False} if relative else {})}
