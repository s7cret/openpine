"""Cross-version historical HA requests and checkpoint continuity."""
import unittest
from copy import deepcopy
from dataclasses import replace

from apps.openpine.launches import requirements_for
from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.contexts import MarketContext
from packages.contracts.prepared_requests import graph
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.driver import HistoricalDriver, MemorySource
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.marketdata import data_identity, read_bars
from tools import heikin_ashi_fixtures as fixtures
from tools.conformance import context_inputs, run_cases
from tools.specification import ROOT


class HeikinAshiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data = read_bars(ROOT / fixtures.DIRECTORY / 'bars.json')
        cls.primary = MarketContext(cls.data.context_id, cls.data.symbol, cls.data.timeframe, 'uploaded-file.1')
        cls.program = import_program(export_program(cls.session.compile(fixtures.source(6))))

    def driver(self, program, bindings=()):
        source = MemorySource(self.data)
        runtime = self.session.validate_program(program)
        contexts = self.session.context_scheduler(program, runtime, source, self.primary, bindings)
        return HistoricalDriver(runtime.prepare(program), source, contexts)

    def test_manual_corpus_all_versions(self):
        report = run_cases(self.session, cases_path=fixtures.CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (10, 1))

    def test_immutable_aliases_and_frozen_input_are_resolved_before_data_acquisition(self):
        with_alias = fixtures.source(6).replace('ha = ticker.heikinashi(syminfo.tickerid)',
            'symbol = input.symbol("")\ntarget = ticker.heikinashi(symbol)\nha = target')
        program = self.session.compile(with_alias)
        pending = requirements_for(program, 'a' * 64)
        self.assertEqual(pending['required_primary_fields'], ['symbol', 'timeframe'])
        self.assertEqual(sum(row.get('candle_type') == 'heikin_ashi' for row in pending['target_templates']), 5)
        ready = requirements_for(program, 'a' * 64, primary_context=self.primary)
        self.assertEqual(len(ready['contexts']), 1)
        self.assertEqual(ready['contexts'][0]['symbol'], 'TEST:HA')
        bound = requirements_for(program, 'a' * 64, {'symbol': 'TEST:OTHER'}, primary_context=self.primary)
        self.assertEqual([(row['symbol'], len(row['requests'])) for row in bound['contexts']],
                         [('TEST:HA', 2), ('TEST:OTHER', 4)])
        row = graph(program, primary=self.primary)['requests'][1]
        self.assertEqual(row['dependencies']['symbol'], ['input:symbol', 'primary:symbol'])
        self.assertEqual(row['raw_targets']['symbol'], '')

    def test_sparse_higher_checkpoint_recovers_transform_state_and_ordinary_cursors(self):
        inputs = context_inputs(ROOT / fixtures.DIRECTORY / 'contexts.json')
        program = import_program(export_program(self.session.compile(fixtures.source(6, higher=True))))
        source = inputs['context_sources']
        uninterrupted = self.driver(program, source)
        uninterrupted.run()
        expected = uninterrupted.finish(data_identity(self.data))
        self.assertEqual(expected.outputs[2].values, (None, 14., 14., 14., 14., 32.))
        for cut in (1, 2, 4, 5):
            saved = []
            self.driver(program, source).run(pause_after=cut, checkpoint=lambda d: saved.append(loads(canonical(d.checkpoint()))))
            groups = saved[0]['groups']
            prefix = 'market-two-minute:off'
            self.assertEqual(set(groups), {prefix, prefix + ':heikin_ashi'})
            self.assertNotIn('heikin_ashi', groups[prefix])
            transformed = groups[prefix + ':heikin_ashi']
            self.assertEqual(transformed['heikin_ashi'], None if cut == 1 else [14., 14.])
            resumed = self.driver(program, source)
            resumed.restore(saved[0])
            resumed.run()
            self.assertEqual(resumed.finish(data_identity(self.data)), expected)
            malformed = deepcopy(saved[0])
            malformed['groups'][prefix + ':heikin_ashi']['heikin_ashi'] = [True, 14.]
            with self.assertRaisesRegex(ValueError, 'Heikin Ashi checkpoint'):
                self.driver(program, source).restore(malformed)

    def test_explicit_lookahead_keeps_its_own_cursor(self):
        body = fixtures.source(6, higher=True).replace('ha, "2", open)',
                                                       'ha, "2", open, lookahead=barmerge.lookahead_on)')
        program = self.session.compile(body)
        result = self.session.execute(program, data=self.data, **context_inputs(ROOT / fixtures.DIRECTORY / 'contexts.json'))
        self.assertEqual(result.outputs[1].values, (14.,) * 6)
        self.assertEqual(result.outputs[2].values, (None, 14., 14., 14., 14., 32.))

    def test_excluded_target_forms_fail_as_early_as_compilation(self):
        cases = (
            ('ha = ticker.heikinashi(syminfo.tickerid)\nha := "TEST:OTHER"\nplot(request.security(ha, "1", open))', 'ARGUMENT_QUALIFIER'),
            ('plot(request.security_lower_tf(ticker.heikinashi(syminfo.tickerid), "1", close))', 'HEIKIN_ASHI_LOWER_TF'),
            ('plot(request.security(ticker.heikinashi(str.tostring(close)), "1", open))', 'ARGUMENT_QUALIFIER'),
        )
        for body, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(OpenPineError) as caught:
                    self.session.compile('//@version=6\nindicator("HA boundary")\n' + body)
                self.assertEqual(caught.exception.diagnostic.code, code)

    def test_missing_ohlc_and_replay_have_explicit_diagnostics(self):
        missing = replace(self.data, bars=(replace(self.data.bars[0], high=None), *self.data.bars[1:]))
        with self.assertRaises(OpenPineError) as caught:
            self.session.execute(self.program, data=missing)
        self.assertEqual(caught.exception.diagnostic.code, 'HEIKIN_ASHI_OHLC')
        with self.assertRaises(OpenPineError) as caught:
            ReplayEngine(self.session, self.program, (self.primary,), self.primary.context_id, None)
        self.assertEqual(caught.exception.diagnostic.code, 'HEIKIN_ASHI_REPLAY')

    def test_saved_strategy_fills_at_the_raw_next_open(self):
        program = import_program(export_program(self.session.compile(fixtures.STRATEGY_BODY)))
        result = self.session.execute(program, data=self.data)
        self.assertEqual(result.outputs[0].values, fixtures.VALUES[4])
        fills = [row['value'] for row in result.simulation['events'] if row['kind'] == 'fill']
        self.assertEqual([(fill['bar_index'], fill['price']) for fill in fills], [(1, '16')])


if __name__ == '__main__':
    unittest.main()
