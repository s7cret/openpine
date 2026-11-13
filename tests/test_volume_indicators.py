"""Pinned volume contracts, independent flows and replay around VWAP resets."""

import unittest
from dataclasses import replace

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.alerts import CONDITION_TEMPLATE_PROFILE
from packages.contracts.contexts import ContextBinding
from packages.contracts.history_storage import plan
from packages.contracts.volume_indicators import AD, BANDS, MFI, OBV, PVT
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.driver import MemorySource
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.runtime.history import StorageBudget
from packages.runtime.series.volume_indicators import MoneyFlow, WeightedPrice, contribution, export_volume, restore_volume
from tests.test_alerts import Alerts, Output
from tests.test_dynamic_windows import rewrite
from tests.test_realtime import PRIMARY
from tests.test_request_expressions import DATA, HIGHER, PRIMARY as REQUEST_PRIMARY, REQUESTED, mapped
from tools.conformance import run_cases
from tools.specification import ROOT
from tools.volume_indicators_fixtures import CASES, EXAMPLE, anchored_tape, source


class VolumeIndicatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_pinned_availability_and_calculation(self):
        report = run_cases(self.session, cases_path=CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (16, 8))

    def test_cumulative_inputs_and_zero_bases(self):
        self.assertIsNone(contribution(AD, (12., 9., 10., None)))
        self.assertEqual(contribution(AD, (10., 10., 10., 5.)), 0.)
        self.assertIsNone(contribution(OBV, (10., None, 5.)))
        self.assertEqual(contribution(OBV, (10., 10., 5.)), 0.)
        self.assertEqual(contribution(OBV, (9., 10., 5.)), -5.)
        self.assertIsNone(contribution(PVT, (1., 0., 5.)))
        self.assertEqual(contribution(PVT, (9., 10., 5.)), -.5)
        self.assertEqual(contribution(PVT, (1e300, -1e300, 1.)), -2.)

    def test_mfi_skips_missing_flow_and_keeps_zero_in_window(self):
        state = MoneyFlow(StorageBudget(), 3)
        rows = ((1., 10., 1, None), (1., 12., 1, 100.), (None, 8., 1, 100.),
                (1., None, 1, 100.), (1., 9., 2, 100.), (0., 5., 2, 100.),
                (1., 4., 3, 900 / 13), (1., 4., 1, None), (1., 3., 2, 0.))
        for calls, (vol, price, length, result) in enumerate(rows, start=1):
            self.assertEqual(state.update(vol, price, length), result)
            snapshot = export_volume(MFI, state)
            state = restore_volume(MFI, loads(canonical(snapshot)), StorageBudget(), calls, 3, 6)
            self.assertEqual(export_volume(MFI, state), snapshot)

    def test_weighted_vwap_bands_and_restore(self):
        state = WeightedPrice()
        self.assertEqual(state.update(5., 9., None, bands=True, multiplier=2.), (None,) * 3)
        self.assertEqual(state.update(0., 7., True, bands=True, multiplier=2.), (None,) * 3)
        self.assertEqual(state.update(1., 10., False, bands=True, multiplier=2.), (10.,) * 3)
        self.assertEqual(state.update(3., 14., False, bands=True, multiplier=2.),
                         (13., 13. + 2 * 3 ** .5, 13. - 2 * 3 ** .5))
        snapshot = export_volume(BANDS, state)
        state = restore_volume(BANDS, loads(canonical(snapshot)), StorageBudget(), 4, 0, 6)
        self.assertEqual(state.update(None, 90., False, bands=True), (13., None, None))
        self.assertEqual(export_volume(BANDS, state), snapshot)
        with self.assertRaises(ValueError):
            restore_volume(BANDS, dict(snapshot, squares=0), StorageBudget(), 4, 0, 6)
        self.assertEqual(state.update(None, 8., True, bands=True, multiplier=2.), (None,) * 3)
        self.assertEqual(state.update(1., 8., True, bands=True, multiplier=-1.), (8.,) * 3)
        self.assertEqual(state.update(1., 12., False, bands=True, multiplier=-1.), (10., 8., 12.))

    def test_security_uses_requested_volume_time_and_history(self):
        direct_code = ('[a,u,l]=ta.vwap(close,bar_index%2==0,1)\n'
                       'm=ta.mfi(close,bar_index%3+1)\n'
                       'plot(a)\nplot(u)\nplot(l)\nplot(m)\nplot(ta.obv)\nplot(ta.vwap)')
        requested_code = (f'[a,u,l]=request.security("{HIGHER.symbol}","2",ta.vwap(close,bar_index%2==0,1))\n'
                          f'm=request.security("{HIGHER.symbol}","2",ta.mfi(close,bar_index%3+1))\n'
                          'plot(a)\nplot(u)\nplot(l)\nplot(m)\n'
                          f'plot(request.security("{HIGHER.symbol}","2",ta.obv))\n'
                          f'plot(request.security("{HIGHER.symbol}","2",ta.vwap))')
        direct = source(6, direct_code)
        requested = source(6, requested_code)
        plans = {r['operation']: r for r in plan(self.session.compile(direct))['calls']}
        self.assertEqual((plans[MFI]['retention'], plans[MFI]['max_cells']), (3, 8))
        self.assertEqual(plans[BANDS]['max_cells'], 0)
        def across_midnight(data):
            return replace(data, bars=tuple(replace(bar, time=86_160_000 + bar.time) for bar in data.bars))

        primary, higher = across_midnight(DATA), across_midnight(REQUESTED)
        _, reference = self.session.run(direct, data=higher)
        _, actual = self.session.run(requested, data=primary, primary_context=REQUEST_PRIMARY,
            context_sources=((ContextBinding(HIGHER, 'a' * 64), MemorySource(higher)),))
        new_day = higher.bars[2]
        self.assertAlmostEqual(reference.outputs[-1].values[2], (new_day.high + new_day.low + new_day.close) / 3)
        for output, expected in zip(actual.outputs, reference.outputs, strict=True):
            self.assertEqual(output.values, mapped(expected.values, 6))

    def test_volume_reference_requires_its_physical_input(self):
        program = self.session.compile(source(6, 'plot(ta.obv)'))

        def forge(node):
            if node.operation != OBV:
                return node
            close, previous, volume = node.arguments
            return replace(node, arguments=(close, previous, replace(volume, expression=close.expression)))

        with self.assertRaises(OpenPineError) as error:
            self.session.validate_program(rewrite(program, forge))
        self.assertEqual(error.exception.diagnostic.code, 'IR_VOLUME_INPUT')

    def test_replay_matches_through_anchor_and_alert_after_resume(self):
        text = (ROOT / EXAMPLE).read_text(encoding='utf-8')
        program = import_program(export_program(self.session.compile(text)))
        events = tuple(anchored_tape(PRIMARY.context_id))

        def create():
            journal = Alerts()
            engine = ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, Output(),
                                  alert_sink=journal, alert_condition_profile=CONDITION_TEMPLATE_PROFILE)
            return engine, journal

        full, journal = create()
        for event in events:
            full.apply(event)
        expected = [(17, 0, 'alertcondition', 'VWAP crossed, MFI 55.56')]
        self.assertEqual(journal.rows, expected)
        for cut in (2, 11, 12, 13, 17):
            head, prefix = create()
            for event in events[:cut]:
                head.apply(event)
            tail, suffix = create()
            tail.restore(loads(canonical(head.checkpoint())))
            tail.apply(events[cut - 1])
            for event in events[cut:]:
                tail.apply(event)
            self.assertEqual(prefix.rows + suffix.rows, expected)
            self.assertEqual(head.sink.rows + tail.sink.rows, full.sink.rows)
            self.assertEqual(tail.checkpoint(), full.checkpoint())
