"""Finite application-corpus orchestration over the existing job/replay services."""
import copy
import re
import threading
import time
import uuid
from dataclasses import asdict, replace

from apps.openpine.corpus_manifest import Corpus
from apps.openpine.corpus_compare import compare, historical, locations, tagged
from apps.openpine.replay import Replays
from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.contexts import MarketContext
from packages.contracts.realtime import MarketEvent
from packages.contracts.calendar import next_open
from packages.contracts.wire import canonical, loads, digest
from packages.execution.artifacts import export_program, import_program
from packages.execution.session import Session
from packages.compiler.semantic.specification import SpecError
from packages.execution.service import rejection
from packages.execution.identity import plan_identity
from packages.marketdata.providers import input_snapshot
from packages.storage import checkpoint_records

PREFIX = 'corpus:run:'
REPORT_LIMIT = 2 * 1024 * 1024
TERMINAL = {'succeeded', 'failed', 'cancelled', 'timed_out'}


def identifier(value):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{32}', value):
        raise ValueError('Corpus run requires a 32-hex identity.')
    return value


def compile_case(corpus, case):
    source = corpus.files[case['id']]['source'].decode('utf-8')
    session = Session(profile_id=HISTORICAL_PROFILE_ID)
    program = None
    try:
        program = session.compile(source, case['files']['source']['path'])
        session.validate_program(program)
        result = {'status': 'compiled', 'version': program.identity.language_version, 'diagnostics': []}
    except (OpenPineError, SpecError) as error:
        result = {**rejection(error), 'version': case['version']}
    expectation = case['expected']
    diagnostic = result.get('diagnostics', [{}])[0] if result.get('diagnostics') else {}
    result['expected'] = result['status'] == expectation['status'] and all(
        diagnostic.get(key) == expectation[key] for key in ('category', 'code') if key in expectation)
    return result, program


def compile_batch(corpus, ids=None):
    cases = []
    for case in corpus.select(ids):
        result, _ = compile_case(corpus, case)
        cases.append({'case_id': case['id'], 'source': case['files']['source'], 'compilation': result})
    return {'schema_version': 'openpine.corpus-compilation.1', 'manifest_sha256': corpus.sha256,
            'status': 'passed' if all(row['compilation']['expected'] for row in cases) else 'failed', 'cases': cases}


class CaseFailure(Exception):
    def __init__(self, code, message, *, diagnostic=None, admission=None):
        super().__init__(message)
        self.code, self.diagnostic, self.admission = code, diagnostic, admission


def failure(error):
    code = getattr(error, 'code', None)
    diagnostic = getattr(error, 'diagnostic', None)
    if diagnostic:
        diagnostic = asdict(diagnostic) if not isinstance(diagnostic, dict) else diagnostic
        code = diagnostic.get('code', code)
    admission = getattr(error, 'admission', None)
    if admission:kind = 'resource_wait'
    elif isinstance(error, InterruptedError) or code == 'CANCELLED':kind = 'cancelled'
    elif isinstance(error, TimeoutError) or code in ('TIMEOUT', 'TIMED_OUT', 'HEARTBEAT_TIMEOUT'):kind = 'timeout'
    elif isinstance(error, FileNotFoundError) or code in ('SNAPSHOT_NOT_FOUND', 'CONTEXT_INPUT', 'DATA_MISSING'):kind = 'data_missing'
    elif diagnostic and diagnostic.get('category') == 'PROFILE_UNCOVERED':kind = 'unsupported_language'
    elif code and any(word in code for word in ('MEMORY', 'DISK', 'RESOURCE', 'QUEUE')):kind = 'resource_failure'
    elif isinstance(error, (ValueError, OSError, CaseFailure)):kind = 'execution_error'
    else:kind = 'internal_error'
    return {'kind': kind, 'code': code or type(error).__name__, 'message': str(error)[:2048],
            'diagnostic': diagnostic, 'admission': admission}


