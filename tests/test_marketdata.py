"""Acquisition boundaries: identities, pages, rate limits and durable recovery."""

import copy
import io
import tempfile
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from packages.contracts.market import InstrumentKey, InstrumentMetadata, HistoryRequest, CandleObservation
from packages.marketdata.catalog import normalize_exchange_info
from packages.marketdata.timeframes import capabilities, next_open, floor_open
from packages.marketdata.history import BinanceHistory
from packages.marketdata.transport import HTTPTransport, RateGovernor, RequestControl
from packages.contracts.marketdata import DataQuery
from packages.marketdata.coverage import coverage_report
from packages.storage.database import SCHEMA_VERSION


KEY=InstrumentKey('binance','spot','BTCUSDT')


def instrument_payload():
    return {'symbols':[{'symbol':'BTCUSDT','baseAsset':'BTC','quoteAsset':'USDT','status':'TRADING',
        'filters':[{'filterType':'PRICE_FILTER','tickSize':'0.01000000'},
                   {'filterType':'LOT_SIZE','stepSize':'0.00001000'}]}]}


def row(t, *, close='10'):
    return [t,'9','12','8',close,'100',t+59999,'1000',1,'0','0','0']


class ContractsTests(unittest.TestCase):
    def test_identity_roundtrip_keeps_markets_distinct_and_rejects_ambiguity(self):
        self.assertNotEqual(KEY.key,InstrumentKey('binance','usdm','BTCUSDT').key)
        self.assertEqual(InstrumentKey.from_payload({'exchange':'binance','market':'spot','symbol':'BTCUSDT','price_kind':'trade'}),KEY)
        self.assertEqual(InstrumentKey.from_pine('BINANCE:币安人生USDT').symbol,'币安人生USDT')
        for value in ('BTCUSDT','BINANCE:BTCUSDT.P','BINANCE:btcusdt','BYBIT:BTCUSDT','BINANCE:BTC/USDT','BINANCE:BTC\u200bUSDT'):
            with self.assertRaises(ValueError): InstrumentKey.from_pine(value)

    def test_catalog_steps_status_and_atomic_validation(self):
        result=normalize_exchange_info(instrument_payload(),1000000)
        self.assertEqual((result[0].tick_size,result[0].quantity_step),('0.01','0.00001'))
        self.assertEqual(InstrumentMetadata.from_payload(result[0].payload()),result[0])
        bad=instrument_payload();bad['symbols']*=2
        with self.assertRaises(ValueError): normalize_exchange_info(bad,1000000)

    def test_calendar_boundaries_and_execution_capabilities(self):
        start=int(datetime(2024,2,1,tzinfo=UTC).timestamp()*1000)
        self.assertEqual(next_open(start,'M')-start,29*86400000)
        sunday=int(datetime(2024,2,4,tzinfo=UTC).timestamp()*1000)
        self.assertEqual(floor_open(sunday,'W'),int(datetime(2024,1,29,tzinfo=UTC).timestamp()*1000))
        self.assertIn('M',capabilities()['history_timeframes'])
        self.assertIn('M',capabilities()['execution_timeframes'])
        self.assertIn('D',capabilities()['live_timeframes'])

    def test_closed_fields_and_strict_boolean_observations(self):
        request=HistoryRequest(KEY,'1',0,60000)
        self.assertEqual(HistoryRequest.from_payload(request.payload()),request)
        with self.assertRaises(ValueError): HistoryRequest.from_payload({**request.payload(),'extra':True})
        args=(KEY,'1',0,59999,60000,60001)
        with self.assertRaises(ValueError): CandleObservation(*args,'false',9.,12.,8.,10.,1.)


class FakeExchange:
    def __init__(self, times=(0,60000,120000,180000), as_of=300000):
        self.times,self.as_of=list(times),as_of
        self.fail=False;self.calls=[]

    def __call__(self,path,params,*,control=None):
        if control:control.check()
        self.calls.append((path,dict(params)))
        if path.endswith('/time'): return {'serverTime':self.as_of}
        if path.endswith('/exchangeInfo'): return instrument_payload()
        if self.fail and params['startTime']>1: raise OSError('recorded connection lost')
        return [row(t) for t in self.times if params['startTime']<=t<=params['endTime']][:params['limit']]


