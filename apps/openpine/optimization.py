"""Research orchestration over ordinary experiments and their shared JobManager."""
from packages.contracts.trading import STRATEGY_DECLARATIONS
from dataclasses import asdict
import re
import threading

from packages.contracts.application import TERMINAL_STATUSES
from packages.contracts import OpenPineError
from packages.contracts.inputs import input_schema, transport_values
from packages.contracts.optimization import SEARCH_SCHEMA, METRICS_SCHEMA, split_windows, metric_spec, STRATEGY_METRICS
from packages.contracts.wire import digest
from packages.execution.identity import plan_identity
from packages.optimizer.search import candidates
from packages.optimizer.metrics import evaluate, rank
from packages.optimizer.identity import identity as optimizer_identity
from packages.storage.optimization import SearchRepository
from packages.storage.results import series_rows
from packages.storage.trades import event_rows


class Searches:
    def __init__(self,manager,experiments,*,start=True):
        self.manager=manager;self.experiments=experiments;self.repository=SearchRepository(manager.db)
        self.stopping=threading.Event();self.thread=None;self.fatal=None;self.cursor=0
        if start:
            self.thread=threading.Thread(target=self._loop,name='openpine-search',daemon=True);self.thread.start()

    def prepare(self,payload):
        required={'name','input_ref','snapshot_id','timeout_seconds','space','method','seed','budget','objective','windows'}
        optional={'inputs','constraints','primary_context','contexts','broker_settings','max_attempts','checkpoint_interval_bars'}
        if type(payload) is not dict or not required<=payload.keys()<=required|optional:
            raise ValueError('Search requires a fixed program/data, finite space, objective, budget, seed and two windows.')
        windows=split_windows(payload['windows']);objective=metric_spec(payload['objective'],objective=True)
        constraints=payload.get('constraints',[])
        if type(constraints) is not list or len(constraints)>16:raise ValueError('At most sixteen metric constraints.')
        constraints=[metric_spec(s) for s in constraints]
        with self.manager.lock,self.manager.limiter.slot('prepare'):
            program=self.experiments._program(payload['input_ref'])
            space=candidates(program,payload['space'],method=payload['method'],budget=payload['budget'],seed=payload['seed'],inputs=payload.get('inputs'))
            strategy=program.statements[0].operation in STRATEGY_DECLARATIONS
            if not strategy and any(s['metric'] in STRATEGY_METRICS for s in [objective,*constraints]):
                raise ValueError('Indicator searches require explicit output metrics; broker metrics require a strategy.')
            compositions={};prepared={};snapshots={}
            base={k:v for k,v in payload.items() if k in {'name','input_ref','timeout_seconds','primary_context','contexts','broker_settings','max_attempts','checkpoint_interval_bars'}}
            if type(base['name']) is not str or not base['name'].strip() or len(base['name'])>100:raise ValueError('Search name requires 1..100 characters.')
            for phase,window in windows.items():
                snapshot=self.manager.market.slice(payload['snapshot_id'],window['warmup_start_ms'],window['end_ms'])
                snapshots[phase]=snapshot
                contexts=[]
                for binding in payload.get('contexts',[]):
                    identity=self.manager.market.manifest(binding['snapshot_id'])['identity']
                    selected=self.manager.market.slice(binding['snapshot_id'],identity['first_ms'],window['end_ms'])
                    contexts.append({**binding,'snapshot_id':selected})
                request={**base,'name':base['name'].strip()+' / '+phase,'snapshot_id':snapshot,'evaluation_window':window,
                         'variants':[s['values'] for s in space['candidates']]}
                if 'contexts' in base:request['contexts']=contexts
                compositions[phase],prepared[phase]=self.experiments.prepare(request)
            manifest={'schema_version':SEARCH_SCHEMA,'name':base['name'].strip(),'input_ref':payload['input_ref'],
                'parent_snapshot_id':payload['snapshot_id'],'parent_contexts':payload.get('contexts',[]),
                'input_schema_sha256':input_schema(program)['sha256'],'program_identity':asdict(program.identity),
                'optimizer_identity':optimizer_identity(),
                'search':space,'objective':objective,'constraints':constraints,'metrics_schema':METRICS_SCHEMA,
                'windows':windows,'snapshots':snapshots,'selection_composition':compositions['selection'],
                'holdout_composition':compositions['holdout']['variants'],'selection_policy':'eligible_score_then_candidate_ordinal',
                'warmup_policy':'runtime_warmed_orders_discarded_fresh_broker_no_forced_end_liquidation'}
            return manifest,prepared['selection'],prepared['holdout']

    def publish_prepared(self, prepared, *, paused=False):
        manifest,selection_plans,holdouts=prepared
        selection=self.experiments.repository.create(manifest['selection_composition'],selection_plans,transaction=False)
        identifier=self.repository.insert(manifest,selection,holdouts)
        if paused:
            self.repository.control(identifier,'paused')
            self.experiments.repository.control(selection,'paused')
        return identifier

    def create(self,payload):
        with self.manager.lock,self.manager.limiter.slot('prepare'):
            if len(self.repository.list(active=True,limit=8))>=8:raise ValueError('At most eight unfinished running searches.')
            prepared=self.prepare(payload)
            with self.manager.db.transaction():identifier=self.publish_prepared(prepared)
        return self.status(identifier)

    def _get(self,identifier):
        if type(identifier) is not str or not re.fullmatch('[0-9a-f]{32}',identifier):raise ValueError('Invalid search identity.')
        return self.repository.get(identifier)

    def status(self,identifier,*,start=0,limit=50):
        if type(start) is not int or start<0 or type(limit) is not int or not 1<=limit<=100:raise ValueError('Search page requires start>=0 and limit 1..100.')
        with self.manager.limiter.slot('read'):
            saved=self._get(identifier);manifest=saved['manifest'];decision=saved['decision']
            selection=self.experiments.status(saved['selection_id'],start=start,limit=limit)
            assessments=self.repository.assessments(identifier);by_ordinal={r['ordinal']:r for r in assessments}
            ranked=rank(assessments,manifest['objective']['direction']);positions={row['ordinal']:i+1 for i,row in enumerate(ranked)}
            rows=[{**row,'assessment':by_ordinal.get(row['ordinal'],{}).get('assessment'),'rank':positions.get(row['ordinal'])} for row in selection['variants']]
            holdout=self.repository.assessments(identifier,'holdout');holdout_group=None
            if saved['holdout_id']:holdout_group=self.experiments.status(saved['holdout_id'],limit=1)
            if saved['desired']=='cancelled':status='cancelled'
            elif saved['desired']=='paused':status='paused'
            elif decision is None:status='selecting'
            elif decision['winner_ordinal'] is None:status='no_eligible_candidate'
            elif not holdout:status='checking_holdout'
            else:status='succeeded' if holdout[0]['assessment']['status']=='eligible' else 'holdout_failed'
            return {'schema_version':SEARCH_SCHEMA,'search_id':identifier,'name':manifest['name'],'manifest_sha256':saved['manifest_sha256'],
                'desired':saved['desired'],'status':status,'error':saved['error'] or self.fatal,'objective':manifest['objective'],
                'windows':manifest['windows'],'method':manifest['search']['method'],'seed':manifest['search']['seed'],
                'total':selection['total'],'assessed':len(assessments),'start':start,'next':selection['next'],
                'selection_id':saved['selection_id'],'counts':selection['counts'],'variants':rows,
                'ranking':[{'ordinal':r['ordinal'],'job_id':r['job_id'],'score':r['assessment']['score']} for r in ranked],
                'decision':decision,'holdout':holdout[0] if holdout else None,
                'holdout_job':holdout_group['variants'][0] if holdout_group else None}

    def list(self):
        return [{k:v for k,v in self.status(identifier,limit=1).items() if k not in ('variants','ranking','holdout_job')}
                for identifier in self.repository.list()]

    def control(self,identifier,action,*,study_owner=False):
        if action not in ('pause','resume','cancel'):raise ValueError('Unknown search control.')
        with self.manager.lock:
            if not study_owner and self.manager.db.execute('SELECT 1 FROM walkforward_folds WHERE search_id=?',(identifier,)):
                raise ValueError('Control the owning walk-forward study instead.')
            saved=self._get(identifier);state=self.status(identifier,limit=1)
            if saved['desired']=='cancelled' and action!='cancel':raise ValueError('Cancelled searches cannot resume.')
            if state['status'] in ('succeeded','holdout_failed','no_eligible_candidate'):return state
            if action=='resume' and saved['desired']!='running' and len(self.repository.list(active=True,limit=8))>=8:
                raise ValueError('At most eight unfinished running searches.')
            groups=[saved['selection_id'],*([saved['holdout_id']] if saved['holdout_id'] else [])]
            if action=='resume':
                active=len(self.experiments.repository.groups(active=True,limit=16))
                needed=sum(self.experiments.repository.group(g)['desired']!='running' and any(k not in TERMINAL_STATUSES for k in self.experiments.repository.counts(g)) for g in groups)
                if active+needed>16:raise ValueError('At most sixteen unfinished running experiments.')
            desired={'pause':'paused','resume':'running','cancel':'cancelled'}[action]
            with self.manager.db.transaction():
                self.repository.control(identifier,desired)
                for group in groups:self.experiments.repository.control(group,desired)
            if action!='resume':
                for group in groups:
                    for job in self.experiments.repository.owned_jobs(group):getattr(self.manager,action)(job)
        return self.status(identifier)

    def _assess(self,saved,phase,ordinal,job):
        if job['status']!='succeeded':
            return {'schema_version':METRICS_SCHEMA,'status':'execution_'+job['status'],'metrics':{},'score':None,'error':job.get('error')}
        response=self.manager.objects.get(job['result_id'],max_bytes=32*1024*1024);summary=response['result'];manifest=saved['manifest']
        if summary.get('evaluation_window')!=manifest['windows'][phase]:raise ValueError('Result evaluation window differs from search.')
        try:
            return evaluate(summary,manifest['windows'][phase],manifest['objective'],manifest['constraints'],
                events=event_rows(self.manager.objects,summary['simulation']['journal']) if summary.get('simulation') else (),
                rows=series_rows(self.manager.objects,summary) if any(s['metric'].startswith('output_') for s in [manifest['objective'],*manifest['constraints']]) else (),
                cancelled=lambda:self.stopping.is_set() or self.manager.db.execute('SELECT desired FROM searches WHERE search_id=?',(saved['search_id'],))[0][0]!='running')
        except (ValueError,TypeError,ArithmeticError) as error:
            return {'schema_version':METRICS_SCHEMA,'status':'invalid_metric','metrics':{},'score':None,'error':str(error)}

    def _tick(self,identifier):
        saved=self._get(identifier)
        if saved['desired']!='running':return
        manifest=saved['manifest'];decision=saved['decision']
        if manifest['optimizer_identity']!=optimizer_identity():raise ValueError('Search requires another optimizer implementation; its scores and decision cannot be silently replaced.')
        for group in [saved['selection_id'],*([saved['holdout_id']] if saved['holdout_id'] else [])]:
            state=self.experiments.repository.group(group)
            if state['desired']!='running' and any(k not in TERMINAL_STATUSES for k in self.experiments.repository.counts(group)):
                self.control(identifier,'cancel' if state['desired']=='cancelled' else 'pause',study_owner=True)
                self.repository.control(identifier,'cancelled' if state['desired']=='cancelled' else 'paused',error=state['error'])
                return
        if decision is None:
            rows=self.experiments.repository.variants(saved['selection_id'],limit=256)
            assessed={r['ordinal'] for r in self.repository.assessments(identifier)}
            for row in rows:
                if row['ordinal'] in assessed or not row['job_id']:continue
                job=self.manager.get(row['job_id'])
                if job['status'] not in TERMINAL_STATUSES:continue
                with self.manager.limiter.slot('read'):assessment=self._assess(saved,'selection',row['ordinal'],job)
                with self.manager.lock:
                    if not any(a['ordinal']==row['ordinal'] for a in self.repository.assessments(identifier)):
                        self.repository.assess(identifier,'selection',row['ordinal'],job,assessment)
                return
            if len(assessed)!=len(rows):return
            ordered=rank(self.repository.assessments(identifier),manifest['objective']['direction'])
            winner=ordered[0]['ordinal'] if ordered else None
            decision={'schema_version':'openpine.search-decision.1','winner_ordinal':winner,
                'ranking':[r['ordinal'] for r in ordered],
                'selection_evidence_sha256':digest(self.repository.assessments(identifier)),
                'policy':manifest['selection_policy']}
            with self.manager.lock,self.manager.db.transaction():
                current=self._get(identifier)
                if current['desired']!='running' or current['decision'] is not None:return
                holdout=None
                if winner is not None:
                    if len(self.experiments.repository.groups(active=True,limit=16))>=16:return
                    request,plan=self.repository.holdout(identifier,winner)
                    composition={'schema_version':'openpine.experiment.1','name':manifest['name']+' / holdout',
                        'input_ref':request['input_ref'],'snapshot_id':request['snapshot_id'],
                        'input_schema_sha256':manifest['input_schema_sha256'],'variants':[manifest['holdout_composition'][winner]]}
                    holdout=self.experiments.repository.create(composition,[(request,plan)],transaction=False)
                self.repository.decide(identifier,decision,holdout)
        elif saved['holdout_id'] and not self.repository.assessments(identifier,'holdout'):
            row=self.experiments.repository.variants(saved['holdout_id'],limit=1)[0]
            if not row['job_id']:return
            job=self.manager.get(row['job_id'])
            if job['status'] not in TERMINAL_STATUSES:return
            with self.manager.limiter.slot('read'):assessment=self._assess(saved,'holdout',decision['winner_ordinal'],job)
            with self.manager.lock:
                if not self.repository.assessments(identifier,'holdout'):self.repository.assess(identifier,'holdout',decision['winner_ordinal'],job,assessment)

    def tick(self):
        if self.manager.stopping.is_set() or self.manager.fatal or self.stopping.is_set():return
        identifiers=self.repository.list(active=True,limit=8)
        if not identifiers:return
        offset=self.cursor%len(identifiers);self.cursor+=1
        for identifier in identifiers[offset:]+identifiers[:offset]:
            try:self._tick(identifier)
            except InterruptedError:continue
            except Exception as error:
                if isinstance(error,OpenPineError) and error.diagnostic.code=='COORDINATOR_BUSY':continue
                if self.stopping.is_set():return
                with self.manager.lock:
                    if not self.manager.db.execute('SELECT 1 FROM searches WHERE search_id=?',(identifier,)):continue
                    self.repository.control(identifier,'paused',error=str(error))
                    saved=self._get(identifier)
                    for group in [saved['selection_id'],*([saved['holdout_id']] if saved['holdout_id'] else [])]:
                        self.experiments.control(group,'pause',search_owner=True,include_status=False)

    def rerun(self,identifier,ordinal,*,phase='selection'):
        with self.manager.lock,self.manager.limiter.slot('prepare'):
            saved=self._get(identifier);manifest=saved['manifest']
            if type(ordinal) is not int or not 0<=ordinal<len(manifest['search']['candidates']):raise ValueError('Invalid candidate ordinal.')
            if phase=='selection':
                row=self.experiments.repository.variants(saved['selection_id'],start=ordinal,limit=1)[0]
                request,plan=row['request'],row['plan']
            elif phase=='holdout' and saved['decision'] and saved['decision']['winner_ordinal']==ordinal:
                request,plan=self.repository.holdout(identifier,ordinal)
            else:raise ValueError('Holdout rerun requires the already selected winner.')
            if (plan.execution_sha256,plan.spec_lock_sha256)!=plan_identity():raise ValueError('Saved candidate requires another implementation/specification.')
            if len(self.experiments.repository.groups(active=True,limit=16))>=16:raise ValueError('Experiment admission limit reached.')
            composition={'schema_version':'openpine.experiment.1','name':manifest['name']+' / repeat '+phase,
                'input_ref':request['input_ref'],'snapshot_id':request['snapshot_id'],'input_schema_sha256':manifest['input_schema_sha256'],
                'variants':[{'request_sha256':digest(request),'plan_sha256':digest(plan.payload())}]}
            group=self.experiments.repository.create(composition,[(request,plan)])
        return self.experiments.status(group)

    def _loop(self):
        while not self.stopping.wait(.2):
            try:self.tick()
            except Exception as error:self.fatal=str(error);return

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=30)
            if self.thread.is_alive():raise RuntimeError('Search supervisor did not stop.')
