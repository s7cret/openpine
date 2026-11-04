"""Linefill identity, resource bounds and replay checkpoint integrity."""
from copy import deepcopy
from dataclasses import asdict
from itertools import combinations
from pathlib import Path
import tempfile
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.codec import decode, encode
from packages.contracts.contexts import MarketContext
from packages.contracts.drawings import EVENT_LIMIT, LIMIT
from packages.contracts.intrabar import PROFILE as INTRABAR
from packages.contracts.realtime import MarketEvent
from packages.execution.artifacts import export_program, import_program
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.runtime.linefills import FilledLines
from packages.runtime.series import KernelError
from packages.storage.objects import ObjectStore
from packages.storage.results import ResultSink
from tests.test_realtime import historical
from tools.installed_lines_smoke import market_events
from tools.linefills_fixtures import DELETE, FILL, UPPER, source
from tools.specification import ROOT


class LinefillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

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
