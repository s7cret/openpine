import unittest
from copy import deepcopy

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.contexts import MarketContext
from packages.contracts.corporate import ActionIndex, normalize
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical, loads, read_json
from packages.execution.artifacts import export_program, import_program
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.marketdata import read_bars
from test_realtime import Sink
from tools.conformance import run_cases
from tools.specification import ROOT


class EconomicDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = ROOT/'conformance/economic-data'
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data = read_json(cls.folder/'revised.json')
        cls.bars = read_bars(cls.folder/'bars.json')
        cls.program = cls.session.compile((cls.folder/'v6.pine').read_text())

    def values(self, dataset):
        return self.session.execute(self.program,data=self.bars,corporate_actions=dataset).outputs[1].values

    def test_corpus(self):
        corpus = 'conformance/economic-data/cases.json'
        self.assertEqual(len(run_cases(self.session,cases_path=corpus)['results']),len(read_json(ROOT/corpus)['cases']))

    def test_immutable_identity_and_conflicts(self):
        data = deepcopy(self.data)
        rows = data['streams'][1]['events']
        rows.reverse()
        rows.append(dict(rows[0]))
        data['streams'].reverse()
        self.assertEqual(ActionIndex(data).sha256, ActionIndex(self.data).sha256)
        rows[-1]['value'] = 9.
        with self.assertRaisesRegex(OpenPineError, 'Conflicting revisions'):
            normalize(data)
        self.assertNotEqual(ActionIndex(read_json(self.folder/'events.json')).sha256, ActionIndex(self.data).sha256)

    def test_missing_empty_null_and_zero(self):
        data = deepcopy(self.data)
        stream = data['streams'][1]
        for val in (None,0.):
            stream['events'][-1]['value'] = val
            self.assertEqual(self.values(data), (None,)+(-.4,)*5+(val,)*2)
        stream['events'].clear()
        self.assertEqual(self.values(data), (None,)*8)
        data['streams'].remove(stream)
        with self.assertRaisesRegex(OpenPineError, 'Missing economic series'):
            self.values(data)

    def test_older_period_revision_does_not_replace_newer_period(self):
        data = deepcopy(self.data)
        rows = data['streams'][1]['events']
        rows.append(dict(rows[0],period_end_ms=rows[0]['period_end_ms']+86400000,
                         available_ms=self.bars.bars[4].time,value=3.))
        self.assertEqual(self.values(data), (None,)+(-.4,)*3+(3.,)*4)

    def test_input_unit_bounds_and_dynamic_codes(self):
        for key,val in (('country_code',[]),('field','CPI'),('unit','USD')):
            data = deepcopy(self.data)
            data['streams'][1][key] = val
            with self.assertRaises(OpenPineError):
                normalize(data)
        with self.assertRaises(OpenPineError):
            self.session.compile('//@version=6'+chr(10)+'indicator("bad")'+chr(10)+'plot(request.economic(syminfo.ticker,"GDP"))')

    def test_intrabar_causality_checkpoint_and_orders(self):
        ctx = MarketContext('primary','TEST:B','D','offline')
        tape = []
        for b in self.bars.bars:
            for revision,(kind,offset) in enumerate((('open',0),('update',43200000),('close',86400000))):
                tape.append(MarketEvent(len(tape),'primary',kind,b.time,b.time+offset,revision,
                                        b.open,b.high,b.low,b.close,b.volume))
        for filename in ('events','revised'):
            dataset = read_json(self.folder/(filename+'.json'))
            revised = filename == 'revised'
            for sample in ('indicator','strategy'):
                with self.subTest(snapshot=filename, sample=sample):
                    path = ROOT/'examples/economic-data'/(sample+'.pine')
                    program = import_program(export_program(self.session.compile(path.read_text())))
                    def engine():
                        sink = Sink()
                        return ReplayEngine(self.session,program,(ctx,),'primary',sink,corporate_actions=dataset),sink
                    reference,outputs = engine()
                    for i,event in enumerate(tape):
                        reference.apply(event)
                        if sample == 'indicator' and i in (3,4,18,19):
                            self.assertEqual(reference.preview['outputs'][1]['value'],
                                             {3:None,4:-.4,18:-.4,19:.2 if revised else -.4}[i])
                    if sample == 'strategy':
                        self.assertEqual([e['bar_index'] for e in outputs.events if e['kind']=='order'],
                                         [6] if revised else [])
                        self.assertEqual([(e['bar_index'],e['value']['price']) for e in outputs.events
                                          if e['kind']=='fill'], [(7,'107')] if revised else [])
                    for cut in (3,4,5,18,19,20,21):
                        before,prior = engine()
                        for event in tape[:cut]:
                            before.apply(event)
                        after,suffix = engine()
                        after.restore(loads(canonical(before.checkpoint())))
                        for event in tape[cut-1:]:
                            after.apply(event)
                            after.apply(event)
                        self.assertEqual(prior.rows+suffix.rows,outputs.rows)
                        self.assertEqual(prior.events+suffix.events,outputs.events)
                        self.assertEqual(after.checkpoint(),reference.checkpoint())
