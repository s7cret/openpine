"""Independent sampled-price expectations and every-event restoration, v1-v6."""
import copy,unittest
from dataclasses import replace
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.contracts.contexts import MarketContext
from packages.contracts.trading import BrokerSettings
from packages.contracts.realtime import MarketEvent
from packages.contracts.live import RECEIVED_PROFILE
from packages.contracts.observed_broker import PROFILE
from packages.contracts.wire import canonical,loads
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session

CONTEXT=MarketContext('primary','TEST:OBSERVED','1','sampled.1')

class Sink:
    def __init__(self):self.rows=[];self.events=[]
    def emit(self,site,title,typ,value,index,time):self.rows.append((index,time,site,value))
    def enable_simulation(self,**kwargs):pass
    def simulation_emit(self,events):self.events.extend(copy.deepcopy(events))

def source(version,commands,*,qty=1,fee=0,extra=''):
    direction='true' if version==1 else 'strategy.long'
    return f'//@version={version}\nstrategy("Observed",default_qty_type=strategy.fixed,default_qty_value={qty},initial_capital=1000,commission_value={fee})\n{extra}if bar_index==0\n'+''.join('    '+line.replace('LONG',direction)+'\n' for line in commands)+ 'plot(strategy.equity)\nplot(strategy.position_size)'

def tape(prices=(100.,94.,112.,101.),*,received=False):
    # An opaque confirmed warmup close, then one growing cumulative candle.
    events=[MarketEvent(0,'primary','close',0,60000,0,100.,100.,100.,100.,1.)]
    for i,p in enumerate(prices):
        kind='open' if i==0 else 'close' if i==len(prices)-1 else 'update'
        at=120000 if kind=='close' else 60000+i*10000
        events.append(MarketEvent(i+1,'primary',kind,60000,at,i,100.,120.,80.,p,float(i+10)))
    return [replace(e,event_time=900000+i) for i,e in enumerate(events)] if received else events

class ObservedBrokerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
    def engine(self,text,*,profile=None,contexts=(CONTEXT,),**kwargs):
        from packages.contracts.realtime import PROFILE as LOGICAL
        out=Sink();program=self.session.compile(text,'observed.pine')
        return ReplayEngine(self.session,program,contexts,'primary',out,broker_profile=PROFILE,
            broker_settings=BrokerSettings(tick_size='1'),profile=profile or LOGICAL,**kwargs),out
    def run_case(self,text,events,**kwargs):
        engine,out=self.engine(text,profile=RECEIVED_PROFILE if events[0].event_time>60000 else None,**kwargs)
        for event in events:engine.apply(event)
        return engine,out
    def assert_resume(self,text,events,**kwargs):
        full,out=self.run_case(text,events,**kwargs)
        for cut in range(1,len(events)+1):
            first,prefix=self.run_case(text,events[:cut],**kwargs);checkpoint=loads(canonical(first.checkpoint()))
            resumed,last=self.engine(text,profile=first.profile,**kwargs);resumed.restore(checkpoint)
            self.assertEqual(resumed.status(),first.status())
            for event in events[cut:]:resumed.apply(event)
            self.assertEqual(prefix.events+last.events,out.events)
            self.assertEqual(prefix.rows+last.rows,out.rows)
            self.assertEqual(resumed.status(),full.status())
        return full,out
    def test_limit_protection_fees_and_unknown_extrema_every_version_and_clock(self):
        for v in range(1,7):
            text=source(v,['strategy.entry("L",LONG,limit=95)','strategy.exit("X","L",stop=90,limit=110)'],qty=2,fee=1)
            for received in (False,True):
                events=tape(received=received);engine,out=self.assert_resume(text,events)
                fills=[e['value'] for e in out.events if e['kind']=='fill']
                self.assertEqual([f['price'] for f in fills],['94','112'])
                self.assertEqual([f['commission'] for f in fills],['1.88','2.24'])
                self.assertEqual(engine.simulation.broker.statistics()['equity'],'1031.88')
                self.assertEqual([f['observation']['sequence'] for f in fills],[2,3])
                self.assertEqual(engine.evaluation.count,2)
    def test_intrabar_fill_is_published_before_close_and_duplicate_does_not_repeat(self):
        text=source(6,['strategy.entry("L",LONG,limit=95)']);events=tape()
        engine,out=self.run_case(text,events[:3]);self.assertEqual(engine.evaluation.count,1)
        self.assertEqual(engine.simulation.broker.statistics()['position_size'],'1')
        before=canonical(out.events);state=canonical(engine.checkpoint());engine.apply(events[2])
        self.assertEqual(canonical(out.events),before);self.assertEqual(canonical(engine.checkpoint()),state)
    def test_stop_limit_activation_then_later_sample(self):
        for v in range(1,7):
            text=source(v,['strategy.entry("L",LONG,stop=105,limit=104)'])
            engine,out=self.assert_resume(text,tape((100.,106.,103.,101.)))
            self.assertEqual([e['value']['price'] for e in out.events if e['kind']=='fill'],['103'])
            self.assertEqual([e['value']['observation']['sequence'] for e in out.events if e['kind']=='entry_activated'],[2])
    def test_partial_exits_can_execute_on_different_updates_same_candle(self):
        for v in range(1,7):
            text=source(v,['strategy.entry("L",LONG)','strategy.exit("A","L",limit=110,qty=1)','strategy.exit("B","L",stop=90,qty=1)'],qty=2)
            engine,out=self.assert_resume(text,tape((100.,112.,88.,100.)))
            self.assertEqual([e['value']['quantity'] for e in out.events if e['kind']=='trade'],['1','1'])
            self.assertEqual(engine.simulation.broker.statistics()['equity'],'1000')
    def test_legacy_profile_still_rejects_protection_and_unknown_profile_rejects(self):
        text=source(6,['strategy.entry("L",LONG)','strategy.exit("X","L",stop=90)'])
        program=self.session.compile(text,'legacy.pine')
        with self.assertRaises(OpenPineError):ReplayEngine(self.session,program,(CONTEXT,),'primary',Sink())
        with self.assertRaises(ValueError):ReplayEngine(self.session,program,(CONTEXT,),'primary',Sink(),broker_profile='future')

    def test_original_six_families_match_independent_oracles_all_versions(self):
        from tools.observed_broker_fixtures import FAMILIES,EXPECTED,source as example,events
        for v in range(1,7):
            for family in FAMILIES:
                with self.subTest(version=v,family=family):
                    engine,out=self.assert_resume(example(v,family),events(family));expected=EXPECTED[family]
                    fills=[e['value'] for e in out.events if e['kind']=='fill']
                    self.assertEqual([f['price'] for f in fills],expected['prices'])
                    self.assertEqual([f['quantity'] for f in fills],expected['quantities'])
                    self.assertEqual([f['commission'] for f in fills],expected['fees'])
                    for key in ('equity','position_size'):self.assertEqual(engine.simulation.broker.statistics()[key],expected[key])

    def test_changed_observation_cursor_and_broker_state_are_rejected(self):
        text=source(6,['strategy.entry("L",LONG,limit=95)']);first,_=self.run_case(text,tape()[:3]);original=first.checkpoint()
        for mutate in (lambda s:s['broker_boundary'].update(observations=0),lambda s:s['broker_boundary']['last_observation'].update(revision=50),
                lambda s:s['broker_boundary']['simulation']['broker'].update(cash='999')):
            state=copy.deepcopy(original);mutate(state);fresh,_=self.engine(text)
            with self.assertRaises(ValueError):fresh.restore(state)

    def test_short_limit_and_protection_use_the_observed_price_all_versions(self):
        for v in range(1,7):
            direction='false' if v==1 else 'strategy.short'
            text=source(v,[f'strategy.entry("S",{direction},limit=105)','strategy.exit("X","S",stop=110,limit=90)'])
            engine,out=self.assert_resume(text,tape((100.,106.,88.,101.)))
            self.assertEqual([e['value']['price'] for e in out.events if e['kind']=='fill'],['106','88'])
            self.assertEqual(engine.simulation.broker.statistics()['equity'],'1018')

    def test_close_time_cancel_or_replacement_does_not_reuse_old_pending_order(self):
        for v in range(1,7):
            direction='true' if v==1 else 'strategy.long'
            for cancel in (False,True):
                command='strategy.cancel("L")' if cancel else f'strategy.entry("L",{direction},limit=92)'
                text=source(v,['strategy.entry("L",LONG,limit=95)'])+f'\nif bar_index==1\n    {command}\n'
                events=tape((100.,100.,100.,100.))
                for revision,(kind,at,p) in enumerate((('open',120000,93.),('update',130000,91.),('close',180000,91.))):
                    events.append(MarketEvent(len(events),'primary',kind,120000,at,revision,93.,93.,91.,p,float(revision)))
                engine,out=self.assert_resume(text,events)
                self.assertEqual([e['value']['price'] for e in out.events if e['kind']=='fill'],[] if cancel else ['91'])

    def test_secondary_context_never_becomes_an_execution_price_all_versions(self):
        higher=MarketContext('higher',CONTEXT.symbol,'2','sampled.1')
        for v in range(1,7):
            api='security' if v<5 else 'request.security';ema='ema' if v<5 else 'ta.ema'
            text=source(v,['strategy.entry("L",LONG,limit=95)','strategy.exit("X","L",limit=110)'])+f'\nplot({api}("TEST:OBSERVED","2",{ema}(close,2)))'
            primary=tape(received=True);secondary=MarketEvent(0,'higher','close',0,900000,0,200.,200.,200.,200.,1.)
            events=[secondary,*[replace(e,sequence=e.sequence+1,event_time=e.event_time+1) for e in primary]]
            engine,out=self.assert_resume(text,events,contexts=(CONTEXT,higher))
            self.assertEqual(engine.simulation.observations,len(primary))
            self.assertEqual([e['value']['price'] for e in out.events if e['kind']=='fill'],['94','112'])

if __name__=='__main__':unittest.main()
