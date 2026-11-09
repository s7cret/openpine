"""Drawing counts stay pinned across binding, eviction, rollback and restore."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from apps.openpine.bootstrap import local_manager
from apps.openpine.replay import Replays
from packages.contracts import BarFrame, HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.drawings import EVENT_LIMIT
from packages.contracts.polylines import MAX_BAR_VERTICES, MAX_VERTICES
from packages.contracts.strategy_ir import broker_policy
from packages.contracts.visual_order import VisualOrder
from packages.execution.artifacts import export_program, import_program
from packages.execution.host_memory import HostAdmission
from packages.execution.session import Session
from packages.marketdata import read_bars
from packages.runtime.lines import Lines
from packages.runtime.polylines import Polylines
from packages.runtime.series import KernelError
from tests.test_realtime import configuration, tape


class DrawingLimitsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.fixtures = Path('conformance/linefills')

    def test_versions_range_types_and_constant_expression(self):
        for version in range(1, 7):
            declaration = 'study' if version < 5 else 'indicator'
            for kind in (declaration, 'strategy'):
                header = f'//@version={version}\n{kind}("Limits", '
                if kind == 'strategy':
                    header += 'default_qty_type=strategy.fixed, '
                fields = ('max_lines_count', 'max_labels_count', 'max_boxes_count', 'max_polylines_count')
                for name in fields:
                    script = header + f'{name}=1)\nplot(close)\n'
                    with self.subTest(version=version, kind=kind, name=name):
                        if version < 4 or (version == 4 and name == fields[-1]):
                            with self.assertRaises(OpenPineError) as error:
                                self.session.compile(script)
                            self.assertEqual(error.exception.diagnostic.code, 'DRAWING_LIMIT_VERSION')
                        else:
                            self.session.compile(script)
        for option, code in (
            ('max_lines_count=1.0', 'ARGUMENT_TYPE'), ('max_lines_count=true', 'ARGUMENT_TYPE'),
            ('max_lines_count=bar_index', 'ARGUMENT_QUALIFIER'), ('max_lines_count=na', 'DRAWING_LIMIT_VALUE'),
            ('max_labels_count=0', 'DRAWING_LIMIT_VALUE'), ('max_boxes_count=-1', 'DRAWING_LIMIT_VALUE'),
            ('max_lines_count=501', 'DRAWING_LIMIT_VALUE'), ('max_polylines_count=101', 'DRAWING_LIMIT_VALUE'),
        ):
            with self.subTest(option=option), self.assertRaises(OpenPineError) as error:
                self.session.compile(f'//@version=6\nindicator("Limits", {option})\nplot(close)\n')
            self.assertEqual(error.exception.diagnostic.code, code)
        program = self.session.compile('//@version=6\nindicator("Limits", max_lines_count=500+1)\nplot(close)\n')
        with self.assertRaises(OpenPineError) as error:
            self.session.validate_program(program).prepare(program)
        self.assertEqual(error.exception.diagnostic.code, 'DRAWING_LIMIT_VALUE')

    def test_saved_ir_combines_drawing_limits_with_existing_options(self):
        for kind in ('indicator', 'strategy'):
            with self.subTest(kind=kind):
                base = f'//@version=6\n{kind}("Limits", overlay=true, '
                if kind == 'strategy':
                    base += 'default_qty_type=strategy.fixed, default_qty_value=2, '
                base += 'scale=scale.left, precision=4, behind_chart=false, explicit_plot_zorder=true'
                body = ')\nplot(close)\n'
                if kind == 'indicator':
                    body += 'line.new(bar_index, close, bar_index+1, close)\n'
                default = self.session.compile(base + body)
                program = self.session.compile(base + ', max_lines_count=1+1' + body)
                restored = import_program(export_program(program))
                result = self.session.execute(restored, data=read_bars(self.fixtures / 'bars.json'))
                if kind == 'strategy':
                    self.assertEqual(broker_policy(restored), broker_policy(default))
                else:
                    self.assertEqual([line.id for line in result.lines], [3, 4])
                self.assertEqual(result.declaration.visual_order, VisualOrder(True, False))
                self.assertEqual(result.declaration.chart_scale, 'left')
                self.assertEqual(result.declaration.number_format.declaration.precision, 4)
                self.assertEqual(result.outputs[0].values, (10., 20., 30., 40.))

    def test_upper_counts_and_default_checkpoint_payload(self):
        for limit, bars, ordinary, polylines in (
            ('', 51, list(range(2, 52)), list(range(2, 52))),
            (', max_lines_count=500, max_labels_count=500, max_boxes_count=500, max_polylines_count=100',
             501, list(range(2, 502)), list(range(402, 502))),
        ):
            with self.subTest(limit=limit):
                program = self.session.compile(f'''//@version=6
indicator("Counts"{limit})
line.new(bar_index, close, bar_index+1, close)
label.new(bar_index, na, "hidden")
box.new(bar_index, close, bar_index+1, 0)
var array<chart.point> points = array.from(chart.point.from_index(0, 12), chart.point.from_index(3, 18))
polyline.new(points)
''')
                runtime = self.session.validate_program(program)
                first = runtime.prepare(program)
                for index in range(bars):
                    first.step(BarFrame('limits', index, index * 60000, 10.))
                state = first.checkpoint()
                resumed = runtime.prepare(program)
                resumed.restore(state)
                self.assertEqual(resumed.checkpoint(), state)
                for kind, identities in (('lines', ordinary), ('labels', ordinary), ('boxes', ordinary),
                                         ('polylines', polylines)):
                    self.assertEqual(set(state[kind]), {'next_id', 'event_count', 'live'})
                    self.assertEqual([row['id'] for row in state[kind]['live']], identities)
                    self.assertEqual(state[kind]['next_id'], bars + 1)

    def test_admission_and_restore_reject_forged_limits(self):
        source = '//@version=6\nindicator("Limits", max_lines_count=1)\nline.new(0, 10, 1, 20)\n'
        program = self.session.compile(source)
        header = program.statements[0]
        argument = header.arguments[-1]
        self.assertEqual(argument.origin, 'default')
        forged = replace(header, arguments=(*header.arguments[:-1],
            replace(argument, expression=replace(argument.expression, value=100))))
        runtime = self.session.validate_program(program)
        with self.assertRaises(OpenPineError) as error:
            runtime.prepare(replace(program, statements=(forged, *program.statements[1:])))
        self.assertEqual(error.exception.diagnostic.code, 'IR_DRAWING_LIMITS')
        first = runtime.prepare(program)
        for index in range(2):
            first.step(BarFrame('limits', index, index * 60000, 10.))
        state = first.checkpoint()
        line = state['lines']['live'][0]
        state['lines']['live'].insert(0, {**line, 'id': 1})
        with self.assertRaisesRegex(ValueError, 'Invalid live line snapshot'):
            runtime.prepare(program).restore(state)

    def test_preview_eviction_isolated_and_confirmed_journal_reopens(self):
        source = (self.fixtures / 'v6-drawing-limits.pine').read_text(encoding='utf-8')
        host = HostAdmission(probe=lambda: None)
        with TemporaryDirectory() as directory:
            root = Path(directory) / 'store'
            with local_manager(root, host_admission=host) as manager:
                replay = Replays(manager)
                identifier = replay.create(configuration(manager, source))['replay_id']
                replay.append(identifier, [event.payload() for event in tape(4)])
                replay.advance(identifier, max_events=4, checkpoint_every=1)
                opened = replay.view(identifier, 'chart', 0, 1)
                self.assertEqual([row['id'] for row in opened['lines']], [1, 2])
                self.assertEqual([row['id'] for row in opened['preview']['lines']], [2, 3])
                self.assertEqual(len(opened['linefills']), 1)
                self.assertEqual(opened['preview']['linefills'], [])
                self.assertEqual(replay.view(identifier, 'line_events', limit=20)['total'], 2)
            with local_manager(root, host_admission=host) as manager:
                replay = Replays(manager)
                self.assertEqual(replay.view(identifier, 'chart', 0, 1), opened)
                replay.advance(identifier, checkpoint_every=1)
                closed = replay.view(identifier, 'chart', 1, 2)
                self.assertIsNone(closed['preview'])
                self.assertEqual(closed['lines'], opened['preview']['lines'])
                self.assertEqual(closed['linefills'], [])
                self.assertEqual([row['id'] for row in closed['polylines']], [4])
                events = replay.view(identifier, 'line_events', limit=20)['events']
                self.assertEqual([row['event']['action'] for row in events],
                                 ['create', 'create', 'update', 'delete', 'create'])
                self.assertEqual(events[-2]['event'], {'action': 'delete', 'id': 1, 'reason': 'capacity'})

    def test_count_eviction_preserves_per_bar_event_and_vertex_budgets(self):
        lines = Lines(capacity=1)
        first = lines.apply('line.new', (0, 1., 1, 2.))
        for _ in range(EVENT_LIMIT - 2):
            lines.apply('line.set_xy1', (first, 0, 3.))
        before = lines.checkpoint(), list(lines.pending)
        with self.assertRaisesRegex(KernelError, 'events'):
            lines.apply('line.new', (0, 5., 1, 6.))
        self.assertEqual((lines.checkpoint(), lines.pending), before)
        polylines = Polylines(capacity=1)
        record = dict(vertices=tuple((i, 1.) for i in range(MAX_VERTICES)), closed=False,
                      xloc='bar_index', line_color=None, fill_color=None)
        for _ in range(MAX_BAR_VERTICES // MAX_VERTICES):
            polylines.reserve_vertices(MAX_VERTICES, False)
            polylines.create(record)
        before = polylines.checkpoint(), list(polylines.pending)
        with self.assertRaisesRegex(KernelError, 'vertex budget'):
            polylines.reserve_vertices(2, False)
        self.assertEqual((polylines.checkpoint(), polylines.pending), before)
