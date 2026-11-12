"""Versioned alert effects, replay deduplication and durable journal output."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from apps.openpine.bootstrap import local_manager
from apps.openpine.replay import Replays
from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.alerts import CONDITION_TEMPLATE_PROFILE
from packages.contracts.application import CompileRequest
from packages.contracts.inputs import settings_for
from packages.contracts.trading import BrokerSettings
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.host_memory import HostAdmission
from packages.execution.realtime import ReplayEngine
from packages.execution.service import Application
from packages.execution.session import Session
from packages.marketdata import read_bars
from tests.test_realtime import PRIMARY, Sink, configuration, tape
from tools.alerts_fixtures import (
    BINDING_MESSAGES, BINDING_OVERRIDES, CONDITION_TEMPLATE, INDICATOR, PROTECTIVE_FILLS,
    binding_fill_source, condition_template_source, fill_source, protective_events, strategy_source,
)
from tools.conformance import ALERT_CASES, run_cases

SOURCE = '//@version=6\n' + INDICATOR
OBSERVED = 'openpine.observed-broker.1'


class Alerts:
    def __init__(self):
        self.rows = []
        self.events = []

    def emit(self, sequence, timestamp, events):
        self.rows.extend((sequence, event['ordinal'], event['kind'], event['message']) for event in events)
        self.events.extend(events)


class Output(Sink):
    def emit(self, site, title, value_type, value, index, timestamp, *, presentation=None):
        super().emit(site, title, value_type, value, index, timestamp)

    def emit_lines(self, index, timestamp, events):
        pass

    def enable_simulation(self, **settings):
        pass


class AlertTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def replay(self, source=SOURCE, *, inputs=None, **settings):
        program = import_program(export_program(self.session.compile(source)))
        if inputs is not None:
            settings['settings'] = settings_for(program, inputs)
        alerts = Alerts()
        engine = ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, Output(),
                              alert_sink=alerts, **settings)
        return engine, alerts

    def test_versioned_fixtures_and_compile_metadata(self):
        report = run_cases(self.session, cases_path=ALERT_CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (13, 17))
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

    def test_condition_templates_use_closed_pass_values_and_resume(self):
        expected = [
            (2, 0, 'alertcondition', 'TEST:REPLAY/1 10 11 10 11 2 '
             '1970-01-01T00:00:00Z 1970-01-01T00:01:00Z 22 22 na {{custom}}'),
            (2, 1, 'alert', 'literal {{close}}'),
            (5, 0, 'alertcondition', 'TEST:REPLAY/1 12 13 12 13 2 '
             '1970-01-01T00:01:00Z 1970-01-01T00:02:00Z 26 26 11 {{custom}}'),
            (5, 1, 'alert', 'literal {{close}}'),
        ]
        events = list(tape(2))
        for version in range(4, 7):
            with self.subTest(version=version):
                source = condition_template_source(version)
                profile = {'alert_condition_profile': CONDITION_TEMPLATE_PROFILE}
                original, literal = self.replay(source)
                full, rendered = self.replay(source, **profile)
                self.assertNotEqual(original.identity, full.identity)
                self.assertEqual(full.qualification()['alert_condition_execution']['profile_id'],
                                 CONDITION_TEMPLATE_PROFILE)
                self.assertEqual(full.status()['alerts']['conditions'][0]['message'], CONDITION_TEMPLATE)
                for event in events:
                    original.apply(event)
                    full.apply(event)
                self.assertEqual(rendered.rows, expected)
                self.assertEqual(original.sink.rows, full.sink.rows)
                self.assertEqual(literal.rows[0][3], CONDITION_TEMPLATE)

                head, before = self.replay(source, **profile)
                for event in events[:5]:
                    head.apply(event)
                saved = loads(canonical(head.checkpoint()))
                wrong, _ = self.replay(source)
                with self.assertRaises(ValueError):
                    wrong.restore(saved)
                restored, after = self.replay(source, **profile)
                restored.restore(saved)
                restored.apply(events[4])
                restored.apply(events[5])
                self.assertEqual(before.rows + after.rows, expected)
                self.assertEqual(restored.checkpoint(), full.checkpoint())

        warm, alerts = self.replay(condition_template_source(), alert_condition_profile=CONDITION_TEMPLATE_PROFILE,
                                   environments={'primary': {'instrument': None, 'history_last_ms': 0}})
        warm.apply(replace(events[2], sequence=0, revision=0))
        self.assertEqual(alerts.rows, [])
        for sequence, event in enumerate(events[3:], start=1):
            warm.apply(replace(event, sequence=sequence))
        self.assertEqual(alerts.rows, [(3, *row[1:]) for row in expected[2:]])

    def test_condition_template_profile_and_plot_rejections(self):
        profile = {'alert_condition_profile': CONDITION_TEMPLATE_PROFILE}
        for version in (2, 3):
            source = (f'//@version={version}\nstudy("Templates")\n'
                      'alertcondition(close > 0, "Price", "{{close}}")\nplot(close)')
            legacy, alerts = self.replay(source)
            for event in tape(1):
                legacy.apply(event)
            self.assertEqual(alerts.rows, [(2, 0, 'alertcondition', '{{close}}')])
            with self.assertRaisesRegex(ValueError, 'Pine v4–v6'):
                self.replay(source, **profile)
        with self.assertRaisesRegex(ValueError, 'requires an indicator'):
            self.replay(strategy_source(), **profile)
        with self.assertRaisesRegex(ValueError, 'Unknown alertcondition replay profile'):
            self.replay(condition_template_source(), alert_condition_profile='unknown')

        source = '//@version=6\nindicator("Plots")\nalertcondition(true, "A", \'{template}\')\n{plots}\n'
        for template, plots in (('{{plot_20}}', 'plot(close)'),
                                ('{{plot_0}}', 'plotshape(true)\nplot(close)')):
            with self.subTest(template=template, plots=plots), self.assertRaises(OpenPineError) as error:
                self.replay(source.format(template=template, plots=plots), **profile)
            self.assertEqual(error.exception.diagnostic.code, 'ALERT_TEMPLATE_PLOT')
        for plots in ('plot(close, "Other")', 'plot(close, "A")\nplot(open, "A")'):
            with self.subTest(plots=plots):
                engine, alerts = self.replay(source.format(template='{{plot("A")}}', plots=plots), **profile)
                opened, update, closed = tape(1)
                engine.apply(opened)
                engine.apply(update)
                with self.assertRaises(OpenPineError) as error:
                    engine.apply(closed)
                self.assertEqual(error.exception.diagnostic.code, 'ALERT_TEMPLATE_PLOT')
                self.assertEqual((alerts.rows, engine.sink.rows, engine.evaluation.count), ([], [], 0))

    def test_condition_template_utf8_limit_applies_before_publication(self):
        source = ('//@version=6\nindicator("Bytes")\n'
                  'alertcondition(true, "Length", "{message}")\nplot(close)\n')
        event = replace(list(tape(1))[-1], sequence=0, revision=0)
        for length in (2038, 2039):
            with self.subTest(length=length):
                engine, alerts = self.replay(source.format(message='é' * length + '{{timenow}}'),
                                             alert_condition_profile=CONDITION_TEMPLATE_PROFILE)
                if length == 2038:
                    engine.apply(event)
                    self.assertEqual(len(alerts.rows[0][3].encode('utf-8')), 4096)
                else:
                    with self.assertRaises(OpenPineError) as error:
                        engine.apply(event)
                    self.assertEqual(error.exception.diagnostic.code, 'ALERT_TEMPLATE_MESSAGE')
                    self.assertEqual((alerts.rows, engine.sink.rows, engine.evaluation.count), ([], [], 0))

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

    def test_fill_message_uses_fill_observation_in_all_versions(self):
        for version in range(1, 7):
            with self.subTest(version=version):
                events = list(tape(3))
                engine, alerts = self.replay(fill_source(version), order_fill_alerts=version < 4)
                self.assertEqual(engine.qualification()['fill_alert_execution']['profile_id'],
                                 'openpine.strategy-fill-alerts.1')
                for event in events:
                    engine.apply(event)
                self.assertEqual(alerts.rows, [
                    (3, 0, 'order_fill', 'Entry 12' if version >= 4 else ''),
                    (6, 0, 'order_fill', 'Close 28' if version >= 4 else ''),
                ])
                fills = [event['value'] for event in engine.sink.events if event['kind'] == 'fill']
                self.assertEqual([(row['order_id'], row['fill_id']) for row in alerts.events],
                                 [(row['order_id'], row['fill_id']) for row in fills])
                if version < 4:
                    control, control_alerts = self.replay(fill_source(version))
                    for event in events:
                        control.apply(event)
                    self.assertEqual(control.sink.events, engine.sink.events)
                    self.assertEqual(control.simulation.summary(), engine.simulation.summary())
                    self.assertFalse(control_alerts.rows)

    def test_disable_flag_is_frozen_and_restore_does_not_repeat_open_fill(self):
        events = list(tape(3))
        for version in (5, 6):
            with self.subTest(version=version):
                source = fill_source(version, disabled='close < 12')
                engine, alerts = self.replay(source)
                for event in events[:3]:
                    engine.apply(event)
                checkpoint = loads(canonical(engine.checkpoint()))
                self.assertEqual(len(checkpoint['alerts']['disabled_orders']), 1)
                resumed, output = self.replay(source)
                resumed.restore(checkpoint)
                for event in events[3:7]:
                    resumed.apply(event)
                self.assertEqual(output.rows, [(6, 0, 'order_fill', 'Close 28')])
                self.assertFalse(alerts.rows)
                tail, tail_output = self.replay(source)
                tail.restore(loads(canonical(resumed.checkpoint())))
                self.assertEqual(tail.apply(events[6]), resumed.status())
                for event in events[7:]:
                    tail.apply(event)
                    resumed.apply(event)
                self.assertFalse(tail_output.rows)
                self.assertEqual(tail.checkpoint(), resumed.checkpoint())
                self.assertEqual(tail.simulation.broker.fill_count, 2)

    def test_observed_pending_replacement_and_cancel(self):
        source = '''//@version=6
strategy("Pending fills", default_qty_type=strategy.fixed)
if bar_index == 0
    strategy.entry("A", strategy.long, limit=8, alert_message="Original", disable_alert=true)
    strategy.entry("B", strategy.long, limit=8, alert_message="Cancelled")
if bar_index == 1
    strategy.entry("A", strategy.long, limit=10, alert_message="New " + str.tostring(close))
    strategy.cancel("B")
plot(strategy.equity)
'''
        settings = {'broker_profile': 'openpine.observed-broker.1'}
        engine, prefix = self.replay(source, **settings)
        events = list(tape(3))
        events[7] = replace(events[7], low=7., close=7.)
        events[8] = replace(events[8], low=7.)
        for event in events[:3]:
            engine.apply(event)
        checkpoint = loads(canonical(engine.checkpoint()))
        self.assertEqual(len(checkpoint['alerts']['disabled_orders']), 1)
        restored, alerts = self.replay(source, **settings)
        restored.restore(checkpoint)
        for event in events[3:]:
            restored.apply(event)
            engine.apply(event)
        self.assertEqual(alerts.rows, [(7, 0, 'order_fill', 'New 7')])
        self.assertEqual(alerts.rows, prefix.rows)
        self.assertEqual(restored.checkpoint(), engine.checkpoint())
        self.assertEqual(restored.checkpoint()['alerts']['disabled_orders'], [])

    def test_fill_and_script_alerts_follow_calculation_phases(self):
        source = '''//@version=6
strategy("Phase fills", default_qty_type=strategy.fixed, process_orders_on_close=true, calc_on_order_fills=true)
if bar_index == 0 and strategy.position_size == 0
    alert("Submit", alert.freq_all)
    strategy.entry("L", strategy.long, alert_message="Fill " + str.tostring(close))
if strategy.position_size > 0
    alert("Held", alert.freq_all)
plot(strategy.equity)
'''
        engine, alerts = self.replay(source)
        for event in tape(1):
            engine.apply(event)
        self.assertEqual(alerts.rows, [(2, 0, 'alert', 'Submit'), (2, 1, 'order_fill', 'Fill 11'),
                                      (2, 2, 'alert', 'Held')])

    def test_fill_bindings_use_frozen_inputs_current_aliases_and_prior_var(self):
        events = list(protective_events(PRIMARY.context_id))
        settings = {'broker_profile': OBSERVED, 'inputs': BINDING_OVERRIDES}
        for version in (4, 5, 6):
            with self.subTest(version=version):
                source = binding_fill_source(version)
                engine, alerts = self.replay(source, **settings)
                control, _ = self.replay(binding_fill_source(version, messages=False), **settings)
                for event in events:
                    engine.apply(event)
                    control.apply(event)
                self.assertEqual([(seq, kind, text) for seq, _, kind, text in alerts.rows], BINDING_MESSAGES)
                self.assertEqual(engine.qualification()['fill_alert_execution']['profile_id'],
                                 'openpine.strategy-fill-alerts.3')
                self.assertEqual([(i, t, value) for i, t, _, value in engine.sink.rows],
                                 [(i, t, value) for i, t, _, value in control.sink.rows])
                def fills(selected):
                    return [(row['bar_index'], row['value']['direction'], row['value']['price'], row['value']['quantity'])
                            for row in selected.sink.events if row['kind'] == 'fill']
                self.assertEqual(fills(engine), fills(control))
                self.assertEqual(engine.simulation.summary()['statistics'], control.simulation.summary()['statistics'])

                for cut in (7, 8, 9):
                    first, prefix = self.replay(source, **settings)
                    for event in events[:cut]:
                        first.apply(event)
                    restored, suffix = self.replay(source, **settings)
                    restored.restore(loads(canonical(first.checkpoint())))
                    restored.apply(events[cut - 1])
                    for event in events[cut:]:
                        restored.apply(event)
                    self.assertEqual(prefix.events + suffix.events, alerts.events)
                    self.assertEqual(first.sink.events + restored.sink.events, engine.sink.events)
                    self.assertEqual(restored.checkpoint(), engine.checkpoint())

    def test_after_close_fill_uses_regular_pass_var_before_recalculation(self):
        source = '''//@version=6
strategy("At close", default_qty_type=strategy.fixed, process_orders_on_close=true, calc_on_order_fills=true)
var float mark = 1.0
if strategy.position_size == 0
    mark := close + 10
    strategy.entry("L", strategy.long, alert_message="Market " + str.tostring(mark))
else
    mark := close + 20
    alert("Held")
plot(mark)
'''
        engine, alerts = self.replay(source)
        for event in tape(1):
            engine.apply(event)
        self.assertEqual(alerts.rows, [(2, 0, 'order_fill', 'Market 21'), (2, 1, 'alert', 'Held')])
        self.assertEqual(engine.sink.rows[-1][-1], 31.)

    def test_fill_before_first_confirmed_var_uses_v6_bool_default_after_restore(self):
        source = '''//@version=6
strategy("Active open", default_qty_type=strategy.fixed, calc_on_every_tick=true)
var bool flag = true
if bar_index == 0 and close == open
    strategy.entry("L", strategy.long, alert_message=str.tostring(flag))
plot(strategy.equity)
'''
        events = list(tape(1))
        engine, prefix = self.replay(source)
        engine.apply(events[0])
        restored, alerts = self.replay(source)
        restored.restore(loads(canonical(engine.checkpoint())))
        for event in events[1:]:
            engine.apply(event)
            restored.apply(event)
        self.assertEqual(alerts.rows, [(1, 0, 'order_fill', 'false')])
        self.assertEqual(alerts.events, prefix.events)
        self.assertEqual(restored.checkpoint(), engine.checkpoint())

    def test_order_close_all_and_exit_alerts_in_all_versions(self):
        for version in range(1, 7):
            with self.subTest(version=version):
                direction = 'true' if version == 1 else 'strategy.long'
                source = f'''//@version={version}
strategy("Order fills", default_qty_type=strategy.fixed)
if bar_index == 0
    strategy.order("A", {direction})
    strategy.order("B", {direction}, limit=13)
    strategy.order("C", {direction}, limit=13)
    strategy.exit("X", "B", limit=13)
if bar_index == 1
    strategy.close_all()
plot(strategy.equity)
'''
                observed, alerts = self.replay(source, broker_profile=OBSERVED, order_fill_alerts=True)
                self.assertEqual(observed.qualification()['fill_alert_execution']['profile_id'],
                                 'openpine.strategy-fill-alerts.2')
                control, quiet = self.replay(source, broker_profile=OBSERVED)
                for event in tape(3):
                    observed.apply(event)
                    control.apply(event)
                self.assertEqual(alerts.rows, [(3, 0, 'order_fill', ''), (3, 1, 'order_fill', ''),
                    (3, 2, 'order_fill', ''),
                    (5, 0, 'order_fill', ''), (6, 0, 'order_fill', '')])
                self.assertEqual(observed.sink.events, control.sink.events)
                self.assertEqual(observed.simulation.summary(), control.simulation.summary())
                self.assertFalse(quiet.rows)
                fills = [row['value'] for row in observed.sink.events if row['kind'] == 'fill']
                self.assertEqual(fills[-1]['quantity'], '2')
                self.assertEqual([(row['order_id'], row['fill_id']) for row in alerts.events],
                                 [(row['order_id'], row['fill_id']) for row in fills])

    def test_close_all_uses_versioned_named_message_and_disable_flag(self):
        for version in range(4, 7):
            with self.subTest(version=version):
                convert = 'tostring' if version == 4 else 'str.tostring'
                disable = '' if version == 4 else ', disable_alert=close < 14'
                source = f'''//@version={version}
strategy("Close all alert", default_qty_type=strategy.fixed)
if bar_index == 0
    strategy.order("A", strategy.long, alert_message="Open")
if bar_index == 1
    strategy.close_all(alert_message="Close " + {convert}(close){disable})
plot(strategy.equity)
'''
                engine, alerts = self.replay(source, broker_profile=OBSERVED)
                for event in tape(3):
                    engine.apply(event)
                expected = [(3, 0, 'order_fill', 'Open')]
                if version == 4:
                    expected.append((6, 0, 'order_fill', 'Close 14'))
                self.assertEqual(alerts.rows, expected)
                self.assertEqual(engine.simulation.broker.fill_count, 2)

    def test_exit_selects_broker_role_and_handles_missing_and_empty_messages(self):
        normal = 'limit=13, stop=8'
        loss = {4: {'low': 7., 'close': 7.}, 5: {'low': 7.}}
        trail = {7: {'low': 11., 'close': 11.}, 8: {'low': 11.}}
        for version, arguments, revisions, sequence, role, expected in (
                (4, normal, {}, 5, 'limit', 'Fallback 13'),
                (5, normal + ', alert_profit="Profit " + str.tostring(close)', {}, 5, 'limit', 'Profit 13'),
                (6, normal + ', alert_loss=""', loss, 4, 'stop', ''),
                (6, normal + ', alert_loss=na', loss, 4, 'stop', 'Fallback 7'),
                (6, 'trail_points=1, trail_offset=1, alert_trailing="Trail " + str.tostring(close)',
                 trail, 7, 'trailing_stop', 'Trail 11')):
            with self.subTest(version=version, role=role, message=expected):
                convert = 'tostring' if version == 4 else 'str.tostring'
                source = f'''//@version={version}
strategy("Protective alerts", default_qty_type=strategy.fixed)
if bar_index == 0
    strategy.order("L", strategy.long, alert_message="Entry")
    strategy.exit("X", "L", {arguments}, alert_message="Fallback " + {convert}(close))
plot(strategy.equity)
'''
                engine, alerts = self.replay(source, broker_profile=OBSERVED, broker_settings=BrokerSettings(tick_size='1'))
                for event in tape(3):
                    engine.apply(replace(event, **revisions.get(event.sequence, {})))
                self.assertEqual(alerts.rows, [(3, 0, 'order_fill', 'Entry'), (sequence, 0, 'order_fill', expected)])
                trigger, = (row['value'] for row in engine.sink.events if row['kind'] == 'protection_trigger')
                self.assertEqual((trigger['role'], trigger['order_id']), (role, alerts.events[1]['order_id']))

    def test_pending_entry_and_partial_protection_restore_at_fill_boundaries(self):
        events = list(protective_events(PRIMARY.context_id))
        reference, complete = self.replay(PROTECTIVE_FILLS, broker_profile=OBSERVED)
        for event in events:
            reference.apply(event)
        self.assertEqual(complete.rows, [
            (4, 0, 'order_fill', 'Entry 9'), (5, 0, 'order_fill', 'Target 13'), (5, 1, 'alert', 'Script'),
        ])
        self.assertEqual(reference.simulation.broker.fill_count, 3)
        for cut in (3, 5, 6, 8):
            with self.subTest(cut=cut):
                first, prefix = self.replay(PROTECTIVE_FILLS, broker_profile=OBSERVED)
                for event in events[:cut]:
                    first.apply(event)
                saved = loads(canonical(first.checkpoint()))
                self.assertEqual(len(saved['alerts']['disabled_orders']), int(cut < 8))
                restored, tail = self.replay(PROTECTIVE_FILLS, broker_profile=OBSERVED)
                restored.restore(saved)
                restored.apply(events[cut-1])
                for event in events[cut:]:
                    restored.apply(event)
                self.assertEqual(prefix.rows + tail.rows, complete.rows)
                self.assertEqual(first.sink.events + restored.sink.events, reference.sink.events)
                self.assertEqual(restored.checkpoint(), reference.checkpoint())

    def test_oca_peer_cancel_and_protective_replacement_use_new_alert_fields(self):
        source = '''//@version=6
strategy("OCA and protection", default_qty_type=strategy.fixed)
if bar_index == 0
    strategy.order("L", strategy.long, qty=2, limit=10, oca_name="g", oca_type=strategy.oca.cancel, alert_message="Winner")
    strategy.order("Peer", strategy.long, limit=10, oca_name="g", oca_type=strategy.oca.cancel, alert_message="Peer")
    strategy.exit("X", "L", qty=1, limit=15, stop=8, disable_alert=true)
    strategy.exit("Y", "L", qty=1, limit=17, stop=7, alert_message="Cancelled")
if bar_index == 1
    strategy.exit("X", "L", qty=1, limit=14, stop=8, alert_profit="Replaced")
    strategy.cancel("Y")
plot(strategy.equity)
'''
        engine, alerts = self.replay(source, broker_profile=OBSERVED)
        for event in protective_events(PRIMARY.context_id):
            engine.apply(event)
        self.assertEqual(alerts.rows, [(4, 0, 'order_fill', 'Winner'), (6, 0, 'order_fill', 'Replaced')])
        self.assertFalse(engine.checkpoint()['alerts']['disabled_orders'])
        self.assertEqual(engine.simulation.broker.fill_count, 2)

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
