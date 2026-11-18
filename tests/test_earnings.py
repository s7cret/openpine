import unittest
from copy import deepcopy
from dataclasses import replace

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.contexts import MarketContext
from packages.execution.realtime import ReplayEngine
from test_realtime import Sink, tape
from packages.contracts.corporate import ActionIndex, normalize
from packages.contracts.wire import read_json
from packages.execution.artifacts import export_program, import_program
from packages.execution.session import Session
from packages.marketdata import read_bars
from tools.conformance import run_cases
from tools.specification import ROOT


class EarningsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = ROOT / 'conformance/earnings'
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.dataset = read_json(cls.folder / 'events.json')
        cls.bars = read_bars(cls.folder / 'bars.json')
        cls.program = import_program(export_program(cls.session.compile((cls.folder / 'v6.pine').read_text())))

    def values(self, data, bars=None):
        result = self.session.execute(self.program, data=bars or self.bars, corporate_actions=data)
        return [output.values for output in result.outputs]

    def test_manual_corpus(self):
        report = run_cases(self.session, cases_path='conformance/earnings/cases.json')
        self.assertEqual(len(report['results']), len(read_json(self.folder / 'cases.json')['cases']))

    def test_order_duplicates_and_conflicting_publications(self):
        data = deepcopy(self.dataset)
        events = data['streams'][-1]['events']
        events.reverse()
        events.append(events[0].copy())
        self.assertEqual(ActionIndex(data).sha256, ActionIndex(self.dataset).sha256)
        events[-1]['value'] = 9
        with self.assertRaisesRegex(OpenPineError, 'Conflicting EPS'):
            normalize(data)

    def test_simultaneous_periods_and_empty_family(self):
        data = deepcopy(self.dataset)
        events = data['streams'][-1]['events']
        events.append({**events[1], 'period_end_ms': 700000, 'value': 2.5})
        self.assertEqual(self.values(data)[0][3], 2.5)
        events.clear()
        self.assertEqual(self.values(data), [(None,) * 8] * 3)
        data['streams'].pop()
        with self.assertRaisesRegex(OpenPineError, 'Missing corporate-action family'):
            self.values(data)

    def test_intrabar_preview_restore_and_duplicate(self):
        context = MarketContext('primary', 'TEST:B', '1', 'offline')
        def engine():
            return ReplayEngine(self.session, self.program, (context,), 'primary', Sink(), corporate_actions=self.dataset)
        events = list(tape(count=8, context=context))
        uninterrupted = engine()
        for event in events[:13]:
            uninterrupted.apply(event)
        self.assertEqual([row['value'] for row in uninterrupted.preview['outputs']][:2], [None, -1.])
        restored = engine()
        restored.restore(uninterrupted.checkpoint())
        for event in events[13:]:
            uninterrupted.apply(event)
            restored.apply(event)
            restored.apply(event)
            if event == events[13]:
                self.assertEqual(restored.preview['outputs'][0]['value'], 0.)
        self.assertEqual(restored.checkpoint(), uninterrupted.checkpoint())

    def test_revision_next_session(self):
        data = deepcopy(self.dataset)
        stream = data['streams'][-1]
        day = 86400000
        stream['end_ms'] += day
        stream['events'][3]['available_ms'] += day - 30000
        bars = replace(self.bars, bars=tuple(
            replace(bar, time=bar.time + day) if i >= 6 else bar
            for i, bar in enumerate(self.bars.bars)))
        self.assertEqual(self.values(data, bars)[0], (None, None, None, 0., 0., 0., -0.25, -0.25))
        revised = read_json(self.folder / 'revised.json')
        self.assertNotEqual(ActionIndex(revised).sha256, ActionIndex(self.dataset).sha256)
        self.assertEqual(self.values(revised)[0][3:6], (0.5,) * 3)
