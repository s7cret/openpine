"""Closed chart point IR, shared heap references and replay durability."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, LiteralIR, OpenPineError, ValueType
from packages.contracts.chart_points import IR_SCHEMA, STATE_SCHEMA
from packages.contracts.contexts import MarketContext
from packages.contracts.intrabar import PROFILE as INTRABAR
from packages.contracts.realtime import MarketEvent
from packages.execution.artifacts import export_program, import_program
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.storage.objects import ObjectStore
from packages.storage.results import ResultSink
from tests.test_realtime import historical
from tools.chart_points_fixtures import BOX, DRAWINGS, FIELDS, FIRST, LABEL, SECOND, SERIES, source
from tools.installed_lines_smoke import market_events
from tools.specification import ROOT

PRIMARY = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
BARS = ROOT / 'conformance/chart-points/bars.json'


class ChartPointsTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        self.events = [MarketEvent.from_payload(e) for e in market_events(BARS, PRIMARY)]

    def test_saved_ir_and_heap_survive_realtime_preview_and_resume(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        objects = ObjectStore(root / 'objects')
        program = import_program(export_program(self.session.compile(source(6, DRAWINGS), 'points.pine')))
        self.assertEqual(program.schema_version, IR_SCHEMA)

        def make():
            sink = ResultSink(objects, tempfile.mkdtemp(dir=root))
            self.addCleanup(sink.close)
            for kind in ('labels', 'lines', 'boxes'):
                getattr(sink, 'enable_' + kind)()
            return ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, sink, calculation_profile=INTRABAR)

        engine = make()
        for event in self.events[:3]:
            engine.apply(event)
        status = engine.status()
        self.assertEqual(status['lines'][0], FIRST)
        self.assertEqual(status['preview']['lines'][0], FIRST)
        self.assertEqual(status['lines'][1]['y1'], 16.)
        self.assertEqual(status['preview']['lines'][1]['y1'], 29.)
        checkpoint = engine.checkpoint()
        self.assertEqual(checkpoint['runtime']['schema_version'], STATE_SCHEMA)
        records = checkpoint['runtime']['heap']['objects']
        self.assertEqual([(row['type_id'], row['fields']) for row in records],
                         [('chart.point', [0, 0, 16.]), ('chart.point', [0, 0, 10.])])
        damaged = deepcopy(checkpoint)
        damaged['runtime']['heap']['objects'][0]['fields'][0] = 'bad timestamp'
        with self.assertRaises(ValueError):
            make().restore(damaged)

        resumed = make()
        resumed.restore(checkpoint)
        resumed.evaluation.output_sink.restore(engine.evaluation.output_sink.checkpoint(1), 1)
        self.assertEqual(resumed.status(), status)
        for event in self.events[3:]:
            resumed.apply(event)
        final = resumed.status()
        self.assertEqual(final['lines'], [FIRST, SECOND])
        self.assertEqual(final['labels'], [LABEL])
        self.assertEqual(final['boxes'], [BOX])
        self.assertIsNone(final['preview'])
        data = historical(self.events, PRIMARY)
        direct = self.session.execute(program, data=data)
        self.assertEqual([list(output.values) for output in direct.outputs], list(SERIES))

    def test_admission_checks_schema_and_now_default(self):
        program = self.session.compile(source(5, FIELDS), 'point-default.pine')
        data = historical(self.events, PRIMARY)
        with self.assertRaises(OpenPineError) as caught:
            self.session.execute(replace(program, schema_version='openpine.typed-ir.29'), data=data)
        self.assertEqual(caught.exception.diagnostic.code, 'IR_POINT_SCHEMA')
        declaration = program.statements[1]
        now = declaration.expression
        price, = now.arguments
        forged = replace(now, arguments=(replace(price, expression=LiteralIR(1., ValueType('float'), now.span)),))
        malformed = replace(program, statements=(program.statements[0], replace(declaration, expression=forged),
                                                 *program.statements[2:]))
        with self.assertRaises(OpenPineError) as caught:
            self.session.execute(malformed, data=data)
        self.assertEqual(caught.exception.diagnostic.code, 'IR_POINT_CALL')

    def test_point_fields_in_typed_functions_and_object_graphs(self):
        program = self.session.compile(source(6, """type Anchor
    chart.point point
value(chart.point p) =>
    p.price
var Anchor state = Anchor.new(chart.point.now())
alias = state.point
alias.price := value(alias) + 2
plot(value(state.point))
"""), 'point-graph.pine')
        result = self.session.execute(program, data=historical(self.events, PRIMARY))
        self.assertEqual(result.outputs[0].values, (12., 14., 16., 18.))
