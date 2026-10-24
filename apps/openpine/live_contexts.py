"""Bounded multi-context warmup and projection on one durable arrival order."""

import copy
import heapq
from dataclasses import asdict

from packages.contracts.contexts import MarketContext
from packages.contracts.context_ir import request_nodes
from packages.contracts.market import InstrumentKey,HistoryRequest
from packages.contracts.live import RECEIVED_PROFILE,MAX_FEEDS
from packages.contracts.wire import digest
from packages.marketdata.live_mapping import initial,project
from packages.marketdata.timeframes import EXECUTION,floor_open,next_open
from packages.marketdata.transport import RequestControl


class LiveContexts:
    def __init__(self,service):
        self.service=service;self.manager=service.manager;self.repository=service.repository

    def create(self,payload,program,settings,*,connect):
        service=self.service;primary=InstrumentKey.from_payload(payload['instrument'])
        bindings={'primary':{'instrument':asdict(primary),'timeframe':payload['timeframe'],'start_ms':payload['start_ms']}}
        keys={(primary.pine_symbol,payload['timeframe']):'primary'}
        explicit=payload.get('contexts',[])
        if type(explicit) is not list or len(explicit)>7:raise ValueError('Live contexts require at most seven additional bindings.')
        for row in explicit:
            if type(row) is not dict or row.keys()!={'context_id','instrument','timeframe','start_ms'}:raise ValueError('Invalid live context binding.')
            key=InstrumentKey.from_payload(row['instrument']);tf=row['timeframe'];context_id=row['context_id']
            MarketContext(context_id,key.pine_symbol,tf,'binance.spot.arrivals.2')
            if context_id in bindings or (key.pine_symbol,tf) in keys:raise ValueError('Duplicate live context identity.')
            bindings[context_id]={k:row[k] for k in ('instrument','timeframe','start_ms')};keys[key.pine_symbol,tf]=context_id
        for node in request_nodes(program):
            symbol,tf=(node.arguments[i].expression.value for i in (0,1))
            if (symbol,tf) in keys:continue
            key=InstrumentKey.from_pine(symbol);context_id='ctx_'+digest({'symbol':symbol,'timeframe':tf})[:12]
            bindings[context_id]={'instrument':asdict(key),'timeframe':tf,'start_ms':floor_open(payload['start_ms'],tf)}
            keys[symbol,tf]=context_id
        if len(bindings)>8:raise ValueError('Live supports at most eight market contexts.')
        feed_configs={};contexts=[]
        for context_id,binding in bindings.items():
            key=InstrumentKey.from_payload(binding['instrument']);tf=binding['timeframe'];start=binding['start_ms']
            if tf not in EXECUTION or type(start) is not int or start<0 or floor_open(start,tf)!=start:raise ValueError('Unsupported or unaligned live context interval.')
            meta=service.history.repository.instrument(key)
            if meta is None or meta.status!='TRADING':raise ValueError('Refresh trading instrument metadata for every live context.')
            binding['instrument_metadata']=meta.payload()
            config={'schema_version':'openpine.live-feed.1','instrument':binding['instrument'],'timeframe':tf,
                'start_ms':start,'ingestion_sha256':service.mapping_identity}
            feed_configs[context_id]=self.repository.choose_config(config,fresh=payload['fresh_feed'])
            contexts.append(asdict(MarketContext(context_id,key.pine_symbol,tf,'binance.spot.arrivals.2')))
        with service.hub.lock:
            needed={self.repository.feed_id(c) for c in feed_configs.values()}-service.hub.active.keys()
            if connect and len(needed)+len(service.hub.active)>MAX_FEEDS:raise ValueError('Live connection budget is full (eight physical feeds).')
        environment_args={};environment_as_of=None
        from packages.contracts.environment import uses_environment,spot_instrument
        if uses_environment(program):
            environment_as_of=service.history.provider.server_time(RequestControl(cancelled=lambda:service.stopping.is_set(),timeout=60))
            from packages.contracts.calendar import shift_open
            environment_args={'environments':{cid:{'instrument':spot_instrument(binding['instrument_metadata']),
                'history_last_ms':shift_open(floor_open(environment_as_of,binding['timeframe']),binding['timeframe'],-1)
                    if floor_open(environment_as_of,binding['timeframe'])>binding['start_ms'] else None} for cid,binding in bindings.items()}}
        replay=service.replays.create({'input_ref':payload['input_ref'],'contexts':contexts,'primary_id':'primary',
            'broker_settings':settings,'timeout_seconds':payload['timeout_seconds'],'event_profile':RECEIVED_PROFILE,**environment_args,
            **({'settings':payload['settings']} if payload.get('settings') else {})})
        with service.hub.lock,self.manager.db.transaction():
            sources={context_id:{'feed_id':self.repository.feed(config),'cursor':0,'recovery_offset':0,'mapper':initial(),
                'warmup_task':None,'warmup_snapshot':None,'warmup_offset':0,'warmup_done':False,'coverage':None,'ignored':0}
                for context_id,config in feed_configs.items()}
            state=self.repository.create_session(sources['primary']['feed_id'],replay['replay_id'],
                {**payload,'bindings':bindings,'broker_settings':settings,'schema_version':'openpine.live-session.2',
                    'profile':RECEIVED_PROFILE,'mapping_sha256':service.mapping_identity},
                {'desired':'running','status':'warming','sources':sources,'mapper':initial(),'cursor':0,
                    'warmup_task':None,'warmup_snapshot':None,'warmup_done':False,'warmup_as_of':environment_as_of,'warmup_available_at':None,
                    'coverage':None,'last_gap':None,'ignored':0,'error':None,'created_at':service.clock()})
            for context_id,source in sources.items():
                if context_id!='primary':self.manager.db.execute('INSERT INTO live_bindings VALUES(?,?,?)',
                    (state['session_id'],context_id,source['feed_id']))
        if connect:
            for source in sources.values():service.hub.start(source['feed_id'])
        service.wake.set();return service.status(state['session_id'])

    def warmup(self,state):
        service=self.service;config=self.repository.session_config(state['session_id']);state=copy.deepcopy(state)
        if state['warmup_as_of'] is None:
            control=RequestControl(cancelled=lambda:service.stopping.is_set() or self.repository.session(state['session_id'])['desired']!='running',timeout=60)
            state['warmup_as_of']=service.history.provider.server_time(control)
            self.repository.save_session(state)
        ready=True
        for context_id,source in state['sources'].items():
            binding=config['bindings'][context_id];end=floor_open(state['warmup_as_of'],binding['timeframe'])
            if source['warmup_done']:continue
            if binding['start_ms']>=end:
                if context_id=='primary':raise ValueError('Primary warmup requires a completed interval.')
                source['warmup_done']=True;continue
            if source['warmup_task'] is None:
                task=service.history.submit(HistoryRequest(InstrumentKey.from_payload(binding['instrument']),binding['timeframe'],
                    binding['start_ms'],end,config['gap_policy'],refresh=config['fresh_feed']).payload())
                source['warmup_task']=task['task_id']
            task=service.history.status(source['warmup_task'])
            if task['status'] in ('failed','cancelled'):raise ValueError(f'{context_id} warmup failed: '+str(task['error']))
            if task['status'] not in ('succeeded','empty'):ready=False;continue
            source['warmup_snapshot']=task['snapshot_id'];source['coverage']=task['coverage']
        state['warmup_task']=state['sources']['primary']['warmup_task']
        if ready and state['warmup_available_at'] is None:state['warmup_available_at']=service.clock()
        self.repository.save_session(state);return state,ready

    def project(self,state):
        service=self.service;config=self.repository.session_config(state['session_id']);new=copy.deepcopy(state)
        events=[];closed_entries=[]

        def emit(context_id,source,bar,available,*,recovered=False,boundary=None,closed=True):
            global_mapper=new['mapper'];mapper={**source['mapper'],'sequence':global_mapper['sequence'],'availability':global_mapper['availability']}
            event,updated=project(mapper,context_id,time=bar['time'],available_at=available,closed=closed,boundary=boundary,
                **{k:bar[k] for k in ('open','high','low','close','volume')},recovered=recovered)
            if event:
                events.append(event.payload())
                aggregate={key:global_mapper[key]+updated[key]-mapper[key] for key in ('clock_clamps','recovered_bars')}
                new['mapper']={**(updated if context_id=='primary' else global_mapper),**aggregate,
                    'sequence':updated['sequence'],'availability':updated['availability']}
            source['mapper']=updated

        if not state['warmup_done']:
            queue=[];iterators=[];finished={}
            try:
                for context_id,source in new['sources'].items():
                    snapshot=source['warmup_snapshot']
                    if source['warmup_done'] or snapshot is None:source['warmup_done']=True;continue
                    selected=self.manager.market.source(snapshot,memory_mb=32);finished[context_id]=selected.identity.count
                    iterator=iter(list(selected.rows(offset=source['warmup_offset'],limit=1000)));iterators.append(iterator)
                    first=next(iterator,None)
                    if first is not None:heapq.heappush(queue,(next_open(first.time,config['bindings'][context_id]['timeframe']),context_id=='primary',context_id,first,iterator))
                while queue and len(events)<1000:
                    boundary,_,context_id,bar,iterator=heapq.heappop(queue);source=new['sources'][context_id]
                    closed_entries.append((source['feed_id'],bar,source['warmup_snapshot'],source['warmup_offset']))
                    emit(context_id,source,asdict(bar),max(new['warmup_available_at'],boundary),boundary=boundary)
                    source['warmup_offset']+=1
                    following=next(iterator,None)
                    if following is not None:heapq.heappush(queue,(next_open(following.time,config['bindings'][context_id]['timeframe']),context_id=='primary',context_id,following,iterator))
                for context_id,count in finished.items():new['sources'][context_id]['warmup_done']=new['sources'][context_id]['warmup_offset']>=count
            finally:
                for iterator in iterators:
                    if hasattr(iterator,'close'):iterator.close()
            new['warmup_done']=all(s['warmup_done'] for s in new['sources'].values())
        else:
            choices=[]
            for context_id,source in new['sources'].items():
                arrival=self.repository.arrival(source['feed_id'],source['cursor'])
                if arrival is not None:choices.append((arrival,context_id))
            if not choices:return False
            arrival,context_id=min(choices);source=new['sources'][context_id]
            rows=list(self.repository.records(source['feed_id'],source['cursor'],1))
            if not rows:raise ValueError('Missing globally ordered source record.')
            sequence,record=rows[0];source['cursor']=sequence+1;source['recovery_offset']=0
            kind=record['kind'];tf=config['bindings'][context_id]['timeframe']
            if kind=='gap':
                source['mapper']['gaps']+=1;new['mapper']['gaps']+=1
                new['last_gap']={**record,'context_id':context_id};new['status']='recovering'
            elif kind=='recovery':
                report=record.get('coverage')
                if report and not report['confirmed_complete'] and config['gap_policy']=='fail':raise ValueError(f'{context_id} recovery has missing confirmed candles.')
                snapshot=record['snapshot_id'];offset=state['sources'][context_id]['recovery_offset']
                if snapshot:
                    selected=self.manager.market.source(snapshot,memory_mb=32)
                    for bar in selected.rows(offset=offset,limit=1000):
                        closed_entries.append((source['feed_id'],bar,snapshot,offset));offset+=1
                        emit(context_id,source,asdict(bar),record['received_at'],boundary=next_open(bar.time,tf),recovered=True)
                    if offset<selected.identity.count:source.update(cursor=sequence,recovery_offset=offset)
                if source['cursor']!=sequence and source['mapper']['active'] is not None and next_open(source['mapper']['active']['time'],tf)<=record['end_ms']:
                    raise ValueError(f'{context_id} recovery omitted an observed unconfirmed candle.')
                source['coverage']=record['coverage']
            elif kind=='observation':
                if record['disposition']=='apply':
                    observed=record['observation']
                    emit(context_id,source,{**observed,'time':observed['open_time']},observed['received_at'],
                        boundary=observed['close_time']+1 if observed['closed'] else observed['open_time'],closed=observed['closed'])
                else:source['ignored']+=1;new['ignored']+=1
            new['last_arrival']=arrival
        primary=new['sources']['primary'];new['cursor']=primary['cursor'];new['warmup_snapshot']=primary['warmup_snapshot'];new['coverage']=primary['coverage']
        with self.manager.db.transaction():
            previous=self.repository.session(state['session_id'])
            if previous['mapper']['sequence']!=state['mapper']['sequence'] or any(previous['sources'][key]['cursor']!=s['cursor'] for key,s in state['sources'].items()):
                raise ValueError('Stale joint live projection cursor.')
            for feed,bar,snapshot,offset in closed_entries:self.repository.closed(feed,bar,register=True,snapshot_id=snapshot,row_offset=offset)
            if events:service.replays.repository.append(state['replay_id'],events,transaction=False)
            self.repository.save_session(new)
        return True
