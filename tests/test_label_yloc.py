"""Vertical label geometry through state, results and open-bar rollback."""
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.codec import decode, encode
from packages.contracts.contexts import MarketContext
from packages.contracts.drawing_clock import projected, stream_positions, timestamps
from packages.contracts.drawings import EVENT_LIMIT
from packages.contracts.labels import chart_labels
from packages.contracts.model import LabelSnapshot, StyledVerticalLabelRecord, VerticalLabelRecord
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
from tools.installed_label_yloc_smoke import replay_events
from tools.label_yloc_fixtures import BELOW, CLOSED, FINAL, GAP, POINT, START, labels, source, vertical_program
from tools.specification import ROOT

PRIMARY = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
BARS = ROOT / 'conformance/label-yloc/bars.json'


class LabelYlocTests(unittest.TestCase):
    def test_saved_program_and_open_bar_checkpoint_restore_vertical_modes(self):
        session = Session(profile_id=HISTORICAL_PROFILE_ID)
        events = [MarketEvent.from_payload(event) for event in replay_events(BARS, PRIMARY)]
        program = import_program(export_program(session.compile(source(6, vertical_program(6)), 'yloc.pine')))
        result = session.execute(program, data=historical(events, PRIMARY))
        self.assertEqual([asdict(row) for row in result.labels], [FINAL, *labels(6)])
        self.assertEqual(decode(encode(result), type(result)), result)
        self.assertIsInstance(result.labels[0], StyledVerticalLabelRecord)

        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        objects = ObjectStore(root / 'objects')

        def engine():
            sink = ResultSink(objects, tempfile.mkdtemp(dir=root))
            self.addCleanup(sink.close)
            sink.enable_labels()
            return ReplayEngine(session, program, (PRIMARY,), PRIMARY.context_id, sink)

        running = engine()
        for event in events[:3]:
            running.apply(event)
        opened = loads(canonical(running.status()))
        self.assertEqual(opened['labels'], [START, *labels(6)])
        self.assertEqual(opened['preview']['labels'], [dict(CLOSED, y=24., yloc='belowbar'), *labels(6)])
        prefix = loads(canonical(running.evaluation.output_sink.checkpoint(1)))
        checkpoint = loads(canonical(running.checkpoint()))
        resumed = engine()
        resumed.evaluation.output_sink.restore(prefix, 1)
        resumed.restore(checkpoint)
        self.assertEqual(loads(canonical(resumed.status())), opened)
        resumed.apply(events[3])
        self.assertEqual(resumed.status()['preview']['labels'],
                         [dict(CLOSED, y=24.5, yloc='belowbar'), *labels(6)])
        self.assertEqual(resumed.status()['labels'], opened['labels'])
        resumed.apply(events[4])
        self.assertEqual(resumed.status()['labels'], [CLOSED, *labels(6)])
        for event in events[5:]:
            resumed.apply(event)
        self.assertEqual(resumed.status()['labels'], [FINAL, *labels(6)])

    def test_only_exact_time_anchors_are_visible_in_bar_relative_modes(self):
        priced_gap = dict(FINAL, id=5, x=150000)
        state = {'labels': [FINAL, BELOW, GAP, POINT, priced_gap]}
        clock = [(0, 0), (60000, 1), (240000, 2), (300000, 3)]
        positions = stream_positions(timestamps(state), clock)
        self.assertEqual(positions, {60000: 1, 150000: 1.5})
        drawings = projected(state, positions)['labels']
        self.assertIsNone(drawings[2]['chart_x'])
        self.assertEqual(chart_labels(drawings, 1, 2),
                         [dict(FINAL, chart_x=1), BELOW, dict(POINT, chart_x=1), dict(priced_gap, chart_x=1.5)])
        self.assertEqual(chart_labels(drawings, 2, 4), [])
        self.assertIsNone(projected({'labels': [GAP]}, {150000: 1.0})['labels'][0]['chart_x'])
        self.assertEqual(chart_labels([dict(FINAL, xloc='bar_index', x=1, y=None)], 1, 2), [])

    def test_mode_updates_preserve_legacy_record_style_and_are_atomic(self):
        store = Labels()
        tag = store.apply('label.new', (1, 25., 'plain'))
        store.apply('label.set_yloc', (tag, 'yloc.belowbar'))
        store.apply('label.set_y', (tag, None))
        raw = dict(id=tag, x=1, y=None, text='plain', xloc='bar_index', yloc='belowbar')
        typed = VerticalLabelRecord(**raw)
        self.assertEqual(store.result_records(), (typed,))
        self.assertEqual(decode(encode(typed), LabelSnapshot), typed)
        self.assertEqual(Labels.restore(dict(next_id=2, event_count=3, live=[raw]), 1).snapshot(), [raw])
        for bad in (dict(raw, yloc='unknown'), {k: v for k, v in raw.items() if k != 'xloc'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Labels.restore(dict(next_id=2, event_count=3, live=[bad]), 1)
        before = store.checkpoint(), store.pending.copy()
        with self.assertRaises(KernelError) as error:
            store.apply('label.set_yloc', (tag, 'invalid'))
        self.assertEqual(error.exception.code, 'LABEL_YLOC')
        self.assertEqual((store.checkpoint(), store.pending), before)
        while len(store.pending) < EVENT_LIMIT:
            store.apply('label.set_y', (tag, None))
        before = store.checkpoint(), store.pending.copy()
        with self.assertRaises(KernelError) as error:
            store.apply('label.set_yloc', (tag, 'yloc.price'))
        self.assertEqual(error.exception.code, 'LABEL_EVENT_LIMIT')
        self.assertEqual((store.checkpoint(), store.pending), before)

    def test_required_setter_arguments_cannot_be_forged_as_defaults(self):
        session = Session(profile_id=HISTORICAL_PROFILE_ID)
        program = session.compile(source(4, 'var label tag = label.new(0, 10, yloc=yloc.price)\n'
                                             'label.set_yloc(tag, yloc.abovebar)\n'), 'yloc.pine')
        statement = program.statements[-1]
        argument = replace(statement.arguments[1], origin='default', source_index=None)
        forged = replace(statement, arguments=(statement.arguments[0], argument))
        events = [MarketEvent.from_payload(event) for event in replay_events(BARS, PRIMARY)]
        with self.assertRaises(OpenPineError) as error:
            session.execute(replace(program, statements=(*program.statements[:-1], forged)),
                            data=historical(events, PRIMARY))
        self.assertEqual(error.exception.diagnostic.code, 'IR_LABEL_YLOC_CALL')
