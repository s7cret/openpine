"""Tick rounding and formatting at the compiler, replay and broker boundaries."""

from dataclasses import replace
from decimal import Decimal, localcontext
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.contexts import ContextBinding
from packages.contracts.tick_price import FORMAT, ROUND
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.marketdata.providers import input_snapshot
from packages.runtime.environment import rounded_price
from tests.test_alerts import Alerts, Output
from tools import tick_price_fixtures as fixtures
from tools.conformance import market_source, run_cases, run_market_case
from tools.semantic_matrix import matrix
from tools.specification import ROOT


class TickPriceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_market_input_corpus_and_function_matrix(self):
        report = run_cases(self.session, cases_path=fixtures.CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (12, 12))
        rows, _ = matrix()
        for version, entries in rows.items():
            rounding = [status for name, _, status, detail in entries
                        if name.rsplit('.', 1)[-1] == 'round_to_mintick' and ROUND in detail]
            self.assertEqual(rounding, ['PARTIAL'] * (0 if version < 4 else 1 if version == 4 else 2))

    def test_exact_half_ticks_and_large_decimal_in_small_decimal_context(self):
        samples = ((100.125, '0.25', '100.25'), (-100.125, '0.25', '-100.00'),
                   (100.12499999999999, '0.25', '100.00'), (-100.12500000000001, '0.25', '-100.25'),
                   (1.000025, '0.00005', '1.00005'), (-0.005, '0.01', '0.00'))
        with localcontext() as ctx:
            ctx.prec = 2
            for price, tick, expected in samples:
                with self.subTest(price=price, tick=tick):
                    self.assertEqual(format(rounded_price(price, tick), 'f'), expected)
            self.assertEqual(rounded_price(1e308, '0.0000000000000001'), Decimal('1e308'))

    def test_environment_contract_checks_forged_format_and_target_scope(self):
        text = fixtures.source(6, 'plot(str.tostring(close,format.mintick)=="100.25"?1:0)')
        program = import_program(export_program(self.session.compile(text)))
        self.assertEqual(program.schema_version, 'openpine.typed-ir.10')
        plot = program.statements[-1]
        argument = plot.arguments[0]
        conditional = argument.expression
        compare = conditional.condition
        call = compare.left
        self.assertEqual(call.operation, FORMAT)
        fmt = call.arguments[1]
        invalid = replace(call, arguments=(call.arguments[0], replace(fmt, expression=replace(fmt.expression, value='#'))))
        condition = replace(compare, left=invalid)
        forged = replace(plot, arguments=(replace(argument, expression=replace(conditional, condition=condition)), *plot.arguments[1:]))
        with self.assertRaises(OpenPineError) as error:
            self.session.validate_program(replace(program, statements=(*program.statements[:-1], forged)))
        self.assertEqual(error.exception.diagnostic.code, 'IR_TICK_PRICE_INPUT')
        body = 'plot(request.security("TEST:TICK-QUARTER", str.tostring(math.round_to_mintick(2)), close))'
        with self.assertRaises(OpenPineError):
            self.session.compile(fixtures.source(6, body))

    def test_requested_symbol_tick_and_formatted_replay_alert_after_resume(self):
        script = (ROOT / fixtures.EXAMPLE).read_text(encoding='utf-8')
        program = import_program(export_program(self.session.compile(script)))
        events = fixtures.tape()
        primary, higher = fixtures.PRIMARY, fixtures.HIGHER

        def snapshot(context, tick):
            candles = tuple((event.open, event.high, event.low, event.close) for event in events
                            if event.context_id == context.context_id and event.kind == 'close')
            return input_snapshot(fixtures.market_input(fixtures.bars(context, candles), tick))

        parent, child = snapshot(primary, '0.01'), snapshot(higher, '0.25')
        primary_upload = replace(primary, source='uploaded-file.1')
        higher_upload = replace(higher, source='uploaded-file.1')
        _, historical = run_market_case(self.session, script, 'two-tick-grids.pine', parent, {
            'primary_context': primary_upload,
            'context_sources': ((ContextBinding(higher_upload, child.sha256), market_source(child)),),
        })
        self.assertEqual(tuple(output.values for output in historical.outputs), ((100.13,) * 8, fixtures.HISTORICAL))

        def create(environments=fixtures.ENVIRONMENTS):
            alerts = Alerts()
            engine = ReplayEngine(self.session, program, (primary, higher), primary.context_id,
                                  Output(), alert_sink=alerts, environments=environments)
            return engine, alerts

        full, alerts = create()
        for event in events:
            full.apply(event)
            if event.context_id == higher.context_id and event.kind == 'update' and event.time == 120_000:
                self.assertEqual([row['value'] for row in full.preview['outputs']], [100.13, 101.25])
                self.assertEqual(alerts.rows, [])
        self.assertEqual(tuple(row[-1] for row in full.sink.rows[::2]), (100.13,) * 8)
        self.assertEqual(tuple(row[-1] for row in full.sink.rows[1::2]), fixtures.REPLAY)
        self.assertEqual([(seq, msg) for seq, _, _, msg in alerts.rows], [fixtures.ALERT])

        head, prefix = create()
        cut = fixtures.ALERT[0]
        for event in events[:cut]:
            head.apply(event)
        checkpoint = loads(canonical(head.checkpoint()))
        tail, suffix = create()
        tail.restore(checkpoint)
        for event in events[cut - 1:]:
            tail.apply(event)
        self.assertEqual(head.sink.rows + tail.sink.rows, full.sink.rows)
        self.assertEqual(prefix.rows + suffix.rows, alerts.rows)
        self.assertEqual(tail.checkpoint(), full.checkpoint())
        swapped, _ = create({**fixtures.ENVIRONMENTS, higher.context_id:
                             {'instrument': fixtures.instrument('0.05'), 'history_last_ms': None}})
        with self.assertRaises(ValueError):
            swapped.restore(checkpoint)

    def test_rounded_limit_is_accepted_with_the_same_frozen_broker_tick(self):
        snapshot = input_snapshot(fixtures.strategy_input())
        text = (ROOT / fixtures.STRATEGY).read_text(encoding='utf-8')
        _, result = run_market_case(self.session, text, 'limit-price.pine', snapshot, {})
        simulation = result.simulation
        self.assertEqual(simulation['policy']['settings']['tick_size'], '0.25')
        order, = (row for row in simulation['events'] if row['kind'] == 'order')
        fill, = (row for row in simulation['events'] if row['kind'] == 'fill')
        self.assertEqual(order['value']['intent']['limit'], '100.25')
        self.assertEqual((fill['bar_index'], fill['value']['price']), (1, '100.25'))