class AcquisitionTests(unittest.TestCase):
    def test_v5_feed_alias_migration_preserves_the_existing_immutable_generation(self):
        import apsw
        from pathlib import Path
        from dataclasses import asdict
        from packages.storage.database import Database,MIGRATIONS
        from packages.storage.live import LiveRepository
        from packages.contracts.wire import canonical
        config={'instrument':asdict(KEY),'timeframe':'1','start_ms':0,'ingestion_sha256':'f'*64}
        identifier=LiveRepository.feed_id(config)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'catalog.sqlite3';connection=apsw.Connection(str(path))
            for migration in MIGRATIONS[:5]:connection.execute(migration)
            connection.execute('INSERT INTO live_feeds VALUES(?,?,?)',(identifier,canonical(config),canonical({'accepted':0})))
            connection.pragma('user_version',5);connection.close()
            db=Database(path)
            try:
                repository=LiveRepository(db,None)
                self.assertEqual(repository.choose_config({**config,'start_ms':60000}),config)
                self.assertEqual(db.execute('SELECT config FROM live_feeds WHERE feed_id=?',(identifier,)),[(canonical(config),)])
                self.assertEqual(db.connection.pragma('user_version'),SCHEMA_VERSION);db.check()
            finally:db.close()

    def test_background_acquisition_waits_for_budget_instead_of_failing_coverage(self):
        import threading,time
        from apps.openpine.bootstrap import local_services
        from apps.openpine.history_service import HistoryJobs
        with tempfile.TemporaryDirectory() as directory,local_services(directory) as services:
            with HistoryJobs(services,provider=BinanceHistory(FakeExchange())) as jobs:
                with services.limiter.slot('prepare'):
                    task=jobs.submit(HistoryRequest(KEY,'1',0,240000).payload())
                    deadline=time.monotonic()+3
                    while jobs.status(task['task_id'])['status']=='queued' and time.monotonic()<deadline:time.sleep(.01)
                    self.assertEqual(jobs.status(task['task_id'])['status'],'running')
                self.assertEqual(jobs.wait(task['task_id'],timeout=10)['status'],'succeeded')

    def test_archive_checksum_and_microsecond_timestamps_have_an_explicit_source(self):
        import csv,hashlib,zipfile
        from packages.marketdata.archive import ArchiveHistory
        start=int(datetime(2025,1,1,tzinfo=UTC).timestamp()*1000)
        name='BTCUSDT-1m-2025-01.zip';buffer=io.BytesIO();text=io.StringIO(newline='');writer=csv.writer(text)
        for t in (start,start+60000):
            value=row(t);value[0]*=1000;value[6]=value[6]*1000+999;writer.writerow(value)
        with zipfile.ZipFile(buffer,'w') as archive:archive.writestr(name[:-4]+'.csv',text.getvalue())
        raw=buffer.getvalue();checksum=(hashlib.sha256(raw).hexdigest()+'  '+name).encode()
        provider=ArchiveHistory(None,downloader=lambda url,control,limit:checksum if url.endswith('.CHECKSUM') else raw)
        request=HistoryRequest(KEY,'1',start,start+120000,source='archive')
        pages=list(provider.pages(request,start=start,stop=start+120000,as_of=start+60*86400000,control=RequestControl()))
        self.assertEqual([b.time for b in pages[0].bars],[start,start+60000])
        provider.downloader=lambda url,control,limit:('0'*64+'  '+name).encode() if url.endswith('.CHECKSUM') else raw
        with self.assertRaises(ValueError):list(provider.pages(request,start=start,stop=start+120000,as_of=start+60*86400000,control=RequestControl()))

    def test_v4_catalog_migrates_without_replacing_existing_metadata(self):
        import apsw
        from pathlib import Path
        from packages.storage.database import Database,MIGRATIONS
        from packages.contracts.wire import canonical
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'catalog.sqlite3';db=apsw.Connection(str(path))
            for migration in MIGRATIONS[:4]:db.execute(migration)
            db.execute('INSERT INTO settings VALUES(?,?)',('preserve',canonical('original')));db.pragma('user_version',4);db.close()
            db=Database(path)
            try:
                self.assertEqual(db.connection.pragma('user_version'),SCHEMA_VERSION)
                self.assertEqual(db.execute('SELECT value FROM settings WHERE name=?',('preserve',)),[(canonical('original'),)])
                db.check()
            finally:db.close()

    def test_page_failure_restart_resumes_only_committed_prefix_and_singleflight(self):
        from apps.openpine.bootstrap import local_services
        from apps.openpine.history_service import HistoryJobs
        with tempfile.TemporaryDirectory() as directory:
            exchange=FakeExchange();exchange.fail=True
            request=HistoryRequest(KEY,'1',0,240000)
            with local_services(directory) as services:
                jobs=HistoryJobs(services,provider=BinanceHistory(exchange,page_limit=2),start=False,checkpoint_rows=2)
                task=jobs.submit(request.payload())
                self.assertEqual(jobs.submit(request.payload())['task_id'],task['task_id'])
                failed=jobs.process(task['task_id'])
                self.assertEqual(failed['status'],'failed',failed)
                self.assertEqual(failed['bars'],2)
                self.assertEqual(failed['cursor'],60001)
                # Simulate death while the next attempt is running.
                jobs.resume(task['task_id']);jobs.repository.save_task({**jobs.status(task['task_id']),'status':'running'})
            exchange.fail=False;exchange.calls=[]
            with local_services(directory) as services:
                jobs=HistoryJobs(services,provider=BinanceHistory(exchange,page_limit=2),start=False,checkpoint_rows=2)
                done=jobs.process(task['task_id'])
                self.assertEqual(done['status'],'succeeded',done)
                self.assertEqual([b.time for b in services.market.source(done['snapshot_id'])],[0,60000,120000,180000])
                self.assertEqual(exchange.calls[0][1]['startTime'],60001)
                self.assertTrue(done['coverage']['confirmed_complete'])

    def test_gap_policy_empty_tail_and_known_earliest_are_separate(self):
        from apps.openpine.bootstrap import local_services
        from apps.openpine.history_service import HistoryJobs
        with tempfile.TemporaryDirectory() as directory, local_services(directory) as services:
            exchange=FakeExchange((60000,180000),as_of=250000)
            jobs=HistoryJobs(services,provider=BinanceHistory(exchange),start=False)
            strict=jobs.process(jobs.submit(HistoryRequest(KEY,'1',0,250000).payload())['task_id'])
            self.assertEqual(strict['status'],'failed',strict)
            self.assertEqual(strict['coverage']['counts'],{'before_available':1,'not_closed':1,'not_fetched':0,'missing_candle':1})
            calls=len(exchange.calls)
            allowed=jobs.process(jobs.submit(HistoryRequest(KEY,'1',0,250000,gap_policy='allow_with_metadata').payload())['task_id'])
            self.assertEqual(allowed['status'],'succeeded',allowed)
            self.assertEqual(len(exchange.calls),calls+1)  # server time only, cache serves all confirmed data

    def test_cancellation_and_failed_page_publication_never_claim_coverage(self):
        from apps.openpine.bootstrap import local_services
        from apps.openpine.history_service import HistoryJobs
        with tempfile.TemporaryDirectory() as directory,local_services(directory) as services:
            jobs=HistoryJobs(services,provider=BinanceHistory(FakeExchange()),start=False)
            task=jobs.submit(HistoryRequest(KEY,'1',0,240000).payload());jobs.cancel(task['task_id'])
            self.assertEqual(jobs.process(task['task_id'])['status'],'cancelled')
            jobs.resume(task['task_id'])
            with patch.object(services.market,'ingest',side_effect=OSError('disk full')):
                failed=jobs.process(task['task_id'])
            self.assertEqual(failed['status'],'failed')
            self.assertEqual(services.db.execute('SELECT count(*) FROM coverage'),[(0,)])

    def test_actual_holes_are_not_inferred_from_fetch_coverage_and_report_is_bounded(self):
        from packages.contracts.model import HistoricalBar
        report=coverage_report(DataQuery('BINANCE:BTCUSDT','1',0,600000),
            [HistoricalBar(t,10.,9.,12.,8.,1.) for t in (0,120000,240000)],as_of=600000,
            unfetched=[(300000,600000)],max_intervals=2)
        self.assertFalse(report['confirmed_complete'])
        self.assertEqual(report['counts']['not_fetched'],5)
        self.assertTrue(report['truncated'])
        self.assertEqual(len(report['intervals']),2)

    def test_http_weight_cooldown_retry_terminal_errors_and_cancellation(self):
        import urllib.error
        clock=[0.]
        control=RequestControl(clock=lambda:clock[0],wait=lambda n:clock.__setitem__(0,clock[0]+n),timeout=20)
        governor=RateGovernor(clock=lambda:clock[0])
        class Response:
            headers={'X-MBX-USED-WEIGHT-1M':'4'}
            def read(self,n):return b'{"serverTime":100}'
            def __enter__(self):return self
            def __exit__(self,*args):pass
        calls=[]
        def opener(*args,**kwargs):
            calls.append(1)
            if len(calls)==1: raise urllib.error.HTTPError('u',429,'limit',{'Retry-After':'2'},io.BytesIO())
            return Response()
        transport=HTTPTransport(opener=opener,governor=governor)
        self.assertEqual(transport('/api/v3/time',{},control=control),{'serverTime':100})
        self.assertGreaterEqual(clock[0],2)
        self.assertEqual(len(calls),2)
        def unavailable(*args,**kwargs):raise urllib.error.HTTPError('u',400,'bad',{},io.BytesIO())
        with self.assertRaises(OSError):HTTPTransport(opener=unavailable)('/api/v3/time',{})
        with self.assertRaises(InterruptedError):transport('/api/v3/time',{},control=RequestControl(cancelled=lambda:True))

    def test_wait_remains_nonnegative_when_time_advances_between_clock_reads(self):
        clock=[0.];delays=[]
        def advancing():clock[0]+=.02;return clock[0]
        def wait(delay):
            self.assertGreaterEqual(delay,0);delays.append(delay)
        control=RequestControl(clock=advancing,wait=wait,timeout=1)
        control.wait(.05)
        self.assertTrue(delays)

    def test_malformed_late_page_is_not_sorted_deduplicated_or_silently_published(self):
        exchange=FakeExchange((60000,0))
        with self.assertRaises(ValueError):list(BinanceHistory(exchange).pages(HistoryRequest(KEY,'1',0,240000)))
