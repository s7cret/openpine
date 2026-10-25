"""One bounded acquisition coordinator shared by CLI and HTTP."""

import threading
import time
from dataclasses import asdict, replace
from contextlib import contextmanager

import apsw

from packages.contracts.market import HistoryRequest, InstrumentKey
from packages.contracts import Diagnostic,OpenPineError
from packages.contracts.marketdata import DataQuery
from packages.contracts.wire import digest
from packages.marketdata.catalog import InstrumentCatalog, normalize_exchange_info
from packages.marketdata.coverage import coverage_report
from packages.marketdata.history import BinanceHistory, metadata
from packages.marketdata.providers import input_snapshot, make_snapshot
from packages.marketdata.timeframes import check, floor_open, first_open
from packages.marketdata.transport import DEFAULT_TRANSPORT, RequestControl
from packages.storage.market import MarketRepository

DONE={'succeeded','empty','failed','cancelled'}


@contextmanager
def background_slot(limiter,kind,control):
    """Only admitted background workers may wait; HTTP remains fail-fast."""
    while True:
        control.check();reservation=limiter.slot(kind)
        try:reservation.__enter__();break
        except Exception as error:
            if getattr(getattr(error,'diagnostic',None),'code',None)!='COORDINATOR_BUSY':raise
            control.wait(.05)
    try:yield
    finally:reservation.__exit__(None,None,None)


