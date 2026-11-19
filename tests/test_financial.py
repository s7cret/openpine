import unittest
from copy import deepcopy
from dataclasses import replace

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.contexts import MarketContext
from packages.contracts.corporate import ActionIndex, normalize
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import read_json
from packages.execution.artifacts import export_program, import_program
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.marketdata import read_bars
from test_realtime import Sink
from tools.conformance import run_cases
from tools.specification import ROOT


class FinancialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = ROOT/'conformance/financial'
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data = read_json(cls.folder/'events.json')
        cls.bars = read_bars(cls.folder/'bars.json')
        cls.program = import_program(export_program(cls.session.compile((cls.folder/'v6-mixed.pine').read_text())))
        cls.rates = read_json(cls.folder/'rates.json')

    def historical(self, data, bars=None):
        return self.session.execute(self.program, data=bars or self.bars, corporate_actions=data, fx_rates=self.rates)

    def test_corpus(self):
        report = run_cases(self.session, cases_path='conformance/financial/cases.json')
        self.assertEqual(len(report['results']),len(read_json(self.folder/'cases.json')['cases']))

    def test_revisions_null_zero_and_empty(self):
        data = read_json(self.folder/'revised.json')
        self.assertNotEqual(ActionIndex(data).sha256, ActionIndex(self.data).sha256)
        self.assertEqual(self.historical(data).outputs[0].values, (None,)*2+(60.,)*28)
        rows = data['streams'][0]['events']
        for raw, expected in ((None,None),(0,0.)):
            rows[-1]['value'] = raw
            self.assertEqual(self.historical(data).outputs[0].values[2:], (expected,)*28)
        rows.clear()
        self.assertEqual(self.historical(data).outputs[0].values, (None,)*30)
        data['streams'].pop(0)
        with self.assertRaisesRegex(OpenPineError, 'Missing corporate-action'):
            self.historical(data)

    def test_duplicate_conflict_and_units(self):
        data = deepcopy(self.data)
        rows = data['streams'][0]['events']
        rows.append(dict(rows[0]))
        data['streams'].reverse()
        self.assertEqual(ActionIndex(data).sha256, ActionIndex(self.data).sha256)
        rows[-1]['value'] = 1.
        with self.assertRaisesRegex(OpenPineError, 'Conflicting financial'):
            normalize(data)
        rows.pop()
        data['streams'][-1]['unit'] = 'shares'
        with self.assertRaises(OpenPineError):
            normalize(data)

    def test_late_older_revision_preserves_latest_period(self):
        data = deepcopy(self.data)
        rows = data['streams'][0]['events']
        base = rows[0]
        rows.append(dict(base,period_start_ms=base['period_start_ms']-1000,
                         period_end_ms=base['period_start_ms']-1, next_start_ms=base['period_start_ms'],
                         available_ms=base['available_ms']+1,value=999.))
        idx = ActionIndex(data)
        _, _, releases = idx.replay_reports['TEST:B','financial','TOTAL_REVENUE','FQ']
        self.assertEqual([r['value'] for r in releases], [100.])

    def test_weekend_gap(self):
        program = self.session.compile((self.folder/'v6-gaps.pine').read_text())
        bars = replace(self.bars,bars=self.bars.bars[:2]+self.bars.bars[4:])
        out = self.session.execute(program,data=bars,corporate_actions=self.data).outputs[0]
        self.assertEqual(out.values, (None,None,100.)+(None,)*25)

    def test_intrabar_release_revision_and_restore(self):
        context = MarketContext('primary','TEST:B','D','offline')
        def engine():
            return ReplayEngine(self.session,self.program,(context,),'primary',Sink(),
                                corporate_actions=read_json(self.folder/'revised.json'),fx_rates=self.rates)
        events = []
        for b in self.bars.bars:
            for revision,(kind,offset) in enumerate((('open',0),('update',43200000),('close',86400000))):
                events.append(MarketEvent(len(events),'primary',kind,b.time,b.time+offset,revision,
                                          b.open,b.high,b.low,b.close,b.volume))
        run = engine()
        restored = None
        for i,event in enumerate(events):
            run.apply(event)
            if restored is not None:
                restored.apply(event)
                restored.apply(event)
                self.assertEqual(restored.checkpoint(), run.checkpoint())
            if i in (26*3,26*3+1,28*3):
                self.assertEqual(run.preview['outputs'][0]['value'],{78:None,79:80.,84:96.}[i])
                restored = engine()
                restored.restore(run.checkpoint())

    def test_fiscal_replay_gaps_and_revised_strategy_ledger(self):
        folder = self.folder
        bars = read_bars(folder/'fiscal-bars.json')
        rates = read_json(folder/'fiscal-rates.json')
        context = MarketContext('primary','TEST:B','D','offline')
        instrument = read_json(ROOT/'examples/environment/input.json')['environment']['instrument']
        environment = {'primary':dict(instrument={**instrument,'currency':'EUR'}, history_last_ms=None)}
        events = [MarketEvent(i,'primary','close',b.time,b.time+86400000,0,b.open,b.high,b.low,b.close,b.volume)
                  for i,b in enumerate(bars.bars)]
        for revised in (False, True):
            dataset = read_json(folder/('fiscal-revised.json' if revised else 'fiscal-events.json'))
            for name, expected in (
                ('carry', [None]*4 + [80.] + [96. if revised else 80.]*3),
                ('pulse', [None]*4 + [80.,96. if revised else None,None,None]),
                ('strategy', [None]*4 + [80.,96. if revised else None,None,None]),
            ):
                with self.subTest(revised=revised, name=name):
                    path = (ROOT/'examples/financial/fiscal-strategy.pine' if name == 'strategy'
                            else folder/f'fiscal-{name}.pine')
                    program = import_program(export_program(self.session.compile(path.read_text())))
                    def engine():
                        sink = Sink()
                        run = ReplayEngine(self.session,program,(context,),'primary',sink,environments=environment,
                                           corporate_actions=dataset,fx_rates=rates)
                        return run,sink
                    reference, full = engine()
                    for event in events:
                        reference.apply(event)
                    self.assertEqual([row[-1] for row in full.rows], expected)
                    if name == 'strategy':
                        self.assertEqual([r['bar_index'] for r in full.events if r['kind']=='order'], [4])
                        self.assertEqual([(r['bar_index'], r['value']['price']) for r in full.events
                                          if r['kind']=='fill'], [(5,'105')])
                    for cut in range(1,len(events)):
                        before, prefix = engine()
                        for event in events[:cut]:
                            before.apply(event)
                        after, suffix = engine()
                        after.restore(before.checkpoint())
                        for event in events[cut-1:]:
                            after.apply(event)
                            after.apply(event)
                        self.assertEqual(prefix.rows+suffix.rows, full.rows)
                        self.assertEqual(prefix.events+suffix.events, full.events)
                        self.assertEqual(after.checkpoint(), reference.checkpoint())

    def test_margin_with_zero_revenue(self):
        dataset = read_json(self.folder/'fiscal-events.json')
        stream = next(s for s in dataset['streams'] if s.get('financial_id') == 'TOTAL_REVENUE')
        stream['events'][0]['value'] = 0.
        program = self.session.compile((ROOT/'examples/financial/fiscal-indicator.pine').read_text())
        out = self.session.execute(program,data=read_bars(self.folder/'fiscal-bars.json'),corporate_actions=dataset)
        self.assertEqual(out.outputs[0].values, (None,)+(0.,)*7)
        self.assertEqual(out.outputs[2].values, (None,)*8)

    def test_april_quarter_history_and_replay_under_both_gap_policies(self):
        for gaps in ('off','on'):
            src = (self.folder/'v6-gaps.pine').read_text().replace('barmerge.gaps_on', 'barmerge.gaps_'+gaps)
            program = self.session.compile(src)
            result = self.session.execute(program,data=self.bars,corporate_actions=self.data)
            self.assertEqual(result.outputs[0].values,
                             (None,None,100.)+((100.,)*27 if gaps=='off' else (None,)*27))
            context = MarketContext('primary','TEST:B','D','offline')
            sink = Sink()
            engine = ReplayEngine(self.session,program,(context,),'primary',sink,corporate_actions=self.data)
            for i,b in enumerate(self.bars.bars):
                for rev,(kind,delay) in enumerate((('open',0),('update',43200000),('close',86400000))):
                    engine.apply(MarketEvent(i*3+rev,'primary',kind,b.time,b.time+delay,rev,
                                             b.open,b.high,b.low,b.close,b.volume))
                    if i==26 and kind!='close':
                        self.assertEqual(engine.preview['outputs'][0]['value'], None if rev==0 else 100.)
            self.assertEqual([r[-1] for r in sink.rows],
                             [None]*26+[100.]+([100.]*3 if gaps=='off' else [None]*3))
