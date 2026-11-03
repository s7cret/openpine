"""Line price, viewport, checkpoint and preview behavior."""
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
import sys
import tempfile
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.codec import decode, encode
from packages.contracts.lines import chart_lines
from packages.execution.artifacts import export_program, import_program
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.runtime.lines import Lines, price_at
from packages.storage.line_journal import line_events
from packages.storage.objects import ObjectStore
from packages.storage.results import ResultSink
from tests.test_realtime import PRIMARY, historical, tape
from tools.line_extensions_fixtures import MOVING
from tools.lines_fixtures import source


class LineExtensionTests(unittest.TestCase):
    def test_price_is_extrapolated_and_rounds_once(self):
        far = 2**63 - 2
        line = dict(x1=far, y1=1., x2=far - 2, y2=3.)
        for mode in ('none', 'left', 'right', 'both'):
            self.assertEqual(price_at(dict(line, extend=mode), far + 1), 0.)
        largest = sys.float_info.max
        extreme = dict(x1=0, y1=-largest, x2=2, y2=largest)
        self.assertEqual(price_at(extreme, 1), 0.)
        self.assertIsNone(price_at(extreme, 3))
        self.assertIsNone(price_at(dict(line, x2=far), far))
        self.assertIsNone(price_at(dict(line, y2=None), far))
        self.assertIsNone(price_at(line, None))
        self.assertIsNone(price_at(None, 0))

    def test_old_records_and_viewport_modes(self):
        store = Lines()
        store.apply('line.new', (20, 10., 10, 20.))
        store.event_count = 1
        old = store.checkpoint()
        store = Lines.restore(old, 1)
        self.assertEqual(store.checkpoint(), old)
        self.assertEqual(chart_lines(store.snapshot(), 0, 3), [])
        for mode, visible in (('none', (False, False)), ('left', (True, False)),
                              ('right', (False, True)), ('both', (True, True))):
            store.apply('line.set_extend', (1, 'extend.' + mode))
            self.assertEqual((bool(chart_lines(store.snapshot(), 0, 3)),
                              bool(chart_lines(store.snapshot(), 30, 33))), visible)
        store.apply('line.set_xy2', (1, 20, 40.))
        self.assertEqual(chart_lines(store.snapshot(), 0, 3), [])
        self.assertTrue(chart_lines(store.snapshot(), 20, 21))

    def test_checkpoint_and_preview_share_prices_but_publish_only_closes(self):
        session = Session(profile_id=HISTORICAL_PROFILE_ID)
        script = source(6, MOVING)
        program = import_program(export_program(session.compile(script, 'line-extension.pine')))
        events = list(tape())
        _, expected = session.run(script, 'line-extension.pine', data=historical(events))
        self.assertEqual(decode(encode(expected), type(expected)), expected)
        with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup:
            root = Path(directory)
            objects = ObjectStore(root / 'objects')

            def engine(name):
                sink = ResultSink(objects, root / name)
                cleanup.callback(sink.close)
                sink.enable_lines()
                return ReplayEngine(session, program, (PRIMARY,), PRIMARY.context_id, sink), sink

            first, sink = engine('first')
            for event in events[:4]:
                first.apply(event)
            status = first.status()
            self.assertEqual(status['lines'][0]['extend'], 'right')
            self.assertEqual(status['preview']['lines'][0]['extend'], 'left')
            self.assertEqual(sink.line_journal.records, 1)
            state, prefix = first.checkpoint(), sink.checkpoint(1)
            resumed, output = engine('resumed')
            resumed.restore(state)
            output.restore(prefix, 1)
            self.assertEqual(resumed.status(), status)
            for event in events[4:]:
                resumed.apply(event)
            final = output.finish(resumed.status())
            self.assertEqual(final['lines'], [asdict(item) for item in expected.lines])
            self.assertEqual(final['outputs'][0]['last'], expected.outputs[0].values[-1])
            rows = list(line_events(objects, final['line_journal'], 0, 10))
            self.assertEqual([r['event']['line']['extend'] for r in rows], ['right', 'left', 'left', 'both'])
