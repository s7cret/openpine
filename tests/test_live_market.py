"""Raw arrivals, received-time execution, subscriber recovery and real sockets."""

import copy
import tempfile
import threading
import unittest
from dataclasses import asdict
from unittest.mock import patch

from apps.openpine.bootstrap import local_manager
from apps.openpine.history_service import HistoryJobs
from apps.openpine.live_service import LiveSessions
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.contexts import MarketContext
from packages.contracts.live import RECEIVED_PROFILE
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.marketdata.catalog import normalize_exchange_info
from packages.marketdata.history import BinanceHistory
from packages.marketdata.live_mapping import initial, project
from packages.marketdata.stream import normalize, BinanceSocket
from test_marketdata import KEY, FakeExchange, instrument_payload
from test_realtime import Sink, text

CONTEXT=MarketContext('primary','BINANCE:BTCUSDT','1','binance.spot.arrivals.1')


def message(t=240000,*,closed=False,event_time=None,close='10',volume='100'):
    return {'e':'kline','s':'BTCUSDT','E':(t+60001 if closed else t+30000) if event_time is None else event_time,
        'k':{'t':t,'T':t+59999,'s':'BTCUSDT','i':'1m','x':closed,'o':'9','h':'12','l':'8','c':close,'v':volume}}


def prepare(manager,*,source=None,clock=lambda:400000):
    history=HistoryJobs(manager,provider=BinanceHistory(FakeExchange(as_of=240000)),start=False)
    history.repository.replace_instruments(normalize_exchange_info(instrument_payload(),240000))
    live=LiveSessions(manager,history,start=False,clock=clock)
    source=source or '//@version=6\nindicator("arrivals")\nplot(close)'
    state=live.create({'input_ref':manager.freeze_input({'source':source,'source_name':'live.pine'}),
        'instrument':asdict(KEY),'timeframe':'1','start_ms':0,'broker_settings':None,'timeout_seconds':30},connect=False)
    return history,live,state['session_id']


class ReceivedProfileTests(unittest.TestCase):
    def test_all_versions_received_times_rollback_and_joint_checkpoint_boundaries(self):
        session=Session(profile_id=HISTORICAL_PROFILE_ID)
        for version in range(1,7):
            for strategy in (False,True):
                program=session.compile(text(version,strategy),'received.pine');sink=Sink()
                engine=ReplayEngine(session,program,(CONTEXT,),'primary',sink,profile=RECEIVED_PROFILE)
                mapper=initial();events=[]
                for index in range(4):
                    for closed in (False,True):
                        event,mapper=project(mapper,'primary',time=index*60000,available_at=900000+len(events),closed=closed,
                            open=10.+index,high=12.+index,low=9.+index,close=11.+index,volume=1. if not closed else 2.)
                        events.append(event);engine.apply(event)
                        state=engine.checkpoint();restored_sink=Sink()
                        # A streaming sink owns the old ledger prefix; in-memory
                        # checkpoints carry it in the broker checkpoint.
                        restored=ReplayEngine(session,program,(CONTEXT,),'primary',restored_sink,profile=RECEIVED_PROFILE)
                        restored.restore(state)
                        self.assertEqual(restored.status(),engine.status())
                self.assertEqual(engine.evaluation.count,4)
                self.assertEqual(engine.status()['profile_id'],RECEIVED_PROFILE)
                self.assertIsNone(engine.preview)
                old=ReplayEngine(session,program,(CONTEXT,),'primary',Sink())
                with self.assertRaises(ValueError):old.apply(events[0])

    def test_raw_source_times_flags_and_interval_are_validated(self):
        item=normalize(message(),KEY,'1',400000)
        self.assertEqual((item.open_time,item.exchange_event_time,item.received_at),(240000,270000,400000))
        from packages.contracts.market import InstrumentKey
        unicode_key=InstrumentKey.from_pine('BINANCE:币安人生USDT');unicode_frame=message()
        unicode_frame['s']=unicode_frame['k']['s']=unicode_key.symbol
        self.assertEqual(normalize(unicode_frame,unicode_key,'1',400000).instrument,unicode_key)
        for mutate in (lambda p:p['k'].update(x='false'),lambda p:p['k'].update(i='1M'),
                       lambda p:p.update(s='ETHUSDT'),lambda p:p['k'].update(c='NaN'),
                       lambda p:p.update(E=250000,k={**p['k'],'x':True})):
            bad=message();mutate(bad)
            with self.assertRaises((ValueError,KeyError)):normalize(bad,KEY,'1',400000)