class HistoryJobs:
    def __init__(self, manager, *, provider=None, start=True, checkpoint_rows=10000):
        self.manager,self.repository=manager,MarketRepository(manager.db)
        self.provider=provider or BinanceHistory()
        self.catalog=InstrumentCatalog(self.repository,self.provider.transport)
        self.checkpoint_rows=checkpoint_rows
        self.stopping,self.wake=threading.Event(),threading.Event()
        self.lock=threading.RLock();self.fatal=None
        self.repository.recover()
        self.thread=None
        if start:
            self.thread=threading.Thread(target=self._loop,name='openpine-history-io',daemon=True)
            self.thread.start()

    def submit(self, payload):
        request=HistoryRequest.from_payload(payload);check(request.timeframe)
        # Bound work even when an exchange returns no bars over a huge range.
        if request.timeframe.isdigit() and (request.end_ms-request.start_ms)//(int(request.timeframe)*60000)>10000000:
            raise ValueError('Historical range exceeds ten million possible bars.')
        state=self.repository.submit(request.payload());self.wake.set();return state

    def submit_file(self, snapshot, query):
        from packages.marketdata.providers import snapshot_payload, validate_query
        validate_query(query)
        state=self.repository.submit({'schema_version':'openpine.file-acquisition.1',
            'input_ref':self.manager.objects.put(snapshot_payload(snapshot)),'query':asdict(query)})
        self.wake.set();return state

    def refresh_catalog(self):
        state=self.repository.submit({'schema_version':'openpine.catalog-acquisition.1'})
        self.wake.set();return state

    def status(self, identifier):
        if self.fatal: raise OpenPineError(Diagnostic('INPUT_UNAVAILABLE','STORAGE_UNAVAILABLE','Acquisition storage stopped: '+self.fatal))
        return self.repository.task(identifier)

    def cancel(self, identifier):
        with self.lock:
            state=self.status(identifier)
            if state['status'] not in DONE:
                self.repository.save_task({**state,'cancel_requested':True,
                    'status':'cancelled' if state['status']=='queued' else state['status']})
        self.wake.set();return self.status(identifier)

    def resume(self, identifier):
        with self.lock:
            state=self.status(identifier)
            if state['status'] not in ('failed','cancelled','interrupted'): raise ValueError('Task is not resumable.')
            self.repository.save_task({**state,'status':'queued','cancel_requested':False,'error':None},reset_cancel=True)
        self.wake.set();return self.status(identifier)

    def wait(self, identifier, timeout=3600):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            state=self.status(identifier)
            if state['status'] in DONE: return state
            time.sleep(.05)
        raise TimeoutError('Timed out waiting for acquisition; task remains available by its identifier.')

    def _control(self, identifier):
        return RequestControl(cancelled=lambda:self.stopping.is_set() or self.repository.task(identifier)['cancel_requested'],timeout=3600)

    def process(self, identifier):
        state=self.repository.task(identifier)
        if state['status']!='queued': return state
        state={**state,'status':'running','error':None,'attempts':state['attempts']+1}
        self.repository.save_task(state)
        control=self._control(identifier)
        try:
            payload=self.repository.request(identifier)
            if payload['schema_version']=='openpine.catalog-acquisition.1':
                control.check()
                result=self.catalog.refresh(control)
                state={**state,'status':'succeeded','catalog':result}
            elif payload['schema_version']=='openpine.file-acquisition.1':
                state=self._file(state,payload,control)
            else: state=self._history(state,HistoryRequest.from_payload(payload),control)
            control.check()
            with self.manager.db.transaction():
                if self.repository.task(identifier)['cancel_requested']: raise InterruptedError('Acquisition cancelled.')
                self.repository.save_task(state)
        except InterruptedError as error:
            current=self.repository.task(identifier)
            self.repository.save_task({**current,'status':'interrupted' if self.stopping.is_set() else 'cancelled','error':str(error)})
        except apsw.Error:
            raise
        except Exception as error:
            current=self.repository.task(identifier)
            self.repository.save_task({**current,'status':'failed','error':str(error)})
        return self.repository.task(identifier)

    def _file(self, state, payload, control):
        query=DataQuery(**payload['query']);snapshot=input_snapshot(self.manager.objects.get(payload['input_ref'],max_bytes=16*1024*1024))
        if (snapshot.data.symbol,snapshot.data.timeframe)!=(query.symbol,query.timeframe): raise ValueError('File identity differs from query.')
        rows=tuple(b for b in snapshot.data.bars if query.start_ms<=b.time<query.end_ms)
        if not rows: return {**state,'status':'empty'}
        snapshot=make_snapshot(replace(snapshot.data,bars=rows),{'provider':'file.2','source_ref':payload['input_ref'],'query':asdict(query)})
        control.check()
        with background_slot(self.manager.limiter,'prepare',control):
            identifier=self.manager.market.save(snapshot)
        return {**state,'status':'succeeded','bars':len(rows),'snapshot_id':identifier,'cursor':query.end_ms}

    def _history(self, state, request, control):
        if request.source=='archive':
            from packages.marketdata.archive import ArchiveHistory
            provider=ArchiveHistory(self.provider)
        else: provider=self.provider
        query=DataQuery(request.instrument.pine_symbol,request.timeframe,request.start_ms,request.end_ms)
        namespace='binance.spot.rest.1' if request.source=='rest' else 'binance.spot.archive.1'
        if state['as_of'] is None:
            as_of=self.provider.server_time(control)
            if request.end_ms>as_of: raise ValueError('Historical range extends beyond exchange time.')
            meta=self.repository.instrument(request.instrument)
            if meta is None:
                rows=normalize_exchange_info(self.provider.transport('/api/v3/exchangeInfo',{'symbol':request.instrument.symbol,'showPermissionSets':'false'},control=control),as_of)
                if len(rows)!=1 or rows[0].instrument!=request.instrument: raise ValueError('Exchange returned a different instrument.')
                self.repository.upsert_instrument(rows[0]);meta=rows[0]
            first=meta.first_available_ms
            if first is None:
                first=self.provider.first_available(request.instrument,as_of,control)
                if first is not None:self.repository.first_available(request.instrument,first)
            end=min(request.end_ms,floor_open(as_of,request.timeframe))
            ranges=([(request.start_ms,end)] if request.refresh else self.manager.market.missing(namespace,DataQuery(query.symbol,query.timeframe,query.start_ms,end))) if end>request.start_ms else []
            from packages.contracts.environment import spot_instrument
            state={**state,'instrument_environment':spot_instrument(meta),'trading_rules':{'schema_version':'openpine.trading-rules.1','tick_size':meta.tick_size,'quantity_step':meta.quantity_step,'minimum_quantity':None,'minimum_notional':None},'as_of':as_of,'first_available_ms':first,'ranges':ranges,'range_index':0,
                'cursor':ranges[0][0] if ranges else end}
            self.repository.save_task(state)
        for index in range(state['range_index'],len(state['ranges'])):
            start,stop=state['ranges'][index]
            cursor=max(start,state['cursor'])
            first=state['first_available_ms']
            if first is not None:
                before=min(stop,floor_open(first,request.timeframe))
                if cursor<before:
                    state={**state,'cursor':before,'range_index':index}
                    self.repository.commit_page(state,self.manager.market,namespace,query,cursor,before,None)
                    cursor=before
            pending=[];hashes=[];batch_start=cursor;batch_stop=cursor;pages=0
            def commit():
                nonlocal state,pending,hashes,batch_start,batch_stop,pages
                control.check()
                with background_slot(self.manager.limiter,'prepare',control):
                    snapshot_id=self.manager.market.ingest(metadata(request),pending,
                        {'provider':namespace,'request':request.payload(),'server_time_ms':state['as_of'],
                         'page_sha256':hashes,'range':'[start,end)','confirmed':True}) if pending else None
                state={**state,'cursor':batch_stop,'range_index':index,'pages':state['pages']+pages,'bars':state['bars']+len(pending)}
                if state['bars']>10000000: raise ValueError('Acquisition exceeds ten million bars.')
                self.repository.commit_page(state,self.manager.market,namespace,query,batch_start,batch_stop,snapshot_id)
                pending=[];hashes=[];pages=0;batch_start=batch_stop
            for page in provider.pages(request,start=cursor,stop=stop,as_of=state['as_of'],control=control):
                pending.extend(page.bars);hashes.append(page.sha256);pages+=1;batch_stop=page.stop_ms
                if len(pending)>=self.checkpoint_rows or pages>=100: commit()
            if pages: commit()
            state={**state,'range_index':index+1,'cursor':state['ranges'][index+1][0] if index+1<len(state['ranges']) else stop}
            self.repository.save_task(state)
        control.check()
        with background_slot(self.manager.limiter,'prepare',control):
            try: snapshot_id=self.manager.market.select(query,namespace=namespace)
            except ValueError as error:
                if getattr(getattr(error,'diagnostic',None),'code',None)!='DATA_EMPTY': raise
                snapshot_id=None
            if snapshot_id and state.get('instrument_environment') is not None:snapshot_id=self.manager.market.with_environment(snapshot_id,state['instrument_environment'],state.get('trading_rules'))
            rows=self.manager.market.source(snapshot_id).rows() if snapshot_id else ()
            report=coverage_report(query,rows,as_of=state['as_of'],first_available=state['first_available_ms'],
                unfetched=self.manager.market.missing(namespace,query))
        state={**state,'snapshot_id':snapshot_id,'coverage':report,'status':'succeeded' if snapshot_id else 'empty'}
        if request.gap_policy=='fail' and not report['confirmed_complete']:
            # Keep the diagnostic report, but never publish a partial range as success.
            state={**state,'status':'failed','error':'Confirmed candle coverage is incomplete.'}
        return state

    def _loop(self):
        while not self.stopping.is_set():
            try:
                ready=self.repository.tasks('queued')
                if ready:self.process(ready[-1]['task_id'])
                else:self.wake.wait(.25);self.wake.clear()
            except Exception as error:
                self.fatal=str(error);return

    def close(self):
        self.stopping.set();self.wake.set()
        if self.thread:
            self.thread.join(timeout=15)
            if self.thread.is_alive(): raise RuntimeError('Acquisition thread did not stop within its network bound.')

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
