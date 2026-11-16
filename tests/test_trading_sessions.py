"""Versioned session calls, validated input settings and exact order boundaries."""

import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from apps.openpine.cli import main as cli
from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError, ValueType
from packages.contracts.inputs import input_schema, settings_for
from packages.contracts.model import LiteralIR
from packages.contracts.strategy_ir import broker_policy
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.driver import HistoricalDriver, MemorySource
from packages.execution.session import Session
from packages.marketdata import data_identity, read_bars
from tools import trading_sessions_fixtures as fixtures
from tools.conformance import run_cases
from tools.semantic_matrix import matrix
from tools.specification import ROOT


class TradingSessionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_versioned_corpus_and_matrix(self):
        report = run_cases(self.session, cases_path=fixtures.CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (28, 17))
        rows, _ = matrix()
        for version, entries in rows.items():
            selected = {name: status for name, _, status, detail in entries if 'TRADING_SESSIONS.md' in detail}
            expected = {'time': 'PARTIAL'}
            if version >= 4:
                expected['time_close'] = 'PARTIAL'
            if version >= 3:
                expected['input' if version < 5 else 'input.session'] = 'PARTIAL'
            self.assertEqual(selected, expected)

    def test_session_inputs_use_one_closed_validation_contract(self):
        for version in range(3, 7):
            with self.subTest(version=version):
                declaration = fixtures.session_input(version, fixtures.DEFAULT)
                text = fixtures.source(version, f'window={declaration}\nplot(time("", window))')
                program = import_program(export_program(self.session.compile(text)))
                self.session.validate_program(program)
                self.assertEqual(program.statements[1].expression.value_type, ValueType('string', 'input'))
                definition, = input_schema(program)['definitions']
                self.assertEqual((definition['input_type'], definition['default']), ('session', fixtures.DEFAULT))
                actual = settings_for(program, {'window': '2200-0200:2'})
                self.assertEqual(actual['values'], {'input:window': '2200-0200:2'})
                for bad in (1, None, {}, '', '2400-0100', '0900-1200:0', '0900-1200,1100-1300'):
                    with self.assertRaises(OpenPineError) as caught:
                        settings_for(program, {'window': bad})
                    self.assertEqual(caught.exception.diagnostic.code, 'INPUT_SETTINGS')

    def test_cli_rejects_invalid_session_override(self):
        parent = ROOT / '.runtime' / 'session-tests'
        parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=parent) as directory:
            overrides = Path(directory) / 'invalid.json'
            overrides.write_text('{"window":"0930-1130:0"}', encoding='utf-8')
            result = Path(directory) / 'result.json'
            code = cli(['run', '--source', str(ROOT / fixtures.EXAMPLE), '--bars', str(ROOT / fixtures.DIRECTORY / 'strategy.json'),
                        '--inputs', str(overrides), '--output', str(result)])
            self.assertEqual(code, 2)
            rejected = json.loads(result.read_bytes())
            self.assertEqual(rejected['diagnostics'][0]['code'], 'INPUT_SETTINGS')

    def test_saved_ir_requires_the_physical_bar_and_exchange_zone_default(self):
        program = self.session.compile(fixtures.source(6, 'plot(time(""))'))
        plot = program.statements[-1]
        argument, *others = plot.arguments
        native = argument.expression
        for index, substitute in ((0, LiteralIR(0, ValueType('int'), native.span)),
                                  (4, LiteralIR('UTC', ValueType('string'), native.span))):
            values = list(native.arguments)
            values[index] = replace(values[index], expression=substitute)
            forged_arg = replace(argument, expression=replace(native, arguments=tuple(values)))
            forged_plot = replace(plot, arguments=(forged_arg, *others))
            forged = replace(program, statements=(*program.statements[:-1], forged_plot))
            with self.assertRaises(OpenPineError) as caught:
                self.session.validate_program(forged)
            self.assertEqual(caught.exception.diagnostic.code, 'IR_SESSION_INPUT')

    def test_two_windows_same_ir_and_checkpoint(self):
        program = import_program(export_program(self.session.compile((ROOT / fixtures.EXAMPLE).read_text(encoding='utf-8'))))
        data = read_bars(ROOT / fixtures.DIRECTORY / 'strategy.json')
        defaults = self.session.execute(program, data=data)
        overrides = {'window': fixtures.OVERRIDE}
        expected = self.session.execute(program, data=data, inputs=overrides)
        for result, orders in ((defaults, fixtures.DEFAULT_ORDERS), (expected, fixtures.OVERRIDE_ORDERS)):
            self.assertEqual(tuple(row['bar_index'] for row in result.simulation['events'] if row['kind'] == 'order'), orders)
            self.assertEqual(result.outputs[0].values, tuple(int(index in orders) for index in range(6)))
            self.assertEqual(tuple(result.settings['values'][f'input:{name}'] for name in ('start', 'end')), fixtures.DATE_RANGE)
        self.assertEqual(expected.settings['values']['input:window'], fixtures.OVERRIDE)
        self.assertEqual(defaults.settings['input_schema_sha256'], expected.settings['input_schema_sha256'])
        self.assertNotEqual(defaults.settings['sha256'], expected.settings['sha256'])

        runtime = self.session.validate_program(program)

        def driver(settings):
            value = HistoricalDriver(runtime.prepare(program, settings=settings), MemorySource(data),
                                     simulation_policy=broker_policy(program))
            self.addCleanup(value.simulation.close)
            return value

        settings = settings_for(program, overrides)
        checkpoints = []
        driver(settings).run(pause_after=3, checkpoint=lambda value: checkpoints.append(loads(canonical(value.checkpoint()))))
        with self.assertRaisesRegex(ValueError, 'settings'):
            driver(defaults.settings).restore(checkpoints[0])
        resumed = driver(settings)
        resumed.restore(checkpoints[0])
        resumed.run()
        self.assertEqual(resumed.finish(data_identity(data)), expected)
