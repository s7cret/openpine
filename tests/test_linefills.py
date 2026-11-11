"""Linefill identity, resource bounds and replay checkpoint integrity."""
from copy import deepcopy
from dataclasses import asdict
from itertools import combinations
from pathlib import Path
import tempfile
import unittest

from apps.openpine.bootstrap import local_manager
from apps.openpine.replay import Replays
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.codec import decode, encode
from packages.contracts.contexts import MarketContext
from packages.contracts.drawings import EVENT_LIMIT, LIMIT
from packages.contracts.intrabar import PROFILE as INTRABAR
from packages.contracts.realtime import MarketEvent
from packages.contracts.trading import BrokerSettings
from packages.execution.artifacts import export_program, import_program
from packages.execution.host_memory import HostAdmission
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.marketdata import read_bars
from packages.runtime.linefills import FilledLines
from packages.runtime.series import KernelError
from packages.storage.checkpoint_records import get as checkpoint_get
from packages.storage.objects import ObjectStore
from packages.storage.results import MIXED_OUTPUT_PREFIX, ResultSink
from tests.test_realtime import configuration, historical, tape
from tools.installed_lines_smoke import market_events
from tools.linefills_fixtures import DELETE, FILL, UPPER, source
from tools.lines_fixtures import strategy_source
from tools.specification import ROOT


class LinefillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_strategy_channel_and_three_journals_resume_in_history(self):
        from test_checkpoints import paused
        from test_jobs import wait
        from test_strategies import request

        bars = read_bars(ROOT / 'conformance/linefills/bars.json')
        text = strategy_source(6, channel=True)
        program = self.session.compile(text, 'channel.pine')
        result = self.session.execute(import_program(export_program(program)), data=bars)
        _, plain = self.session.run(strategy_source(6, with_drawings=False), 'channel.pine', data=bars)

        def trades(run):
            return [(row['kind'], row['bar_index'], row['value'].get('price'), row['value'].get('quantity'))
                    for row in run.simulation['events'] if row['kind'] in ('order', 'fill', 'trade')]

        self.assertEqual(trades(result), trades(plain))
        self.assertEqual(result.outputs[0].values, plain.outputs[0].values)
        self.assertEqual(result.simulation['statistics'], plain.simulation['statistics'])
        self.assertEqual([row for row in trades(result) if row[0] == 'fill'],
                         [('fill', 1, '19', '1'), ('fill', 3, '39', '1')])

        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        host = HostAdmission(probe=lambda: None)
        with local_manager(root, host_admission=host, max_workers=1) as manager:
            req = request(manager, text=text, bars=bars)
            baseline = manager.submit(req)['job_id']
            self.assertEqual(wait(manager, baseline)['status'], 'succeeded')
            expected = manager.result(baseline)['result']
            labels = manager.label_events(baseline)['events']
            lines = manager.line_events(baseline)['events']
            job = manager.submit(req, pause_after=2)['job_id']
            paused(manager, job)
            checkpoint = manager.services.checkpoints.manifest(manager.get(job)['checkpoint_id'])
            prefix = checkpoint_get(manager.objects, checkpoint['output_ref'])
            self.assertEqual(prefix['schema_version'], MIXED_OUTPUT_PREFIX)
            self.assertEqual((prefix['label_journal']['records'], prefix['line_journal']['records']), (2, 3))
            self.assertIn('simulation', prefix)
        with local_manager(root, host_admission=host, max_workers=1) as manager:
            manager.resume(job)
            self.assertEqual(wait(manager, job)['status'], 'succeeded', manager.summary(job))
            self.assertEqual(manager.result(job)['result'], expected)
            self.assertEqual(manager.label_events(job)['events'], labels)
            self.assertEqual(manager.line_events(job)['events'], lines)
            window = manager.chart(job, start=1, limit=2)
            self.assertEqual(window['lines'], expected['lines'])
            self.assertEqual(window['linefills'], [FILL])
            self.assertEqual(window['tables'][0]['cells'][0]['text'], '1020')

    def test_strategy_channel_replay_resumes_before_preview_eviction(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        host = HostAdmission(probe=lambda: None)
        events = [event.payload() for event in tape()]
        with local_manager(root, host_admission=host) as manager:
            replay = Replays(manager)
            req = configuration(manager, strategy_source(6, channel=True, on_tick=True, evict=True))
            req['broker_settings'] = asdict(BrokerSettings(tick_size='0.5'))
            baseline = replay.create(req)['replay_id']
            replay.append(baseline, events)
            replay.advance(baseline, checkpoint_every=1)
            expected = {view: replay.view(baseline, view, limit=100)
                        for view in ('chart', 'ledger', 'line_events', 'label_events')}
            identifier = replay.create(req)['replay_id']
            replay.append(identifier, events)
            partial = replay.advance(identifier, max_events=7, checkpoint_every=1)
            self.assertEqual(partial['linefills'], [FILL])
            self.assertEqual(partial['preview']['linefills'], [])
            self.assertEqual([line['id'] for line in partial['lines']], [1, 2])
            self.assertEqual([line['id'] for line in partial['preview']['lines']], [2, 3])
            prefix = replay.repository.checkpoint(identifier)['output']
            self.assertEqual(prefix['schema_version'], MIXED_OUTPUT_PREFIX)
            self.assertEqual((prefix['completed'], prefix['simulation_completed'], prefix['observations']), (2, 3, 7))
            self.assertEqual(prefix['line_journal']['records'], 3)
        with local_manager(root, host_admission=host) as manager:
            replay = Replays(manager)
            self.assertEqual(replay.status(identifier)['preview'], partial['preview'])
            final = replay.advance(identifier, checkpoint_every=1)
            self.assertEqual(final['linefills'], [])
            self.assertIsNone(final['preview'])
            for view, rows in expected.items():
                self.assertEqual(replay.view(identifier, view, limit=100), rows)
            actions = [row['event'] for row in expected['line_events']['events']]
            self.assertEqual(actions[-2], {'action': 'delete', 'id': 1, 'reason': 'capacity'})

    def test_saved_ir_and_replay_restore_the_same_live_graph(self):
        script = source(6, DELETE)
        program = import_program(export_program(self.session.compile(script, 'linefills.pine')))
        self.assertEqual(program.schema_version, 'openpine.typed-ir.28')
        primary = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
        bars = ROOT / 'conformance/linefills/bars.json'
        events = [MarketEvent.from_payload(row) for row in market_events(bars, primary)]
        _, expected = self.session.run(script, 'linefills.pine', data=historical(events, primary))
        self.assertEqual(decode(encode(expected), type(expected)), expected)
        self.assertEqual(expected.linefills, ())
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        objects = ObjectStore(root / 'objects')
        for profile in (None, INTRABAR):
            with self.subTest(profile=profile):
                def make():
                    sink = ResultSink(objects, tempfile.mkdtemp(dir=root))
                    sink.enable_lines()
                    return ReplayEngine(self.session, program, (primary,), primary.context_id,
                                        sink, calculation_profile=profile)

                first = make()
                for event in events[:3]:
                    first.apply(event)
                status = first.status()
                self.assertEqual(status['lines'][0]['y2'], 35.)
                self.assertEqual(status['preview']['lines'][0]['y2'], 30.)
                self.assertEqual(status['linefills'], [{**FILL, 'color': '#12345680'}])
                self.assertEqual(status['preview']['linefills'], [{**FILL, 'color': '#FF000080'}])
                state = first.checkpoint()
                self.assertEqual(state['runtime']['schema_version'], 'openpine.runtime-state.20')
                output_state = first.evaluation.output_sink.checkpoint(1)
                self.assertEqual(output_state['schema_version'], 'openpine.output-prefix.5')
                resumed = make()
                resumed.restore(state)
                output = resumed.evaluation.output_sink
                output.restore(output_state, 1)
                output.validate_lines(resumed.evaluation.lines)
                self.assertEqual(resumed.status(), status)
                for event in events[3:6]:
                    resumed.apply(event)
                self.assertEqual(resumed.status()['linefills'], [FILL])
                self.assertEqual(resumed.status()['lines'][0], UPPER)
                opened = resumed.apply(events[6])
                self.assertEqual(opened['linefills'], [FILL])
                self.assertEqual(opened['preview']['linefills'], [])
                final = resumed.apply(events[7])
                self.assertEqual(final['linefills'], [])
                self.assertEqual(final['lines'], [asdict(line) for line in expected.lines])
                self.assertIsNone(final['preview'])

                broken = deepcopy(state)
                broken['runtime']['lines']['fills']['live'][0]['line2'] = 90
                with self.assertRaisesRegex(ValueError, 'missing boundary'):
                    make().restore(broken)
                if profile:
                    broken = deepcopy(state)
                    broken['intrabar']['preview']['linefills'][0]['line1'] = 90
                    with self.assertRaisesRegex(ValueError, 'missing boundary'):
                        make().restore(broken)

    def test_pair_replacement_and_line_eviction_cascade(self):
        lines = FilledLines()
        for i in range(LIMIT):
            lines.apply('line.new', (0, float(i), 3, float(i)))
        old = lines.apply('linefill.new', (1, 2, '#00112280'))
        other = lines.apply('linefill.new', (2, 3, None))
        new = lines.apply('linefill.new', (2, 1, '#11223380'))
        self.assertEqual((old, other, new), (1, 2, 3))
        self.assertIsNone(lines.apply('linefill.get_line1', (old,)))
        self.assertEqual(lines.apply('linefill.get_line1', (new,)), 2)
        self.assertEqual(lines.apply('linefill.get_line2', (new,)), 1)
        preview = lines.fork()
        preview.apply('line.new', (0, 10., 3, 30.))
        self.assertNotIn(1, preview.live)
        self.assertEqual([row['id'] for row in preview.fill_snapshot()], [other])
        self.assertIn(new, lines.fills)
        lines.apply('line.delete', (2,))
        self.assertEqual(lines.fill_snapshot(), [])

    def test_fill_capacity_and_quota_reject_before_mutation(self):
        lines = FilledLines()
        for i in range(11):
            lines.apply('line.new', (0, float(i), 3, float(i)))
        for a, b in combinations(lines.live, 2):
            lines.apply('linefill.new', (a, b, '#12345680'))
        self.assertEqual(len(lines.fills), LIMIT)
        self.assertEqual(min(lines.fills), 6)
        previous = lines.checkpoint()
        for _ in range(EVENT_LIMIT - lines.fill_calls):
            lines.apply('linefill.delete', (None,))
        with self.assertRaises(KernelError) as error:
            lines.apply('linefill.new', (1, 2, None))
        self.assertEqual(error.exception.code, 'LINEFILL_CALL_LIMIT')
        self.assertEqual(lines.checkpoint(), previous)
