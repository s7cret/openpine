"""Stored label text options, nominal format values, and rollback."""
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, LiteralIR, OpenPineError, ValueType
from packages.contracts.codec import decode, encode
from packages.contracts.contexts import MarketContext
from packages.contracts.drawings import EVENT_LIMIT
from packages.contracts.model import LabelRecord, LabelSnapshot, StyledTextLabelRecord, TextLabelRecord
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
from tools.label_text_fixtures import START, changes, final_label, labels, source, text_program
from tools.specification import ROOT

PRIMARY = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
BARS = ROOT / 'conformance/label-text/bars.json'


class LabelTextTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        self.events = [MarketEvent.from_payload(event) for event in market_events(BARS, PRIMARY)]

    def test_saved_program_and_open_bar_checkpoint_preserve_text_and_format_binding(self):
        program = import_program(export_program(self.session.compile(source(6, text_program(6)), 'text.pine')))
        result = self.session.execute(program, data=historical(self.events, PRIMARY))
        self.assertEqual([asdict(row) for row in result.labels], [final_label(6), *labels(6)])
        self.assertEqual(decode(encode(result), type(result)), result)
        self.assertIsInstance(result.labels[0], StyledTextLabelRecord)
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        objects = ObjectStore(root / 'objects')

        def engine():
            sink = ResultSink(objects, tempfile.mkdtemp(dir=root))
            self.addCleanup(sink.close)
            sink.enable_labels()
            return ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, sink)

        running = engine()
        for event in self.events[:3]:
            running.apply(event)
        opened = loads(canonical(running.status()))
        self.assertEqual(opened['labels'], [START, *labels(6)])
        self.assertEqual(opened['preview']['labels'], [changes(6, preview=True)[-1], *labels(6)])
        prefix = loads(canonical(running.evaluation.output_sink.checkpoint(1)))
        checkpoint = loads(canonical(running.checkpoint()))
        resumed = engine()
        resumed.evaluation.output_sink.restore(prefix, 1)
        resumed.restore(checkpoint)
        self.assertEqual(loads(canonical(resumed.status())), opened)
        resumed.apply(self.events[3])
        self.assertEqual(resumed.status()['labels'], [changes(6)[-1], *labels(6)])
        for event in self.events[4:]:
            resumed.apply(event)
        self.assertEqual(resumed.status()['labels'], [final_label(6), *labels(6)])

    def test_legacy_tooltip_updates_preserve_appearance_and_are_atomic(self):
        store = Labels()
        tag = store.apply('label.new', (1, 25., 'plain'))
        legacy = LabelRecord(id=tag, x=1, y=25., text='plain')
        self.assertEqual(decode(encode(legacy), LabelSnapshot), legacy)
        store.apply('label.set_tooltip', (tag, 'hint'))
        raw = dict(asdict(legacy), xloc='bar_index', yloc='price', textalign=None,
                   tooltip='hint', text_font_family='default', text_formatting=0)
        typed = TextLabelRecord(**raw)
        self.assertEqual(store.result_records(), (typed,))
        self.assertEqual(decode(encode(typed), LabelSnapshot), typed)
        self.assertEqual(Labels.restore(dict(next_id=2, event_count=2, live=[raw]), 1).snapshot(), [raw])
        promoted = store.fork()
        promoted.apply('label.set_color', (tag, '#112233FF'))
        self.assertEqual(promoted.result_records()[0].textalign, 'center')
        self.assertEqual(store.result_records(), (typed,))
        before = store.checkpoint(), store.pending.copy()
        for operation, invalid, code in (('label.set_text_formatting', True, 'LABEL_TEXT_FORMATTING'),
                                         ('label.set_tooltip', 'a'*4097, 'LABEL_TOOLTIP'),
                                         ('label.set_textalign', 'text.align_bottom', 'LABEL_TEXTALIGN')):
            with self.subTest(operation=operation), self.assertRaises(KernelError) as error:
                store.apply(operation, (tag, invalid))
            self.assertEqual(error.exception.code, code)
            self.assertEqual((store.checkpoint(), store.pending), before)
        while len(store.pending) < EVENT_LIMIT:
            store.apply('label.set_tooltip', (tag, 'hint'))
        before = store.checkpoint(), store.pending.copy()
        with self.assertRaises(KernelError) as error:
            store.apply('label.set_tooltip', (tag, ''))
        self.assertEqual(error.exception.code, 'LABEL_EVENT_LIMIT')
        self.assertEqual((store.checkpoint(), store.pending), before)

    def test_admission_rejects_forged_nominal_format(self):
        program = self.session.compile(source(6, 'label.new(0, 10, text_formatting=text.format_bold)\n'), 'text.pine')
        node = program.statements[-1]
        invalid = replace(node.arguments[-1], expression=LiteralIR(4, ValueType('text_format'), node.span))
        forged = replace(node, arguments=(*node.arguments[:-1], invalid))
        with self.assertRaises(OpenPineError) as error:
            self.session.execute(replace(program, statements=(*program.statements[:-1], forged)),
                                 data=historical(self.events, PRIMARY))
        self.assertEqual(error.exception.diagnostic.code, 'IR_LITERAL_TYPE')
