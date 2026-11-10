"""Table style admission, relocation, rendering state and replay checkpoints."""
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.codec import decode, encode
from packages.contracts.intrabar import PROFILE as INTRABAR
from packages.contracts.model import OverlayStyledTableRecord, StyledTableRecord, TableRecord
from packages.contracts.realtime import MarketEvent
from packages.contracts.tables import TEXT_LIMIT
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
from tools.tables_fixtures import RELOCATE, RELOCATED, source, style_source, styled_table

BARS = Path('conformance/tables/bars.json')


class TableStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.bars = read_bars(BARS)

    def test_saved_style_and_legacy_shapes_in_v4_v5_v6(self):
        for version in (4, 5, 6):
            with self.subTest(version=version):
                script = source(version, style_source(version), overlay=False)
                program = import_program(export_program(self.session.compile(script)))
                result = self.session.execute(program, data=self.bars)
                record = result.tables[0]
                self.assertIs(type(record), OverlayStyledTableRecord if version >= 5 else StyledTableRecord)
                self.assertEqual(result, decode(encode(result), type(result)))
                self.assertEqual({**asdict(record), 'cells': [asdict(cell) for cell in record.cells]},
                                 styled_table(version, 'final'))
                _, legacy = self.session.run(source(version), data=self.bars)
                self.assertIs(type(legacy.tables[0]), TableRecord)
                self.assertEqual(legacy, decode(encode(legacy), type(legacy)))

    def test_relocation_retires_loser_and_inventory_preserves_creation_order(self):
        for version in (4, 5, 6):
            with self.subTest(version=version):
                body = RELOCATE
                if version >= 5:
                    body += '''var table extra = na
if bar_index == 2
    extra := table.new(position.top_center, 1, 1)
    table.set_position(array.get(table.all, 0), position.middle_left)
    table.cell_set_text(array.get(table.all, 1), 0, 0, "extra")
plot(array.size(table.all))
'''
                _, result = self.session.run(source(version, body), data=self.bars)
                expected = [RELOCATED]
                if version >= 5:
                    self.assertEqual(result.outputs[0].values, (2, 1, 2, 2))
                    expected = [dict(RELOCATED, position='middle_left'),
                                dict(id=4, position='top_center', columns=1, rows=1, bgcolor=None,
                                     cells=[dict(column=0, row=0, text='extra',
                                                 text_color='#363A45FF', bgcolor=None)])]
                self.assertEqual([{**asdict(record), 'cells': [asdict(cell) for cell in record.cells]}
                                  for record in result.tables], expected)

    def test_invalid_updates_leave_state_and_position_occupancy_unchanged(self):
        tables = Tables()
        legacy = tables.apply('table.new_overlay', ('bottom_right', 1, 1, None, True))
        tables.apply('table.cell', (legacy, 0, 0, 'keep', '#FFFFFFFF', '#12345680'))
        base = tables.checkpoint()
        tables = Tables.restore(base, 1)
        tables.apply('table.set_frame_width', (legacy, 64))
        tables.apply('table.set_frame_color', (legacy, '#AABBCCFF'))
        record = tables.live[legacy]
        self.assertIs(type(record), OverlayStyledTableRecord)
        self.assertEqual(record.cells[0].text, 'keep')
        before = tables.checkpoint()
        for operation, values, code in (
                ('table.set_position', (legacy, 'somewhere'), 'TABLE_VALUE'),
                ('table.set_frame_width', (legacy, -1), 'TABLE_STYLE'),
                ('table.set_border_width', (legacy, 65), 'TABLE_STYLE'),
                ('table.new_styled', ('bottom_right', 1, 1, None, None, -1, None, 0), 'TABLE_STYLE'),
                ('table.cell_set_text', (legacy, 0, 0, 'x' * (TEXT_LIMIT + 1)), 'TABLE_VALUE'),
                ('table.cell_set_text_color', (legacy, 0, 1, None), 'TABLE_INDEX')):
            with self.subTest(operation=operation), self.assertRaises(KernelError) as error:
                tables.apply(operation, values)
            self.assertEqual(error.exception.code, code)
            self.assertEqual(tables.checkpoint(), before)
        tables.apply('table.set_position', (legacy, 'bottom_right'))
        self.assertEqual(tables.checkpoint(), before)

    def test_omitted_setter_options_reset_and_forged_ir_default_is_rejected(self):
        script = source(6, '''id = table.new(position.top_right, 1, 1, bgcolor=#112233, frame_color=#FF0000, frame_width=3, border_color=#FFFFFF, border_width=2)
table.set_bgcolor(id)
table.set_frame_color(id)
table.set_frame_width(id)
table.set_border_color(id)
table.set_border_width(id)
table.cell_set_text(id, 0, 0, "")
''')
        program, result = self.session.run(script, data=self.bars)
        record = result.tables[0]
        self.assertEqual((record.bgcolor, record.frame_color, record.frame_width,
                          record.border_color, record.border_width), (None, None, 0, None, 0))
        self.assertEqual(record.cells[0].text, '')
        declaration, store, *rest = program.statements
        call = store.expression
        arg = replace(call.arguments[5], origin='default', source_index=None)
        invalid = replace(call, arguments=(*call.arguments[:5], arg, *call.arguments[6:]))
        forged = replace(program, statements=(declaration, replace(store, expression=invalid), *rest))
        with self.assertRaises(OpenPineError) as error:
            self.session.validate_program(forged)
        self.assertEqual(error.exception.diagnostic.code, 'IR_TABLE_DEFAULT')

    def test_replay_restores_styled_checkpoint_and_preview(self):
        program = self.session.compile(source(6, style_source(6), overlay=False))
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
        self.assertEqual(status['tables'], [styled_table(6, 'first')])
        self.assertEqual(status['preview']['tables'], [styled_table(6, 'preview')])
        checkpoint = first.checkpoint()
        for phase, field, bad in (
                ('runtime', 'frame_width', 65), ('preview', 'border_width', True), ('preview', 'frame_color', 'red')):
            invalid = deepcopy(checkpoint)
            record = (invalid['runtime']['tables']['live'][0] if phase == 'runtime'
                      else invalid['intrabar']['preview']['tables'][0])
            record[field] = bad
            with self.subTest(phase=phase, field=field), self.assertRaisesRegex(ValueError, 'Invalid table record'):
                make().restore(invalid)
        incomplete = deepcopy(checkpoint)
        del incomplete['runtime']['tables']['live'][0]['frame_width']
        with self.assertRaisesRegex(ValueError, 'Invalid table record'):
            make().restore(incomplete)
        resumed = make()
        resumed.restore(checkpoint)
        resumed.evaluation.output_sink.restore(first.evaluation.output_sink.checkpoint(1), 1)
        self.assertEqual(resumed.status(), status)
        for event in events[3:]:
            resumed.apply(event)
        final = resumed.status()
        self.assertIsNone(final['preview'])
        self.assertEqual(final['tables'], [styled_table(6, 'final')])
