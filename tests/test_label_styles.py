"""Price label appearance through closed records, resume and preview rollback."""
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, LiteralIR, OpenPineError, ValueType
from packages.contracts.chart_points import IR_SCHEMA
from packages.contracts.codec import decode, encode
from packages.contracts.contexts import MarketContext
from packages.contracts.drawings import EVENT_LIMIT
from packages.contracts.model import LabelRecord, LabelSnapshot, LocatedLabelRecord, StyledLabelRecord
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.runtime.labels import Labels
from packages.runtime.series import KernelError
from packages.storage.objects import ObjectStore
from packages.storage.results import ResultSink
from tests.test_realtime import historical
from tools.installed_lines_smoke import market_events
from tools.label_styles_fixtures import (
    CLOSED, LEGACY, POINT, PREVIEW, START, appearance, changed_size, final_label, source,
)
from tools.specification import ROOT


PRIMARY = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
BARS = ROOT / 'conformance/label-styles/bars.json'


class LabelStyleTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        self.events = [MarketEvent.from_payload(event) for event in market_events(BARS, PRIMARY)]

    def test_saved_ir_result_and_open_bar_checkpoint_preserve_appearance(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        objects = ObjectStore(root / 'objects')
        program = import_program(export_program(self.session.compile(source(6, appearance(6)), 'label.pine')))
        self.assertEqual(program.schema_version, IR_SCHEMA)
        result = self.session.execute(program, data=historical(self.events, PRIMARY))
        self.assertEqual([asdict(label) for label in result.labels], [final_label(6), POINT])
        self.assertEqual(decode(encode(result), type(result)), result)
        self.assertIsInstance(result.labels[0], StyledLabelRecord)

        def make():
            sink = ResultSink(objects, tempfile.mkdtemp(dir=root))
            self.addCleanup(sink.close)
            sink.enable_labels()
            return ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, sink)

        running = make()
        for event in self.events[:3]:
            running.apply(event)
        status = loads(canonical(running.status()))
        self.assertEqual(status['labels'], [START, POINT])
        self.assertEqual(status['preview']['labels'], [changed_size(PREVIEW, 6, preview=True), POINT])
        prefix = loads(canonical(running.evaluation.output_sink.checkpoint(1)))
        checkpoint = loads(canonical(running.checkpoint()))
        resumed = make()
        resumed.evaluation.output_sink.restore(prefix, 1)
        resumed.restore(checkpoint)
        self.assertEqual(loads(canonical(resumed.status())), status)
        resumed.apply(self.events[3])
        self.assertEqual(resumed.status()['labels'], [changed_size(CLOSED, 6), POINT])
        for event in self.events[4:]:
            resumed.apply(event)
        self.assertEqual(resumed.status()['labels'], [final_label(6), POINT])

    def test_admission_rejects_forged_default_and_location(self):
        program = self.session.compile(source(4, 'label.new(0, 10, style=label.style_label_left)\n'), 'label.pine')
        declaration, constructor = program.statements
        for index, value, base, origin in ((4, 'yloc.abovebar', 'string', 'default'), (5, '#FF0000FF', 'color', 'default')):
            with self.subTest(index=index):
                argument = constructor.arguments[index]
                self.assertEqual(argument.origin, origin)
                forged = replace(argument, expression=LiteralIR(value, ValueType(base), constructor.span))
                statement = replace(constructor, arguments=(*constructor.arguments[:index], forged, *constructor.arguments[index+1:]))
                with self.assertRaises(OpenPineError) as caught:
                    self.session.execute(replace(program, statements=(declaration, statement)),
                                         data=historical(self.events, PRIMARY))
                self.assertEqual(caught.exception.diagnostic.code, 'IR_LABEL_STYLE_CALL')

    def test_old_records_and_failed_updates_keep_their_shape(self):
        for record in (LabelRecord(**LEGACY), LocatedLabelRecord(**LEGACY, xloc='bar_index')):
            raw = asdict(record)
            self.assertEqual(decode(encode(record), LabelSnapshot), record)
            labels = Labels.restore(dict(next_id=2, event_count=1, live=[raw]), 1)
            self.assertEqual(labels.snapshot(), [raw])
            self.assertEqual(labels.result_records(), (record,))
        for operation, value, code in (('label.set_size', -1, 'LABEL_SIZE'),
                                       ('label.set_size', None, 'LABEL_SIZE'),
                                       ('label.set_style', 'up', 'LABEL_STYLE')):
            with self.subTest(operation=operation, value=value), self.assertRaises(KernelError) as caught:
                labels.apply(operation, (1, value))
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(labels.snapshot(), [raw])
            self.assertEqual(labels.pending, [])
        for _ in range(EVENT_LIMIT):
            labels.apply('label.set_text', (1, 'start'))
        before = labels.checkpoint(), labels.pending.copy()
        with self.assertRaises(KernelError) as caught:
            labels.apply('label.set_style', (1, 'label.style_none'))
        self.assertEqual(caught.exception.code, 'LABEL_EVENT_LIMIT')
        self.assertEqual((labels.checkpoint(), labels.pending), before)

    def test_styled_checkpoint_rejects_partial_records_and_invalid_sizes(self):
        large = StyledLabelRecord(**dict(START, size=2**63-1))
        self.assertEqual(decode(encode(large), LabelSnapshot), large)
        incomplete = START.copy()
        del incomplete['textcolor']
        for raw in (incomplete, dict(START, size=True), dict(START, size=2**63), dict(START, style='arrow')):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                Labels.restore(dict(next_id=2, event_count=1, live=[raw]), 1)