class LivePersistenceTests(unittest.TestCase):
    def test_fresh_source_generation_admits_revised_history_without_rewriting_old_confirmations(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            history,live,old=prepare(manager)
            old_state=live.repository.session(old);old_feed=old_state['feed_id']
            live.hub.ingest(old_feed,message(closed=True),received_at=400000)
            exchange=FakeExchange((0,60000,120000,180000,240000),as_of=300000)
            def revised(path,params,*,control=None):
                result=exchange(path,params,control=control)
                if path.endswith('/klines'):
                    for bar in result:
                        if bar[0]==240000:bar[4]='11'
                return result
            history.provider=BinanceHistory(revised)
            payload={'input_ref':manager.freeze_input({'source':'//@version=6\nindicator("revision")\nplot(close)','source_name':'revision.pine'}),
                'instrument':asdict(KEY),'timeframe':'1','start_ms':0,'broker_settings':None,'timeout_seconds':30}
            normal=live.create(payload,connect=False)['session_id']
            live.tick(normal);history.process(live.repository.session(normal)['warmup_task']);live.tick(normal)
            self.assertEqual(live.status(normal)['status'],'failed')
            fresh=live.create({**payload,'fresh_feed':True},connect=False)['session_id']
            live.tick(fresh);history.process(live.repository.session(fresh)['warmup_task']);live.tick(fresh)
            state=live.status(fresh)
            self.assertNotEqual(state['feed_id'],old_feed)
            self.assertEqual(state['replay']['confirmed_bars'],5,state)
            self.assertEqual(live.view(fresh,'chart',0,100)['bars'][-1]['close'],11.)
            self.assertTrue(live.repository.closed(old_feed,normalize(message(closed=True),KEY,'1',400000)))
            self.assertEqual(live.repository.feed_id(live.repository.choose_config(live.repository.config(old_feed))),state['feed_id'])

    def test_failed_last_subscriber_releases_the_physical_feed_without_manual_stop(self):
        import time
        class Socket:
            def __init__(self,*args):self.stopped=threading.Event()
            def recv(self,timeout=.5):self.stopped.wait(timeout);return None
            def close(self):self.stopped.set()
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            history,live,identifier=prepare(manager)
            history.thread=threading.Thread(target=history._loop,daemon=True);history.thread.start()
            live.hub.socket_factory=Socket
            state=live.repository.session(identifier);live.hub.start(state['feed_id'])
            live.repository.save_session({**state,'status':'failed','error':'fixture semantic rejection'},desired='stopped')
            deadline=time.monotonic()+10
            while state['feed_id'] in live.hub.active and time.monotonic()<deadline:time.sleep(.02)
            self.assertNotIn(state['feed_id'],live.hub.active)
            self.assertEqual(live.repository.state(state['feed_id'])['status'],'stopped')
            live.close();history.close()

    def test_projection_version_is_pinned_and_changed_implementation_requires_a_new_session(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            history,live,identifier=prepare(manager)
            before=live.status(identifier)['feed_id']
            with patch('apps.openpine.live_service.implementation_identity',return_value='0'*64):
                changed=LiveSessions(manager,history,start=False)
            with self.assertRaises(ValueError):changed.resume(identifier)
            changed.tick(identifier)
            self.assertEqual(changed.status(identifier)['status'],'failed')
            self.assertEqual(changed.status(identifier)['replay']['accepted_events'],0)
            config=changed.repository.config(before)
            self.assertNotEqual(changed.repository.feed({**config,'ingestion_sha256':'0'*64}),before)

    def test_killed_native_worker_replays_the_same_accepted_prefix_without_duplicate_input(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            history,live,identifier=prepare(manager)
            state=live.repository.session(identifier);live.repository.save_session({**state,'warmup_done':True})
            live.hub.ingest(state['feed_id'],message(),received_at=400000)
            real_popen=subprocess.Popen;pids=[]
            def killed(*args,**kwargs):
                process=real_popen(*args,**kwargs);pids.append(process.pid);process.kill();process.wait(timeout=5);return process
            with patch('apps.openpine.replay.subprocess.Popen',side_effect=killed):live.tick(identifier)
            failed=live.status(identifier)
            self.assertEqual(failed['status'],'execution_interrupted',failed)
            self.assertEqual(failed['desired'],'running')
            self.assertEqual(failed['replay']['accepted_events'],1)
            self.assertEqual(failed['replay']['sequence'],-1)
            self.assertTrue(pids)
            live.tick(identifier)
            recovered=live.status(identifier)
            self.assertEqual(recovered['replay']['accepted_events'],1)
            self.assertEqual(recovered['replay']['sequence'],0)
            self.assertEqual(recovered['replay']['preview']['outputs'][0]['value'],10.)

    def test_backup_restore_keeps_live_tail_and_candle_compaction_preserves_values(self):
        from pathlib import Path
        from packages.storage.backup import backup,restore
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);store=root/'store'
            with local_manager(store) as manager:
                history,live,identifier=prepare(manager)
                state=live.repository.session(identifier);feed=state['feed_id']
                live.repository.save_session({**state,'warmup_done':True})
                live.hub.ingest(feed,message(closed=True),received_at=400000);live.tick(identifier)
                before=live.view(identifier,'chart',0,100)
                self.assertEqual([b['time'] for b in before['bars']],[240000])
                self.assertEqual(manager.db.execute('SELECT count(*) FROM live_closed WHERE bar IS NOT NULL'),[(1,)])
                backup(manager.db,store,root/'backup')
            restore(root/'backup',root/'restored')
            with local_manager(root/'restored') as manager:
                history=HistoryJobs(manager,provider=BinanceHistory(FakeExchange()),start=False)
                live=LiveSessions(manager,history,start=False)
                self.assertEqual(live.view(identifier,'chart',0,100),before)
                live.repository.flush_closed(manager.market,feed,force=True)
                self.assertEqual(live.view(identifier,'chart',0,100),before)
                self.assertEqual(manager.db.execute('SELECT count(*) FROM live_closed WHERE bar IS NOT NULL'),[(0,)])
                # Maintenance may expire coverage but not the snapshots retained
                # by the immutable session/source index.
                import time
                manager.services.lifecycle.prune(before=time.time(),expire_coverage=True,apply=True)
                self.assertEqual(live.view(identifier,'chart',0,100),before)

    def test_batched_execution_consumes_every_observation_and_replaces_one_preview(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            history,live,identifier=prepare(manager)
            state=live.repository.session(identifier);feed=state['feed_id']
            live.repository.save_session({**state,'warmup_done':True})
            for index in range(20):live.hub.ingest(feed,message(event_time=270000+index,volume=str(100+index)),received_at=400000+index)
            advances=[];advance=live.replays.advance
            def run(*args,**kwargs):advances.append(1);return advance(*args,**kwargs)
            with patch.object(live.replays,'advance',side_effect=run):live.tick(identifier)
            status=live.status(identifier)
            self.assertEqual(status['replay']['accepted_events'],20)
            self.assertEqual(status['replay']['sequence'],19)
            self.assertEqual(status['replay']['confirmed_bars'],0)
            self.assertEqual(status['replay']['preview']['revision'],19)
            self.assertEqual(advances,[1])

    def test_raw_dedup_late_frames_closed_revision_and_shared_feed_identity(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            history,live,identifier=prepare(manager)
            feed=live.repository.session(identifier)['feed_id']
            same=live.repository.feed({**live.repository.config(feed),'start_ms':60000})
            self.assertEqual(feed,same)
            from packages.contracts.market import InstrumentKey
            foreign=message();foreign['s']=foreign['k']['s']='ETHUSDT'
            foreign=normalize(foreign,InstrumentKey('binance','spot','ETHUSDT'),'1',400000)
            with self.assertRaises(ValueError):live.repository.append(feed,{'kind':'observation','observation':foreign.payload()})
            self.assertEqual(live.repository.state(feed)['accepted'],0)
            live.hub.ingest(feed,message(),received_at=400000)
            live.hub.ingest(feed,message(),received_at=400001)
            live.hub.ingest(feed,message(event_time=260000),received_at=400002)
            live.hub.ingest(feed,message(closed=True),received_at=400003)
            live.hub.ingest(feed,message(closed=True),received_at=400004)
            self.assertEqual([r['disposition'] for _,r in live.repository.records(feed)],['apply','duplicate','late','apply','duplicate'])
            with self.assertRaises(ValueError):live.hub.ingest(feed,message(closed=True,close='11'),received_at=400005)
            self.assertEqual(live.repository.state(feed)['accepted'],5)

    def test_projection_and_source_cursor_commit_together_after_disk_failure(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            history,live,identifier=prepare(manager)
            state=live.repository.session(identifier);state={**state,'warmup_done':True}
            live.repository.save_session(state)
            live.hub.ingest(state['feed_id'],message(),received_at=400000)
            with patch.object(live.repository,'save_session',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):live._project(state)
            self.assertEqual(live.repository.session(identifier)['cursor'],0)
            self.assertEqual(live.replays.status(state['replay_id'])['accepted_events'],0)
            live._project(live.repository.session(identifier))
            self.assertEqual(live.repository.session(identifier)['cursor'],1)
            self.assertEqual(live.replays.status(state['replay_id'])['accepted_events'],1)
            self.assertFalse(live._project(live.repository.session(identifier)))

    def test_real_worker_warmup_arrivals_restart_and_gap_reconciliation_all_versions(self):
        for version in range(1,7):
            with self.subTest(version=version),tempfile.TemporaryDirectory() as directory:
                with local_manager(directory) as manager:
                    history,live,identifier=prepare(manager,source=text(version,True))
                    live.tick(identifier)
                    task=live.repository.session(identifier)['warmup_task']
                    history.process(task)
                    live.tick(identifier)
                    state=live.repository.session(identifier);feed=state['feed_id']
                    self.assertEqual(live.status(identifier)['replay']['confirmed_bars'],4)
                    live.hub.ingest(feed,message(),received_at=400000);live.tick(identifier)
                    self.assertEqual(live.status(identifier)['replay']['confirmed_bars'],4)
                    live.repository.append(feed,{'kind':'gap','reason':'fixture disconnect','received_at':400001,'intrabar_lost':True})
                    live.tick(identifier)
                    # The close was missed on the wire. REST supplies only the
                    # final bar, with an explicit recovery record in the tape.
                    from packages.contracts import HistoricalBar
                    from packages.marketdata.history import metadata
                    from packages.contracts.market import HistoryRequest
                    snapshot=manager.market.ingest(metadata(HistoryRequest(KEY,'1',240000,300000)),
                        [HistoricalBar(240000,11.,9.,12.,8.,101.)],{'provider':'recorded-rest.1'})
                    live.repository.append(feed,{'kind':'recovery','snapshot_id':snapshot,'start_ms':240000,'end_ms':300000,
                        'coverage':None,'received_at':400002,'intrabar_reconstructed':False})
                    # Leave both the projection and execution pending across restart.
                with local_manager(directory) as manager:
                    history=HistoryJobs(manager,provider=BinanceHistory(FakeExchange(as_of=300000)),start=False)
                    live=LiveSessions(manager,history,start=False)
                    live.tick(identifier)
                    status=live.status(identifier)
                    self.assertEqual(status['replay']['confirmed_bars'],5,status)
                    self.assertEqual(status['mapper']['gaps'],1)
                    self.assertEqual(status['mapper']['recovered_bars'],1)
                    ledger=live.view(identifier,'ledger',0,1000)
                    live.tick(identifier)
                    self.assertEqual(live.view(identifier,'ledger',0,1000),ledger)
                    self.assertEqual(status['mapper']['active'],None)

    def test_archived_raw_records_restore_and_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            history,live,identifier=prepare(manager)
            feed=live.repository.session(identifier)['feed_id']
            for index in range(3):live.repository.append(feed,{'kind':'diagnostic','code':str(index)})
            with patch('packages.storage.live.BLOCK',2):live.repository.archive(feed,manager.directory/'raw-proof')
            self.assertEqual([r['code'] for _,r in live.repository.records(feed)],['0','1','2'])
            sha=manager.db.execute('SELECT object_id FROM live_parts')[0][0]
            manager.objects.path(sha).write_bytes(b'corrupt')
            with self.assertRaises(ValueError):list(live.repository.records(feed))

    def test_real_local_websocket_is_bounded_and_closes_cleanly(self):
        from websockets.sync.client import connect
        from websockets.sync.server import serve
        def handler(socket):socket.send(canonical(message()).decode('utf-8'))
        with serve(handler,'127.0.0.1',0) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            url='ws://127.0.0.1:'+str(server.socket.getsockname()[1])
            client=BinanceSocket(KEY,'1',connector=lambda uri,**kwargs:connect(url,**kwargs))
            try:self.assertEqual(client.recv(timeout=2),message())
            finally:client.close();server.shutdown();thread.join(timeout=3)
