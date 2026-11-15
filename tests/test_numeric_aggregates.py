"""Verify versioned scalar aggregates and saved-IR replay through two contexts."""

from dataclasses import replace
import sys
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.alerts import CONDITION_TEMPLATE_PROFILE
from packages.contracts.context_ir import request_nodes
from packages.contracts.contexts import ContextBinding
from packages.contracts.model import ValueType
from packages.contracts.numeric_functions import AVERAGE, NumericError, invoke, validate_call
from packages.contracts.prepared_requests import graph
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.driver import MemorySource
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from tests.test_alerts import Alerts, Output
from tests.test_realtime import historical
from tools import numeric_aggregates_fixtures as fixtures
from tools.conformance import run_cases
from tools.semantic_matrix import matrix
from tools.specification import ROOT


class NumericAggregateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_pinned_variadic_overloads_have_an_explicit_slice(self):
        report = run_cases(self.session, cases_path=fixtures.CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (15, 6))
        rows, _ = matrix()
        for version, entries in rows.items():
            prefix = 'math.' if version >= 5 else ''
            for operation in ('avg', 'min', 'max'):
                selected = [(status, detail) for name, _, status, detail in entries if name == prefix + operation]
                expected_count = (2 if operation == 'avg' else 8) if version >= 5 else 1
                self.assertEqual(len(selected), expected_count, (version, operation))
                for status, detail in selected:
                    self.assertEqual(status, 'PARTIAL')
                    self.assertIn('numeric.' + operation, detail)

    def test_average_stays_finite_with_large_terms_and_preserves_tiny_values(self):
        large = sys.float_info.max
        tiny = float.fromhex('0x0.0000000000001p-1022')
        for args, expected in (((large, large), large), ((large, large, -large, -large, 5), 1.),
                               ((tiny, tiny, tiny), tiny), ((1, 2), 1.5),
                               ((2**63 - 1, -2**63), -.5), ((None, 2.), None)):
            with self.subTest(args=args):
                self.assertEqual(invoke(AVERAGE, args), expected)
        with self.assertRaises(NumericError) as error:
            invoke(AVERAGE, (float('inf'), 1.))
        self.assertEqual(error.exception.code, 'NUMERIC_RANGE')

    def test_modern_qualifiers_and_pure_numeric_computed_target(self):
        for version in (5, 6):
            cases = (('2', ValueType('int', 'const')), ('2.', ValueType('float', 'const')),
                     ('period', ValueType('int', 'input')), ('ratio', ValueType('float', 'input')),
                     ('int(syminfo.mintick)', ValueType('int', 'simple')),
                     ('syminfo.mintick', ValueType('float', 'simple')),
                     ('bar_index', ValueType('int', 'series')), ('close', ValueType('float', 'series')))
            plots = [f'plot(math.{operation}({source}, 3, 4))'
                     for operation in ('min', 'max') for source, _ in cases]
            expected = [kind for _ in ('min', 'max') for _, kind in cases]
            body = '\n'.join(('period = input.int(2)', 'ratio = input.float(2.)', *plots,
                    'plot(math.avg(period, 2))', 'plot(math.avg(period, close))',
                    'plot(request.security("TEST:MIDPOINT", str.tostring(int(math.avg(period, 2))), close))'))
            program = import_program(export_program(self.session.compile(fixtures.source(version, body))))
            published = [node.arguments[0].expression.value_type for node in program.statements[3:-1]]
            self.assertEqual(published, [*expected, ValueType('float', 'simple'), ValueType('float', 'series')])
            self.assertEqual(graph(program)['requests'][0]['timeframe'], '2')
            timeframe = request_nodes(program)[0].arguments[1].expression
            average = timeframe.arguments[0].expression.arguments[0].expression
            self.assertEqual(validate_call(average, version).operation, AVERAGE)
            with self.assertRaises(NumericError):
                validate_call(replace(average, value_type=ValueType('float', 'const')), version)

    def test_saved_ir_replay_crosses_only_at_confirmation(self):
        script = (ROOT / fixtures.EXAMPLE).read_text(encoding='utf-8')
        program = import_program(export_program(self.session.compile(script)))
        events = fixtures.tape()
        primary, higher = fixtures.PRIMARY, fixtures.HIGHER
        _, result = self.session.run(script, data=historical(events, primary), primary_context=primary,
            context_sources=((ContextBinding(higher, 'a' * 64), MemorySource(historical(events, higher))),))
        output, = result.outputs
        self.assertEqual(output.values, fixtures.HISTORICAL)

        def create():
            alerts = Alerts()
            return ReplayEngine(self.session, program, (primary, higher), primary.context_id, Output(),
                                alert_sink=alerts, alert_condition_profile=CONDITION_TEMPLATE_PROFILE), alerts

        full, alerts = create()
        for step in events:
            full.apply(step)
            if step.context_id == higher.context_id and step.kind == 'update' and step.time == 120_000:
                self.assertEqual([row['value'] for row in full.preview['outputs']], [12.])
                self.assertEqual(alerts.rows, [])
        self.assertEqual(tuple(row[-1] for row in full.sink.rows), fixtures.REPLAY)
        self.assertEqual([(seq, msg) for seq, _, _, msg in alerts.rows], [fixtures.ALERT])
        for cut in (2, 13, 18, fixtures.ALERT[0], fixtures.ALERT[0] + 1):
            head, prefix = create()
            for step in events[:cut]:
                head.apply(step)
            tail, suffix = create()
            tail.restore(loads(canonical(head.checkpoint())))
            for step in events[cut - 1:]:
                tail.apply(step)
            self.assertEqual(prefix.rows + suffix.rows, alerts.rows)
            self.assertEqual(head.sink.rows + tail.sink.rows, full.sink.rows)
            self.assertEqual(tail.checkpoint(), full.checkpoint())
