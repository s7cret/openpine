"""Style contracts, saved line records and replay checkpoints."""
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, LiteralIR, OpenPineError, ValueType
from packages.contracts.chart_points import IR_SCHEMA
from packages.contracts.codec import decode, encode
from packages.contracts.contexts import MarketContext
from packages.contracts.drawings import EVENT_LIMIT
from packages.contracts.model import LineRecord, LineSnapshot, StyledLineRecord
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
from tools.installed_lines_smoke import market_events
from tools.line_styles_fixtures import CLOSED, FILL, FINAL, FLOOR, POINTS, PREVIEW, PRICES, START, appearance, source
from tools.specification import ROOT


PRIMARY = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
BARS = ROOT / 'conformance/line-styles/bars.json'


class LineStyleTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        self.events = [MarketEvent.from_payload(event) for event in market_events(BARS, PRIMARY)]

    def test_saved_ir_result_and_open_bar_checkpoint_preserve_style(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        objects = ObjectStore(root / 'objects')
        program = import_program(export_program(self.session.compile(source(6, appearance(6)), 'style.pine')))
        self.assertEqual(program.schema_version, IR_SCHEMA)
        result = self.session.execute(program, data=historical(self.events, PRIMARY))
        self.assertEqual([asdict(line) for line in result.lines], [FINAL, FLOOR, POINTS])
        self.assertEqual(list(result.outputs[0].values), PRICES)
        self.assertEqual(decode(encode(result), type(result)), result)
        self.assertIsInstance(result.lines[0], StyledLineRecord)

        def make():
            sink = ResultSink(objects, tempfile.mkdtemp(dir=root))
            self.addCleanup(sink.close)
            sink.enable_lines()
            return ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, sink)

        running = make()
        for event in self.events[:3]:
            running.apply(event)
        status = loads(canonical(running.status()))
        self.assertEqual(status['lines'], [START, FLOOR, POINTS])
        self.assertEqual(status['preview']['lines'], [PREVIEW, FLOOR, POINTS])
        self.assertEqual(status['preview']['linefills'], [FILL])
        prefix = loads(canonical(running.evaluation.output_sink.checkpoint(1)))
        checkpoint = loads(canonical(running.checkpoint()))
        resumed = make()
        resumed.evaluation.output_sink.restore(prefix, 1)
        resumed.restore(checkpoint)
        self.assertEqual(loads(canonical(resumed.status())), status)
        resumed.apply(self.events[3])
        self.assertEqual(resumed.status()['lines'][0], CLOSED)
        for event in self.events[4:]:
            resumed.apply(event)
        self.assertEqual(resumed.status()['lines'], [FINAL, FLOOR, POINTS])
        self.assertEqual(resumed.status()['linefills'], [FILL])

    def test_style_admission_rejects_a_forged_constructor_default(self):
        program = self.session.compile(source(4, 'line.new(0, 10, 2, 20, style=line.style_dashed)\n'), 'style.pine')
        declaration, constructor = program.statements
        default = constructor.arguments[-1]
        self.assertEqual(default.origin, 'default')
        forged = replace(default, expression=LiteralIR(2, ValueType('int'), constructor.span))
        statement = replace(constructor, arguments=(*constructor.arguments[:-1], forged))
        with self.assertRaises(OpenPineError) as caught:
            self.session.execute(replace(program, statements=(declaration, statement)), data=historical(self.events, PRIMARY))
        self.assertEqual(caught.exception.diagnostic.code, 'IR_LINE_STYLE_CALL')

    def test_old_records_and_failed_mutations_keep_their_shape(self):
        lines = Lines()
        line_id = lines.apply('line.new', (0, 10., 2, 20.))
        old = LineRecord(id=line_id, x1=0, y1=10., x2=2, y2=20.)
        self.assertEqual(decode(encode(old), LineSnapshot), old)
        old_checkpoint = dict(next_id=2, event_count=1, live=[asdict(old)])
        self.assertEqual(Lines.restore(old_checkpoint, 1).snapshot(), [asdict(old)])
        before = lines.checkpoint(), lines.pending.copy()
        for operation, value, code in (('line.set_width', 65, 'LINE_WIDTH'), ('line.set_style', 'solid', 'LINE_STYLE')):
            with self.subTest(operation=operation), self.assertRaises(KernelError) as caught:
                lines.apply(operation, (line_id, value))
            self.assertEqual(caught.exception.code, code)
            self.assertEqual((lines.checkpoint(), lines.pending), before)
        while len(lines.pending) < EVENT_LIMIT:
            lines.apply('line.set_xy1', (line_id, 0, 10.))
        before = lines.checkpoint(), lines.pending.copy()
        with self.assertRaises(KernelError) as caught:
            lines.apply('line.set_color', (line_id, '#FF1122FF'))
        self.assertEqual(caught.exception.code, 'LINE_EVENT_LIMIT')
        self.assertEqual((lines.checkpoint(), lines.pending), before)
