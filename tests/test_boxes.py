"""Box geometry, bounds and drawing checkpoint isolation."""
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.boxes import chart_boxes
from packages.contracts.codec import decode, encode
from packages.contracts.drawings import LIMIT
from packages.contracts.intrabar import PROFILE as INTRABAR
from packages.execution.artifacts import export_program, import_program
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.runtime.boxes import Boxes
from packages.storage.box_journal import box_events
from packages.storage.drawing_journal import mark_drawing_chunks
from packages.storage.objects import ObjectStore
from packages.storage.results import ResultSink
from tests.test_realtime import PRIMARY, historical, tape
from tools.boxes_fixtures import source


class BoxTests(unittest.TestCase):
    def test_boxes_lines_and_labels_restore_their_own_prefixes(self):
        session = Session(profile_id=HISTORICAL_PROFILE_ID)
        script = source(6)
        program = import_program(export_program(session.compile(script, 'boxes.pine')))
        self.assertEqual(program.schema_version, 'openpine.typed-ir.26')
        events = list(tape())
        _, expected = session.run(script, 'boxes.pine', data=historical(events))
        self.assertEqual(decode(encode(expected), type(expected)), expected)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            objects = ObjectStore(root / 'objects')

            def make(name):
                sink = ResultSink(objects, root / name)
                sink.enable_labels()
                sink.enable_lines()
                sink.enable_boxes()
                return ReplayEngine(session, program, (PRIMARY,), PRIMARY.context_id, sink,
                                    calculation_profile=INTRABAR), sink

            first, sink = make('first')
            for event in events[:4]:
                first.apply(event)
            status = first.status()
            self.assertEqual(status['boxes'][0]['bgcolor'], '#12345680')
            self.assertEqual(status['preview']['boxes'][0]['bgcolor'], '#FF000080')
            self.assertEqual(sink.box_journal.records, 3)
            state, prefix = first.checkpoint(), sink.checkpoint(1)
            self.assertEqual(state['runtime']['schema_version'], 'openpine.runtime-state.18')
            self.assertEqual(prefix['schema_version'], 'openpine.output-prefix.6')
            marked = set()
            mark_drawing_chunks(prefix, marked.add)
            self.assertEqual(len(marked), 3)
            resumed, output = make('resumed')
            resumed.restore(state)
            output.restore(prefix, 1)
            for kind, store in resumed.evaluation.drawing_stores().items():
                getattr(output, f'validate_{kind}')(store)
            self.assertEqual(resumed.status(), status)
            for event in events[4:]:
                resumed.apply(event)
            final = output.finish(resumed.status())
            for kind in ('boxes', 'lines', 'labels'):
                self.assertEqual(final[kind], [asdict(item) for item in getattr(expected, kind)])
            rows = list(box_events(objects, final['box_journal'], 0, 20))
            self.assertEqual([row['bar_index'] for row in rows], [0] * 3 + [1] * 4 + [2] * 3 + [3] * 2)

    def test_box_eviction_and_deleted_references(self):
        boxes = Boxes()
        values = (0, 10., 3, 5., '#2196F3FF', None)
        for i in range(LIMIT):
            self.assertEqual(boxes.apply('box.new', values), i + 1)
        boxes.event_count = LIMIT
        boxes = Boxes.restore(boxes.checkpoint(), 1)
        self.assertEqual(boxes.apply('box.new', values), LIMIT + 1)
        self.assertEqual(boxes.pending[0], {'action': 'delete', 'id': 1, 'reason': 'capacity'})
        self.assertNotIn(1, boxes.live)
        boxes.apply('box.set_bgcolor', (1, '#123456FF'))
        boxes.apply('box.delete', (1,))
        self.assertEqual(len(boxes.pending), 2)

    def test_window_keeps_reversed_and_degenerate_geometry(self):
        def zone(i, left, top, right, bottom):
            return dict(id=i, left=left, top=top, right=right, bottom=bottom,
                        border_color='#2196F3FF', bgcolor=None)
        boxes = [zone(1, 9, 5., -3, 10.), zone(2, 2, 7., 2, 5.), zone(3, 1, 7., 4, 7.),
                 zone(4, 2, 7., 2, 7.), zone(5, None, 7., 4, 6.), zone(6, 5, 7., 9, 6.)]
        self.assertEqual(chart_boxes(boxes, 2, 5), boxes[:3])
        self.assertEqual(chart_boxes(boxes, 2, 2), [])
