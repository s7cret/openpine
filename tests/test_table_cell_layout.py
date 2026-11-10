"""Versioned table-cell layout, compatibility, mutation and replay."""
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.codec import decode, encode
from packages.contracts.intrabar import PROFILE as INTRABAR
from packages.contracts.model import LayoutTableCell, TableCell
from packages.contracts.realtime import MarketEvent
from packages.execution.artifacts import export_program, import_program
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.marketdata import read_bars
from packages.runtime.series import KernelError
from packages.runtime.tables import Tables
from packages.storage.objects import ObjectStore
from packages.storage.results import ResultSink
from tests.test_realtime import PRIMARY
from tools.installed_lines_smoke import market_events
from tools.tables_fixtures import cell_layout_source, layout_table, source

BARS = Path('conformance/tables/bars.json')


class TableCellLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.bars = read_bars(BARS)

    def test_qualified_layout_keeps_legacy_cells_readable_through_saved_ir_and_result(self):
        for version in (4, 5, 6):
            with self.subTest(version=version):
                script = source(version, cell_layout_source(version), overlay=False)
                program = import_program(export_program(self.session.compile(script)))
                result = self.session.execute(program, data=self.bars)
                record, = result.tables
                self.assertEqual(result, decode(encode(result), type(result)))
                self.assertIs(type(record.cells[0]), LayoutTableCell)
                self.assertIs(type(record.cells[2]), TableCell)
                self.assertEqual({**asdict(record), 'cells': [asdict(cell) for cell in record.cells]},
                                 layout_table(version, 'final'))

    def test_partial_updates_preserve_fields_and_default_reset_recovers_old_shape(self):
        tables = Tables()
        identity = tables.apply('table.new', ('top_left', 1, 1, None))
        def apply(operation, *args):
            tables.apply(operation, (identity, 0, 0, *args))

        apply('table.cell', 'old', '#FFFFFFFF', '#12345680')
        original = tables.checkpoint()
        tables = Tables.restore(original, 1)
        apply('table.cell_set_text_font_family', 'font.family_monospace')
        apply('table.cell_set_tooltip', '<b>tip</b>')
        apply('table.cell_set_text', 'new')
        apply('table.cell_set_width', 25)
        styled = tables.live[identity].cells[0]
        self.assertEqual((styled.text, styled.text_color, styled.bgcolor, styled.width,
                          styled.text_font_family, styled.tooltip),
                         ('new', '#FFFFFFFF', '#12345680', 25.0, 'monospace', '<b>tip</b>'))
        apply('table.cell_set_tooltip', None)
        self.assertEqual(tables.live[identity].cells[0], replace(styled, tooltip=''))
        apply('table.cell', 'reset', None, None)
        self.assertIs(type(tables.live[identity].cells[0]), TableCell)
        tables.apply('table.clear', (identity, 0, 0, None, None))
        apply('table.cell_set_text_valign', 'text.align_bottom')
        self.assertEqual(tables.live[identity].cells[0].text_color, '#363A45FF')
        apply('table.cell_set_text_valign', 'text.align_center')
        self.assertIs(type(tables.live[identity].cells[0]), TableCell)

    def test_invalid_layout_updates_are_atomic(self):
        tables = Tables()
        identity = tables.apply('table.new', ('top_left', 1, 1, None))
        tables.apply('table.cell_set_width', (identity, 0, 0, 100))
        before = tables.checkpoint()
        for operation, value in (
                ('width', -1), ('height', float('nan')), ('width', float('inf')), ('height', True),
                ('width', 100.01), ('text_size', 257), ('text_size', 'typo'), ('text_formatting', 4),
                ('text_font_family', 'cursive'), ('text_halign', 'text.align_bottom'), ('tooltip', 'x' * 4097),
                ('tooltip', chr(0xD800))):
            with self.subTest(operation=operation, value=repr(value)[:30]), self.assertRaises(KernelError) as error:
                tables.apply('table.cell_set_' + operation, (identity, 0, 0, value))
            self.assertEqual(error.exception.code, 'TABLE_CELL_LAYOUT')
            self.assertEqual(tables.checkpoint(), before)
        operation = 'table.cell_layout_format'
        malformed = (identity, 0, 0, 'replacement', 10., -1., '#FFFFFFFF', 'left', 'top', 12,
                     None, '', 'default', 0)
        with self.assertRaises(KernelError):
            tables.apply(operation, malformed)
        self.assertEqual(tables.checkpoint(), before)

    def test_layout_defaults_in_saved_ir_are_closed(self):
        script = source(6, '''id = table.new(position.top_left, 1, 1)
table.cell(id, 0, 0, width=30)
''')
        program = self.session.compile(script)
        declaration, store, call = program.statements
        self.assertEqual(call.operation, 'table.cell_layout_format')
        original = call.arguments[5]
        forged_default = replace(original, expression=replace(original.expression, value=12.))
        forged_call = replace(call, arguments=(*call.arguments[:5], forged_default, *call.arguments[6:]))
        with self.assertRaises(OpenPineError) as error:
            self.session.validate_program(replace(program, statements=(declaration, store, forged_call)))
        self.assertEqual(error.exception.diagnostic.code, 'IR_TABLE_DEFAULT')

    def test_replay_restores_confirmed_and_preview_layout_independently(self):
        program = self.session.compile(source(6, cell_layout_source(6), overlay=False))
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        objects = ObjectStore(root / 'objects')
        events = [MarketEvent.from_payload(row) for row in market_events(BARS, PRIMARY)]

        def make():
            sink = ResultSink(objects, tempfile.mkdtemp(dir=root))
            self.addCleanup(sink.close)
            return ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, sink,
                                calculation_profile=INTRABAR)

        first = make()
        for event in events[:3]:
            first.apply(event)
        status = first.status()
        self.assertEqual(status['tables'], [layout_table(6, 'first')])
        self.assertEqual(status['preview']['tables'], [layout_table(6, 'preview')])
        checkpoint = first.checkpoint()
        for field, bad in (('width', -1.), ('height', True), ('text_size', 'unknown'),
                           ('tooltip', False), ('text_formatting', 99)):
            invalid = deepcopy(checkpoint)
            invalid['intrabar']['preview']['tables'][0]['cells'][0][field] = bad
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'Invalid table cell'):
                make().restore(invalid)
        incomplete = deepcopy(checkpoint)
        del incomplete['runtime']['tables']['live'][0]['cells'][0]['text_halign']
        with self.assertRaisesRegex(ValueError, 'Invalid table cell'):
            make().restore(incomplete)
        resumed = make()
        resumed.restore(checkpoint)
        resumed.evaluation.output_sink.restore(first.evaluation.output_sink.checkpoint(1), 1)
        self.assertEqual(resumed.status(), status)
        for event in events[3:]:
            resumed.apply(event)
        final = resumed.status()
        self.assertIsNone(final['preview'])
        self.assertEqual(final['tables'], [layout_table(6, 'final')])
