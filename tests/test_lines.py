"""Lines and labels retain independent identities through preview and restore."""
import copy
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.codec import decode, encode
from packages.contracts.drawings import LIMIT
from packages.contracts.intrabar import PROFILE as INTRABAR
from packages.contracts.lines import chart_lines
from packages.execution.artifacts import export_program, import_program
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.storage.drawing_journal import mark_drawing_chunks
from packages.storage.line_journal import line_events
from packages.storage.objects import ObjectStore
from packages.storage.results import ResultSink
from tests.test_realtime import PRIMARY, historical, tape
from tools.lines_fixtures import source


class LineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_combined_drawing_replay_restores_independent_streams(self):
        script = source(6)
        program = import_program(export_program(self.session.compile(script, 'drawings.pine')))
        self.assertEqual(program.schema_version, 'openpine.typed-ir.25')
        events = list(tape())
        _, expected = self.session.run(script, 'drawings.pine', data=historical(events))
        self.assertEqual(decode(encode(expected), type(expected)), expected)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            objects = ObjectStore(root / 'objects')

            def engine(name):
                sink = ResultSink(objects, root / name)
                sink.enable_lines()
                sink.enable_labels()
                replay = ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, sink,
                                      calculation_profile=INTRABAR)
                return replay, sink

            replay, sink = engine('first')
            for event in events[:4]:
                replay.apply(event)
            status = replay.status()
            self.assertEqual(status['lines'][0]['x2'], 0)
            self.assertEqual(status['preview']['lines'][0]['x2'], 1)
            self.assertEqual(status['labels'][0]['id'], status['lines'][0]['id'])
            self.assertEqual(sink.line_journal.records, 3)
            self.assertEqual(sink.label_journal.records, 2)
            state = replay.checkpoint()
            self.assertEqual(state['runtime']['schema_version'], 'openpine.runtime-state.17')
            prefix = sink.checkpoint(1)
            self.assertEqual(prefix['schema_version'], 'openpine.output-prefix.5')
            marked = set()
            mark_drawing_chunks(prefix, marked.add)
            self.assertEqual(len(marked), 2)
            resumed, output = engine('resumed')
            resumed.restore(copy.deepcopy(state))
            output.restore(copy.deepcopy(prefix), 1)
            output.validate_lines(resumed.evaluation.lines)
            output.validate_labels(resumed.evaluation.labels)
            self.assertEqual(resumed.status(), status)
            for event in events[4:]:
                resumed.apply(event)
            publication = output.finish(resumed.status())
            self.assertEqual(publication['lines'], [asdict(item) for item in expected.lines])
            self.assertEqual(publication['labels'], [asdict(item) for item in expected.labels])
            rows = list(line_events(objects, publication['line_journal'], 0, 100))
            self.assertEqual([row['bar_index'] for row in rows], [0] * 3 + [1] * 4 + [2] * 2 + [3] * 2)
            self.assertEqual(rows[6]['event'], {'action': 'delete', 'id': 2, 'reason': 'explicit'})

    def test_eviction_and_monotone_line_ids_after_restore(self):
        script = source(6, """var line oldest = line.new(0, close, 1, close)
if bar_index == 0
    for i = 1 to 49
        line.new(i, close, i, close + 1)
if bar_index > 0
    line.new(0, close, 1, close)
line.set_xy2(oldest, 2, 5)
""")
        program = self.session.compile(script, 'line-eviction.pine')
        events = list(tape(2))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sink = ResultSink(ObjectStore(root / 'objects'), root / 'sink')
            sink.enable_lines()
            first = ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, sink)
            for event in events[:3]:
                first.apply(event)
            self.assertEqual(len(first.status()['lines']), LIMIT)
            saved = first.checkpoint()
            resumed = ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, sink)
            resumed.restore(saved)
            for event in events[3:]:
                resumed.apply(event)
            self.assertEqual([record['id'] for record in resumed.status()['lines']], list(range(2, 52)))
            publication = sink.finish(resumed.status())['line_journal']
            self.assertEqual([r['event'] for r in line_events(sink.objects, publication, 51, 2)], [
                {'action': 'delete', 'id': 1, 'reason': 'capacity'},
                {'action': 'create', 'line': {'id': 51, 'x1': 0, 'y1': 13., 'x2': 1, 'y2': 13.}},
            ])

    def test_chart_window_checks_segment_intersection(self):
        def line(i, x1, y1, x2, y2):
            return dict(id=i, x1=x1, y1=y1, x2=x2, y2=y2)

        lines = [line(1, -8, 1., 25, 3.), line(2, 25, 3., -8, 1.), line(3, 4, 5., 4, 2.),
                 line(4, 10, 1., 10, 2.), line(5, None, 1., 4, 3.), line(6, 3, 2., 3, 2.),
                 line(7, -4, 2., 0, 1.)]
        self.assertEqual(chart_lines(lines, 1, 10), lines[:3])
        self.assertEqual(chart_lines(lines, 10, 10), [])
