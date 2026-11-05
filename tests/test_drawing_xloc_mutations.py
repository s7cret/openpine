"""Drawing coordinates survive admission, raw storage and preview rollback."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, LiteralIR, OpenPineError, ValueType
from packages.contracts.contexts import MarketContext
from packages.contracts.drawing_xloc import IR_SCHEMA, STATE_SCHEMA
from packages.contracts.drawings import EVENT_LIMIT
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.runtime.lines import Lines
from packages.runtime.series import KernelError
from packages.storage.objects import ObjectStore
from packages.storage.results import ResultSink
from tests.test_realtime import historical
from tools.drawing_xloc_mutations_fixtures import (
    FILL, FLOOR, INDEX_BOX, INDEX_LABEL, INDEX_LINE, TIME_BOX, TIME_LABEL, TIME_LINE, drawings, series, source,
)
from tools.installed_lines_smoke import market_events
from tools.specification import ROOT


PRIMARY = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
BARS = ROOT / 'conformance/drawing-xloc-mutations/bars.json'


class DrawingXlocMutationTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        self.events = [MarketEvent.from_payload(e) for e in market_events(BARS, PRIMARY)]

    def test_saved_ir_and_checkpoint_preserve_raw_coordinates_and_rollback(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        objects = ObjectStore(root / 'objects')
        program = import_program(export_program(self.session.compile(source(6, drawings(6)), 'xloc-change.pine')))
        self.assertEqual(program.schema_version, IR_SCHEMA)
        direct = self.session.execute(program, data=historical(self.events, PRIMARY))
        self.assertEqual(tuple(list(output.values) for output in direct.outputs), series(6))

        def make():
            sink = ResultSink(objects, tempfile.mkdtemp(dir=root))
            self.addCleanup(sink.close)
            for kind in ('labels', 'lines', 'boxes'):
                getattr(sink, 'enable_' + kind)()
            return ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, sink)

        first = make()
        for event in self.events[:5]:
            first.apply(event)
        status = loads(canonical(first.status()))
        self.assertEqual(status['lines'], [TIME_LINE, FLOOR])
        self.assertEqual(status['labels'], [TIME_LABEL])
        self.assertEqual(status['boxes'], [TIME_BOX])
        preview = status['preview']
        self.assertEqual(preview['lines'][0], {**INDEX_LINE, 'x1': -1, 'xloc': 'bar_index', 'extend': 'none'})
        self.assertEqual(preview['labels'], [{**INDEX_LABEL, 'x': -1, 'xloc': 'bar_index'}])
        self.assertEqual(preview['boxes'], [{**INDEX_BOX, 'left': -1, 'xloc': 'bar_index'}])
        self.assertEqual(preview['linefills'], [FILL])
        checkpoint = loads(canonical(first.checkpoint()))
        self.assertEqual(checkpoint['runtime']['schema_version'], STATE_SCHEMA)
        self.assertEqual(checkpoint['runtime']['lines']['live'], [TIME_LINE, FLOOR])
        prefix = loads(canonical(first.evaluation.output_sink.checkpoint(2)))
        resumed = make()
        resumed.evaluation.output_sink.restore(prefix, 2)
        resumed.restore(checkpoint)
        self.assertEqual(loads(canonical(resumed.status())), status)
        for event in self.events[5:]:
            resumed.apply(event)
        final = resumed.status()
        self.assertEqual(final['lines'], [TIME_LINE, FLOOR])
        self.assertEqual(final['labels'], [TIME_LABEL])
        self.assertEqual(final['boxes'], [TIME_BOX])
        self.assertEqual(final['linefills'], [FILL])
        self.assertIsNone(final['preview'])

    def test_saved_ir_rejects_forged_setter_token_and_default(self):
        program = self.session.compile(source(6, 'line.set_xloc(na, 1, 2, xloc.bar_time)\n'), 'xloc-change.pine')
        declaration, setter = program.statements
        mode = setter.arguments[-1]
        variants = (replace(mode, expression=LiteralIR('bar_time', ValueType('string'), setter.span)),
                    replace(mode, origin='default', source_index=None))
        for forged in variants:
            with self.subTest(forged=forged), self.assertRaises(OpenPineError) as caught:
                statement = replace(setter, arguments=(*setter.arguments[:-1], forged))
                self.session.execute(replace(program, statements=(declaration, statement)), data=historical(self.events, PRIMARY))
            self.assertEqual(caught.exception.diagnostic.code, 'IR_DRAWING_XLOC')

    def test_atomic_update_keeps_signed_coordinates_and_checks_quota(self):
        lines = Lines()
        identity = lines.apply('line.new', (0, 25., 3, 40.))
        for coordinates, code in (((-(2**63) - 1, 1), 'LINE_VALUE'), ((1, 2**63), 'LINE_VALUE')):
            with self.subTest(coordinates=coordinates), self.assertRaises(KernelError) as caught:
                lines.apply('line.set_xloc', (identity, *coordinates, 'xloc.bar_time'))
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(lines.snapshot(), [INDEX_LINE])
            self.assertEqual(len(lines.pending), 1)

        lines.apply('line.set_xloc', (identity, -(2**63), 2**63 - 1, 'xloc.bar_time'))
        self.assertEqual(lines.apply('line.get_x1', (identity,)), -(2**63))
        self.assertEqual(lines.apply('line.get_x2', (identity,)), 2**63 - 1)
        self.assertEqual(len(lines.pending), 2)
        while len(lines.pending) < EVENT_LIMIT:
            lines.apply('line.set_xloc', (identity, 0, 1, 'xloc.bar_index'))
        before = lines.checkpoint(), lines.pending.copy()
        with self.assertRaises(KernelError) as caught:
            lines.apply('line.set_xloc', (identity, 1000, 2000, 'xloc.bar_time'))
        self.assertEqual(caught.exception.code, 'LINE_EVENT_LIMIT')
        self.assertEqual((lines.checkpoint(), lines.pending), before)
        self.assertEqual(lines.apply('line.get_x1', (identity,)), 0)
        lines.apply('line.set_xloc', (None, 1000, 2000, 'xloc.bar_time'))
        self.assertEqual((lines.checkpoint(), lines.pending), before)
