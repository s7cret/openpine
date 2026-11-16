"""Closed IR defaults and deterministic DST presentation through replay."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.model import ReferenceIR, ValueType
from packages.contracts.scalar_functions import ScalarBudget, ScalarError
from packages.contracts.time_format import DEFAULT_PATTERN, OPERATION
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.runtime.time_format import TimeFormatError, load_zone, render
from packages.storage.objects import ObjectStore
from packages.storage.results import ResultSink
from tests.test_alerts import Alerts
from tools import time_format_fixtures as fixtures
from tools.conformance import run_cases
from tools.semantic_matrix import matrix
from tools.specification import ROOT


class TimeFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_all_versions_and_coverage(self):
        report = run_cases(self.session, cases_path=fixtures.CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (10, 18))
        rows, _ = matrix()
        for version, entries in rows.items():
            statuses = [status for name, _, status, detail in entries
                        if name == 'str.format_time' and OPERATION in detail]
            self.assertEqual(statuses, ['PARTIAL'] if version >= 5 else [])

    def test_saved_ir_binds_both_defaults_and_rejects_forgery(self):
        for version in (5, 6):
            with self.subTest(version=version):
                text = fixtures.source(version, 'stamp = str.format_time(0)\nplot(time)')
                program = import_program(export_program(self.session.compile(text)))
                self.session.validate_program(program)
                self.assertEqual(program.schema_version, 'openpine.typed-ir.10')
                store = program.statements[1]
                call = store.expression
                self.assertEqual(call.value_type, ValueType('string', 'series'))
                self.assertEqual(call.operation, OPERATION)
                time, pattern, zone = call.arguments
                self.assertEqual((time.origin, pattern.origin, zone.origin), ('source', 'default', 'default'))
                self.assertEqual(pattern.expression.value, DEFAULT_PATTERN)
                self.assertIsInstance(zone.expression, ReferenceIR)
                self.assertEqual(zone.expression.contract.slot, 'environment.syminfo.timezone')
                wrong_mask = replace(pattern, expression=replace(pattern.expression, value='Z'))
                wrong_zone = replace(zone, expression=replace(pattern.expression, value='UTC'))
                for arguments in ((time, wrong_mask, zone), (time, pattern, wrong_zone)):
                    forged = replace(store, expression=replace(call, arguments=arguments))
                    with self.assertRaises(OpenPineError) as failure:
                        self.session.validate_program(replace(program, statements=(program.statements[0], forged, program.statements[-1])))
                    self.assertEqual(failure.exception.diagnostic.code, 'IR_TIME_FORMAT_DEFAULT')

    def test_pinned_zone_resources_and_limits(self):
        load_zone.cache_clear()
        with patch('packages.runtime.time_format.ZoneInfo', wraps=ZoneInfo,
                   side_effect=AssertionError('System zoneinfo must be bypassed.')) as loader:
            rendered = render(fixtures.milliseconds(2025, 11, 2, 6, 30), fixtures.MASK, fixtures.NY_ZONE, ScalarBudget())
            self.assertEqual(rendered, fixtures.FALL_SECOND)
            loader.from_file.assert_called_once()
        self.assertEqual(render(0, 'Z', 'GMT0', ScalarBudget()), '+0000')
        with self.assertRaises(TimeFormatError) as failure:
            render(-62135596800000, 'yyyy', 'UTC-1', ScalarBudget())
        self.assertEqual(failure.exception.code, 'TIME_FORMAT_RANGE')
        with self.assertRaisesRegex(ScalarError, 'before allocation'):
            render(0, 'Z-' * 1025, 'UTC', ScalarBudget())

    def test_checkpoint_keeps_dst_label_and_one_confirmed_alert(self):
        context = fixtures.PRIMARY
        events = fixtures.tape()
        source = (ROOT / fixtures.EXAMPLE).read_text(encoding='utf-8')
        program = import_program(export_program(self.session.compile(source)))
        root = Path(self.enterContext(TemporaryDirectory()))
        objects = ObjectStore(root / 'objects')

        def create(name):
            sink, alerts = ResultSink(objects, root / name), Alerts()
            self.addCleanup(sink.close)
            sink.enable_labels()
            engine = ReplayEngine(self.session, program, (context,), context.context_id, sink, alert_sink=alerts)
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
        output.restore(loads(canonical(sink.checkpoint(0))), 0)
        for event in events[cut:]:
            resumed.apply(event)
        self.assertEqual(resumed.checkpoint(), full.checkpoint())
        self.assertEqual(later.rows, full_alerts.rows)
        label, = resumed.status()['labels']
        self.assertEqual(label['text'], fixtures.ALERT[1])
        self.assertEqual([(seq, text) for seq, _, _, text in later.rows], [fixtures.ALERT])
