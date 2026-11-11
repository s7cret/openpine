"""Versioned alert effects, replay deduplication and durable journal output."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from apps.openpine.bootstrap import local_manager
from apps.openpine.replay import Replays
from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.application import CompileRequest
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.host_memory import HostAdmission
from packages.execution.realtime import ReplayEngine
from packages.execution.service import Application
from packages.execution.session import Session
from packages.marketdata import read_bars
from tests.test_realtime import PRIMARY, Sink, configuration, tape
from tools.alerts_fixtures import INDICATOR, strategy_source
from tools.conformance import ALERT_CASES, run_cases

SOURCE = '//@version=6\n' + INDICATOR


class Alerts:
    def __init__(self):
        self.rows = []

    def emit(self, sequence, timestamp, events):
        self.rows.extend((sequence, event['ordinal'], event['kind'], event['message']) for event in events)


class Output(Sink):
    def emit_lines(self, index, timestamp, events):
        pass

    def enable_simulation(self, **settings):
        pass


class AlertTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def replay(self, source=SOURCE, **settings):
        program = import_program(export_program(self.session.compile(source)))
        alerts = Alerts()
        engine = ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, Output(),
                              alert_sink=alerts, **settings)
        return engine, alerts

    def test_versioned_fixtures_and_compile_metadata(self):
        report = run_cases(self.session, cases_path=ALERT_CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (8, 5))
        source = '''//@version=6
indicator("Alerts")
title = "Break" + "out"
alertcondition(close > 10, title)
plot(close)
'''
        compiled = Application().handle(CompileRequest(source))
        self.assertEqual(compiled['status'], 'compiled', compiled)
        condition, = compiled['alert_conditions']
        self.assertEqual((condition['title'], condition['message']), ('Breakout', ''))

    def test_frequency_and_historical_boundary_survive_resume(self):
        engine, alerts = self.replay()
        events = list(tape(2))
        for event in events[:2]:
            engine.apply(event)
        self.assertEqual(alerts.rows, [(1, 0, 'alert', 'open 10.5'), (1, 1, 'alert', 'sample')])
        saved = loads(canonical(engine.checkpoint()))
        restored, tail = self.replay()
        restored.restore(saved)
        restored.apply(events[1])
        self.assertEqual(tail.rows, [])
        for event in events[2:]:
            restored.apply(event)
        self.assertEqual(tail.rows, [
            (2, 0, 'alertcondition', 'Breakout'), (2, 1, 'alert', 'sample'), (2, 2, 'alert', 'confirmed'),
            (3, 0, 'alert', 'open 12'), (3, 1, 'alert', 'sample'), (4, 0, 'alert', 'sample'),
            (5, 0, 'alertcondition', 'Breakout'), (5, 1, 'alert', 'sample'), (5, 2, 'alert', 'confirmed'),
        ])
        self.assertEqual(restored.status()['alerts']['records'], 11)
        for event in events[2:]:
            engine.apply(event)
        self.assertEqual(engine.checkpoint(), restored.checkpoint())
        warm, warm_alerts = self.replay(environments={'primary': {'instrument': None, 'history_last_ms': 0}})
        warm.apply(replace(events[2], sequence=0, revision=0))
        self.assertFalse(warm_alerts.rows)
        self.assertEqual(warm.status()['alerts']['records'], 0)

    def test_once_is_per_written_call_and_all_keeps_loop_order(self):
        source = '''//@version=6
indicator("Frequencies")
if close > 10
    alert("first")
    alert("second")
    for i = 0 to 1
        alert("loop " + str.tostring(i), alert.freq_all)
plot(close)
'''
        engine, alerts = self.replay(source)
        for event in tape(1):
            engine.apply(event)
        self.assertEqual(alerts.rows, [
            (1, 0, 'alert', 'first'), (1, 1, 'alert', 'second'),
            (1, 2, 'alert', 'loop 0'), (1, 3, 'alert', 'loop 1'),
            (2, 0, 'alert', 'loop 0'), (2, 1, 'alert', 'loop 1'),
        ])
        too_many, collected = self.replay(source.replace('to 1', 'to 64'))
        events = list(tape(1))
        too_many.apply(events[0])
        with self.assertRaises(OpenPineError) as error:
            too_many.apply(events[1])
        self.assertEqual(error.exception.diagnostic.code, 'ALERT_LIMIT')
        self.assertEqual(collected.rows, [])

    def test_strategy_default_and_tick_alerts_preserve_historical_trades(self):
        for tick, sequences in ((False, [2, 5]), (True, [1, 2, 3, 4, 5])):
            with self.subTest(tick=tick):
                engine, alerts = self.replay(strategy_source(every_tick=tick))
                for event in tape(2):
                    engine.apply(event)
                self.assertEqual([row[0] for row in alerts.rows], sequences)
                self.assertEqual(engine.status()['alerts']['conditions'], [])
        bars = read_bars(Path(ALERT_CASES).parent/'bars.json')
        _, observed = self.session.run(strategy_source(), data=bars)
        _, control = self.session.run(strategy_source(with_alerts=False), data=bars)
        def trades(run):
            return [(row['kind'], row['bar_index'], row['value'].get('price'), row['value'].get('quantity'))
                    for row in run.simulation['events'] if row['kind'] in ('order', 'fill', 'trade')]
        self.assertEqual(trades(observed), trades(control))
        self.assertEqual(observed.simulation['statistics'], control.simulation['statistics'])
        self.assertEqual(observed.outputs[0].values, control.outputs[0].values)

    def test_worker_restarts_inside_open_bar_and_publishes_alert_pages(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        host = HostAdmission(probe=lambda: None)
        events = [event.payload() for event in tape(2)]
        with local_manager(root, host_admission=host) as manager:
            replays = Replays(manager)
            baseline = replays.create(configuration(manager, SOURCE))['replay_id']
            replays.append(baseline, events)
            replays.advance(baseline, checkpoint_every=1)
            expected = replays.view(baseline, 'alert_events', limit=100)
            chart = replays.view(baseline, 'chart')
            replay = replays.create(configuration(manager, SOURCE))['replay_id']
            replays.append(replay, events)
            state = replays.advance(replay, max_events=2, checkpoint_every=1)
            self.assertEqual((state['sequence'], state['confirmed_bars'], state['alerts']['records']), (1, 0, 2))
            checkpoint = replays.repository.checkpoint(replay)
            self.assertEqual(checkpoint['schema_version'], 'openpine.replay-checkpoint.2')
            self.assertEqual(checkpoint['output']['schema_version'], 'openpine.output-prefix.5')
            self.assertEqual(checkpoint['alert_journal']['records'], 2)
            self.assertEqual(len(checkpoint['engine']['alerts']['seen']), 1)
            retained = set()
            from packages.storage.replay import references
            references(manager.db, manager.objects, retained.add)
            self.assertIn(checkpoint['alert_journal']['pending']['object_id'], retained)
        with local_manager(root, host_admission=host) as manager:
            replays = Replays(manager)
            replays.advance(replay, checkpoint_every=1)
            self.assertEqual(replays.view(replay, 'alert_events', limit=100), expected)
            page = replays.view(replay, 'alert_events', start=2, limit=3)
            self.assertEqual(page, {'events': expected['events'][2:5], 'next': 5, 'total': 11})
            self.assertEqual(replays.view(replay, 'chart'), chart)
