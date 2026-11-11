"""Polyline snapshots, durable point arrays and runtime admission."""
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest

from apps.openpine.bootstrap import local_manager
from apps.openpine.replay import Replays
from packages.contracts import HISTORICAL_PROFILE_ID, LiteralIR, OpenPineError, ValueType
from packages.contracts.codec import decode, encode
from packages.contracts.contexts import MarketContext
from packages.contracts.polylines import IR_SCHEMA, MAX_LIVE_VERTICES, MAX_VERTICES, STATE_SCHEMA
from packages.contracts.realtime import MarketEvent
from packages.contracts.trading import BrokerSettings
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.host_memory import HostAdmission
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.marketdata import read_bars
from packages.runtime.polylines import Polylines
from packages.runtime.series import KernelError
from packages.storage.checkpoint_records import get as checkpoint_get
from packages.storage.objects import ObjectStore
from packages.storage.results import MIXED_OUTPUT_PREFIX, ResultSink
from tests.test_realtime import configuration, historical, tape
from tools.boxes_fixtures import STRATEGY_ZONE
from tools.installed_lines_smoke import market_events
from tools.lines_fixtures import strategy_source as limit_source
from tools.polylines_fixtures import DRAWINGS, FIRST, POLYGON, SECOND, source, strategy_region_source
from tools.specification import ROOT

PRIMARY = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
BARS = ROOT / 'conformance/polylines/bars.json'


class PolylineTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        self.events = [MarketEvent.from_payload(e) for e in market_events(BARS, PRIMARY)]

    def test_strategy_region_restores_all_journals_and_time_coordinates(self):
        from test_checkpoints import paused
        from test_jobs import wait
        from test_strategies import request

        bars = read_bars(BARS)
        text = strategy_region_source(6)
        program = self.session.compile(text, 'region.pine')
        result = self.session.execute(import_program(export_program(program)), data=bars)
        _, plain = self.session.run(limit_source(6, with_drawings=False), 'region.pine', data=bars)

        def trades(run):
            return [(row['kind'], row['bar_index'], row['value'].get('price'), row['value'].get('quantity'))
                    for row in run.simulation['events'] if row['kind'] in ('order', 'fill', 'trade')]

        self.assertEqual(trades(result), trades(plain))
        self.assertEqual(result.outputs[0].values, plain.outputs[0].values)
        self.assertEqual(result.simulation['statistics'], plain.simulation['statistics'])

        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        host = HostAdmission(probe=lambda: None)
        with local_manager(root, host_admission=host, max_workers=1) as manager:
            req = request(manager, text=text, bars=bars)
            baseline = manager.submit(req)['job_id']
            self.assertEqual(wait(manager, baseline)['status'], 'succeeded', manager.summary(baseline))
            expected = manager.result(baseline)['result']
            readers = (manager.label_events, manager.line_events, manager.box_events, manager.polyline_events)
            journals = [read(baseline)['events'] for read in readers]
            job = manager.submit(req, pause_after=2)['job_id']
            paused(manager, job)
            checkpoint = manager.services.checkpoints.manifest(manager.get(job)['checkpoint_id'])
            prefix = checkpoint_get(manager.objects, checkpoint['output_ref'])
            self.assertEqual(prefix['schema_version'], MIXED_OUTPUT_PREFIX)
            self.assertIn('simulation', prefix)
            self.assertEqual([prefix[f'{kind}_journal']['records'] for kind in ('label', 'line', 'box', 'polyline')],
                             [2, 3, 2, 1])
        with local_manager(root, host_admission=host, max_workers=1) as manager:
            manager.resume(job)
            self.assertEqual(wait(manager, job)['status'], 'succeeded', manager.summary(job))
            self.assertEqual(manager.result(job)['result'], expected)
            readers = (manager.label_events, manager.line_events, manager.box_events, manager.polyline_events)
            self.assertEqual([read(job)['events'] for read in readers], journals)
            projected = {**POLYGON, 'id': 1, 'chart_vertices': [(0, 12.), (1.5, 42.), (3, 15.)]}
            for start in (0, 1):
                chart = manager.chart(job, start=start, limit=2)
                self.assertEqual(chart['boxes'], [STRATEGY_ZONE])
                self.assertEqual(chart['polylines'], [projected])

    def test_strategy_region_replay_restores_both_preview_evictions(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        host = HostAdmission(probe=lambda: None)
        events = [event.payload() for event in tape()]
        views = ('chart', 'ledger', 'label_events', 'line_events', 'box_events', 'polyline_events')
        with local_manager(root, host_admission=host) as manager:
            replay = Replays(manager)
            req = configuration(manager, strategy_region_source(6, on_tick=True, evict=True))
            req['broker_settings'] = asdict(BrokerSettings(tick_size='0.5'))
            baseline = replay.create(req)['replay_id']
            replay.append(baseline, events)
            replay.advance(baseline, checkpoint_every=1)
            expected = {view: replay.view(baseline, view, limit=100) for view in views}
            identifier = replay.create(req)['replay_id']
            replay.append(identifier, events)
            partial = replay.advance(identifier, max_events=7, checkpoint_every=1)
            for kind in ('boxes', 'polylines'):
                self.assertEqual([row['id'] for row in partial[kind]], [1])
                self.assertEqual([row['id'] for row in partial['preview'][kind]], [2])
            self.assertEqual(partial['preview']['polylines'][0]['vertices'][1], [120000, 14.])
            prefix = replay.repository.checkpoint(identifier)['output']
            self.assertEqual(prefix['schema_version'], MIXED_OUTPUT_PREFIX)
            self.assertEqual((prefix['completed'], prefix['simulation_completed'], prefix['observations']), (2, 3, 7))
            self.assertEqual((prefix['box_journal']['records'], prefix['polyline_journal']['records']), (2, 1))
        with local_manager(root, host_admission=host) as manager:
            replay = Replays(manager)
            self.assertEqual(replay.status(identifier)['preview'], partial['preview'])
            final = replay.advance(identifier, checkpoint_every=1)
            self.assertIsNone(final['preview'])
            self.assertEqual(final['polylines'][0]['vertices'][1], [120000, 15.])
            self.assertEqual(final['boxes'][0]['top'], 15.)
            for view in views:
                self.assertEqual(replay.view(identifier, view, limit=100), expected[view])
            for view in ('box_events', 'polyline_events'):
                events = expected[view]['events']
                self.assertEqual(events[-2]['event'], {'action': 'delete', 'id': 1, 'reason': 'capacity'})

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