class Runs:
    """One finite controller; workers/admission/persistence remain the app's services.

    Interrupted runs are immutable partial evidence, never silently restarted.
    Explicit rerun creates a new identity, with a link to the previous report.
    """
    def __init__(self, manager, corpus=None, *, recover=True):
        self.manager = manager
        self.corpus = corpus or Corpus()
        self.replays = Replays(manager)
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.cancelled = threading.Event()
        self.thread = None
        self.active_id = None
        # One catalogue query, no unbounded materialisation of old reports.
        rows = manager.db.execute("SELECT value FROM settings WHERE name LIKE ? AND json_extract(CAST(value AS TEXT),'$.status')='running'", (PREFIX+'%',)) if recover else []
        for raw, in rows:
            index=loads(raw)
            try:state = manager.objects.get(index['report_ref'], max_bytes=REPORT_LIMIT)
            except FileNotFoundError:
                if not self._expired(index):raise
                self._cancel_jobs({'cases':[{'jobs':index.get('jobs',[])}]})
                index.update(status='expired')
                manager.db.execute('UPDATE settings SET value=? WHERE name=?',(canonical(index),PREFIX+index['run_id']))
                continue
            self._cancel_jobs(state)
            state.update(status='interrupted', finished_at=time.time(), interruption='Coordinator stopped; use explicit rerun.')
            self._save(state)

    def _save(self, state):
        raw = canonical(state)
        if len(raw) > REPORT_LIMIT:raise ValueError('Corpus report exceeds 2 MiB.')
        ref = self.manager.objects.put(state)
        with self.manager.db.transaction():
            self.manager.services.lifecycle.pin('corpus:'+state['run_id']+':'+ref, ref)
            previous = self.manager.db.execute('SELECT value FROM settings WHERE name=?', (PREFIX+state['run_id'],))
            self.manager.db.execute('INSERT INTO settings(name,value) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value',
                (PREFIX+state['run_id'], canonical({'run_id':state['run_id'], 'status':state['status'], 'report_ref':ref,
                    'case_ids':state['case_ids'], 'manifest_sha256':state['manifest_sha256'], 'created_at':state['created_at'],
                    'jobs':[{'job_id':job['job_id']} for case in state.get('cases',[]) for job in case.get('jobs',[])]})))
            # Only the latest report root is retained; it contains the full case evidence.
            if previous:
                old = loads(previous[0][0]).get('report_ref')
                if old and old != ref:self.manager.db.execute('DELETE FROM pins WHERE name=?', ('corpus:'+state['run_id']+':'+old,))

    def report(self, run_id):
        identifier(run_id)
        with self.manager.limiter.slot('read'), self.lock:
            rows = self.manager.db.execute('SELECT value FROM settings WHERE name=?', (PREFIX+run_id,))
            if not rows:raise KeyError(run_id)
            index = loads(rows[0][0])
            try:value = self.manager.objects.get(index['report_ref'], max_bytes=REPORT_LIMIT)
            except FileNotFoundError:
                if self._expired(index):raise KeyError(run_id) from None
                raise
        return {'sha256': index['report_ref'], 'report': value}

    def _expired(self,index):
        return not self.manager.objects.path(index['report_ref']).exists() and not self.manager.db.execute(
            'SELECT 1 FROM pins WHERE name=?',('corpus:'+index['run_id']+':'+index['report_ref'],))

    def list(self):
        rows = self.manager.db.execute("SELECT value FROM settings WHERE name LIKE ? ORDER BY json_extract(CAST(value AS TEXT),'$.created_at') DESC,name LIMIT 50", (PREFIX+'%',))
        values=[loads(raw) for raw, in rows]
        return {'runs':[{k:('expired' if k=='status' and self._expired(row) else v) for k,v in row.items() if k!='jobs'} for row in values], 'limit':50}

    def create(self, payload):
        if (type(payload) is not dict or not {'request_id', 'case_ids'} <= payload.keys() <= {'request_id', 'case_ids', 'parent_id'}):
            raise ValueError('Corpus run needs request_id and case_ids, optionally parent_id.')
        run_id = identifier(payload['request_id'])
        selected = self.corpus.select(payload['case_ids'])
        parent = payload.get('parent_id')
        if parent is not None:self.report(identifier(parent))
        with self.lock:
            try:old = self.report(run_id)['report']
            except KeyError:pass
            else:
                if old['case_ids'] != payload['case_ids'] or old['manifest_sha256'] != self.corpus.sha256 or old['parent_id'] != parent:
                    raise ValueError('Corpus request identity was already used for other inputs.')
                return {'run_id':run_id, 'status':old['status']}
            if self.stop.is_set():raise ValueError('Corpus service is closed.')
            if self.thread and self.thread.is_alive():
                from packages.execution.limits import CoordinatorBusy
                raise CoordinatorBusy('corpus_controller', 'research', {}, 0)
            state = {'schema_version':'openpine.corpus-report.1','run_id':run_id,'status':'running',
                'created_at':time.time(),'finished_at':None,'parent_id':parent,'manifest_sha256':self.corpus.sha256,
                'identity':list(plan_identity()), 'case_ids':[c['id'] for c in selected], 'cases':[],
                'current':None, 'scope':'Owned application corpus; not a TradingView compatibility percentage.'}
            self._save(state)
            self.active_id = run_id
            self.cancelled.clear()
            self.thread = threading.Thread(target=self._run, args=(state,selected),name='openpine-corpus',daemon=False)
            self.thread.start()
            return {'run_id':run_id,'status':'running'}

    def _publish(self, state):
        with self.lock:self._save(state)

    def _run(self, state, selected):
        try:
            for case in selected:
                if self.stop.is_set() or self.cancelled.is_set():raise InterruptedError('Corpus execution stopped.')
                row = {'case_id':case['id'], 'version':case['version'],'family':case['family'],
                    'provenance':copy.deepcopy(case),'status':'running','jobs':[], 'replays':[], 'comparisons':[]}
                state['cases'].append(row);state['current']=case['id'];self._publish(state)
                try:self._case(state, row, case)
                except Exception as error:
                    self._cancel_jobs({'cases':[row]})
                    row.update(status='failed', failure=failure(error))
                self._publish(state)
            state['status'] = 'passed' if all(c['status'] in ('passed','expected_rejection') for c in state['cases']) else 'failed'
        except InterruptedError:
            state['status']='interrupted'
        except Exception as error:
            state.update(status='failed', failure=failure(error))
        finally:
            if self.stop.is_set():state['status']='interrupted'
            elif self.cancelled.is_set():state['status']='cancelled'
            state.update(current=None,finished_at=time.time())
            state['summary'] = summary(state['cases'])
            self._publish(state)

    def _job(self, state, row, request, bars, *, pause=False):
        manager = self.manager
        # Publish ownership BEFORE enqueue so a restart can find every attempt.
        job_id = uuid.uuid4().hex
        row['jobs'].append({'job_id':job_id,'route': 'source' if 'source' in manager.objects.get(request['input_ref']) else 'ir',
                            'pause_after':max(1,len(bars)//2) if pause else None})
        self._publish(state)
        with manager.lock, manager.preparation.batch():
            frozen = manager.preparation.freeze(request)
            plan = replace(manager.preparation.build_plan(frozen,manager.policy),checkpoint_interval_bars=1)
            manager.enqueue_prepared(frozen, plan, job_id=job_id, pause_after=max(1,len(bars)//2) if pause else None)
        deadline = time.monotonic()+180
        while True:
            if self.stop.wait(.05) or self.cancelled.is_set():manager.cancel(job_id);raise InterruptedError('Corpus execution stopped.')
            status = manager.get(job_id)
            if status['status']=='paused':
                if not pause:raise CaseFailure('UNEXPECTED_PAUSE','Historical job paused unexpectedly.')
                row['jobs'][-1]['resumed_from_bar'] = status['completed']
                manager.resume(job_id);pause=False
            elif status['status'] in TERMINAL:break
            if time.monotonic()>=deadline:
                manager.cancel(job_id)
                if status.get('waiting'):raise CaseFailure('RESOURCE_WAIT','Corpus job admission deadline.',admission=status['waiting'])
                raise TimeoutError('Corpus job deadline.')
        row['jobs'][-1]['status'] = status['status']
        if status['status']!='succeeded':
            error = status.get('error') or {}
            diagnostic=error.get('diagnostic')
            if status.get('result_id'):
                response=manager.result(job_id)
                diagnostic=next(iter(response.get('diagnostics',[])),diagnostic)
            raise CaseFailure(error.get('code','EXECUTION_FAILED'),error.get('message','Historical job failed.'),diagnostic=diagnostic)
        manifest = manager.services.checkpoints.manifest(status['checkpoint_id'],plan=plan,total=len(bars))
        if manifest['completed'] != len(bars):raise ValueError('Final checkpoint cursor differs.')
        native = checkpoint_records.get(manager.objects, manifest['runtime_ref'],limit_bytes=32*1024*1024,streaming=False)
        return historical(manager.result(job_id),bars), native

    def _replay(self, state, row, config, tape, *, split=False):
        status = self.replays.create(config);replay_id=status['replay_id']
        row['replays'].append({'replay_id':replay_id,'split':split});self._publish(state)
        self.replays.append(replay_id,tape)
        cancelled=lambda:self.stop.is_set() or self.cancelled.is_set()
        if split:self.replays.advance(replay_id,max_events=max(1,len(tape)//2),checkpoint_every=1,cancelled=cancelled)
        self.replays.advance(replay_id,max_events=len(tape),checkpoint_every=1,cancelled=cancelled)
        status = self.replays.status(replay_id)
        if status['pending_events']:raise ValueError('Replay did not consume the pinned tape.')
        chart = self.replays.view(replay_id,'chart',limit=5000)
        ledger = self.replays.view(replay_id,'ledger',limit=5000)
        if chart['next'] != chart['total'] or ledger['next'] != ledger['total']:
            raise ValueError('Corpus logical output exceeds the bounded comparison window.')
        values = {'rows':chart['rows'],'events':ledger['events'],'statistics':(status.get('simulation') or {}).get('statistics')}
        self.replays.repository.checkpoint(replay_id)  # Validate catalogue/config/chain before materialising this tiny fixture.
        engine = checkpoint_records.get(self.manager.objects,status['checkpoint_ref'],limit_bytes=32*1024*1024,streaming=False)['engine']
        row['replays'][-1].update(sequence=status['sequence'],engine_sha256=digest(tagged(engine)))
        return values, engine

    def _case(self, state, row, case):
        compilation, program = compile_case(self.corpus,case);row['compilation']=compilation
        if not compilation['expected']:
            row.update(status='failed',failure={'kind':'unexpected_compilation','code':'CORPUS_EXPECTATION','message':'Compilation differs from pinned expectation.'});return
        if program is None:row['status']='expected_rejection';return
        files=self.corpus.files[case['id']]
        data=loads(files['bars']);bars=data['bars']
        source={'source':files['source'].decode('utf-8'),'source_name':case['files']['source']['path']}
        with self.manager.limiter.slot('prepare'):
            snapshot=self.manager.market.save(input_snapshot(data))
            source_ref=self.manager.freeze_input(source)
            artifact=export_program(program)
            imported=import_program(artifact)
            ir_ref=self.manager.freeze_input({'artifact':export_program(imported)})
            request={'schema_version':'openpine.job-request.2','input_ref':source_ref,'snapshot_id':snapshot,'timeout_seconds':120}
            primary=asdict(MarketContext(data['context_id'],data['symbol'],data['timeframe'],'uploaded-file.1'))
            contexts=[primary];tapes=[(primary,bars)]
            if 'context_config' in files:
                context_config=loads(files['context_config'])
                if context_config['primary_context']!=primary or len(context_config['contexts'])!=1:
                    raise ValueError('Corpus context config differs from pinned primary.')
                extra=context_config['contexts'][0]['context'];higher=loads(files['higher'])
                if context_config['contexts'][0]['bars'] != 'higher.json':raise ValueError('Unknown corpus context data binding.')
                binding={'context':extra,'snapshot_id':self.manager.market.save(input_snapshot(higher))}
                request.update(schema_version='openpine.job-request.4',primary_context=primary,contexts=[binding])
                contexts.append(extra);tapes.append((extra,higher['bars']))
            if 'input_config' in files and loads(files['input_config']) != case['inputs']:
                raise ValueError('Corpus input overrides differ from their pinned provenance.')
            if case['inputs']:request.update(schema_version='openpine.job-request.6',inputs=case['inputs'])
        row['snapshot_id']=snapshot;row['source_locations']=locations(program)
        row['actual_source_route']='source' if 'source' in self.manager.objects.get(source_ref) else 'ir_with_source_provenance'
        full, native = self._job(state,row,request,bars)
        ir, _ = self._job(state,row,{**request,'input_ref':ir_ref},bars)
        resumed, restored = self._job(state,row,request,bars,pause=True)
        def check(name,left,right):
            row['comparisons'].append(compare(name,left,right,source_locations=row['source_locations'],context_id=primary['context_id']))
        check('source_submission_vs_saved_ir',full,ir)
        check('history_vs_checkpoint_resume',full,resumed)
        check('history_final_native_state',native,restored)
        outputs={}
        for item in full['rows']:
            outputs.setdefault(item[2],[]).append(item[3])
        expected_rows=[]
        if len(outputs)!=len(case['oracle']['outputs']):
            check('independent_oracle',case['oracle']['outputs'],list(outputs.values()))
        else:
            for index,bar in enumerate(bars):
                for output_id,values in zip(outputs,case['oracle']['outputs'],strict=True):
                    if len(values)!=len(bars):raise ValueError('Corpus oracle length differs from its input.')
                    expected_rows.append([index,bar['time'],output_id,values[index]])
            check('independent_oracle',{'rows':expected_rows},{'rows':full['rows']})
        tape=[]
        for context,values in tapes:
            for bar in values:
                tape.append((next_open(bar['time'],context['timeframe']),context['context_id']==primary['context_id'],context['context_id'],bar))
        events=[MarketEvent(i,context,'close',bar['time'],event_time,0,*(float(bar[key]) for key in ('open','high','low','close','volume'))).payload()
                for i,(event_time,_,context,bar) in enumerate(sorted(tape,key=lambda item:item[:3]))]
        row['tape_sha256']=digest(events)
        config={'input_ref':ir_ref,'contexts':contexts,'primary_id':primary['context_id'],'broker_settings':None,'timeout_seconds':120,'inputs':case['inputs']}
        fresh, engine=self._replay(state,row,config,events)
        replay, restored_engine=self._replay(state,row,config,events,split=True)
        check('replay_fresh_vs_resume',fresh,replay)
        check('replay_final_native_state',engine,restored_engine)
        if case['history_replay']['equivalent']:check('history_vs_close_only_replay',full,fresh)
        else:row['comparisons'].append({'name':'history_vs_close_only_replay','status':'not_equivalent','reason':case['history_replay']['reason']})
        row['status']='passed' if all(c['status'] in ('passed','not_equivalent') for c in row['comparisons']) else 'failed'

    def _cancel_jobs(self, state):
        for case in state.get('cases',[]):
            for job in case.get('jobs',[]):
                try:
                    if self.manager.get(job['job_id'])['status'] not in TERMINAL:self.manager.cancel(job['job_id'])
                except KeyError:pass

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=30)
        if self.thread and self.thread.is_alive():raise RuntimeError('Corpus controller did not stop.')

    def cancel(self, run_id):
        identifier(run_id)
        with self.lock:
            # Cancellation must remain available when the read-memory budget is exhausted.
            rows=self.manager.db.execute('SELECT value FROM settings WHERE name=?',(PREFIX+run_id,))
            if not rows:raise KeyError(run_id)
            report=loads(rows[0][0])
            if report['status']=='running' and self.active_id==run_id:self.cancelled.set()
        return {'run_id':run_id,'status':report['status'],'cancel_requested':self.cancelled.is_set() and self.active_id==run_id}


def summary(cases):
    counts={name:sum(c['status']==name for c in cases) for name in ('passed','expected_rejection','failed','running')}
    versions={str(v):{name:sum(c['version']==v and c['status']==name for c in cases) for name in counts} for v in range(1,7)}
    gaps={}
    for case in cases:
        if case['status']=='expected_rejection':
            diagnostic=case['compilation']['diagnostics'][0]
            key=diagnostic['code'];gaps.setdefault(key,[]).append(case['case_id'])
    return {'counts':counts,'versions':versions,'measured_cases':len(cases),
        'gaps':[{'code':key,'case_ids':ids,'next_action':'Review the pinned request-expression boundary before adding expression support.'} for key,ids in sorted(gaps.items())],
        'coverage_basis':'Selected owned examples only; expected rejections are not execution successes.'}


def evidence(report):
    """Portable semantic receipt, excluding random run/job IDs and wall clocks."""
    return {'manifest_sha256':report['manifest_sha256'],'identity':report['identity'],
        'cases':[{'case_id':row['case_id'],'version':row['version'],'status':row['status'],
                  'compilation':row.get('compilation'), 'comparisons':row['comparisons'],
                  'source_sha256':row['provenance']['files']['source']['sha256'],
                  'tape_sha256':row.get('tape_sha256'),'actual_source_route':row.get('actual_source_route')}
                 for row in report['cases']]}
