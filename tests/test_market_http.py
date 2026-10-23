"""The same acquisition/live services are exercised through authenticated HTTP."""

import queue
import tempfile
import threading
import time
import unittest
from dataclasses import asdict
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from unittest.mock import patch

from apps.openpine.live_feeds import FeedHub
from apps.ui.server import Server
from packages.contracts.market import HistoryRequest
from packages.contracts.wire import canonical,loads
from packages.marketdata.history import BinanceHistory
from packages.marketdata.stream import StreamDisconnected
from test_marketdata import FakeExchange,KEY
from test_live_market import message


class Socket:
    def __init__(self):self.queue=queue.Queue(16);self.closed=False
    def recv(self,timeout=.5):
        try:item=self.queue.get(timeout=timeout)
        except queue.Empty:return None
        if isinstance(item,Exception):raise item
        return item
    def close(self):self.closed=True


class FeedClock:
    def __init__(self):self.value=0.
    def __call__(self):return self.value
    def advance(self,seconds):self.value+=seconds


class FeedIdleTests(unittest.TestCase):
    def test_idle_watchdog_reconnects_only_after_thirty_seconds(self):
        clock=FeedClock();records=[];sockets=[];repairs=[]
        class Repository:
            current={'accepted':0,'epoch':0,'reconnects':0,'status':'stopped','error':None}
            def state(self,identifier):return dict(self.current)
            def config(self,identifier):return {'instrument':asdict(KEY),'timeframe':'1'}
            def sessions(self,identifier,active=True):return ['subscriber']
            def cursor(self,subscriber,identifier):return 0
            def save(self,value):self.current=dict(value)
            def append(self,identifier,record):
                records.append(record);self.current['accepted']+=1;return len(records)-1
        class Stop:
            stopped=False
            waits=[]
            def is_set(self):return self.stopped
            def set(self):self.stopped=True
            def wait(self,seconds):self.waits.append(seconds);return self.stopped
        class IdleSocket:
            def __init__(self):self.closed=False;self.reads=0
            def recv(self,timeout=.5):
                self.reads+=1
                if self.reads>2:raise AssertionError('The idle watchdog failed to reconnect.')
                clock.advance(30. if self.reads==1 else .001)
                return None
            def close(self):self.closed=True
        stop=Stop();repository=Repository()
        def factory(*args):
            socket=IdleSocket();sockets.append(socket)
            if len(sockets)==2:stop.set()
            return socket
        hub=FeedHub(None,None,repository,socket_factory=factory,clock=lambda:123456)
        hub._reconcile=lambda identifier,stop:repairs.append(identifier)
        entry={'stop':stop,'socket':None,'thread':None};hub.active['feed']=entry
        with patch('apps.openpine.live_feeds.time',SimpleNamespace(time=time.time,monotonic=clock)):
            hub._run('feed',entry)
        self.assertEqual([s.reads for s in sockets],[2,0])
        self.assertTrue(all(s.closed for s in sockets))
        self.assertEqual(repairs,['feed','feed'])
        self.assertEqual(repository.current['reconnects'],1)
        self.assertEqual(stop.waits,[.5])
        self.assertEqual(records,[{'kind':'gap','reason':'No exchange observations for thirty seconds.',
            'received_at':123456,'intrabar_lost':True,'source':'network'}])
        self.assertFalse(hub.active)


class MarketHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.exchange=FakeExchange(as_of=240000);cls.sockets=[]
        # This recorded socket has no autonomous exchange traffic. Advance its
        # silence clock explicitly, so slow worker/HTTP work does not fabricate
        # extra wire disconnects. Only FeedHub sees this clock; worker deadlines,
        # HTTP waits and all other uses of real monotonic time remain unchanged.
        cls.feed_clock=FeedClock()
        feed_time=patch('apps.openpine.live_feeds.time',SimpleNamespace(time=time.time,monotonic=cls.feed_clock))
        feed_time.start();cls.addClassCleanup(feed_time.stop)
        def factory(*args):
            socket=Socket();cls.sockets.append(socket);return socket
        cls.server=Server(('127.0.0.1',0),cls.temp.name,market_provider=BinanceHistory(cls.exchange),socket_factory=factory)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}';cls.token=cls.call('/api/config')[1]['token']

    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.temp.cleanup()

    @classmethod
    def call(cls,path,body=None,headers=None):
        request=Request(cls.base+path,data=canonical(body) if body is not None else None,
            headers={'Content-Type':'application/json','X-OpenPine-Token':getattr(cls,'token',''),**(headers or {})})
        for attempt in range(8):
            try:
                with urlopen(request,timeout=60) as response:return response.status,loads(response.read())
            except HTTPError as error:
                status=error.code;value=loads(error.read());error.close()
                if status==503 and any(d['code']=='COORDINATOR_BUSY' for d in value['diagnostics']) and attempt<7:
                    time.sleep(min(.8,.05*2**attempt));continue
                return status,value

    def wait(self,function,predicate,timeout=90):
        deadline=time.monotonic()+timeout;last=None
        while time.monotonic()<deadline:
            last=function()
            if predicate(last):return last
            time.sleep(.05)
        self.fail(str(last))

    def test_async_history_catalog_and_strict_ranges(self):
        code,task=self.call('/api/market/catalog/refresh',{});self.assertEqual(code,202,task)
        self.wait(lambda:self.call('/api/market/history/'+task['task_id'])[1],lambda s:s['status']=='succeeded')
        instruments=self.call('/api/market/instruments?q=BTC&limit=5')[1]['instruments']
        self.assertEqual(instruments[0]['tick_size'],'0.01')
        code,task=self.call('/api/market/history',HistoryRequest(KEY,'1',0,240000).payload())
        self.assertEqual(code,202,task)
        state=self.wait(lambda:self.call('/api/market/history/'+task['task_id'])[1],lambda s:s['status'] in ('succeeded','failed'))
        self.assertEqual(state['status'],'succeeded',state);self.assertEqual(state['coverage']['bars'],4)
        catalog=self.call('/api/market/storage?start=0&limit=50')[1]
        self.assertGreater(catalog['storage']['object_bytes'],0)
        self.assertTrue(any(row['snapshot_id']==state['snapshot_id'] and row['coverage']['confirmed_complete'] for row in catalog['datasets']))
        self.assertEqual(self.call('/api/market/storage?limit=101')[0],400)
        self.assertEqual(self.call('/api/market/storage?start=-1')[0],400)
        self.assertEqual(self.call('/api/market/maintenance/preview',{'before_ms':int(time.time()*1000),'grace_seconds':3600}, {'X-OpenPine-Token':'bad'})[0],403)
        self.assertEqual(self.call('/api/market/capabilities')[1]['live_contexts'],8)
        self.assertEqual(self.call('/api/market/instruments?limit=201')[0],400)
        self.assertEqual(self.call('/api/market/history',HistoryRequest(KEY,'1',0,240000).payload(),{'X-OpenPine-Token':'bad'})[0],403)

    def test_two_versions_share_socket_preview_gap_repair_stop_and_resume(self):
        task=self.call('/api/market/catalog/refresh',{})[1]
        self.wait(lambda:self.call('/api/market/history/'+task['task_id'])[1],lambda s:s['status']=='succeeded')
        identifiers=[]
        for version in (1,6):
            declaration='indicator' if version>=5 else 'study'
            code,compiled=self.call('/api/compile',{'source':f'//@version={version}\n{declaration}("live")\nplot(close)','source_name':'live.pine'})
            self.assertEqual(code,200,compiled)
            code,state=self.call('/api/market/live',{'input_ref':compiled['input_ref'],'instrument':asdict(KEY),
                'timeframe':'1','start_ms':0,'broker_settings':None,'timeout_seconds':30})
            self.assertEqual(code,202,state);identifiers.append(state['session_id'])
        for identifier in identifiers:
            self.wait(lambda:self.call('/api/market/live/'+identifier)[1],lambda s:s['replay']['confirmed_bars']==4)
        self.assertEqual(len(self.sockets),1)
        self.sockets[0].queue.put(message())
        for identifier in identifiers:
            state=self.wait(lambda:self.call('/api/market/live/'+identifier)[1],lambda s:s['replay']['preview'] is not None)
            self.assertEqual(state['replay']['confirmed_bars'],4)
        self.exchange.as_of=300000;self.exchange.times.append(240000)
        self.sockets[0].queue.put(StreamDisconnected('recorded wire disconnect'))
        for identifier in identifiers:
            state=self.wait(lambda:self.call('/api/market/live/'+identifier)[1],lambda s:s['replay']['confirmed_bars']==5)
            self.assertGreaterEqual(state['mapper']['gaps'],1)
            self.assertEqual(state['mapper']['recovered_bars'],1)
            page=self.call(f'/api/market/live/{identifier}/chart?start=1&limit=2')[1]
            self.assertEqual([b['time'] for b in page['bars']],[60000,120000])
            self.assertEqual(self.call(f'/api/market/live/{identifier}/chart?limit=5001')[0],400)
        self.assertEqual(len(self.sockets),2)
        self.call(f'/api/market/live/{identifiers[0]}/stop',{})
        self.assertFalse(self.sockets[-1].closed)
        self.call(f'/api/market/live/{identifiers[1]}/stop',{})
        self.assertTrue(self.sockets[-1].closed)
        self.assertTrue(self.call(f'/api/market/live/{identifiers[0]}/journal?start=0&limit=100')[1]['records'])
        with urlopen(self.base+'/market.js') as response:self.assertIn(b'waitAcquisition',response.read())
