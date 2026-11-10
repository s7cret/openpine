"""Single-field drawing access follows the existing version and lifecycle rules."""

from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from apps.openpine.bootstrap import local_manager
from apps.openpine.replay import Replays
from packages.contracts import BarFrame, HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.drawing_properties import OPERATIONS
from packages.contracts.drawings import EVENT_LIMIT
from packages.execution.artifacts import export_program, import_program
from packages.execution.host_memory import HostAdmission
from packages.execution.session import Session
from packages.marketdata import read_bars
from packages.runtime.intrabar import IntrabarMemory
from packages.runtime.lines import Lines
from packages.runtime.series import KernelError
from tests.test_realtime import configuration, historical, tape
from tools.linefills_fixtures import PROPERTY_SURVIVORS


FIXTURES = Path('conformance/linefills')


class DrawingPropertyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.bars = read_bars(FIXTURES / 'bars.json')

    def test_single_field_writes_keep_modes_appearance_and_raw_prices(self):
        source = '''//@version=6
indicator("Properties", overlay=false)
var line guide = line.new(0, 10, 3, 20, width=4, style=line.style_dashed, force_overlay=true)
var label tag = label.new(0, 5, "seed", xloc=xloc.bar_time, yloc=yloc.abovebar, tooltip="tag")
var box zone = box.new(0, 25, 180000, 6, xloc=xloc.bar_time, border_width=3, text="zone")
if bar_index == 1
    line alias = guide
    line.set_x2(alias, 2)
    line.set_y1(alias, 14)
    label.set_x(tag, 60000)
    label.set_y(tag, 11)
    box.set_left(zone, 60000)
    box.set_bottom(zone, 8)
plot(line.get_y1(guide))
plot(label.get_y(tag))
plot(box.get_bottom(zone))
'''
        program = import_program(export_program(self.session.compile(source)))
        result = self.session.execute(program, data=self.bars)
        self.assertEqual([row.values for row in result.outputs],
                         [(10., 14., 14., 14.), (5., 11., 11., 11.), (6., 8., 8., 8.)])
        line, label, box = map(asdict, (result.lines[0], result.labels[0], result.boxes[0]))
        self.assertEqual((line['xloc'], line['x2'], line['y2'], line['width'], line['style'], line['force_overlay']),
                         ('bar_index', 2, 20., 4, 'dashed', True))
        self.assertEqual((label['xloc'], label['x'], label['yloc'], label['text'], label['tooltip']),
                         ('bar_time', 60000, 'abovebar', 'seed', 'tag'))
        self.assertEqual((box['xloc'], box['left'], box['right'], box['top'], box['border_width'], box['text']),
                         ('bar_time', 60000, 180000, 25., 3, 'zone'))

    def test_nullable_label_text_round_trips_without_reference_heap(self):
        for version in (4, 5, 6):
            with self.subTest(version=version):
                declaration = 'study' if version == 4 else 'indicator'
                source = f'''//@version={version}
{declaration}("Text", max_labels_count=2)
var label evicted = label.new(0, 7, "retired")
var label removed = label.new(1, 8, "deleted")
var label empty = label.new(2, 9)
var string initial = label.get_text(empty)
var string gone = label.get_text(evicted)
varip string during = label.get_text(na)
label.delete(removed)
gone := label.get_text(removed)
label.set_x(removed, 99)
plot(label.get_y(removed))
'''
                program = self.session.compile(source)
                runtime = self.session.validate_program(program)
                first = runtime.prepare(program)
                self.assertIsNone(first.heap)
                first.step(BarFrame('properties', 0, 0, 10.))
                ids = {binding.name: binding.binding_id for binding in program.bindings}
                state = first.checkpoint()
                self.assertEqual(state['persistent'][ids['initial']], '')
                self.assertIsNone(state['persistent'][ids['gone']])
                self.assertIsNone(state['persistent'][ids['during']])
                self.assertEqual(state['labels']['live'], [dict(id=3, x=2, y=9., text='')])
                resumed = runtime.prepare(program)
                resumed.restore(state)
                self.assertEqual(resumed.checkpoint(), state)
                memory = IntrabarMemory()
                memory.capture(first, first)
                values, heap = memory.decode(resumed)
                self.assertIsNone(heap)
                self.assertEqual(values, {ids['during']: None})

    def test_versions_wrong_types_and_forged_defaults(self):
        for version in (1, 2, 3):
            for name, parameters in OPERATIONS.items():
                kind = name.partition('.')[0]
                args = 'na, 1' if len(parameters) == 2 else 'na'
                with self.subTest(version=version, name=name), self.assertRaises(OpenPineError) as error:
                    self.session.compile(f'//@version={version}\nstudy("Access")\n{name}({args})\nplot(close)\n')
                self.assertEqual(error.exception.diagnostic.code, kind.upper() + '_VERSION')
        for call in ('line.set_x1(na, 1.5)', 'box.set_top(na, "bad")',
                     'label.get_text(box.new(0, 2, 3, 1))'):
            with self.subTest(call=call), self.assertRaises(OpenPineError) as error:
                self.session.compile(f'//@version=6\nindicator("Access")\n{call}\nplot(close)\n')
            self.assertEqual(error.exception.diagnostic.code, 'ARGUMENT_TYPE')
        program = self.session.compile('//@version=6\nindicator("Access")\nline.set_y1(na, 1)\nplot(close)\n')
        call = program.statements[1]
        forged = replace(call, arguments=(call.arguments[0], replace(call.arguments[1], origin='default', source_index=None)))
        with self.assertRaises(OpenPineError) as error:
            self.session.validate_program(replace(program, statements=(program.statements[0], forged, program.statements[2])))
        self.assertEqual(error.exception.diagnostic.code, 'IR_DRAWING_PROPERTY')

    def test_getter_and_stale_write_consume_no_events_at_quota(self):
        store = Lines(capacity=1)
        retired = store.apply('line.new', (0, 10., 3, 20.))
        live = store.apply('line.new', (0, 15., 3, 25.))
        for _ in range(EVENT_LIMIT - 3):
            store.apply('line.set_y2', (live, 30.))
        before = store.checkpoint(), list(store.pending)
        store.apply('line.set_x2', (retired, 9))
        self.assertIsNone(store.apply('line.get_y2', (retired,)))
        self.assertEqual(store.apply('line.get_y2', (live,)), 30.)
        with self.assertRaisesRegex(KernelError, 'events'):
            store.apply('line.set_y1', (live, 99.))
        self.assertEqual((store.checkpoint(), store.pending), before)

    def test_preview_eviction_and_single_field_journal_survive_reopen(self):
        source = (FIXTURES / 'v6-drawing-properties-copy.pine').read_text(encoding='utf-8')
        events = list(tape(4))
        _, result = self.session.run(source, 'replay.pine', data=historical(events))
        self.assertEqual([asdict(line) for line in result.lines], PROPERTY_SURVIVORS)
        self.assertEqual(result.linefills, ())
        host = HostAdmission(probe=lambda: None)
        with TemporaryDirectory() as directory:
            root = Path(directory) / 'store'
            with local_manager(root, host_admission=host) as manager:
                replay = Replays(manager)
                identifier = replay.create(configuration(manager, source))['replay_id']
                replay.append(identifier, [event.payload() for event in events])
                replay.advance(identifier, max_events=4, checkpoint_every=1)
                opened = replay.view(identifier, 'chart', 0, 1)
                self.assertEqual([line['id'] for line in opened['lines']], [1, 2])
                self.assertEqual(opened['lines'][1]['y2'], 10.)
                self.assertEqual(opened['preview']['lines'], PROPERTY_SURVIVORS)
                self.assertEqual(len(opened['linefills']), 1)
                self.assertEqual(opened['preview']['linefills'], [])
                self.assertEqual({row['title']: row['value'] for row in opened['preview']['outputs']},
                                 {'original': None, 'copy': 25., 'lines': 2, 'fills': 0})
                self.assertEqual(replay.view(identifier, 'line_events', limit=20)['total'], 4)
            with local_manager(root, host_admission=host) as manager:
                replay = Replays(manager)
                self.assertEqual(replay.view(identifier, 'chart', 0, 1), opened)
                replay.advance(identifier, checkpoint_every=1)
                closed = replay.view(identifier, 'chart', 0, 4)
                self.assertEqual(closed['lines'], PROPERTY_SURVIVORS)
                self.assertEqual(closed['linefills'], [])
                self.assertIsNone(closed['preview'])
                events = replay.view(identifier, 'line_events', limit=20)['events']
                self.assertEqual([row['event']['action'] for row in events],
                                 ['create', 'create', 'update', 'update', 'update', 'update', 'delete', 'create'])
                self.assertEqual(events[-2]['event'], {'action': 'delete', 'id': 1, 'reason': 'capacity'})
