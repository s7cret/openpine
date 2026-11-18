import unittest
from copy import deepcopy

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.contexts import MarketContext
from packages.contracts.corporate import ActionIndex, BoundActions
from packages.contracts.fx import DAY, Rates, normalize
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical, loads, read_json
from packages.execution.artifacts import export_program, import_program
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from tools.conformance import run_cases
from tools.specification import ROOT
from test_realtime import Sink


class CurrencyRatesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = ROOT/'conformance/currency-rates'
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.fx = read_json(cls.folder/'rates.json')
        cls.actions = read_json(cls.folder/'events.json')
        cls.context = MarketContext('primary','TEST:B','D','offline')

    def test_versions_and_manual_corpus(self):
        report = run_cases(self.session,cases_path='conformance/currency-rates/cases.json')
        self.assertEqual(len(report['results']),len(read_json(self.folder/'cases.json')['cases']))

    def test_normalization_and_revisions(self):
        data = deepcopy(self.fx)
        rows = data['pairs'][0]['rates']
        rows.reverse()
        rows.append(rows[0].copy())
        self.assertEqual(normalize(data),normalize(self.fx))
        rows[-1]['rate'] = .75
        with self.assertRaisesRegex(OpenPineError,'Conflicting'):
            normalize(data)
        rows.pop()
        rows.append(dict(day_ms=7*DAY, available_ms=12*DAY, rate=.25))
        self.assertEqual(Rates(data).bind('USD','EUR')(12*DAY),.8)
        rows.append(dict(day_ms=8*DAY, available_ms=12*DAY, rate=.75))
        self.assertEqual(Rates(data).bind('USD','EUR')(12*DAY),.75)
        for rate in (0.,-1.,True,float('inf'),float('nan'),5e-324):
            rows[-1]['rate'] = rate
            with self.assertRaises(OpenPineError):
                normalize(data)

    def test_direct_priority_and_unavailable_pair(self):
        data = deepcopy(self.fx)
        stream = data['pairs'][0]
        data['pairs'].append({**stream,'base':'EUR','quote':'USD','rates':[
            dict(day_ms=7*DAY,available_ms=8*DAY,rate=1.9)]})
        data['pairs'].append({**stream,'base':'GBP','quote':'CHF','rates':[]})
        rates = Rates(data)
        self.assertEqual(rates.bind('EUR','USD')(11*DAY),1.9)
        self.assertEqual(rates.bind('USD','EUR')(11*DAY),.5)
        self.assertIsNone(rates.bind('GBP','EUR',True)(11*DAY))
        with self.assertRaisesRegex(OpenPineError,'direct or inverse'):
            rates.bind('GBP','EUR')
        rows = data['pairs'][0]['rates']
        rows[0]['available_ms'] = 7*DAY+1
        rates = Rates(data)
        self.assertEqual(rates.bind('USD','EUR')(8*DAY),.5)
        self.assertEqual(rates.bind('USD','EUR')(11*DAY+4999),.5)
        self.assertEqual(rates.bind('USD','EUR')(11*DAY+5000),.8)

    def test_chart_currency_zero_loss_and_missing(self):
        index = ActionIndex(self.actions)
        req = dict(symbol='TEST:B',family='earnings',ignore_invalid_symbol=False,field='earnings.actual',
                   gaps='off',lookahead='off',call_site_id='s',currency='',default_chart_currency=True)
        from types import SimpleNamespace
        frame = SimpleNamespace(time=12*DAY,observation_time_ms=None)
        bound = BoundActions(index,'D',[req],'EUR',Rates(self.fx))
        self.assertEqual(bound.value('s',frame,None),-1.6)
        data = deepcopy(self.actions)
        data['streams'][0]['events'][-1]['value'] = 0
        bound = BoundActions(ActionIndex(data),'D',[req],'EUR',Rates(self.fx))
        self.assertEqual(bound.value('s',frame,None),0.)

    def test_intrabar_publication_and_duplicate_restore(self):
        source = '//@version=6\nindicator("FX preview")\nplot(request.currency_rate("USD","EUR",true))'
        program = self.session.compile(source)
        def engine():
            return ReplayEngine(self.session,program,(self.context,),'primary',Sink(),fx_rates=self.fx)
        def event(k, kind, offset):
            return MarketEvent(k,'primary',kind,11*DAY,11*DAY+offset,k,100.,100.,100.,100.,0.)
        first = engine()
        first.apply(event(0,'open',0))
        self.assertEqual(first.preview['outputs'][0]['value'],.5)
        before = event(1,'update',4999)
        first.apply(before)
        self.assertEqual(first.preview['outputs'][0]['value'],.5)
        restored = engine()
        restored.restore(loads(canonical(first.checkpoint())))
        for e in (before,event(2,'update',5000)):
            first.apply(e)
            restored.apply(e)
            restored.apply(e)
        self.assertEqual(restored.preview['outputs'][0]['value'],.8)
        self.assertEqual(restored.checkpoint(),first.checkpoint())

    def test_strategy_every_cut_and_pinned_snapshots(self):
        source = (ROOT/'examples/currency-rates/strategy.pine').read_text()
        program = import_program(export_program(self.session.compile(source)))
        bars = read_json(self.folder/'bars.json')['bars']
        events = [MarketEvent(i,'primary','close',b['time'],b['time']+DAY,0,b['open'],b['high'],
                              b['low'],b['close'],b['volume']) for i,b in enumerate(bars)]
        def engine(actions, rates):
            sink = Sink()
            return ReplayEngine(self.session,program,(self.context,),'primary',sink,
                                corporate_actions=actions,fx_rates=rates),sink
        full, result = engine(self.actions,self.fx)
        for event in events:
            full.apply(event)
        self.assertEqual([v['bar_index'] for v in result.events if v['kind']=='order'],[3])
        self.assertEqual([(v['bar_index'],v['value']['price']) for v in result.events if v['kind']=='fill'],[(4,'104')])
        for cut in range(1,len(events)):
            first, prefix = engine(self.actions,self.fx)
            for event in events[:cut]:
                first.apply(event)
            checkpoint = loads(canonical(first.checkpoint()))
            restored, tail = engine(self.actions,self.fx)
            restored.restore(checkpoint)
            for event in events[cut-1:]:
                restored.apply(event)
                restored.apply(event)
            self.assertEqual(prefix.events+tail.events,result.events)
            self.assertEqual(prefix.rows+tail.rows,result.rows)
            self.assertEqual(restored.checkpoint(),full.checkpoint())
        for actions,rates in (({**self.actions,'source':'corrected'},self.fx),
                              (self.actions,read_json(self.folder/'revised.json'))):
            changed,_ = engine(actions,rates)
            with self.assertRaises(ValueError):
                changed.restore(checkpoint)
