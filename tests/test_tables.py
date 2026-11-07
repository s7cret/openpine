"""Table lifecycle, durable preview boundaries and resource limits."""
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.codec import decode, encode
from packages.contracts.drawings import EVENT_LIMIT
from packages.contracts.intrabar import PROFILE as INTRABAR
from packages.contracts.model import OverlayTableRecord, TableRecord
from packages.contracts.tables import CELL_LIMIT, POSITIONS, TEXT_LIMIT
from packages.execution.artifacts import export_program, import_program
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.runtime.series import KernelError
from packages.runtime.tables import Tables
from packages.storage.objects import ObjectStore
from packages.storage.results import ResultSink
from tests.test_realtime import PRIMARY, historical, tape
from tools.tables_fixtures import OVERLAY, overlay_tables, source, status_table


class TableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_saved_ir_and_replay_restore_confirmed_and_preview(self):
        script = source(6, overlay=False)
        program = import_program(export_program(self.session.compile(script, 'tables.pine')))
        self.assertEqual(program.schema_version, 'openpine.typed-ir.27')
        events = list(tape())
        _, expected = self.session.run(script, 'tables.pine', data=historical(events))
        self.assertEqual(decode(encode(expected), type(expected)), expected)
        self.assertEqual(expected.tables[0].cells[1].text, '17')

        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        objects = ObjectStore(root / 'objects')
        for profile in (None, INTRABAR):
            with self.subTest(profile=profile):
                def make():
                    sink = ResultSink(objects, tempfile.mkdtemp(dir=root))
                    sink.enable_boxes()
                    return ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id,
                                        sink, calculation_profile=profile)
                first = make()
                for event in events[:4]:
                    first.apply(event)
                status = first.status()
                self.assertFalse(status['table_overlay'])
                self.assertEqual(status['tables'], [status_table('11', '#AA2222FF')])
                self.assertEqual(status['preview']['tables'], [status_table('12', '#AA2222FF')])
                state = first.checkpoint()
                prefix = first.evaluation.output_sink.checkpoint(1)
                self.assertEqual(state['runtime']['schema_version'], 'openpine.runtime-state.19')
                resumed = make()
                resumed.restore(state)
                output = resumed.evaluation.output_sink
                output.restore(prefix, 1)
                output.validate_boxes(resumed.evaluation.boxes)
                self.assertEqual(resumed.status(), status)
                for event in events[4:]:
                    resumed.apply(event)
                final = resumed.status()
                self.assertIsNone(final['preview'])
                self.assertEqual(final['tables'], [status_table('17', '#AA2222FF')])
                self.assertEqual(final['boxes'], [asdict(box) for box in expected.boxes])
                invalid = deepcopy(state)
                invalid['runtime']['tables']['live'][0]['cells'][0]['column'] = 2
                with self.assertRaisesRegex(ValueError, 'Invalid table cell'):
                    make().restore(invalid)
                if profile:
                    invalid = deepcopy(state)
                    invalid['intrabar']['preview']['tables'][0]['cells'][0]['text'] = 'x' * (TEXT_LIMIT + 1)
                    with self.assertRaisesRegex(ValueError, 'Invalid table cell'):
                        make().restore(invalid)

    def test_overlay_replacement_and_open_bar_restore_preserve_table_identity(self):
        events = list(tape())
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        objects = ObjectStore(root / 'objects')
        for version in (5, 6):
            with self.subTest(version=version):
                script = source(version, OVERLAY, overlay=False)
                program = import_program(export_program(self.session.compile(script, 'table-overlay.pine')))
                expected = self.session.execute(program, data=historical(events))
                self.assertIs(type(expected.tables[0]), TableRecord)
                self.assertIsInstance(expected.tables[1], OverlayTableRecord)
                self.assertEqual(decode(encode(expected), type(expected)), expected)

                def make():
                    sink = ResultSink(objects, tempfile.mkdtemp(dir=root))
                    self.addCleanup(sink.close)
                    return ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, sink,
                                        calculation_profile=INTRABAR)

                first = make()
                for event in events[:4]:
                    first.apply(event)
                status = first.status()
                self.assertFalse(status['table_overlay'])
                self.assertEqual(status['tables'], overlay_tables())
                self.assertEqual(status['preview']['tables'], overlay_tables('12'))
                state = first.checkpoint()
                invalid = deepcopy(state)
                invalid['intrabar']['preview']['tables'][-1]['force_overlay'] = 1
                with self.assertRaisesRegex(ValueError, 'Invalid table record'):
                    make().restore(invalid)
                resumed = make()
                resumed.restore(state)
                resumed.evaluation.output_sink.restore(first.evaluation.output_sink.checkpoint(1), 1)
                self.assertEqual(resumed.status(), status)
                for event in events[4:]:
                    resumed.apply(event)
                final = resumed.status()
                self.assertIsNone(final['preview'])
                self.assertEqual(final['tables'], overlay_tables('17'))
                restored = make()
                restored.restore(resumed.checkpoint())
                self.assertEqual(restored.status()['tables'], final['tables'])

    def test_overlay_admission_rejects_a_forged_true_default(self):
        script = source(6, 'table.new(position.top_right, 1, 1, force_overlay=true)\n')
        program = self.session.compile(script, 'table-overlay.pine')
        declaration, call = program.statements
        flag = replace(call.arguments[-1], origin='default', source_index=None)
        forged = replace(program, statements=(declaration, replace(call, arguments=(*call.arguments[:-1], flag))))
        with self.assertRaises(OpenPineError) as caught:
            self.session.execute(forged, data=historical(list(tape())))
        self.assertEqual(caught.exception.diagnostic.code, 'IR_TABLE_DEFAULT')

    def test_all_positions_and_replace_under_budget(self):
        tables = Tables()
        for position in POSITIONS:
            tables.apply('table.new', (position, 1, 1, None))
        self.assertEqual([t['position'] for t in tables.snapshot()], list(POSITIONS))
        self.assertEqual(tables.snapshot(), Tables.restore(tables.checkpoint(), 1).snapshot())
        before = tables.checkpoint()
        with self.assertRaises(KernelError) as error:
            tables.apply('table.new', (POSITIONS[0], CELL_LIMIT, 1, None))
        self.assertEqual(error.exception.code, 'TABLE_CELL_LIMIT')
        self.assertEqual(tables.checkpoint(), before)
        fresh = tables.apply('table.new', (POSITIONS[0], CELL_LIMIT - 8, 1, None))
        self.assertEqual(fresh, 10)
        tables.apply('table.delete', (1,))
        self.assertIn(fresh, tables.live)

    def test_cell_bounds_and_update_budget_are_atomic(self):
        tables = Tables()
        identity = tables.apply('table.new', ('bottom_right', 2, 2, None))
        tables.apply('table.cell', (identity, 1, 1, 'old', '#FFFFFFFF', '#12345680'))
        before = tables.checkpoint()
        for operation, values, code in (
                ('table.cell', (identity, 0, 0, 'x' * (TEXT_LIMIT + 1), None, None), 'TABLE_VALUE'),
                ('table.clear', (identity, 1, 1, 0, 0), 'TABLE_INDEX')):
            with self.assertRaises(KernelError) as error:
                tables.apply(operation, values)
            self.assertEqual(error.exception.code, code)
            self.assertEqual(tables.checkpoint(), before)
        for _ in range(EVENT_LIMIT - tables.calls):
            tables.apply('table.delete', (None,))
        with self.assertRaises(KernelError) as error:
            tables.apply('table.delete', (identity,))
        self.assertEqual(error.exception.code, 'TABLE_CALL_LIMIT')
        self.assertEqual(tables.checkpoint(), before)
