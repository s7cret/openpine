"""Live sessions compose durable acquisition, pure projection and isolated replay."""

import threading
import time
import tempfile
import hashlib
import apsw
from pathlib import Path
from dataclasses import asdict

from packages.contracts.contexts import MarketContext
from packages.contracts.live import RECEIVED_PROFILE,MAX_FEEDS
from packages.contracts.context_ir import request_nodes
from packages.contracts.market import HistoryRequest, InstrumentKey
from packages.contracts.trading import BrokerSettings
from packages.contracts.wire import digest,canonical,loads
from packages.execution.artifacts import import_program
from packages.execution.session import Session
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts import Diagnostic,OpenPineError
from packages.marketdata.live_mapping import initial, project, implementation_identity
from packages.marketdata.timeframes import EXECUTION, floor_open, next_open
from packages.marketdata.transport import RequestControl
from packages.storage.live import LiveRepository
from apps.openpine.replay import Replays
from apps.openpine.live_feeds import FeedHub


class LiveSessions:
    def __init__(self,manager,history,*,socket_factory=None,start=True,clock=lambda:int(time.time()*1000)):
        self.manager,self.history,self.clock=manager,history,clock
        self.mapping_identity=digest({'mapping':implementation_identity(),'application':{
            name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ('live_service.py','live_feeds.py','live_contexts.py')}})
        self.repository=LiveRepository(manager.db,manager.objects);self.replays=Replays(manager)
        self.stopping,self.wake=threading.Event(),threading.Event();self.lock=threading.RLock();self.fatal=None
        self.hub=FeedHub(manager,history,self.repository,clock=clock,notify=self.wake.set,
            **({'socket_factory':socket_factory} if socket_factory else {}))
        from apps.openpine.live_contexts import LiveContexts
        self.graph=LiveContexts(self)
        self.thread=None
        if start:
            for state in self.repository.sessions(active=True):
                if self.repository.session_config(state['session_id']).get('mapping_sha256')!=self.mapping_identity:
                    self.repository.save_session({**state,'status':'failed','error':'Live projection implementation changed; create a new session.'},desired='stopped')
                elif state['desired']=='running':
                    for feed in self.feeds(state):self.hub.start(feed)
            self.thread=threading.Thread(target=self._loop,name='openpine-live-projection',daemon=True);self.thread.start()

    def create(self,payload,*,connect=True):
        fields={'input_ref','instrument','timeframe','start_ms','broker_settings','timeout_seconds'}
        if type(payload) is not dict or not fields<=payload.keys()<=fields|{'gap_policy','fresh_feed','contexts'}:raise ValueError('Invalid live session fields.')
        payload={**payload,'gap_policy':payload.get('gap_policy','fail'),'fresh_feed':payload.get('fresh_feed',False)}
        if payload['gap_policy'] not in ('fail','allow_with_metadata'):raise ValueError('Invalid live gap policy.')
        if type(payload['fresh_feed']) is not bool:raise ValueError('fresh_feed requires a strict boolean.')
        key=InstrumentKey.from_payload(payload['instrument']);key.pine_symbol
        tf=payload['timeframe']
        if (tf not in EXECUTION or type(payload['start_ms']) is not int or payload['start_ms']<0
                or floor_open(payload['start_ms'],tf)!=payload['start_ms']):raise ValueError('Live requires an aligned supported UTC interval.')
        with self.lock,self.manager.limiter.slot('prepare'):
            if len(self.repository.sessions(active=True))>=8:raise ValueError('Eight live sessions are already active.')
            feed_config={'schema_version':'openpine.live-feed.1','instrument':asdict(key),'timeframe':tf,
                'start_ms':payload['start_ms'],'ingestion_sha256':self.mapping_identity}
            feed_config=self.repository.choose_config(feed_config,fresh=payload['fresh_feed'])
            feed_key=self.repository.feed_id(feed_config)
            with self.hub.lock:
                if connect and feed_key not in self.hub.active and len(self.hub.active)>=MAX_FEEDS:raise ValueError('Live connection budget is full.')
            meta=self.history.repository.instrument(key)
            if meta is None or meta.status!='TRADING':raise ValueError('Prepare history or refresh the catalogue for a trading instrument first.')
            content=self.manager.objects.get(payload['input_ref'])
            program=import_program(self.manager.objects.get(content['artifact_ref'])) if 'artifact_ref' in content else Session(profile_id=HISTORICAL_PROFILE_ID).compile(content['source'],content['source_name'])
            strategy=bool(program.statements and getattr(program.statements[0],'operation',None)=='simulation.strategy')
            settings=payload['broker_settings']
            if settings is None and strategy:settings=asdict(BrokerSettings(meta.tick_size,meta.quantity_step))
            if payload.get('contexts') or request_nodes(program):return self.graph.create(payload,program,settings,connect=connect)
            context=MarketContext('primary',key.pine_symbol,tf,'binance.spot.arrivals.1')
            replay=self.replays.create({'input_ref':payload['input_ref'],'contexts':[asdict(context)],'primary_id':'primary',
                'broker_settings':settings,'timeout_seconds':payload['timeout_seconds'],'event_profile':RECEIVED_PROFILE})
            feed=self.repository.feed(feed_config)
            with self.hub.lock:
                state=self.repository.create_session(feed,replay['replay_id'],{**payload,'instrument_metadata':meta.payload(),
                    'schema_version':'openpine.live-session.1','profile':RECEIVED_PROFILE,'broker_settings':settings,
                    'mapping_sha256':self.mapping_identity},
                    {'desired':'running','status':'warming','cursor':0,'recovery_offset':0,'mapper':initial(),
                     'warmup_task':None,'warmup_snapshot':None,'warmup_offset':0,'warmup_available_at':None,
                     'warmup_done':False,'coverage':None,'last_gap':None,'ignored':0,'error':None,'created_at':self.clock()})
        if connect:self.hub.start(feed)
        self.wake.set();return self.status(state['session_id'])

    @staticmethod
    def feeds(state):return {s['feed_id'] for s in state['sources'].values()} if state.get('sources') else {state['feed_id']}

    def status(self,identifier):
        if self.fatal:raise OpenPineError(Diagnostic('INPUT_UNAVAILABLE','STORAGE_UNAVAILABLE','Live coordinator stopped: '+self.fatal))
        state=self.repository.session(identifier)
        config=self.repository.session_config(identifier)
        source=self.repository.state(state['feed_id']);replay=self.replays.status(state['replay_id'])
        sources={key:{**selected,'source':self.repository.state(selected['feed_id'])} for key,selected in state.get('sources',{}).items()}
        return {**state,'status':'stopped' if state['desired']=='stopped' and state['status'] not in ('failed','execution_failed') else state['status'],
            'source':source,'replay':replay,'preview_candle':source['last_observation'],
            'sources':sources,'unprojected_records':sum(s['source']['accepted']-s['cursor'] for s in sources.values()) if sources else source['accepted']-state['cursor'],
            'configuration':{**{k:config[k] for k in ('instrument','timeframe','start_ms')},'gap_policy':config.get('gap_policy','fail'),
                'fresh_feed':config.get('fresh_feed',False),'bindings':config.get('bindings',{})},
            'profile_id':RECEIVED_PROFILE,'intrabar_reconstructed':False}

    def stop(self,identifier):
        state=self.repository.session(identifier)
        self.repository.save_session({**state,'status':state['status'] if state['status'] in ('failed','execution_failed') else 'stopped'},desired='stopped')
        for feed in self.feeds(state):self.hub.stop(feed,only_if_unreferenced=True)
        self.wake.set();return self.status(identifier)

    def resume(self,identifier):
        state=self.repository.session(identifier)
        if self.repository.session_config(identifier).get('mapping_sha256')!=self.mapping_identity:raise ValueError('Projection changed; create a new session.')
        if state['status']=='failed':raise ValueError('A semantic/source conflict requires a new live session.')
        with self.hub.lock:self.repository.save_session({**state,'status':'recovering','error':None,'execution_failures':0},desired='running')
        for feed in self.feeds(state):self.hub.start(feed)
        self.wake.set();return self.status(identifier)

    def _warmup(self,state):
        if 'sources' in state:return self.graph.warmup(state)
        if state['warmup_task'] is None:
            config=self.repository.session_config(state['session_id']);key=InstrumentKey.from_payload(config['instrument'])
            control=RequestControl(cancelled=lambda:self.stopping.is_set() or self.repository.session(state['session_id'])['desired']!='running',timeout=60)
            end=floor_open(self.history.provider.server_time(control),config['timeframe'])
            if config['start_ms']>=end:raise ValueError('Warmup must include at least one completed interval before live data.')
            task=self.history.submit(HistoryRequest(key,config['timeframe'],config['start_ms'],end,config.get('gap_policy','fail'),
                refresh=config.get('fresh_feed',False)).payload())
            state={**state,'warmup_task':task['task_id']};self.repository.save_session(state)
        task=self.history.status(state['warmup_task'])
        if task['status'] in ('failed','cancelled'):raise ValueError('Warmup failed: '+str(task['error']))
        if task['status'] not in ('succeeded','empty'):return state,False
        if state['warmup_available_at'] is None:
            state={**state,'warmup_snapshot':task['snapshot_id'],'warmup_available_at':self.clock(),'coverage':task['coverage']}
            self.repository.save_session(state)
        return state,True

    def _project(self,state):
        if 'sources' in state:return self.graph.project(state)
        events=[];closed_entries=[];mapper=state['mapper'];identifier=state['session_id']
        config=self.repository.session_config(identifier);tf=config['timeframe']

        def emit(bar,available,recovered):
            nonlocal mapper
            event,mapper=project(mapper,'primary',time=bar.time,available_at=max(available,next_open(bar.time,tf)),closed=True,
                **{k:getattr(bar,k) for k in ('open','high','low','close','volume')},recovered=recovered)
            if event:events.append(event.payload())

        if not state['warmup_done']:
            snapshot=state['warmup_snapshot'];offset=state['warmup_offset']
            if snapshot:
                source=self.manager.market.source(snapshot)
                for bar in source.rows(offset=offset,limit=1000):
                    closed_entries.append((bar,snapshot,offset))
                    emit(bar,state['warmup_available_at'],False);offset+=1
                finished=offset>=source.identity.count
            else:finished=True
            new={**state,'warmup_offset':offset,'warmup_done':finished,'mapper':mapper}
        else:
            records=list(self.repository.records(state['feed_id'],state['cursor'],1))
            if not records:return False
            sequence,record=records[0];new={**state,'cursor':sequence+1,'recovery_offset':0}
            kind=record['kind']
            if kind=='gap':
                mapper={**mapper,'gaps':mapper['gaps']+1};new['last_gap']=record;new['status']='recovering'
            elif kind=='recovery':
                report=record.get('coverage')
                if report is not None and not report['confirmed_complete'] and config.get('gap_policy','fail')=='fail':
                    raise ValueError('REST recovery has missing confirmed candles; create a session with an explicit gap policy or repair the source.')
                offset=state['recovery_offset'];snapshot=record['snapshot_id']
                if snapshot:
                    source=self.manager.market.source(snapshot)
                    for bar in source.rows(offset=offset,limit=1000):
                        closed_entries.append((bar,snapshot,offset));emit(bar,record['received_at'],True);offset+=1
                    if offset<source.identity.count:new.update(cursor=sequence,recovery_offset=offset)
                if new['cursor']!=sequence and mapper['active'] is not None and next_open(mapper['active']['time'],tf)<=record['end_ms']:
                    raise ValueError('REST did not supply the missing confirmed candle; live state cannot be repaired implicitly.')
                new['coverage']=record['coverage']
            elif kind=='observation':
                if record['disposition']=='apply':
                    o=record['observation']
                    event,mapper=project(mapper,'primary',time=o['open_time'],available_at=o['received_at'],closed=o['closed'],
                        boundary=o['close_time']+1 if o['closed'] else o['open_time'],
                        **{k:o[k] for k in ('open','high','low','close','volume')})
                    if event:events.append(event.payload())
                else:new['ignored']+=1
            new['mapper']=mapper
        with self.manager.db.transaction():
            if self.repository.session(identifier)['cursor']!=state['cursor']:raise ValueError('Stale live projection cursor.')
            for bar,snapshot,offset in closed_entries:
                self.repository.closed(state['feed_id'],bar,register=True,snapshot_id=snapshot,row_offset=offset)
            if events:self.replays.repository.append(state['replay_id'],events,transaction=False)
            self.repository.save_session(new)
        return True

    def _advance(self,state):
        identifier=state['session_id']
        if not self.replays.status(state['replay_id'])['pending_events']:return True
        try:
            self.replays.advance(state['replay_id'],max_events=1000,checkpoint_every=1000,
                cancelled=lambda:self.stopping.is_set() or self.repository.session(identifier)['desired']!='running')
        except InterruptedError:return False
        except Exception as error:
            if getattr(getattr(error,'diagnostic',None),'code',None)=='COORDINATOR_BUSY':return False
            state=self.repository.session(identifier);failures=state.get('execution_failures',0)+1
            self.repository.save_session({**state,'status':'execution_interrupted' if failures<3 else 'execution_failed',
                'execution_failures':failures,'error':str(error)},desired='running' if failures<3 else 'stopped')
            return False
        state=self.repository.session(identifier)
        self.repository.save_session({**state,'execution_failures':0,'error':None})
        return True

    def tick(self,identifier,*,execute=True):
        state=self.repository.session(identifier)
        if state['desired']!='running':return
        try:
            if self.repository.session_config(identifier).get('mapping_sha256')!=self.mapping_identity:raise ValueError('Projection changed; create a new session.')
            if execute and not self._advance(state):return
            if not execute and self.replays.status(state['replay_id'])['pending_events']>=2000:return
            if not state['warmup_done']:
                state,ready=self._warmup(state)
                if not ready:return
            started=time.monotonic();first_sequence=state['mapper']['sequence']
            with self.manager.limiter.slot('prepare'):
                for _ in range(1000):
                    if not self._project(state):break
                    state=self.repository.session(identifier)
                    if (state['desired']!='running' or state['mapper']['sequence']-first_sequence>=1000
                            or time.monotonic()-started>=.25):break
            state=self.repository.session(identifier)
            if execute and not self._advance(state):return
            source_states=[self.repository.state(feed) for feed in self.feeds(state)]
            failed=any(s['status']=='failed' for s in source_states)
            source_status='source_failed' if failed else 'recovering' if any(s['status'] in ('recovering','backoff') for s in source_states) else self.repository.state(state['feed_id'])['status']
            self.repository.save_session({**self.repository.session(identifier),
                'status':'warming' if not state['warmup_done'] else source_status},desired='stopped' if failed else None)
        except InterruptedError:
            return
        except (OSError,apsw.Error) as error:
            self.repository.save_session({**self.repository.session(identifier),'status':'storage_interrupted','error':str(error)},desired='stopped')
        except Exception as error:
            if getattr(getattr(error,'diagnostic',None),'code',None)=='COORDINATOR_BUSY':return
            # Accepted replay inputs and the raw cursor remain resumable after
            # process failure; source/semantic conflicts require explicit action.
            self.repository.save_session({**self.repository.session(identifier),'status':'failed','error':str(error)},desired='stopped')

    def execution_metrics(self):
        rows=self.manager.db.execute("SELECT value FROM settings WHERE name='live_execution_metrics'")
        return loads(rows[0][0]) if rows else {'batches':0,'events':0,'worker_launches':0,'child_cpu_seconds':0.,'last_batch':None}

    def _dispatch(self):
        sessions={s['replay_id']:s['session_id'] for s in self.repository.sessions(active=True)
            if self.replays.status(s['replay_id'])['pending_events']}
        if not sessions:return
        try:
            report=self.replays.advance_many(list(sessions),max_events=1000,checkpoint_every=1000,
                cancelled=lambda replay:self.stopping.is_set() or self.repository.session(sessions[replay])['desired']!='running')
        except InterruptedError:return
        except Exception as error:
            if getattr(getattr(error,'diagnostic',None),'code',None)=='COORDINATOR_BUSY':return
            report={'results':[{'replay_id':replay,'status':'failed','error':str(error)} for replay in sessions
                if self.replays.status(replay)['pending_events']]}
        for row in report['results']:
            state=self.repository.session(sessions[row['replay_id']])
            if state['desired']!='running' or row['status']=='interrupted':continue
            if row['status']=='succeeded':
                self.repository.save_session({**state,'execution_failures':0,'error':None,'last_execution':row})
            else:
                failures=state.get('execution_failures',0)+1
                self.repository.save_session({**state,'execution_failures':failures,'error':row['error'],
                    'status':'execution_interrupted' if failures<3 else 'execution_failed'},desired='running' if failures<3 else 'stopped')
        if 'metrics' in report:
            before=self.execution_metrics();metrics=report['metrics']
            value={**before,'batches':before['batches']+1,'events':before['events']+sum(r.get('events',0) for r in report['results']),
                'worker_launches':before['worker_launches']+metrics['worker_launches'],'child_cpu_seconds':before['child_cpu_seconds']+metrics['child_cpu_seconds'],
                'last_batch':metrics}
            self.manager.db.execute("INSERT INTO settings VALUES('live_execution_metrics',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(canonical(value),))

    def view(self,identifier,action,start=0,limit=1000):
        state=self.repository.session(identifier)
        with self.manager.limiter.slot('read'):
            result=self.replays.view(state['replay_id'],action,start,limit)
            if action=='chart':
                config=self.repository.session_config(identifier);replay=self.replays.status(state['replay_id'])
                closed=replay.get('contexts',{}).get('primary',{}).get('last_closed')
                result['bars']=[asdict(b) for b in self.repository.candles(self.manager.market,state['feed_id'],config['start_ms'],
                    closed+1,offset=start,limit=max(1,result['next']-start))] if closed is not None else []
                result['outputs']=replay['outputs']
            return result

    def _loop(self):
        while not self.stopping.is_set():
            try:
                for state in self.repository.sessions(active=True):
                    if self.stopping.is_set():break
                    if state['desired']=='running':self.tick(state['session_id'],execute=False)
                self._dispatch()
                for (feed,) in self.manager.db.execute('SELECT feed_id FROM live_feeds'):
                    try:
                        with self.manager.limiter.slot('prepare'),tempfile.TemporaryDirectory(prefix='live-tape-',dir=self.manager.directory) as directory:
                            self.repository.archive(feed,directory)
                            self.repository.flush_closed(self.manager.market,feed)
                    except Exception as error:
                        if getattr(getattr(error,'diagnostic',None),'code',None)!='COORDINATOR_BUSY':raise
                self.wake.wait(.1);self.wake.clear()
            except Exception as error:self.fatal=str(error);return

    def close(self):
        self.stopping.set();self.wake.set();self.hub.close()
        if self.thread:
            self.thread.join(timeout=15)
            if self.thread.is_alive():raise RuntimeError('Live projection did not stop within the cancellation bound.')
        for (feed,) in self.manager.db.execute('SELECT feed_id FROM live_feeds'):
            try:
                with self.manager.limiter.slot('prepare'):self.repository.flush_closed(self.manager.market,feed,force=True)
            except Exception as error:
                if getattr(getattr(error,'diagnostic',None),'code',None)!='COORDINATOR_BUSY':raise

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
