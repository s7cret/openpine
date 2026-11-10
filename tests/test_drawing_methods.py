"""Drawing instance calls lower to existing contracts and preserve observable effects."""
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.drawings import KINDS
from packages.contracts.intrabar import PROFILE as INTRABAR
from packages.contracts.realtime import MarketEvent
from packages.execution.artifacts import export_program, import_program
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.marketdata import read_bars
from packages.runtime.checkpoints import layout
from packages.storage.box_journal import box_events
from packages.storage.label_journal import label_events
from packages.storage.line_journal import line_events
from packages.storage.objects import ObjectStore
from packages.storage.polyline_journal import polyline_events
from packages.storage.results import ResultSink
from tests.test_realtime import PRIMARY
from tools.installed_lines_smoke import market_events
from tools.tables_fixtures import DRAWING_RECEIVER_ONCE, drawing_methods_body, drawing_methods_table, source

BARS = Path('conformance/tables/bars.json')
JOURNALS = {'label': label_events, 'line': line_events, 'box': box_events, 'polyline': polyline_events}


class DrawingMethodTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.bars = read_bars(BARS)

    def compile(self, version, body):
        return self.session.compile(source(version, body), 'drawing-methods.pine')

    def reject(self, version, body):
        with self.assertRaises(OpenPineError) as error:
            self.compile(version, body)
        diagnostic = error.exception.diagnostic
        self.assertEqual(diagnostic.language_version, version)
        return diagnostic.category, diagnostic.code

    def test_both_forms_select_same_contracts_and_results_in_v5_and_v6(self):
        for version in (5, 6):
            with self.subTest(version=version):
                programs = [import_program(export_program(self.compile(version, drawing_methods_body(instance=instance))))
                            for instance in (False, True)]
                self.assertEqual([program.schema_version for program in programs], ['openpine.typed-ir.31'] * 2)
                self.assertTrue(all(not program.functions for program in programs))

                def contracts(program):
                    return [(call.operation, call.overload_id, call.signature_sha256, call.evaluation_order,
                             tuple((arg.origin, arg.source_index) for arg in call.arguments))
                            for call in layout(program)[0].values()]
                self.assertEqual(contracts(programs[0]), contracts(programs[1]))
                first, second = [self.session.execute(program, data=self.bars) for program in programs]
                for kind in KINDS:
                    self.assertEqual(getattr(first, kind), getattr(second, kind))
                self.assertEqual(first.outputs[0].values, second.outputs[0].values)
                self.assertEqual(second.outputs[0].values, (25.0,) * 4)

    def test_receiver_is_evaluated_once_before_named_arguments(self):
        for version in (5, 6):
            with self.subTest(version=version):
                result = self.session.execute(self.compile(version, DRAWING_RECEIVER_ONCE), data=self.bars)
                self.assertEqual(result.outputs[0].values, (3,) * 4)
                table, = result.tables
                cell, = table.cells
                self.assertEqual((table.id, table.columns, cell.column, cell.row, cell.text),
                                 (1, 5, 3, 2, 'Ordered'))

    def test_known_method_diagnostics_share_qualified_binding_and_early_versions_reject(self):
        for version in range(1, 5):
            self.assertEqual(self.reject(version, 'plot(close.merge_cells(0, 0, 1, 0))\n'),
                             ('LANGUAGE_INVALID', 'METHOD_VERSION'))
        setup = 'var table t = table.new(position.top_left, 2, 2)\n'
        for version in (5, 6):
            for instance, qualified, code in (
                    ('t.merge_cells(0, 0, 1)', 'table.merge_cells(t, 0, 0, 1)', 'MISSING_ARGUMENT'),
                    ('t.clear(bad=0)', 'table.clear(t, bad=0)', 'UNKNOWN_ARGUMENT'),
                    ('t.cell(0, 0, "", color.blue)', 'table.cell(t, 0, 0, "", color.blue)', 'TABLE_POSITIONAL_PROFILE'),
                    ('t.clear(table_id=t, start_column=0, start_row=0)',
                     'table.clear(t, table_id=t, start_column=0, start_row=0)', 'DUPLICATE_ARGUMENT')):
                with self.subTest(version=version, instance=instance):
                    self.assertEqual(self.reject(version, setup + instance + '\n'),
                                     self.reject(version, setup + qualified + '\n'))
                    self.assertEqual(self.reject(version, setup + instance + '\n')[1], code)
            body = 'f() =>\n    CALL\n    1\nplot(f())\n'
            self.assertEqual(self.reject(version, setup + body.replace('CALL', 't.clear(0, 0)')),
                             self.reject(version, setup + body.replace('CALL', 'table.clear(t, 0, 0)')))

    def test_v6_formatting_and_constructor_and_receiver_boundaries(self):
        setup = 'var table t = table.new(position.top_left, 2, 2)\n'
        for body in ('t.new(position.bottom_right, 1, 1)', 't.set_xy(0, 0)'):
            self.assertEqual(self.reject(6, setup + body + '\n'), ('LANGUAGE_INVALID', 'METHOD_UNAVAILABLE'))
        call = 't.cell_set_text_formatting(0, 0, text.format_bold + text.format_italic)\n'
        self.assertEqual(self.reject(5, setup + call), ('LANGUAGE_INVALID', 'METHOD_UNAVAILABLE'))
        actual = self.session.execute(self.compile(6, setup + call), data=self.bars)
        self.assertEqual(actual.tables[0].cells[0].text_formatting, 3)

    def test_both_forms_keep_confirmed_preview_and_journals_equal_after_replay_resume(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        objects = ObjectStore(root / 'objects')
        events = [MarketEvent.from_payload(row) for row in market_events(BARS, PRIMARY)]

        def engine(program):
            sink = ResultSink(objects, tempfile.mkdtemp(dir=root))
            self.addCleanup(sink.close)
            for kind in ('labels', 'lines', 'boxes', 'polylines'):
                getattr(sink, 'enable_' + kind)()
            replay = ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, sink,
                                  calculation_profile=INTRABAR)
            return replay, sink

        def state(value):
            preview = value['preview']
            return ({kind: value[kind] for kind in KINDS},
                    None if preview is None else {kind: preview[kind] for kind in KINDS})

        all_states = []
        for instance in (False, True):
            body = drawing_methods_body(instance=instance)
            program = import_program(export_program(self.compile(6, body)))
            first, sink = engine(program)
            for event in events[:3]:
                first.apply(event)
            before = first.status()
            self.assertEqual(before['tables'][0], drawing_methods_table('Header', transient=True))
            self.assertEqual(before['preview']['tables'], [drawing_methods_table('Open')])
            self.assertEqual(len(before['polylines']), 1)
            self.assertEqual(before['preview']['polylines'], [])
            resumed, output = engine(program)
            resumed.restore(first.checkpoint())
            output.restore(sink.checkpoint(1), 1)
            self.assertEqual(resumed.status(), before)
            for event in events[3:]:
                resumed.apply(event)
            final = resumed.status()
            self.assertEqual(final['tables'], [drawing_methods_table()])
            historical = self.session.execute(program, data=self.bars)
            self.assertEqual(final['lines'], [asdict(line) for line in historical.lines])
            publication = output.finish(final)
            events_by_kind = {kind: list(reader(objects, publication[kind + '_journal'], 0, 100))
                              for kind, reader in JOURNALS.items()}
            self.assertTrue(all(events_by_kind.values()))
            self.assertEqual(events_by_kind['polyline'][-1]['event'],
                             {'action': 'delete', 'id': 1, 'reason': 'explicit'})
            all_states.append((state(before), state(final), events_by_kind))
        self.assertEqual(all_states[0], all_states[1])
