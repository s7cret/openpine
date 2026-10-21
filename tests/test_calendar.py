"""Independent UTC calendar expectations, historical availability and replay restore."""
import copy
import unittest
from datetime import UTC,datetime

from packages.contracts import HISTORICAL_PROFILE_ID,HistoricalData,HistoricalBar
from packages.contracts.calendar import aligned,floor_open,next_open,requestable,shift_open
from packages.contracts.contexts import MarketContext,ContextBinding
from packages.contracts.live import RECEIVED_PROFILE
from packages.contracts.realtime import MarketEvent,PROFILE
from packages.execution.session import Session
from packages.execution.driver import MemorySource,HistoricalDriver
from packages.execution.realtime import ReplayEngine
from packages.marketdata import data_identity
from packages.marketdata.stream import normalize
from test_realtime import Sink,text
from test_marketdata import KEY


def at(value):return int(datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp()*1000)


class CalendarTests(unittest.TestCase):
    def test_independent_leap_month_monday_and_utc_day_boundaries(self):
        self.assertEqual(next_open(at('2024-02-01'),'M'),at('2024-03-01'))
        self.assertEqual(next_open(at('2025-02-01'),'M')-at('2025-02-01'),28*86400000)
        self.assertEqual(next_open(at('2024-02-01'),'M')-at('2024-02-01'),29*86400000)
        self.assertEqual(floor_open(at('2024-02-04'),'W'),at('2024-01-29'))
        self.assertEqual(next_open(at('2024-03-10'),'D')-at('2024-03-10'),86400000)
        self.assertEqual(shift_open(at('2024-03-01'),'M',-1),at('2024-02-01'))
        self.assertTrue(requestable('W','M'));self.assertFalse(requestable('M','W'))
        self.assertFalse(requestable('1000','D'));self.assertFalse(requestable('5','3'))
        self.assertEqual(MarketContext('p','TEST:X','W','recorded').time_rule,'calendar-utc.1')
        with self.assertRaises(ValueError):MarketContext('p','TEST:X','M','recorded',time_rule='fixed-duration-utc.1')

    def test_daily_monthly_availability_and_joint_checkpoint_all_versions(self):
        primary=MarketContext('primary','TEST:CAL','D','recorded.1')
        higher=MarketContext('monthly','TEST:CAL','M','recorded.1')
        dates=('2024-01-30','2024-01-31','2024-02-01','2024-02-28','2024-02-29','2024-03-01')
        daily=HistoricalData('primary','TEST:CAL','D',tuple(HistoricalBar(at(d),10.) for d in dates))
        monthly=HistoricalData('monthly','TEST:CAL','M',tuple(HistoricalBar(at(d),float(v)) for d,v in
            (('2024-01-01',100),('2024-02-01',200),('2024-03-01',300))))
        binding=ContextBinding(higher,data_identity(monthly).sha256)
        session=Session(profile_id=HISTORICAL_PROFILE_ID)
        for version in range(1,7):
            declaration='study' if version<5 else 'indicator'
            request='security("TEST:CAL","M",close,false)' if version<3 else 'security("TEST:CAL","M",close,false,false)' if version<5 else 'request.security("TEST:CAL","M",close,gaps=barmerge.gaps_off,lookahead=barmerge.lookahead_off)'
            program=session.compile(f'//@version={version}\n{declaration}("calendar")\nplot({request})')
            def driver():
                runtime=session.validate_program(program);source=MemorySource(daily)
                graph=session.context_scheduler(program,runtime,source,primary,[(binding,MemorySource(monthly))])
                return HistoricalDriver(runtime.prepare(program),source,graph)
            first=driver();first.run();expected=first.finish(data_identity(daily))
            self.assertEqual(list(expected.outputs[0].values),[100.,100.,200.,200.,200.,300.] if version<3 else [None,100.,100.,100.,200.,200.])
            for cut in (1,3,5):
                paused=driver();saved=[];paused.run(pause_after=cut,checkpoint=lambda d:saved.append(copy.deepcopy(d.checkpoint())))
                resumed=driver();resumed.restore(saved[0]);resumed.run()
                self.assertEqual(resumed.finish(data_identity(daily)),expected)
        weekly=HistoricalData('primary','TEST:CAL','W',tuple(HistoricalBar(at(d),10.) for d in ('2024-01-29','2024-02-26')))
        primary=MarketContext('primary','TEST:CAL','W','recorded.1')
        _,result=session.run('//@version=6\nindicator("week-month")\nplot(request.security("TEST:CAL","M",close,lookahead=barmerge.lookahead_off))',
            data=weekly,primary_context=primary,context_sources=[(binding,MemorySource(monthly))])
        self.assertEqual(list(result.outputs[0].values),[100.,200.])

    def test_monthly_observed_and_received_replay_restore_all_versions(self):
        session=Session(profile_id=HISTORICAL_PROFILE_ID);context=MarketContext('primary','BINANCE:BTCUSDT','M','recorded.1')
        for version in range(1,7):
            program=session.compile(text(version))
            for profile in (PROFILE,RECEIVED_PROFILE):
                engine=ReplayEngine(session,program,(context,),'primary',Sink(),profile=profile)
                for index,date in enumerate(('2024-02-01','2024-03-01')):
                    start=at(date);end=next_open(start,'M')
                    for revision,kind,stamp in ((0,'open',start),(1,'update',start+86400000),(2,'close',end)):
                        event=MarketEvent(index*3+revision,'primary',kind,start,stamp+(1000 if profile==RECEIVED_PROFILE else 0),revision,
                            10.,12.,9.,11.,float(revision))
                        engine.apply(event);saved=engine.checkpoint()
                        restored=ReplayEngine(session,program,(context,),'primary',Sink(),profile=profile);restored.restore(saved)
                        self.assertEqual(restored.status(),engine.status())
                self.assertEqual(engine.evaluation.count,2)
                payload={'e':'kline','s':KEY.symbol,'E':end+1,'k':{'s':KEY.symbol,'t':start,'T':end-1,
                    'i':'1M','x':True,'o':'10','h':'12','l':'9','c':'11','v':'2'}}
                self.assertEqual(normalize(payload,KEY,'M',end+2000).close_time,end-1)
                self.assertTrue(aligned(start,'M'))


if __name__=='__main__':unittest.main()
