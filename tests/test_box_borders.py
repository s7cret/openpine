"""Durable box borders and extension visibility."""
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, LiteralIR, OpenPineError, ValueType
from packages.contracts.boxes import chart_boxes
from packages.contracts.chart_points import IR_SCHEMA
from packages.contracts.codec import decode, encode
from packages.contracts.contexts import MarketContext
from packages.contracts.drawings import EVENT_LIMIT
from packages.contracts.model import BoxRecord, BoxSnapshot, StyledBoxRecord
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.runtime.boxes import Boxes
from packages.runtime.series import KernelError
from packages.storage.objects import ObjectStore
from packages.storage.results import ResultSink
from tests.test_realtime import historical
from tools.box_borders_fixtures import CLOSED, FINAL_TIME, LEGACY, POINTS, PREVIEW, START, appearance, source
from tools.installed_lines_smoke import market_events
from tools.specification import ROOT


PRIMARY = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
BARS = ROOT / 'conformance/box-borders/bars.json'


class BoxBorderTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        self.events = [MarketEvent.from_payload(event) for event in market_events(BARS, PRIMARY)]

    def test_saved_ir_result_and_open_bar_checkpoint_preserve_borders(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        objects = ObjectStore(root / 'objects')
        program = import_program(export_program(self.session.compile(source(6, appearance(6)), 'border.pine')))
        self.assertEqual(program.schema_version, IR_SCHEMA)
        result = self.session.execute(program, data=historical(self.events, PRIMARY))
        self.assertEqual([asdict(box) for box in result.boxes], [FINAL_TIME, POINTS])
        self.assertEqual(decode(encode(result), type(result)), result)
        self.assertIsInstance(result.boxes[0], StyledBoxRecord)

        def make():
            sink = ResultSink(objects, tempfile.mkdtemp(dir=root))
            self.addCleanup(sink.close)
            sink.enable_boxes()
            return ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, sink)

        running = make()
        for event in self.events[:3]:
            running.apply(event)
        status = loads(canonical(running.status()))
        self.assertEqual(status['boxes'], [START, POINTS])
        self.assertEqual(status['preview']['boxes'], [PREVIEW, POINTS])
        prefix = loads(canonical(running.evaluation.output_sink.checkpoint(1)))
        checkpoint = loads(canonical(running.checkpoint()))
        resumed = make()
        resumed.evaluation.output_sink.restore(prefix, 1)
        resumed.restore(checkpoint)
        self.assertEqual(loads(canonical(resumed.status())), status)
        resumed.apply(self.events[3])
        self.assertEqual(resumed.status()['boxes'][0], CLOSED)
        for event in self.events[4:]:
            resumed.apply(event)
        self.assertEqual(resumed.status()['boxes'], [FINAL_TIME, POINTS])

    def test_admission_rejects_a_forged_constructor_default(self):
        program = self.session.compile(source(4, 'box.new(0, 30, 1, 10, extend=extend.right)\n'), 'border.pine')
        declaration, constructor = program.statements
        default = constructor.arguments[5]
        self.assertEqual(default.origin, 'default')
        forged = replace(default, expression=LiteralIR(2, ValueType('int'), constructor.span))
        statement = replace(constructor, arguments=(*constructor.arguments[:5], forged, *constructor.arguments[6:]))
        with self.assertRaises(OpenPineError) as caught:
            self.session.execute(replace(program, statements=(declaration, statement)), data=historical(self.events, PRIMARY))
        self.assertEqual(caught.exception.diagnostic.code, 'IR_BOX_BORDER_CALL')

    def test_old_records_and_failed_mutations_keep_their_shape(self):
        boxes = Boxes()
        identity = boxes.apply('box.new', (0, 30., 1, 10., '#2196F3FF', '#2196F3FF'))
        old = BoxRecord(**LEGACY)
        self.assertEqual(decode(encode(old), BoxSnapshot), old)
        old_checkpoint = dict(next_id=2, event_count=1, live=[LEGACY])
        self.assertEqual(Boxes.restore(old_checkpoint, 1).snapshot(), [LEGACY])
        before = boxes.checkpoint(), boxes.pending.copy()
        invalid = (('box.set_border_width', None, 'BOX_BORDER_WIDTH'), ('box.set_extend', 'right', 'BOX_EXTEND'),
                   ('box.set_border_style', 'line.style_arrow_left', 'BOX_BORDER_STYLE'))
        for operation, value, code in invalid:
            with self.subTest(operation=operation), self.assertRaises(KernelError) as caught:
                boxes.apply(operation, (identity, value))
            self.assertEqual(caught.exception.code, code)
            self.assertEqual((boxes.checkpoint(), boxes.pending), before)
        while len(boxes.pending) < EVENT_LIMIT:
            boxes.apply('box.set_lefttop', (identity, 0, 30.))
        before = boxes.checkpoint(), boxes.pending.copy()
        with self.assertRaises(KernelError) as caught:
            boxes.apply('box.set_extend', (identity, 'extend.both'))
        self.assertEqual(caught.exception.code, 'BOX_EVENT_LIMIT')
        self.assertEqual((boxes.checkpoint(), boxes.pending), before)

    def test_extension_visibility_uses_projected_anchors_and_visible_border(self):
        right = {**FINAL_TIME, 'chart_left': 1.5, 'chart_right': .5}
        self.assertEqual(chart_boxes([right], 2, 4), [right])
        self.assertEqual(chart_boxes([{**right, 'extend': 'left'}], 2, 4), [])
        self.assertEqual(chart_boxes([{**right, 'border_color': '#E0112200'}], 2, 4), [])
        self.assertEqual(chart_boxes([{**right, 'chart_left': .5}], 2, 4), [])
        self.assertEqual(chart_boxes([{**right, 'chart_left': None}], 2, 4), [])
