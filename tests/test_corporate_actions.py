import unittest
from copy import deepcopy
from dataclasses import replace

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.contexts import MarketContext
from packages.contracts.corporate import ActionIndex, normalize
from packages.contracts.wire import canonical, loads, read_json
from packages.execution.artifacts import export_program, import_program
from packages.execution.driver import HistoricalDriver, MemorySource
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.marketdata import read_bars
from tools.conformance import run_cases
from tools.specification import ROOT
from test_realtime import Sink, tape


class CorporateActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = ROOT/'conformance/corporate-actions'
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.actions = read_json(folder/'events.json')
        cls.bars = read_bars(folder/'bars.json')
        cls.script = (folder/'v6.pine').read_text()
        cls.program = import_program(export_program(cls.session.compile(cls.script)))
        cls.primary = MarketContext(cls.bars.context_id,cls.bars.symbol,'1','offline')

    def test_all_versions_and_manual_oracle(self):
        report = run_cases(self.session,cases_path='conformance/corporate-actions/cases.json')
        self.assertGreaterEqual(report['executed_programs'],3)
        self.assertGreaterEqual(report['expected_rejections'],3)

    def test_dataset_identity_and_conflicts(self):
        duplicate = deepcopy(self.actions)
        events = duplicate['streams'][0]['events']
        events.reverse()
        events.append(events[0])
        self.assertEqual(normalize(self.actions),normalize(duplicate))
        events[-1] = {**events[-1],'gross':5}
        with self.assertRaisesRegex(OpenPineError,'Conflicting'):
            normalize(duplicate)
        revised = deepcopy(self.actions)
        revised['streams'][0]['events'][0]['gross'] += 1
        self.assertNotEqual(ActionIndex(revised).sha256,ActionIndex(self.actions).sha256)
        revised['streams'][1]['events'][0]['denominator'] = 0
        with self.assertRaisesRegex(OpenPineError,'ratio'):
            normalize(revised)

    def test_resume_across_visibility_and_revised_snapshot(self):
        runtime = self.session.validate_program(self.program)
        def driver(actions):
            return HistoricalDriver(runtime.prepare(self.program),MemorySource(self.bars),corporate_actions=actions)
        baseline = driver(self.actions)
        baseline.run()
        for cut in (2,3,6,7):
            checkpoints = []
            driver(self.actions).run(pause_after=cut,checkpoint=lambda d: checkpoints.append(loads(canonical(d.checkpoint()))))
            restored = driver(self.actions)
            restored.restore(checkpoints[0])
            restored.run()
            self.assertEqual(restored.checkpoint(),baseline.checkpoint())
            other = {**self.actions,'source':'revised'}
            with self.assertRaisesRegex(ValueError,'snapshot differs'):
                driver(other).restore(checkpoints[0])

    def test_replay_preview_duplicate_delivery_and_restore(self):
        def replay():
            return ReplayEngine(self.session,self.program,(self.primary,),self.primary.context_id,Sink(),corporate_actions=self.actions)
        engine = replay()
        events = list(tape(count=8,context=self.primary))
        for event in events[:7]:
            engine.apply(event)
        self.assertIsNone(engine.preview['outputs'][0]['value'])
        engine.apply(events[7])
        self.assertEqual(engine.preview['outputs'][0]['value'],2.)
        saved = loads(canonical(engine.checkpoint()))
        other = replay()
        other.restore(saved)
        for event in events[8:]:
            engine.apply(event)
            other.apply(event)
            other.apply(event)
        self.assertEqual(canonical(other.checkpoint()),canonical(engine.checkpoint()))

    def test_coverage_currency_and_unknown_symbol_are_distinct(self):
        def execute(call, actions):
            program = self.session.compile('//@version=6\nindicator("Cases")\nplot('+call+')')
            return self.session.execute(program,data=self.bars,corporate_actions=actions).outputs[0].values
        call = 'request.dividends("TEST:B")'
        for action, value, code in ((call,None,'ACTION_SNAPSHOT'),
                (call.replace('TEST:B','TEST:UNKNOWN'),self.actions,'ACTION_SYMBOL'),
                (call[:-1]+',currency=currency.EUR)',self.actions,'ACTION_CURRENCY'),
                (call,{**self.actions,'streams':self.actions['streams'][1:]},'ACTION_COVERAGE')):
            with self.assertRaises(OpenPineError) as caught:
                execute(action,value)
            self.assertEqual(caught.exception.diagnostic.code,code)
        empty = deepcopy(self.actions)
        empty['streams'][0]['events'].clear()
        self.assertEqual(execute(call,empty),(None,)*8)
        self.assertEqual(execute('request.dividends("TEST:UNKNOWN",ignore_invalid_symbol=true)',self.actions),(None,)*8)
        self.assertEqual(execute('request.dividends(syminfo.tickerid,currency=currency.USD)',self.actions)[2],2.)

    def test_open_alignment_and_gaps_across_missing_primary_candles(self):
        text = '//@version=6\nindicator("Cutoff")\nplot(request.dividends("TEST:B",gaps=barmerge.gaps_on,lookahead=barmerge.lookahead_on))'
        program = self.session.compile(text)
        source = replace(self.bars,bars=tuple(b for i,b in enumerate(self.bars.bars) if i not in (2,3)))
        result = self.session.execute(program,data=source,corporate_actions=self.actions)
        self.assertEqual(result.outputs[0].values,(None,None,2.,None,3.,None))
