"""Date-bound IR, portable defaults and independently checked order boundaries."""

import unittest
from dataclasses import replace

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError, ValueType
from packages.contracts.date_input import MAX_TIMESTAMP, MIN_TIMESTAMP, parse_date
from packages.contracts.inputs import input_schema, settings_for
from packages.contracts.strategy_ir import broker_policy
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.driver import HistoricalDriver, MemorySource
from packages.execution.session import Session
from packages.marketdata import data_identity, read_bars
from tools import date_inputs_fixtures as fixtures
from tools.conformance import run_cases
from tools.semantic_matrix import matrix
from tools.specification import ROOT


class DateInputsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_versioned_corpus_and_matrix(self):
        report = run_cases(self.session, cases_path=fixtures.CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (6, 18))
        rows, _ = matrix()
        for version, entries in rows.items():
            selected = {name: status for name, _, status, detail in entries if 'DATE_INPUTS.md' in detail}
            expected = {} if version < 4 else {'timestamp': 'PARTIAL', 'input' if version == 4 else 'input.time': 'PARTIAL'}
            self.assertEqual(selected, expected)
        series = next(status for name, overload, status, _ in rows[6] if name == 'timestamp' and overload == '1')
        self.assertEqual(series, 'MISSING')

    def test_date_range_and_fixed_offset(self):
        self.assertEqual(parse_date('0001-01-01'), MIN_TIMESTAMP)
        self.assertEqual(parse_date('9999-12-31T23:59:59.999Z'), MAX_TIMESTAMP)
        self.assertEqual(parse_date('1 Jan 1970 00:00:00.01 GMT-0100'), 3_600_010)
        self.assertEqual(parse_date('1970-01-01T00:00 UTC+5:30'), -19_800_000)
        for value in ('0001-01-01T00:00+00:01', '9999-12-31T23:59:59.999-00:01', '01 Xyz 2025',
                      '2025-11-02T24:00Z', '2025-11-02T00:00:60Z', '2025-11-02 America/New_York',
                      ' ' * 128 + '2025-11-02'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_date(value)

    def test_compile_constant_roundtrip_and_reject_invalid_input(self):
        for version in (4, 5, 6):
            with self.subTest(version=version):
                bound_input = fixtures.time_input(version, 'epoch - 1', ', title="Date", group="Backtest"')
                text = fixtures.source(version, f'epoch = timestamp("1970-01-01")\ndate = {bound_input}\nplot(date)')
                program = import_program(export_program(self.session.compile(text)))
                self.session.validate_program(program)
                self.assertEqual(program.statements[1].expression.value_type, ValueType('int'))
                self.assertEqual(program.statements[2].expression.value_type, ValueType('int', 'input'))
                definition, = input_schema(program)['definitions']
                self.assertEqual((definition['input_type'], definition['default'], definition['group']), ('time', -1, 'Backtest'))
                self.assertEqual(settings_for(program, {'date': {'$int': str(MAX_TIMESTAMP)}})['values'], {'input:date': MAX_TIMESTAMP})
                for bad in (True, 1.0, '1', None, MIN_TIMESTAMP - 1, MAX_TIMESTAMP + 1):
                    with self.assertRaises(OpenPineError):
                        settings_for(program, {'date': bad})
                store = program.statements[2]
                forged = replace(store, expression=replace(store.expression,
                                 definition=replace(store.expression.definition, options=(-1,))))
                with self.assertRaises(OpenPineError):
                    self.session.validate_program(replace(program, statements=(*program.statements[:2], forged, program.statements[-1])))

    def test_two_windows_same_ir_and_checkpoint(self):
        text = (ROOT / fixtures.EXAMPLE).read_text(encoding='utf-8')
        program = import_program(export_program(self.session.compile(text)))
        data = read_bars(ROOT / fixtures.DIRECTORY / 'bars.json')
        original = self.session.execute(program, data=data)
        overrides = fixtures.inputs(fixtures.OVERRIDE_WINDOW)
        expected = self.session.execute(program, data=data, inputs=overrides)
        for result, orders in ((original, fixtures.DEFAULT_ORDERS), (expected, fixtures.OVERRIDE_ORDERS)):
            self.assertEqual(tuple(row['bar_index'] for row in result.simulation['events'] if row['kind'] == 'order'), orders)
            self.assertEqual(result.outputs[0].values, tuple(int(index in orders) for index in range(6)))
        self.assertNotEqual(original.settings['sha256'], expected.settings['sha256'])
        self.assertEqual(original.settings['input_schema_sha256'], expected.settings['input_schema_sha256'])

        runtime = self.session.validate_program(program)

        def driver(settings):
            value = HistoricalDriver(runtime.prepare(program, settings=settings), MemorySource(data),
                                     simulation_policy=broker_policy(program))
            self.addCleanup(value.simulation.close)
            return value

        edited_settings = settings_for(program, overrides)
        checkpoints = []
        driver(edited_settings).run(pause_after=3, checkpoint=lambda value: checkpoints.append(loads(canonical(value.checkpoint()))))
        with self.assertRaisesRegex(ValueError, 'settings'):
            driver(original.settings).restore(checkpoints[0])
        resumed = driver(edited_settings)
        resumed.restore(checkpoints[0])
        resumed.run()
        self.assertEqual(resumed.finish(data_identity(data)), expected)
