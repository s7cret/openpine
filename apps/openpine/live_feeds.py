"""Shared physical feeds. Raw records precede execution and reconnect repair."""

import tempfile
import threading
import time
import itertools

from packages.contracts.market import InstrumentKey, HistoryRequest
from packages.contracts.wire import digest
from packages.marketdata.stream import BinanceSocket, StreamDisconnected, normalize
from packages.marketdata.timeframes import EXECUTION, floor_open, next_open
from packages.marketdata.transport import RequestControl
from apps.openpine.history_service import background_slot


class FeedHub:
    def __init__(self,manager,history,repository,*,socket_factory=BinanceSocket,clock=lambda:int(time.time()*1000),notify=lambda:None):
        self.manager,self.history,self.repository=manager,history,repository
        self.socket_factory,self.clock,self.notify=socket_factory,clock,notify
        self.lock=threading.RLock();self.active={};self.closed=False

    def start(self,identifier):
        while True:
            with self.lock:
                if self.closed:raise ValueError('Live service is closing.')
                previous=self.active.get(identifier)
                if previous is None:
                    if len(self.active)>=2:raise ValueError('Live connection budget is full (two physical feeds).')
                    config=self.repository.config(identifier)
                    InstrumentKey.from_payload(config['instrument']).pine_symbol
                    if config['timeframe'] not in EXECUTION:raise ValueError('Unsupported live interval.')
                    entry={'stop':threading.Event(),'socket':None,'thread':None}
                    thread=threading.Thread(target=self._run,args=(identifier,entry),name='openpine-feed-'+identifier[:6],daemon=True)
                    entry['thread']=thread;self.active[identifier]=entry;thread.start();return
                if not previous['stop'].is_set():return
            # Its finally must be able to acquire this same lock.
            previous['thread'].join(timeout=15)
            if previous['thread'].is_alive():raise RuntimeError('Previous live generation has not stopped.')

    def _record(self,identifier,record):
        sequence=self.repository.append(identifier,record)
        self.notify()
        return sequence

    def _reconcile(self,identifier,stop):
        config=self.repository.config(identifier);state=self.repository.state(identifier)
        key=InstrumentKey.from_payload(config['instrument']);tf=config['timeframe']
        def cancelled():
            with self.lock:
                if not self.repository.sessions(identifier,active=True):stop.set()
            return stop.is_set()
        control=RequestControl(cancelled=cancelled,timeout=3600)
        as_of=self.history.provider.server_time(control);end=floor_open(as_of,tf)
        # Re-read the last closed candle too: revisions must never disappear into deduplication.
        start=max(config['start_ms'],state['last_closed_ms'] if state['last_closed_ms'] is not None else config['start_ms'])
        if start>=end:return
        task=self.history.submit(HistoryRequest(key,tf,start,end,'allow_with_metadata',refresh=True).payload())
        while True:
            control.check();result=self.history.status(task['task_id'])
            if result['status'] in ('succeeded','empty','failed','cancelled'):break
            stop.wait(.1)
        if result['status'] not in ('succeeded','empty'):raise OSError('REST reconciliation failed: '+str(result['error']))
        last=state['last_closed_ms']
        if result['snapshot_id']:
            # Bounded read; only hashes are kept for future closed-candle comparisons.
            with background_slot(self.manager.limiter,'read',control):
                iterator=enumerate(self.manager.market.source(result['snapshot_id']))
                while batch:=list(itertools.islice(iterator,1000)):
                    with self.manager.db.transaction():
                        for offset,bar in batch:
                            self.repository.closed(identifier,bar,register=True,snapshot_id=result['snapshot_id'],row_offset=offset)
                            last=bar.time
        self._record(identifier,{'kind':'recovery','snapshot_id':result['snapshot_id'],'start_ms':start,'end_ms':end,
            'coverage':result['coverage'],'received_at':self.clock(),'epoch':self.repository.state(identifier)['epoch'],
            'intrabar_reconstructed':False})
        current=self.repository.state(identifier)
        self.repository.save({**current,'last_closed_ms':last})

    def ingest(self,identifier,payload,*,received_at=None):
        config=self.repository.config(identifier);state=self.repository.state(identifier)
        observation=normalize(payload,InstrumentKey.from_payload(config['instrument']),config['timeframe'],
            self.clock() if received_at is None else received_at)
        previous=state['last_observation'];disposition='apply'
        if state['last_closed_ms'] is not None and observation.open_time<=state['last_closed_ms']:
            known=self.repository.closed(identifier,observation) if observation.closed else True
            if observation.closed and not known:raise ValueError('Late insertion changes confirmed history; create a new session.')
            disposition='duplicate' if observation.closed else 'superseded'
        elif previous is not None and observation.open_time==previous['open_time']:
            same=all(observation.payload()[k]==previous[k] for k in ('open_time','close_time','open','high','low','close','volume','closed','exchange_event_time'))
            if same:disposition='duplicate'
            elif observation.exchange_event_time<previous['exchange_event_time']:disposition='late'
        if (disposition=='apply' and previous is not None and observation.open_time>previous['open_time'] and not previous['closed']
                and (state['last_closed_ms'] is None or previous['open_time']>state['last_closed_ms'])):
            self._record(identifier,{'kind':'diagnostic','code':'MISSING_CLOSE','raw_payload':payload,'received_at':observation.received_at})
            raise StreamDisconnected('A new candle arrived before the previous confirmed close; reconcile REST.')
        if disposition=='apply' and previous is not None and observation.open_time==previous['open_time']:
            if (observation.open!=previous['open'] or observation.high<previous['high'] or observation.low>previous['low']
                    or observation.volume<previous['volume']):raise ValueError('Cumulative live candle moved backwards.')
        if disposition=='apply' and observation.closed:self.repository.closed(identifier,observation,register=True)
        self._record(identifier,{'kind':'observation','observation':observation.payload(),'raw_payload':payload,
            'disposition':disposition,'epoch':state['epoch']})
        return observation

    def _run(self,identifier,entry):
        stop=entry['stop'];failures=0
        try:
            if self.repository.state(identifier)['accepted']:
                self._record(identifier,{'kind':'gap','reason':'Feed started after a stop/restart.',
                    'received_at':self.clock(),'intrabar_lost':True,'source':'restart'})
            while not stop.is_set():
                state=self.repository.state(identifier)
                self.repository.save({**state,'status':'connecting' if not failures else 'recovering','error':None})
                socket=None
                try:
                    config=self.repository.config(identifier)
                    socket=self.socket_factory(InstrumentKey.from_payload(config['instrument']),config['timeframe'])
                    with self.lock:entry['socket']=socket
                    # The socket is already receiving into a bounded buffer during REST repair.
                    self._reconcile(identifier,stop)
                    self.repository.save({**self.repository.state(identifier),'status':'live','error':None})
                    last_message=time.monotonic()
                    while not stop.is_set():
                        with self.lock:
                            subscribers=self.repository.sessions(identifier,active=True)
                            if not subscribers:stop.set()
                        if stop.is_set():break
                        accepted=self.repository.state(identifier)['accepted']
                        if subscribers and any(accepted-s['cursor']>10000 for s in subscribers):
                            self.repository.save({**self.repository.state(identifier),'status':'backpressure'})
                            stop.wait(.1);continue
                        message=socket.recv(timeout=.5)
                        if message is None:
                            if time.monotonic()-last_message>30:raise StreamDisconnected('No exchange observations for thirty seconds.')
                            continue
                        self.ingest(identifier,message);last_message=time.monotonic();failures=0
                        if self.repository.state(identifier)['status']!='live':
                            self.repository.save({**self.repository.state(identifier),'status':'live'})
                except InterruptedError:
                    if not stop.is_set():raise
                except (OSError,TimeoutError) as error:
                    if stop.is_set():break
                    failures+=1
                    self._record(identifier,{'kind':'gap','reason':str(error),'received_at':self.clock(),
                        'intrabar_lost':True,'source':'network'})
                    state=self.repository.state(identifier)
                    self.repository.save({**state,'status':'backoff','reconnects':state['reconnects']+1,'error':str(error)})
                    if failures>=5:raise OSError('Five consecutive reconnect attempts failed; resume explicitly.') from error
                    stop.wait(min(30,.5*2**(failures-1)))
                finally:
                    if socket is not None:socket.close()
                    with self.lock:entry['socket']=None
        except Exception as error:
            self.repository.save({**self.repository.state(identifier),'status':'failed','error':str(error)})
        finally:
            if stop.is_set():self.repository.save({**self.repository.state(identifier),'status':'stopped'})
            with self.lock:self.active.pop(identifier,None)
            self.notify()

    def stop(self,identifier,*,only_if_unreferenced=False):
        with self.lock:
            if only_if_unreferenced and self.repository.sessions(identifier,active=True):return
            entry=self.active.get(identifier)
            if entry:
                entry['stop'].set()
                if entry['socket'] is not None:entry['socket'].close()
        if entry:
            entry['thread'].join(timeout=15)
            if entry['thread'].is_alive():raise RuntimeError('Live feed did not stop within its network timeout.')

    def close(self):
        with self.lock:self.closed=True;identifiers=list(self.active)
        for identifier in identifiers:self.stop(identifier)
