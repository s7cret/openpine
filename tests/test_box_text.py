"""Pine box text persists with geometry and rolls back with the open bar."""
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, LiteralIR, OpenPineError, ValueType
from packages.contracts.codec import decode, encode
from packages.contracts.contexts import MarketContext
from packages.contracts.drawings import EVENT_LIMIT
from packages.contracts.model import BoxRecord, BoxSnapshot, TextBoxRecord
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
from tools.box_text_fixtures import LEGACY, START, changes, final_updates, point, source, text_program
from tools.installed_lines_smoke import market_events
from tools.specification import ROOT

PRIMARY = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
BARS = ROOT / 'conformance/box-text/bars.json'


class BoxTextTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        self.events = [MarketEvent.from_payload(row) for row in market_events(BARS, PRIMARY)]

    def test_saved_ir_and_open_bar_checkpoint_preserve_box_text(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        objects = ObjectStore(root / 'objects')
        program = import_program(export_program(self.session.compile(source(6, text_program(6)), 'box-text.pine')))
        result = self.session.execute(program, data=historical(self.events, PRIMARY))
        expected = [final_updates(6)[-1], point(6)]
        self.assertEqual([asdict(box) for box in result.boxes], expected)
        self.assertIsInstance(result.boxes[0], TextBoxRecord)
        self.assertEqual(decode(encode(result), type(result)), result)

        def replay():
            sink = ResultSink(objects, tempfile.mkdtemp(dir=root))
            self.addCleanup(sink.close)
            sink.enable_boxes()
            return ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, sink)

        running = replay()
        for event in self.events[:3]:
            running.apply(event)
        status = running.status()
        self.assertEqual(status['boxes'], [START, point(6)])
        self.assertEqual(status['preview']['boxes'], [changes(6, preview=True)[-1], point(6)])
        resumed = replay()
        resumed.evaluation.output_sink.restore(loads(canonical(running.evaluation.output_sink.checkpoint(1))), 1)
        resumed.restore(loads(canonical(running.checkpoint())))
        self.assertEqual(loads(canonical(resumed.status())), loads(canonical(status)))
        resumed.apply(self.events[3])
        self.assertEqual(resumed.status()['boxes'], [changes(6)[-1], point(6)])
        self.assertIsNone(resumed.status()['preview'])
        for event in self.events[4:]:
            resumed.apply(event)
        self.assertEqual(resumed.status()['boxes'], expected)

    def test_forged_default_and_force_overlay_are_rejected_on_saved_ir(self):
        program = self.session.compile(source(6, 'box.new(0, 35, 2, 5, text="zone")\n'), 'box-text.pine')
        declaration, call = program.statements
        for position, value, kind in ((11, 'large', 'string'), (17, True, 'bool')):
            with self.subTest(position=position), self.assertRaises(OpenPineError) as caught:
                argument = call.arguments[position]
                self.assertEqual(argument.origin, 'default')
                forged = replace(argument, expression=LiteralIR(value, ValueType(kind), call.span),
                                 origin='source' if position == 17 else 'default')
                if position == 17:
                    forged = replace(forged, source_index=5)
                statement = replace(call, arguments=(*call.arguments[:position], forged, *call.arguments[position + 1:]))
                self.session.execute(replace(program, statements=(declaration, statement)),
                                     data=historical(self.events, PRIMARY))
            self.assertEqual(caught.exception.diagnostic.code, 'IR_BOX_TEXT_CALL')

    def test_legacy_state_upgrade_and_rejected_updates_are_atomic(self):
        old = BoxRecord(**LEGACY)
        self.assertEqual(decode(encode(old), BoxSnapshot), old)
        state = dict(next_id=2, event_count=1, live=[LEGACY])
        boxes = Boxes.restore(state, 1)
        self.assertEqual(boxes.snapshot(), [LEGACY])
        self.assertIsNone(boxes.apply('box.set_text', (2, 'absent')))
        self.assertEqual(boxes.checkpoint(), state)
        boxes.apply('box.set_text', (1, 'new'))
        boxes.apply('box.set_extend', (1, 'extend.right'))
        self.assertEqual(boxes.snapshot(), [dict(START, border_color=LEGACY['border_color'], bgcolor=LEGACY['bgcolor'],
                                                text='new', extend='right')])
        before = boxes.checkpoint(), boxes.pending.copy()
        for operation, value, code in (('box.set_text', 'x' * 4097, 'BOX_TEXT'),
                                       ('box.set_text_size', -1, 'BOX_TEXT_SIZE'),
                                       ('box.set_text_formatting', 4, 'BOX_TEXT_FORMATTING')):
            with self.subTest(operation=operation), self.assertRaises(KernelError) as caught:
                boxes.apply(operation, (1, value))
            self.assertEqual(caught.exception.code, code)
            self.assertEqual((boxes.checkpoint(), boxes.pending), before)
        while len(boxes.pending) < EVENT_LIMIT:
            boxes.apply('box.set_text', (1, 'same'))
        before = boxes.checkpoint(), boxes.pending.copy()
        with self.assertRaises(KernelError) as caught:
            boxes.apply('box.set_text', (1, 'overflow'))
        self.assertEqual(caught.exception.code, 'BOX_EVENT_LIMIT')
        self.assertEqual((boxes.checkpoint(), boxes.pending), before)
