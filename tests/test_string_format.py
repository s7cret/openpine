"""Versioned string templates through conformance, saved IR and replay."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.model import ValueType
from packages.contracts.scalar_functions import MAX_STRING, ScalarBudget, ScalarError
from packages.contracts.string_format import MAX_VALUES, OPERATION
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.runtime.string_format import FormatError, render
from packages.storage.objects import ObjectStore
from packages.storage.results import ResultSink
from tests.test_alerts import Alerts
from tools import string_format_fixtures as fixtures
from tools.conformance import run_cases
from tools.semantic_matrix import matrix
from tools.specification import ROOT


class StringFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_versioned_corpus_and_function_matrix(self):
        report = run_cases(self.session, cases_path=fixtures.CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (9, 18))
        rows, _ = matrix()
        for version, entries in rows.items():
            statuses = [status for name, _, status, detail in entries if name == 'str.format' and OPERATION in detail]
            self.assertEqual(statuses, ['PARTIAL'] * (0 if version < 4 else 1 if version == 4 else 2))

    def test_saved_ir_types_and_positional_contract(self):
        for version in (4, 5, 6):
            with self.subTest(version=version):
                last = f'str.format("{{{MAX_VALUES - 1}}}",' + ','.join(map(str, range(MAX_VALUES))) + ')'
                body = '\n'.join(('plain = str.format("L {0}", 1)', 'value = str.format("{0}", close)',
                                 'format = close > open ? "{0}" : "[{0}]"', 'dynamic = str.format(format, 1)',
                                 'wide = ' + last, 'plot(wide == "31" ? 1 : 0)'))
                program = import_program(export_program(self.session.compile(fixtures.source(version, body))))
                self.session.validate_program(program)
                plain, value, _, dynamic, wide = program.statements[1:-1]
                self.assertEqual([node.expression.value_type for node in (plain, value, dynamic, wide)],
                                 [ValueType('string', qualifier) for qualifier in ('simple', 'series', 'series', 'simple')])
                self.assertEqual(len(wide.expression.arguments), MAX_VALUES + 1)
                call = plain.expression
                arguments = tuple(replace(arg, source_index=1 - i) for i, arg in enumerate(call.arguments))
                forged = replace(plain, expression=replace(call, arguments=arguments, evaluation_order=(1, 0)))
                with self.assertRaises(OpenPineError) as failure:
                    self.session.validate_program(replace(program, statements=(program.statements[0], forged, *program.statements[2:])))
                self.assertEqual(failure.exception.diagnostic.code, 'IR_STRING_FORMAT_INPUT')

    def test_quotes_and_shared_string_budget(self):
        self.assertEqual(render("'{0} It''s' {0}", ('{1}',), ScalarBudget()), "{0} It's {1}")
        self.assertEqual(render('{000001}', ('left', 'right'), ScalarBudget()), 'right')
        self.assertEqual(render('{99,number,0.00}', (), ScalarBudget()), '{99,number,0.00}')
        self.assertEqual(render('{0,number,percent}', (1e308,), ScalarBudget()).count(','), 103)
        with self.assertRaisesRegex(FormatError, 'Unclosed apostrophe'):
            render("value '{0}", (1,), ScalarBudget())
        with self.assertRaisesRegex(FormatError, 'Unsupported'):
            render('{0,number,0E0}', (1.,), ScalarBudget())
        with self.assertRaisesRegex(ScalarError, 'before allocation'):
            render('{0}{0}', ('x' * (MAX_STRING // 2 + 1),), ScalarBudget())
        with self.assertRaisesRegex(ScalarError, 'work budget'):
            render('{0}', ('value',), ScalarBudget(remaining=8))

    def test_saved_ir_restores_one_label_and_one_confirmed_alert(self):
        context = fixtures.PRIMARY
        events = fixtures.tape()
        text = (ROOT / fixtures.EXAMPLE).read_text(encoding='utf-8')
        program = import_program(export_program(self.session.compile(text)))
        root = Path(self.enterContext(TemporaryDirectory()))
        objects = ObjectStore(root / 'objects')

        def create(name):
            sink, alerts = ResultSink(objects, root / name), Alerts()
            self.addCleanup(sink.close)
            sink.enable_labels()
            engine = ReplayEngine(self.session, program, (context,), context.context_id, sink, alert_sink=alerts,
                                  environments=fixtures.ENVIRONMENTS)
            return engine, sink, alerts

        full, _, full_alerts = create('full')
        for event in events:
            full.apply(event)
        head, sink, earlier = create('head')
        cut = fixtures.ALERT[0]
        for event in events[:cut]:
            head.apply(event)
        self.assertEqual(head.status()['labels'], [])
        self.assertEqual(head.preview['labels'][0]['text'], fixtures.PREVIEW)
        self.assertEqual(earlier.rows, [])
        resumed, output, later = create('resumed')
        resumed.restore(loads(canonical(head.checkpoint())))
        output.restore(loads(canonical(sink.checkpoint(1))), 1)
        for event in events[cut:]:
            resumed.apply(event)
        self.assertEqual(resumed.checkpoint(), full.checkpoint())
        self.assertEqual(later.rows, full_alerts.rows)
        label, = resumed.status()['labels']
        self.assertEqual(label['text'], fixtures.ALERT[1])
        self.assertEqual([(seq, text) for seq, _, _, text in later.rows], [fixtures.ALERT])
