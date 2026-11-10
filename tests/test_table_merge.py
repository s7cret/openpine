"""Table merge ranges, compatibility, resource boundaries and replay."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.codec import decode, encode
from packages.contracts.drawings import EVENT_LIMIT
from packages.contracts.intrabar import PROFILE as INTRABAR
from packages.contracts.model import OverlayStyledMergedTableRecord, TableRecord
from packages.contracts.realtime import MarketEvent
from packages.contracts.tables import MERGE, table_payload
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
from tools.tables_fixtures import MERGE as SCRIPT, MERGE_AFTER, merge_range, merge_table, merged_after_tables, source

BARS = Path('conformance/tables/bars.json')


class TableMergeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.bars = read_bars(BARS)

    def test_versioned_saved_ir_and_mixed_result_round_trip(self):
        for version in (5, 6):
            with self.subTest(version=version):
                for script, overlay, expected in (
                        (SCRIPT, False, [merge_table('final')]),
                        (MERGE_AFTER, True, merged_after_tables())):
                    program = import_program(export_program(self.session.compile(source(version, script, overlay=overlay))))
                    result = self.session.execute(program, data=self.bars)
                    self.assertEqual(result, decode(encode(result), type(result)))
                    self.assertEqual([table_payload(record) for record in result.tables], expected)
                self.assertIs(type(result.tables[1]), TableRecord)

    def test_relocation_style_promotion_and_clear_preserve_ranges(self):
        tables = Tables()
        identity = tables.apply('table.new_overlay', ('top_right', 3, 3, None, True))
        tables.apply('table.cell', (identity, 1, 1, 'covered', None, '#112233FF'))
        legacy = tables.checkpoint()
        tables = Tables.restore(legacy, 1)
        self.assertEqual(tables.checkpoint(), legacy)
        tables.apply(MERGE, (identity, 0, 0, 1, 1))
        tables.apply('table.set_frame_width', (identity, 2))
        self.assertIs(type(tables.live[identity]), OverlayStyledMergedTableRecord)
        tables.apply('table.set_position', (identity, 'bottom_left'))
        tables.apply('table.cell_set_text', (identity, 0, 0, 'anchor'))
        tables.apply('table.clear', (identity, 1, 1, None, None))
        self.assertEqual([cell.text for cell in tables.live[identity].cells], ['anchor'])
        tables.apply('table.clear', (identity, 0, 0, 2, 2))
        self.assertEqual(tables.live[identity].cells, ())
        checkpoint = tables.checkpoint()
        self.assertEqual(checkpoint['live'][0]['merges'], [merge_range(0, 0, 1, 1)])
        self.assertEqual(Tables.restore(checkpoint, 1).checkpoint(), checkpoint)
        tables.apply('table.cell_set_text', (identity, 0, 0, 'new'))
        self.assertEqual(tables.live[identity].cells[0].text, 'new')
        fresh = tables.apply('table.new', ('bottom_left', 3, 3, None))
        tables.apply(MERGE, (identity, 0, 0, 1, 1))
        self.assertNotIn(identity, tables.live)
        self.assertIs(type(tables.live[fresh]), TableRecord)

    def test_invalid_merges_and_call_limit_leave_state_unchanged(self):
        tables = Tables()
        identity = tables.apply('table.new', ('top_left', 4, 4, None))
        tables.apply(MERGE, (identity, 2, 2, 3, 3))
        tables.apply(MERGE, (identity, 0, 0, 1, 1))
        before = tables.checkpoint()
        self.assertEqual(before['live'][0]['merges'], [merge_range(0, 0, 1, 1), merge_range(2, 2, 3, 3)])
        for rectangle, code in (
                ((0, 0, 1, 1), 'TABLE_MERGE_OVERLAP'), ((1, 1, 2, 2), 'TABLE_MERGE_OVERLAP'),
                ((3, 0, 2, 1), 'TABLE_MERGE_RANGE'), ((0, 2, 1, 0), 'TABLE_MERGE_RANGE'),
                ((0, 0, 4, 0), 'TABLE_INDEX'), ((-1, 0, 1, 0), 'TABLE_INDEX')):
            with self.subTest(rectangle=rectangle), self.assertRaises(KernelError) as error:
                tables.apply(MERGE, (identity, *rectangle))
            self.assertEqual(error.exception.code, code)
            self.assertEqual(tables.checkpoint(), before)
        tables.apply(MERGE, (identity, 0, 0, 0, 0))
        self.assertEqual(tables.checkpoint(), before)
        for _ in range(EVENT_LIMIT - tables.calls):
            tables.apply(MERGE, (None, 0, 0, 1, 1))
        with self.assertRaises(KernelError) as error:
            tables.apply(MERGE, (identity, 2, 0, 3, 1))
        self.assertEqual(error.exception.code, 'TABLE_CALL_LIMIT')
        self.assertEqual(tables.checkpoint(), before)

    def test_restore_rejects_noncanonical_and_overlapping_ranges(self):
        tables = Tables()
        identity = tables.apply('table.new', ('top_right', 3, 3, None))
        tables.apply(MERGE, (identity, 0, 0, 1, 1))
        original = tables.checkpoint()
        rectangle = merge_range(0, 0, 1, 1)
        for ranges in ([], [merge_range(0, 0, 0, 0)], [merge_range(0, 0, 3, 1)],
                       [dict(rectangle, start_row=True)], [rectangle, merge_range(1, 1, 2, 2)],
                       [merge_range(2, 0, 2, 1), rectangle], [rectangle, rectangle], [dict(rectangle, extra=1)]):
            state = deepcopy(original)
            state['live'][0]['merges'] = ranges
            with self.subTest(ranges=ranges), self.assertRaisesRegex(ValueError, 'Invalid table merge'):
                Tables.restore(state, 1)
        self.assertEqual(Tables.restore(original, 1).checkpoint(), original)

    def test_replay_checkpoint_keeps_preview_topology_separate_from_confirmed(self):
        program = self.session.compile(source(6, SCRIPT, overlay=False))
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
        self.assertEqual(status['tables'], [merge_table('first')])
        self.assertEqual(status['preview']['tables'], [merge_table('preview')])
        checkpoint = first.checkpoint()
        invalid = deepcopy(checkpoint)
        invalid['intrabar']['preview']['tables'][0]['merges'][1]['start_column'] = 1
        with self.assertRaisesRegex(ValueError, 'Invalid table merge'):
            make().restore(invalid)
        resumed = make()
        resumed.restore(checkpoint)
        resumed.evaluation.output_sink.restore(first.evaluation.output_sink.checkpoint(1), 1)
        self.assertEqual(resumed.status(), status)
        phases = {3: 'confirmed', 5: 'cleared', 7: 'final'}
        for event in events[3:]:
            resumed.apply(event)
            if event.sequence in phases:
                current = resumed.status()
                self.assertIsNone(current['preview'])
                self.assertEqual(current['tables'], [merge_table(phases[event.sequence])])
