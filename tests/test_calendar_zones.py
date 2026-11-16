"""Calendar-zone contracts through IR, chart execution and worker recovery."""

import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.execution.artifacts import export_program, import_program
from packages.execution.driver import HistoricalDriver, MemorySource
from packages.execution.session import Session
from packages.marketdata import data_identity, read_bars
from packages.storage import checkpoint_records
from packages.storage.objects import ObjectStore
from tools import calendar_zones_fixtures as fixtures
from tools.conformance import run_cases
from tools.semantic_matrix import matrix
from tools.specification import ROOT


class CalendarZoneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data = read_bars(ROOT / fixtures.DIRECTORY / 'fall.json')

    def test_versioned_corpus_and_matrix(self):
        report = run_cases(self.session, cases_path=fixtures.CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (17, 10))
        rows, _ = matrix()
        fields = {'year', 'month', 'weekofyear', 'dayofmonth', 'dayofweek', 'hour', 'minute', 'second'}
        for version, entries in rows.items():
            selected = {name: status for name, _, status, detail in entries if 'CALENDAR_ZONES.md' in detail}
            expected = fields if version >= 4 else {'weekofyear'}
            if version >= 3:
                expected = expected | {'timestamp'}
            self.assertEqual(selected, dict.fromkeys(expected, 'PARTIAL'))

    def test_round_trip_ir_and_checkpoint_between_folds(self):
        text = (ROOT / fixtures.EXAMPLE).read_text(encoding='utf-8')
        program = import_program(export_program(self.session.compile(text, fixtures.EXAMPLE)))
        runtime = self.session.validate_program(program)

        def driver(settings=None):
            return HistoricalDriver(runtime.prepare(program, settings=settings), MemorySource(self.data))

        expected = self.session.execute(program, data=self.data)
        self.assertEqual(tuple(output.values for output in expected.outputs), fixtures.EXAMPLE_OUTPUTS)
        with TemporaryDirectory() as path:
            objects = ObjectStore(Path(path) / 'objects')
            checkpoints = []
            driver().run(pause_after=fixtures.PAUSE_AFTER, checkpoint=lambda value: checkpoints.append(
                checkpoint_records.put(objects, value.checkpoint(streaming=True))))
            saved, = checkpoints
            state = checkpoint_records.get(objects, saved, streaming=True)
            self.assertEqual(state['last_time'], 1730611800000)
            utc = self.session.execute(program, data=self.data, inputs={'zone': 'UTC'})
            self.assertEqual(utc.outputs[1].values, (0, 5, 6, 8))
            with self.assertRaisesRegex(ValueError, 'settings'):
                driver(utc.settings).restore(state)
            resumed = driver()
            resumed.restore(state)
            resumed.run()
        self.assertEqual(resumed.finish(data_identity(self.data)), expected)

    def test_previous_contracts_remain_admitted_and_simple_zoned_result(self):
        program = self.session.compile(fixtures.source(6,
            'plot(hour(time))\nplot(hour(time,timezone="UTC"))\n'
            'plot(timestamp(2024,11,3,8,0))\nplot(timestamp("UTC",2024,11,3,8))\n'
            'plot(timestamp("2024-11-03 08:00 UTC"))'))
        outputs = self.session.execute(import_program(export_program(program)), data=self.data).outputs
        self.assertEqual(outputs[0].values, outputs[1].values)
        self.assertEqual([out.values for out in outputs[2:]], [(fixtures.BOUNDARY,) * 4] * 3)
        self.assertEqual([out.value_type.qualifier for out in outputs[2:]], ['const', 'simple', 'const'])

    def test_saved_ir_rejects_altered_timestamp_default(self):
        program = self.session.compile(fixtures.source(6, 'plot(timestamp("UTC",2024,11,3))'))
        plot = program.statements[-1]
        sample, *other = plot.arguments
        call = sample.expression
        clock = call.arguments[-1]
        altered = replace(clock, expression=replace(clock.expression, value=1))
        call = replace(call, arguments=(*call.arguments[:-1], altered))
        draft = replace(program, statements=(*program.statements[:-1],
            replace(plot, arguments=(replace(sample, expression=call), *other))))
        with self.assertRaises(OpenPineError) as rejected:
            self.session.validate_program(draft)
        self.assertEqual(rejected.exception.diagnostic.code, 'IR_CALENDAR_DEFAULT')

    def test_legacy_timestamp_optional_clock_fields_follow_artifacts(self):
        with self.assertRaises(OpenPineError) as rejected:
            self.session.compile(fixtures.source(3, 'plot(timestamp("UTC",2024,11,3))'))
        self.assertEqual(rejected.exception.diagnostic.code, 'MISSING_ARGUMENT')
        for version in (4, 5, 6):
            program = self.session.compile(fixtures.source(version, 'plot(timestamp("UTC",2024,11,3))'))
            values = self.session.execute(program, data=self.data).outputs[0].values
            self.assertEqual(values, (1730592000000,) * 4)
