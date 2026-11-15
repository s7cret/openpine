"""Versioned angle contracts, numeric boundaries and stateful consumer proof."""

import math
import sys
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, ValueType
from packages.contracts.alerts import CONDITION_TEMPLATE_PROFILE
from packages.contracts.contexts import ContextBinding
from packages.contracts.numeric_functions import NumericError, invoke, validate_call
from packages.contracts.prepared_requests import graph
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.driver import MemorySource
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from tests.test_alerts import Alerts, Output
from tests.test_realtime import historical
from tools import trigonometry_fixtures as fixtures
from tools.conformance import run_cases
from tools.semantic_matrix import matrix
from tools.specification import ROOT


class TrigonometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_fixture_outcomes_and_all_78_reference_rows(self):
        report = run_cases(self.session, cases_path=fixtures.CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (22, 19))
        rows, _ = matrix()
        for version, entries in rows.items():
            prefix = 'math.' if version >= 5 else ''
            names = ('sin', 'cos', 'tan', 'asin', 'acos', 'atan')
            for name in (*names, 'todegrees', 'toradians'):
                selected = [(state, detail) for source, _, state, detail in entries if source == prefix + name]
                expected = (4 if version >= 5 else 1) if name in names else int(version >= 4)
                self.assertEqual(len(selected), expected, (version, name))
                for state, detail in selected:
                    self.assertEqual(state, 'PARTIAL')
                    self.assertIn(f'numeric.{name}', detail)

    def test_domains_tangent_poles_and_extreme_conversions(self):
        for operation, value, expected in (
            ('asin', -1., -1.5707963267948966), ('asin', 1., 1.5707963267948966),
            ('acos', -1., 3.141592653589793), ('acos', 1., 0.),
            ('atan', 1e308, 1.5707963267948966), ('atan', -1e308, -1.5707963267948966),
            ('toradians', 1e308, 1.7453292519943295e306),
        ):
            with self.subTest(operation=operation, value=value):
                self.assertTrue(math.isclose(invoke('numeric.' + operation, (value,)), expected, rel_tol=1e-14))
        for name in ('asin', 'acos'):
            self.assertIsNone(invoke('numeric.' + name, (math.nextafter(1., math.inf),)))
            self.assertIsNone(invoke('numeric.' + name, (math.nextafter(-1., -math.inf),)))
            self.assertIsNone(invoke('numeric.' + name, (None,)))
        left = invoke('numeric.tan', (1.5707953267948966,))
        right = invoke('numeric.tan', (1.5707973267948966,))
        self.assertTrue(math.isclose(left, 1e6, rel_tol=1e-8))
        self.assertTrue(math.isclose(right, -1e6, rel_tol=1e-8))
        for value, code in ((sys.float_info.max, 'NUMERIC_OVERFLOW'), (math.inf, 'NUMERIC_RANGE')):
            with self.assertRaises(NumericError) as error:
                invoke('numeric.todegrees', (value,))
            self.assertEqual(error.exception.code, code)

    def test_input_qualifiers_constants_and_computed_target(self):
        for version in (4, 5, 6):
            modern = version >= 5
            prefix = 'math.' if modern else ''
            fmt = 'str.tostring' if modern else 'tostring'
            request = 'request.security' if modern else 'security'
            name = 'angle' if modern else 'x'
            outputs = ('math.pi', 'math.e', 'math.phi', 'math.rphi',
                       f'{prefix}cos({name}=0)', f'{prefix}sin(a)',
                       f'{prefix}atan(syminfo.mintick)', f'{prefix}tan(close)',
                       f'{prefix}toradians(180)')
            body = '\n'.join((
                'a = input.float(0.5)' if modern else 'a = input(0.5)',
                'constant() => math.pi',
                *(f'plot({expression})' for expression in outputs),
                f'plot({request}("TEST:ANGLES", {fmt}(int({prefix}cos(constant() * 0)) + 1), close))',
            ))
            program = import_program(export_program(self.session.compile(fixtures.source(version, body))))
            nodes = [node.arguments[0].expression for node in program.statements[2:-1]]
            expected = ('const',) * 5 + ('input', 'simple', 'series', 'series')
            self.assertEqual([node.value_type for node in nodes], [ValueType('float', q) for q in expected])
            for node in nodes[4:]:
                self.assertIsNotNone(validate_call(node, version))
            self.assertEqual(graph(program)['requests'][0]['timeframe'], '2')

    def test_saved_ir_replay_preserves_the_one_confirmed_angle_alert(self):
        source = (ROOT / fixtures.EXAMPLE).read_text(encoding='utf-8')
        program = import_program(export_program(self.session.compile(source)))
        events = fixtures.tape()
        primary, higher = fixtures.PRIMARY, fixtures.HIGHER
        higher_data = (ContextBinding(higher, 'a' * 64), MemorySource(historical(events, higher)))
        result = self.session.execute(program, data=historical(events, primary), primary_context=primary,
                                      context_sources=(higher_data,))
        self.assertEqual(result.outputs[0].values, fixtures.HISTORICAL)

        def create():
            alerts = Alerts()
            engine = ReplayEngine(self.session, program, (primary, higher), primary.context_id, Output(),
                                  alert_sink=alerts, alert_condition_profile=CONDITION_TEMPLATE_PROFILE)
            return engine, alerts

        full, alerts = create()
        for event in events:
            full.apply(event)
            if event.context_id == higher.context_id and event.kind == 'update' and event.time == 120_000:
                self.assertEqual([row['value'] for row in full.preview['outputs']], [fixtures.ANGLE])
                self.assertEqual(alerts.rows, [])
        self.assertEqual(tuple(row[-1] for row in full.sink.rows), fixtures.REPLAY)
        self.assertEqual([(seq, msg) for seq, _, _, msg in alerts.rows], [fixtures.ALERT])
        for cut in (2, 13, 18, fixtures.ALERT[0], fixtures.ALERT[0] + 1):
            head, prefix = create()
            for event in events[:cut]:
                head.apply(event)
            tail, suffix = create()
            tail.restore(loads(canonical(head.checkpoint())))
            for event in events[cut - 1:]:
                tail.apply(event)
            self.assertEqual(head.sink.rows + tail.sink.rows, full.sink.rows)
            self.assertEqual(prefix.rows + suffix.rows, alerts.rows)
            self.assertEqual(tail.checkpoint(), full.checkpoint())
