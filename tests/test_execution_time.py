"""Pine execution clock, durable inputs and observed intrabar calculations."""

import unittest
from dataclasses import replace

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.alerts import CONDITION_TEMPLATE_PROFILE
from packages.contracts.calendar import utc_datetime
from packages.contracts.contexts import ContextBinding, MarketContext
from packages.contracts.live import RECEIVED_PROFILE
from packages.contracts.storage import ResourcePolicy, RunPlan
from packages.contracts.wire import canonical, digest, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.driver import HistoricalDriver, MemorySource
from packages.execution.preparation import make_plan, validate_request
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.marketdata import data_identity, read_bars
from tools import execution_time_fixtures as fixtures
from tools.conformance import run_cases
from tools.semantic_matrix import render
from tools.specification import ROOT


class Output:
    def __init__(self):
        self.rows = []

    def emit(self, site, title, value_type, value, index, timestamp, **kwargs):
        self.rows.append((title, value))


class Alerts:
    def __init__(self):
        self.messages = []

    def emit(self, sequence, timestamp, events):
        self.messages.extend(event['message'] for event in events)


class ExecutionTimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data = read_bars(ROOT / fixtures.DIRECTORY / 'bars.json')
        cls.context = MarketContext(cls.data.context_id, cls.data.symbol, cls.data.timeframe, 'recorded.1')

    def replay(self, program, *, alert=False, contexts=None, environments=None):
        output, alerts = Output(), Alerts()
        engine = ReplayEngine(self.session, program, contexts or (self.context,), self.context.context_id, output,
                              profile=RECEIVED_PROFILE, alert_sink=alerts, environments=environments,
                              alert_condition_profile=CONDITION_TEMPLATE_PROFILE if alert else None)
        return engine, output, alerts

    def test_corpus_and_version_boundary(self):
        report = run_cases(self.session, cases_path=fixtures.CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (4, 6))
        matrix = render().decode('utf-8')
        self.assertIn('| `timenow` | v3–v6 | PARTIAL |', matrix)
        self.assertIn('| `timenow` | v1–v2 | N/A |', matrix)

    def test_historical_clock_is_bound_without_changing_legacy_wire_records(self):
        request = {'schema_version': 'openpine.job-request.2', 'input_ref': 'a' * 64,
                   'snapshot_id': 'b' * 64, 'timeout_seconds': 60}
        old = make_plan(request, ResourcePolicy()).payload()
        self.assertNotIn('execution_time_ms', old)
        self.assertEqual(RunPlan.from_payload(old).payload(), old)
        for invalid in (True, -1, None, '1', 1.5):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_request({**request, 'execution_time_ms': invalid})
        plan = make_plan({**request, 'execution_time_ms': fixtures.EXECUTION_TIME}, ResourcePolicy())
        self.assertEqual(RunPlan.from_payload(plan.payload()), plan)
        self.assertNotEqual(digest(old), digest(plan.payload()))

        program = self.session.compile(fixtures.source(6, 'plot(timenow)'))
        runtime = self.session.validate_program(program)

        def driver(timestamp):
            return HistoricalDriver(runtime.prepare(program), MemorySource(self.data), execution_time_ms=timestamp)

        original = driver(fixtures.EXECUTION_TIME)
        saved = []
        original.run(pause_after=1, checkpoint=lambda value: saved.append(loads(canonical(value.checkpoint()))))
        with self.assertRaisesRegex(ValueError, 'another environment'):
            driver(fixtures.EXECUTION_TIME + 1).restore(saved[0])
        resumed = driver(fixtures.EXECUTION_TIME)
        resumed.restore(saved[0])
        resumed.run()
        self.assertEqual(resumed.finish(data_identity(self.data)).outputs[0].values, fixtures.HISTORICAL_OUTPUTS[0])

    def test_delayed_close_repeat_and_restart_preserve_clock_gaps_and_alerts(self):
        program = import_program(export_program(self.session.compile(
            (ROOT / fixtures.EXAMPLE).read_text(encoding='utf-8'), fixtures.EXAMPLE)))
        events = fixtures.tape()
        direct, output, messages = self.replay(program, alert=True)
        previews = {}
        for event in events:
            status = direct.apply(event)
            self.assertEqual(direct.apply(event), status)
            if event.kind != 'close':
                previews[event.sequence] = status['preview']
                self.assertEqual(status['preview']['outputs'][0]['value'], event.event_time)
        self.assertEqual(tuple(value for name, value in output.rows if name == 'Clock'), fixtures.CONFIRMED_TIMES)
        self.assertEqual(tuple(value for name, value in output.rows if name == 'Update gap'), fixtures.REPLAY_OUTPUTS[1])
        self.assertEqual(messages.messages, [utc_datetime(ts).isoformat(timespec='seconds').replace('+00:00', 'Z')
                                              + '|' + str(ts) for ts in fixtures.CONFIRMED_TIMES])
        for cut in (fixtures.OPEN_CUT, fixtures.UPDATE_CUT):
            with self.subTest(cut=cut):
                first, prefix, before = self.replay(program, alert=True)
                for event in events[:cut]:
                    first.apply(event)
                resumed, tail, after = self.replay(program, alert=True)
                resumed.restore(loads(canonical(first.checkpoint())))
                self.assertEqual(resumed.status()['preview'], previews[cut - 1])
                for event in events[cut:]:
                    resumed.apply(event)
                self.assertEqual(resumed.status(), direct.status())
                self.assertEqual(prefix.rows + tail.rows, output.rows)
                self.assertEqual(before.messages + after.messages, messages.messages)

    def test_replay_prefix_freezes_clock_and_binds_it_to_identity(self):
        program = self.session.compile(fixtures.source(6, 'plot(timenow, "Clock")'))
        environment = {'instrument': None, 'history_last_ms': fixtures.OPENINGS[0],
                       'execution_time_ms': fixtures.EXECUTION_TIME}
        engine, output, _ = self.replay(program, environments={self.context.context_id: environment})
        events = fixtures.tape()
        engine.apply(events[0])
        state = loads(canonical(engine.checkpoint()))
        changed, _, _ = self.replay(program, environments={self.context.context_id:
                                    {**environment, 'execution_time_ms': fixtures.EXECUTION_TIME + 1}})
        with self.assertRaisesRegex(ValueError, 'another program/context'):
            changed.restore(state)
        missing, _, _ = self.replay(program, environments={self.context.context_id:
                                    {'instrument': None, 'history_last_ms': fixtures.OPENINGS[0]}})
        with self.assertRaises(OpenPineError) as error:
            missing.apply(events[0])
        self.assertEqual(error.exception.diagnostic.code, 'EXECUTION_TIME_REQUIRED')
        for event in events[1:]:
            engine.apply(event)
        self.assertEqual(tuple(value for _, value in output.rows),
                         (fixtures.EXECUTION_TIME, *fixtures.CONFIRMED_TIMES[1:]))

    def test_requested_context_uses_its_own_observation_and_shared_historical_clock(self):
        target = MarketContext('requested', 'TEST:SECOND', '1', 'recorded.1')
        data = replace(self.data, context_id=target.context_id, symbol=target.symbol)
        binding = ContextBinding(target, data_identity(data).sha256)
        program = self.session.compile(fixtures.source(6, 'plot(timenow, "Clock")\n'
            'plot(request.security("TEST:SECOND", "1", timenow), "Requested")'))
        historical = self.session.execute(program, data=self.data, primary_context=self.context,
            context_sources=((binding, MemorySource(data)),), execution_time_ms=fixtures.EXECUTION_TIME)
        self.assertEqual(tuple(row.values for row in historical.outputs), (fixtures.HISTORICAL_OUTPUTS[0],) * 2)

        engine, output, _ = self.replay(program, contexts=(self.context, target))
        primary_open, primary_update, primary_close = fixtures.tape()[1:4]
        secondary_open = replace(primary_open, context_id=target.context_id, event_time=primary_open.time)
        secondary_update = replace(primary_update, context_id=target.context_id, event_time=primary_open.time + 6_000)
        secondary_close = replace(primary_close, context_id=target.context_id, event_time=primary_open.time + 61_000)
        events = (secondary_open, primary_open, secondary_update, primary_update, secondary_close, primary_close)
        for sequence, event in enumerate(events):
            status = engine.apply(replace(event, sequence=sequence))
            if sequence in (2, 3):
                self.assertEqual(tuple(row['value'] for row in status['preview']['outputs']),
                                 (event.event_time, secondary_update.event_time))
        self.assertEqual(output.rows, [('Clock', primary_close.event_time), ('Requested', secondary_close.event_time)])
