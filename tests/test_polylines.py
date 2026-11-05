"""Polyline snapshots, durable point arrays and runtime admission."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, LiteralIR, OpenPineError, ValueType
from packages.contracts.codec import decode, encode
from packages.contracts.contexts import MarketContext
from packages.contracts.polylines import IR_SCHEMA, MAX_LIVE_VERTICES, MAX_VERTICES, STATE_SCHEMA
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.runtime.polylines import Polylines
from packages.runtime.series import KernelError
from packages.storage.objects import ObjectStore
from packages.storage.results import ResultSink
from tests.test_realtime import historical
from tools.installed_lines_smoke import market_events
from tools.polylines_fixtures import DRAWINGS, FIRST, POLYGON, SECOND, source
from tools.specification import ROOT

PRIMARY = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
BARS = ROOT / 'conformance/polylines/bars.json'


class PolylineTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        self.events = [MarketEvent.from_payload(e) for e in market_events(BARS, PRIMARY)]

    def test_saved_ir_heap_and_journal_restore_at_open_bar(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        objects = ObjectStore(root / 'objects')
        program = import_program(export_program(self.session.compile(source(6, DRAWINGS), 'polylines.pine')))
        self.assertEqual(program.schema_version, IR_SCHEMA)
        result = self.session.execute(program, data=historical(self.events, PRIMARY))
        self.assertEqual(decode(encode(result), type(result)), result)

        def make():
            sink = ResultSink(objects, tempfile.mkdtemp(dir=root))
            self.addCleanup(sink.close)
            sink.enable_polylines()
            return ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, sink)

        first = make()
        for event in self.events[:3]:
            first.apply(event)
        status = loads(canonical(first.status()))
        self.assertEqual(status['polylines'], [FIRST])
        self.assertEqual(status['preview']['polylines'], [FIRST, {**SECOND, 'vertices': [[0, 29.], [3, 45.]]}])
        checkpoint = loads(canonical(first.checkpoint()))
        self.assertEqual(checkpoint['runtime']['schema_version'], STATE_SCHEMA)
        heap = checkpoint['runtime']['heap']['objects']
        array = next(row for row in heap if row.get('element') == 'chart.point')
        points = {row['id']: row['fields'] for row in heap if row.get('type_id') == 'chart.point'}
        self.assertEqual([points[key] for key in array['refs']], [[0, 0, 10.], [None, 3, 40.]])

        prefix = loads(canonical(first.evaluation.output_sink.checkpoint(1)))
        self.assertEqual(prefix['schema_version'], 'openpine.output-prefix.7')
        resumed = make()
        resumed.evaluation.output_sink.restore(prefix, 1)
        resumed.restore(checkpoint)
        self.assertEqual(loads(canonical(resumed.status())), status)
        for event in self.events[3:]:
            resumed.apply(event)
        self.assertEqual(loads(canonical(resumed.status()))['polylines'], [FIRST, POLYGON])
        self.assertEqual(resumed.evaluation.output_sink.polyline_journal.records, 4)

    def test_polyline_admission_rejects_forged_curve(self):
        program = self.session.compile(source(5, 'polyline.new(array.new<chart.point>(2, chart.point.now()))\n'), 'curve.pine')
        call = program.statements[1]
        args = list(call.arguments)
        args[1] = replace(args[1], expression=LiteralIR(True, ValueType('bool'), call.span))
        forged = replace(program, statements=(program.statements[0], replace(call, arguments=tuple(args))))
        with self.assertRaises(OpenPineError) as caught:
            self.session.execute(forged, data=historical(self.events, PRIMARY))
        self.assertEqual(caught.exception.diagnostic.code, 'IR_POLYLINE_CALL')

    def test_vertex_budget_is_checked_before_identity_and_journal_mutation(self):
        store = Polylines()
        record = {k: v for k, v in FIRST.items() if k != 'id'}
        record['vertices'] = tuple((i, 1.) for i in range(MAX_VERTICES))
        for _ in range(MAX_LIVE_VERTICES // MAX_VERTICES):
            store.reserve_vertices(MAX_VERTICES, False)
            store.create(record)
        before = store.checkpoint(), list(store.pending)
        with self.assertRaisesRegex(KernelError, 'vertex budget'):
            store.reserve_vertices(2, False)
        self.assertEqual((store.checkpoint(), store.pending), before)
        for identity in tuple(store.live):
            store.apply('polyline.delete', (identity,))
        self.assertEqual(store.live, {})
        with self.assertRaisesRegex(KernelError, 'vertex budget'):
            store.reserve_vertices(2, False)
