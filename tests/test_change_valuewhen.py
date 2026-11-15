"""Versioned change, nominal event colors and checkpointed two-context replay."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.alerts import CONDITION_TEMPLATE_PROFILE
from packages.contracts.checkpoint_stream import RecordSequence
from packages.contracts.contexts import ContextBinding
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.driver import MemorySource
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.runtime.checkpoints import layout
from packages.runtime.history import StorageBudget
from packages.runtime.series.signals import EventState, export_signal, fork_signal, restore_signal
from packages.storage import checkpoint_records
from packages.storage.objects import ObjectStore
from tests.test_alerts import Alerts, Output
from tests.test_realtime import historical
from tools import change_valuewhen_fixtures as fixtures
from tools.conformance import run_cases
from tools.semantic_matrix import matrix
from tools.specification import ROOT


class ColoredOutput(Output):
    def emit(self, site, title, value_type, value, index, timestamp, *, presentation=None):
        self.rows.append((index, value, presentation['color'] if presentation else None))


class ChangeValueWhenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_versioned_change_and_valuewhen_conformance(self):
        report = run_cases(self.session, cases_path=fixtures.CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (22, 6))
        rows, _ = matrix()
        self.assertEqual([status for name, _, status, _ in rows[5] if name == 'ta.change'], ['PARTIAL'] * 6)
        for version in (5, 6):
            self.assertEqual([status for name, _, status, _ in rows[version] if name == 'ta.valuewhen'], ['PARTIAL'] * 4)

    def test_color_overload_preserves_numeric_identity_and_diagnostics(self):
        for version in (5, 6):
            for source, base, suffix in (('na', 'float', ''), ('bar_index', 'int', ''), ('color(na)', 'color', '.color')):
                with self.subTest(version=version, source=source):
                    text = fixtures.source(version, f'x=ta.valuewhen(source={source},occurrence=0,condition=true)\nplot(1)')
                    program = import_program(export_program(self.session.compile(text)))
                    call, = (node for node in layout(program)[0].values() if node.operation == 'signals.valuewhen')
                    self.assertEqual(call.value_type.base, base)
                    self.assertEqual(call.overload_id, f'overload.openpine.signals.v{version}.valuewhen{suffix}')
            with self.assertRaises(OpenPineError) as error:
                self.session.compile(fixtures.source(version, 'plot(ta.valuewhen(true,"#12AB5680",0))'))
            self.assertEqual(error.exception.diagnostic.code, 'ARGUMENT_TYPE')

    def test_rgba_checkpoint_roundtrip_and_preview_preserve_missing_events(self):
        green, red = fixtures.GREEN, fixtures.RED
        state = EventState(StorageBudget(), 'valuewhen', 'color', 5)
        self.assertIsNone(state.update(False, green, 1))
        self.assertIsNone(state.update(True, green, 1))
        self.assertEqual(state.update(True, None, 1), green)
        kind, saved = loads(canonical(export_signal(state)))
        self.assertEqual(saved['slots'], [0x12AB5680, None])
        preview = fork_signal(state, StorageBudget())
        self.assertIsNone(preview.update(True, red, 1))
        self.assertEqual(export_signal(state), (kind, saved))
        restored = restore_signal(kind, saved, StorageBudget(), 3, 'color', 5)
        self.assertIsNone(restored.update(True, red, 1))
        self.assertEqual(restored.update(True, green, 1), red)
        for token in (-1, 2**32, '#12AB5680', False, 1.0):
            damaged = deepcopy(saved)
            damaged['slots'][0] = token
            with self.subTest(token=token), self.assertRaises(ValueError):
                restore_signal(kind, damaged, StorageBudget(), 3, 'color', 5)

    def test_maximum_color_event_queue_fits_checkpoint_decode_budget(self):
        size = 100_000
        state = EventState(StorageBudget(), 'valuewhen', 'color', 6)
        for i in range(size):
            result = state.update(True, f'#{i:08X}', size - 1)
        self.assertEqual(result, '#00000000')
        limit = 64 * 1024**2
        with TemporaryDirectory() as directory:
            objects = ObjectStore(Path(directory))
            native = {'kernels': RecordSequence(1, lambda _: export_signal(state)[1])}
            sha = checkpoint_records.put(objects, native, limit_bytes=limit)
            saved = checkpoint_records.get(objects, sha, limit_bytes=limit)['kernels'][0]
            restored = restore_signal('valuewhen', saved, StorageBudget(), size, 'color', 6)
        self.assertEqual(restored.update(True, fixtures.GREEN, size - 1), '#00000001')

    def test_saved_ir_replay_resumes_before_requested_direction_alert(self):
        script = (ROOT / fixtures.EXAMPLE).read_text(encoding='utf-8')
        program = import_program(export_program(self.session.compile(script)))
        events = fixtures.tape()
        primary, higher = fixtures.PRIMARY, fixtures.HIGHER
        _, result = self.session.run(script, data=historical(events, primary), primary_context=primary,
            context_sources=((ContextBinding(higher, 'a' * 64), MemorySource(historical(events, higher))),))
        output, = result.outputs
        self.assertEqual((output.values, output.colors), (fixtures.HISTORICAL, fixtures.HISTORICAL_COLORS))

        def create():
            alerts = Alerts()
            replay = ReplayEngine(self.session, program, (primary, higher), primary.context_id, ColoredOutput(),
                                  alert_sink=alerts, alert_condition_profile=CONDITION_TEMPLATE_PROFILE)
            return replay, alerts

        full, alerts = create()
        for step in events:
            full.apply(step)
        self.assertEqual(full.sink.rows, list(zip(range(8), fixtures.REPLAY, fixtures.REPLAY_COLORS, strict=True)))
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
