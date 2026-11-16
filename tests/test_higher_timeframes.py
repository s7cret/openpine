"""Higher-period boundaries, reference compatibility and worker input identity."""

import unittest
from dataclasses import replace

from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.contexts import MarketContext
from packages.contracts.environment import PERIOD_SLOT, PERIOD_V6_SLOT
from packages.contracts.inputs import input_schema, settings_for
from packages.contracts.prepared_requests import graph
from packages.contracts.strategy_ir import broker_policy
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.driver import HistoricalDriver, MemorySource
from packages.execution.session import Session
from packages.marketdata import data_identity, read_bars
from tools import higher_timeframes_fixtures as fixtures
from tools.conformance import run_cases
from tools.semantic_matrix import matrix
from tools.specification import ROOT


class HigherTimeframeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_versioned_corpus_and_matrix(self):
        report = run_cases(self.session, cases_path=fixtures.CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (18, 6))
        rows, _ = matrix()
        for version, entries in rows.items():
            selected = {name: status for name, _, status, detail in entries if 'HIGHER_TIMEFRAMES.md' in detail}
            self.assertEqual(selected, {'time': 'PARTIAL', 'time_close': 'PARTIAL'} if version >= 4 else {'time': 'PARTIAL'})

    def test_period_spelling_is_bound_to_the_reference_contract(self):
        text = fixtures.source(6, 'plot(timeframe.period == "1D" ? 1 : 0)')
        program = self.session.compile(text)
        plot = program.statements[-1]
        sample, *remaining = plot.arguments
        conditional = sample.expression
        condition = conditional.condition
        self.assertEqual(condition.left.contract.slot, PERIOD_V6_SLOT)
        legacy, = self.session.catalog_for(6).legacy_references
        self.assertEqual(legacy.slot, PERIOD_SLOT)
        old_condition = replace(condition, left=replace(condition.left, contract=legacy))
        old_sample = replace(sample, expression=replace(conditional, condition=old_condition))
        old_plot = replace(plot, arguments=(old_sample, *remaining))
        old = replace(program, statements=(*program.statements[:-1], old_plot))
        data = read_bars(ROOT / fixtures.DIRECTORY / 'daily.json')
        for candidate, expected in ((program, (1, 1)), (old, (0, 0))):
            restored = import_program(export_program(candidate))
            self.assertEqual(self.session.execute(restored, data=data).outputs[0].values, expected)

    def test_primary_targets_resolve_canonical_context_and_keep_raw_spelling(self):
        data = read_bars(ROOT / fixtures.DIRECTORY / 'daily.json')
        context = MarketContext(data.context_id, data.symbol, data.timeframe, 'test.1')
        for version, name in ((5, 'D'), (6, '1D')):
            for expression in ('timeframe.period', 'target'):
                with self.subTest(version=version, expression=expression):
                    text = fixtures.source(version, 'target=input.timeframe("1D")\n'
                        f'plot(request.security("TEST:HIGHER", {expression}, close))\nplot(timeframe.in_seconds())')
                    program = import_program(export_program(self.session.compile(text)))
                    request, = graph(program, primary=context)['requests']
                    self.assertEqual(request['timeframe'], 'D')
                    self.assertEqual(request['raw_targets']['timeframe'], name if expression == 'timeframe.period' else '1D')
                    result = self.session.execute(program, data=data, primary_context=context)
                    self.assertEqual([row.values for row in result.outputs], [(10., 10.), (86_400, 86_400)])

    def test_two_presets_same_ir_and_checkpoint_before_period_open(self):
        program = import_program(export_program(self.session.compile((ROOT / fixtures.EXAMPLE).read_text(encoding='utf-8'))))
        data = read_bars(ROOT / fixtures.DIRECTORY / 'strategy.json')
        timeframe = input_schema(program)['definitions'][-1]
        self.assertEqual((timeframe['input_type'], timeframe['default'], timeframe['options']), ('timeframe', '1D', ('1D', '1W')))
        daily = self.session.execute(program, data=data)
        weekly = self.session.execute(program, data=data, inputs={'tf': fixtures.OVERRIDE})
        for result, orders in ((daily, fixtures.DEFAULT_ORDERS), (weekly, fixtures.OVERRIDE_ORDERS)):
            self.assertEqual(tuple(row['bar_index'] for row in result.simulation['events'] if row['kind'] == 'order'), orders)
            self.assertEqual(result.outputs[0].values, tuple(int(index in orders) for index in range(len(data.bars))))
        self.assertEqual(daily.settings['input_schema_sha256'], weekly.settings['input_schema_sha256'])
        self.assertNotEqual(daily.settings['sha256'], weekly.settings['sha256'])

        runtime = self.session.validate_program(program)

        def driver(settings):
            value = HistoricalDriver(runtime.prepare(program, settings=settings), MemorySource(data),
                                     simulation_policy=broker_policy(program))
            self.addCleanup(value.simulation.close)
            return value

        settings = settings_for(program, {'tf': fixtures.OVERRIDE})
        checkpoints = []
        driver(settings).run(pause_after=fixtures.PAUSE_AFTER,
                             checkpoint=lambda value: checkpoints.append(loads(canonical(value.checkpoint()))))
        with self.assertRaisesRegex(ValueError, 'settings'):
            driver(daily.settings).restore(checkpoints[0])
        resumed = driver(settings)
        resumed.restore(checkpoints[0])
        resumed.run()
        self.assertEqual(resumed.finish(data_identity(data)), weekly)
