"""Typed, read-only snapshots of live Pine drawing IDs."""

from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from apps.openpine.bootstrap import local_manager
from apps.openpine.replay import Replays
from packages.contracts import BarFrame, HISTORICAL_PROFILE_ID, OpenPineError, ValueType
from packages.execution.artifacts import export_program, import_program
from packages.execution.host_memory import HostAdmission
from packages.execution.session import Session
from packages.marketdata import read_bars
from tests.test_realtime import configuration, historical, tape
from tools.linefills_fixtures import INVENTORY_FINAL


FIXTURES = Path('conformance/linefills')


class DrawingInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.bars = read_bars(FIXTURES / 'bars.json')

    def test_all_six_inventories_have_typed_ids_and_empty_results_after_deletion(self):
        body = '''indicator("Inventory")
line left = line.new(0, 20, 3, 20)
line right = line.new(0, 5, 3, 5)
linefill.new(left, right, #336699)
label.new(na, na)
box.new(0, 20, 3, 5)
table oldest = table.new(position.top_right, 1, 1)
table[] tables = table.all
table.new(position.top_right, 1, 1)
table.delete(array.get(tables, 0))
var array<chart.point> points = array.from(chart.point.from_index(0, 12), chart.point.from_index(3, 18))
polyline.new(points)
plot(array.size(line.all), "line")
plot(array.size(label.all), "label")
plot(array.size(box.all), "box")
plot(array.size(table.all), "table")
plot(array.size(linefill.all), "linefill")
plot(array.size(polyline.all), "polyline")
linefill.delete(array.get(linefill.all, 0))
label.delete(array.get(label.all, 0))
box.delete(array.get(box.all, 0))
table.delete(array.get(table.all, 0))
polyline.delete(array.get(polyline.all, 0))
array<line> lines = line.all
for item in lines
    line.delete(item)
plot(array.size(lines), "snapshot")
plot(array.size(line.all) + array.size(label.all) + array.size(box.all) +
 array.size(table.all) + array.size(linefill.all) + array.size(polyline.all), "live")
'''
        for version in (5, 6):
            with self.subTest(version=version):
                program = self.session.compile(f'//@version={version}\n' + body)
                result = self.session.execute(import_program(export_program(program)), data=self.bars)
                self.assertEqual({out.title: out.values for out in result.outputs}, {
                    name: (count,) * 4 for name, count in dict(line=2, label=1, box=1, table=1,
                    linefill=1, polyline=1, snapshot=2, live=0).items()})
                self.assertTrue(all(not getattr(result, kind) for kind in
                                    ('lines', 'labels', 'boxes', 'tables', 'linefills', 'polylines')))

    def test_version_mutation_persistence_and_foreign_types_reject(self):
        for version in range(1, 5):
            for kind in ('line', 'label', 'box', 'table', 'linefill', 'polyline'):
                with self.subTest(version=version, kind=kind), self.assertRaises(OpenPineError) as error:
                    self.session.compile(f'//@version={version}\nstudy("Inventory")\nids = {kind}.all\nplot(close)\n')
                self.assertEqual(error.exception.diagnostic.code, 'DRAWING_ALL_VERSION')
        for source, code in (
            ('ids = line.all\narray.push(ids, line.new(0, 5, 3, 5))', 'DRAWING_ARRAY_READ_ONLY'),
            ('array.clear(line.all)', 'DRAWING_ARRAY_READ_ONLY'),
            ('var ids = line.all', 'DRAWING_ARRAY_SCOPE'),
            ('varip ids = line.all', 'DRAWING_ARRAY_SCOPE'),
            ('ids = line.all\nprevious = ids[1]', 'HISTORY_SOURCE'),
            ('size() => array.size(line.all)\nvalue = size()', 'DRAWING_ARRAY_SCOPE'),
            ('line.delete(array.get(box.all, 0))', 'ARGUMENT_TYPE'),
            ('value = request.security(syminfo.tickerid, "1", array.size(line.all))', 'LINE_REQUEST'),
        ):
            with self.subTest(source=source), self.assertRaises(OpenPineError) as error:
                self.session.compile('//@version=6\nindicator("Inventory")\n' + source + '\nplot(close)\n')
            self.assertEqual(error.exception.diagnostic.code, code)

    def test_index_rules_follow_pine_version_and_mutating_ir_is_rejected(self):
        body = '''indicator("Inventory")
line.new(0, 10, 3, 20)
line.new(0, 20, 3, 30)
count = array.size(line.all)
plot(line.get_price(array.get(line.all, -1), 3))
'''
        with self.assertRaises(OpenPineError) as error:
            self.session.run('//@version=5\n' + body, data=self.bars)
        self.assertEqual(error.exception.diagnostic.code, 'ARRAY_INDEX')
        program, result = self.session.run('//@version=6\n' + body, data=self.bars)
        self.assertEqual(result.outputs[0].values, (30.,) * 4)
        store = program.statements[3]
        forged = replace(store, expression=replace(store.expression, operation='clear', value_type=ValueType('void')))
        with self.assertRaises(OpenPineError) as error:
            self.session.validate_program(replace(program, statements=(*program.statements[:3], forged, *program.statements[4:])))
        self.assertEqual(error.exception.diagnostic.code, 'IR_ARRAY_CONTRACT')

    def test_snapshot_work_is_charged_by_number_of_ids(self):
        source = '''//@version=6
indicator("Inventory", max_lines_count=500)
if bar_index == 0
    for i = 0 to 499
        line.new(0, 10, 3, 20)
if bar_index == 1
    for i = 0 to 2000
        count = array.size(line.all)
plot(array.size(line.all))
'''
        program = self.session.compile(source)
        evaluation = self.session.validate_program(program).prepare(program)
        evaluation.step(BarFrame('inventory', 0, 0, 10.))
        with self.assertRaises(OpenPineError) as error:
            evaluation.step(BarFrame('inventory', 1, 60000, 20.))
        self.assertEqual(error.exception.diagnostic.code, 'OPERATION_BUDGET')

    def test_eviction_inventory_and_linefill_survive_durable_replay(self):
        source = (FIXTURES / 'v6-drawing-inventory.pine').read_text(encoding='utf-8')
        events = list(tape(4))
        _, history = self.session.run(source, 'replay.pine', data=historical(events))
        self.assertEqual([asdict(line) for line in history.lines], [INVENTORY_FINAL])
        self.assertEqual(history.linefills, ())
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
                self.assertEqual([line['id'] for line in opened['preview']['lines']], [2, 3])
                self.assertEqual(len(opened['linefills']), 1)
                self.assertEqual(opened['preview']['linefills'], [])
                self.assertEqual([row['value'] for row in opened['preview']['outputs']], [2, 25., 0])
                self.assertEqual(replay.view(identifier, 'line_events', limit=20)['total'], 4)
            with local_manager(root, host_admission=host) as manager:
                replay = Replays(manager)
                self.assertEqual(replay.view(identifier, 'chart', 0, 1), opened)
                replay.advance(identifier, checkpoint_every=1)
                closed = replay.view(identifier, 'chart', 0, 4)
                self.assertEqual(closed['lines'], [INVENTORY_FINAL])
                self.assertEqual(closed['linefills'], [])
                self.assertIsNone(closed['preview'])
                events = replay.view(identifier, 'line_events', limit=20)['events']
                self.assertEqual([row['event'] for row in events if row['event']['action'] == 'delete'], [
                    {'action': 'delete', 'id': 1, 'reason': 'capacity'},
                    {'action': 'delete', 'id': 2, 'reason': 'explicit'},
                ])
