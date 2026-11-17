"""Visible chart bounds, historical recovery and version availability."""

import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.contexts import MarketContext
from packages.contracts.storage import ResourcePolicy, RunPlan
from packages.contracts.wire import canonical, digest, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.driver import HistoricalDriver, MemorySource
from packages.execution.preparation import make_plan, validate_request
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.marketdata import data_identity, read_bars
from tools import visible_range_fixtures as fixtures
from tools.conformance import run_cases
from tools.semantic_matrix import render
from tools.specification import ROOT


class VisibleRangeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data = read_bars(ROOT / fixtures.DIRECTORY / 'bars.json')
        cls.program = import_program(export_program(cls.session.compile(fixtures.source(6), fixtures.EXAMPLE)))

    def test_versioned_corpus_and_coverage(self):
        report = run_cases(self.session, cases_path=fixtures.CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (8, 10))
        matrix = render().decode('utf-8')
        for name in (fixtures.LEFT, fixtures.RIGHT):
            self.assertIn(f'| `{name}` | v5–v6 | PARTIAL |', matrix)
            self.assertIn(f'| `{name}` | v1–v4 | N/A |', matrix)

    def test_checkpoint_binds_the_exact_range_and_preserves_full_history(self):
        runtime = self.session.validate_program(self.program)

        def driver(bounds):
            return HistoricalDriver(runtime.prepare(self.program), MemorySource(self.data), visible_range=bounds)

        saved = []
        driver(fixtures.FIRST).run(pause_after=2, checkpoint=lambda value: saved.append(loads(canonical(value.checkpoint()))))
        with self.assertRaisesRegex(ValueError, 'another environment'):
            driver(fixtures.SECOND).restore(saved[0])
        resumed = driver(fixtures.FIRST)
        resumed.restore(saved[0])
        resumed.run()
        result = resumed.finish(data_identity(self.data))
        self.assertEqual(result.data.times, fixtures.OPENINGS)
        self.assertEqual(tuple(row.values for row in result.outputs), fixtures.values(fixtures.FIRST))
        self.assertEqual(tuple(row.value_type.qualifier for row in result.outputs[-2:]), ('input', 'input'))

    def test_range_changes_the_plan_hash_and_legacy_wire_still_round_trips(self):
        request = {'schema_version': 'openpine.job-request.2', 'input_ref': 'a' * 64,
                   'snapshot_id': 'b' * 64, 'timeout_seconds': 60}
        base = make_plan(request, ResourcePolicy()).payload()
        self.assertNotIn('visible_range', base)
        self.assertEqual(RunPlan.from_payload(base).payload(), base)
        for invalid in (None, {}, {'left_ms': True, 'right_ms': 1}, {'left_ms': 2, 'right_ms': 1}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_request({**request, 'visible_range': invalid})
        plans = [make_plan({**request, 'visible_range': bounds}, ResourcePolicy()).payload()
                 for bounds in (fixtures.FIRST, fixtures.SECOND, fixtures.SINGLE)]
        self.assertEqual(len({digest(plan) for plan in plans}), len(plans))
        for plan in plans:
            self.assertEqual(RunPlan.from_payload(plan).payload(), plan)

    def test_replay_rejects_a_historical_viewport_program(self):
        context = MarketContext(self.data.context_id, self.data.symbol, self.data.timeframe, 'recorded.1')
        with self.assertRaises(OpenPineError) as error:
            ReplayEngine(self.session, self.program, (context,), context.context_id, None)
        self.assertEqual((error.exception.diagnostic.category, error.exception.diagnostic.code),
                         ('PROFILE_UNCOVERED', 'VISIBLE_RANGE_REPLAY'))
