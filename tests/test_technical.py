"""Independent numerical oracles and lifecycle checks for technical kernels."""

import copy
import json
import math
import random
import unittest
from fractions import Fraction

from packages.runtime.history import StorageBudget
from packages.runtime.operations import KERNELS
from packages.runtime.series import KernelError
from packages.runtime.series.technical import (
    ATRState, CellOverlay, ExtremumState, RMAState, RSIState,
    export_technical, fork_technical, restore_technical, true_range,
)
from packages.runtime.state import KernelContext


def rma_oracle(values, length):
    seed, previous, output = [], None, []
    for value in values:
        if value is not None:
            source = Fraction(value)
            if previous is None:
                seed.append(source)
                if len(seed) == length:
                    previous = sum(seed) / length
            else:
                previous = (source + (length - 1) * previous) / length
        output.append(None if previous is None else float(previous))
    return output


class TechnicalTests(unittest.TestCase):
    def close(self, actual, expected):
        self.assertEqual(len(actual), len(expected))
        for got, wanted in zip(actual, expected):
            if wanted is None:
                self.assertIsNone(got)
            else:
                self.assertTrue(math.isclose(got, wanted, rel_tol=2e-14, abs_tol=2e-14), (got, wanted))

    def test_rma_seed_missing_and_recurrence_against_fraction_oracle(self):
        values = [None, 1., 2., None, 6., 3., None, -8., 4., 0., 9.]
        for length in (1, 2, 3, 7, 12):
            state = RMAState()
            self.close([state.update(value, length) for value in values], rma_oracle(values, length))

    def test_rma_seed_avoids_large_sum_overflow_and_cancellation(self):
        state = RMAState()
        self.assertIsNone(state.update(1e308, 2))
        self.assertEqual(state.update(1e308, 2), 1e308)
        state = RMAState()
        values = [1e308, 1., -1e308]
        self.close([state.update(value, 3) for value in values], [None, None, 1. / 3.])
        self.assertEqual(state.total, 0)

    def test_lengths_are_bounded_positive_and_fixed_after_first_call(self):
        factories = (RMAState, RSIState, lambda: ExtremumState(StorageBudget(), True))
        for factory in factories:
            for length in (0, -1, 100001, True, 1., None):
                with self.subTest(factory=factory, length=length), self.assertRaises(KernelError):
                    factory().update(1., length)
            state = factory()
            state.update(None, 2)
            with self.assertRaisesRegex(KernelError, "constant"):
                state.update(1., 3)

    def test_true_range_uses_explicit_previous_physical_close_and_missing_policy(self):
        self.assertEqual(true_range(12., 8., 20., False), 12.)
        self.assertEqual(true_range(12., 8., 5., False), 7.)
        self.assertEqual(true_range(12., 8., 10., False), 4.)
        self.assertEqual(true_range(12., 8., None, True), 4.)
        self.assertIsNone(true_range(12., 8., None, False))
        for high, low in ((None, 8.), (12., None), (None, None)):
            self.assertIsNone(true_range(high, low, 10., True))

    def test_atr_smooths_true_ranges_and_preserves_state_on_missing_ohlc(self):
        state = ATRState()
        rows = [(12., 8., None), (15., 13., 10.), (None, None, 14.),
                (9., 5., 14.), (11., 9., None), (10., 8., 10.)]
        expected_tr = [4., 5., None, 9., 2., 2.]
        self.close([state.update(*row, 3) for row in rows], rma_oracle(expected_tr, 3))

    def test_rsi_uses_defined_source_deltas_and_independent_rma_oracle(self):
        values = [None, 10., 12., None, 11., 15., 13., 13., None, 17., 9.]
        previous, ups, downs = None, [], []
        for value in values:
            delta = None if value is None or previous is None else value - previous
            ups.append(None if delta is None else max(delta, 0.))
            downs.append(None if delta is None else max(-delta, 0.))
            if value is not None:
                previous = value
        gains, losses = rma_oracle(ups, 3), rma_oracle(downs, 3)
        expected = [None if up is None or down is None or up == down == 0 else
                    100. if down == 0 else 100. - 100. / (1. + up / down)
                    for up, down in zip(gains, losses)]
        state = RSIState()
        self.close([state.update(value, 3) for value in values], expected)

    def test_rsi_constant_rising_falling_and_subnormal_edges(self):
        for values, expected in (([7.] * 6, None), ([1., 2., 3., 4., 5., 6.], 100.),
                                 ([6., 5., 4., 3., 2., 1.], 0.)):
            state = RSIState()
            output = [state.update(value, 3) for value in values]
            self.assertEqual(output, [None] * 3 + [expected] * 3)
        state = RSIState()
        state.update(0., 1)
        self.assertEqual(state.update(5e-324, 1), 100.)
        self.assertEqual(state.update(0., 1), 0.)

    def test_rsi_numeric_overflow_reports_kernel_error(self):
        state = RSIState()
        state.update(-1e308, 2)
        with self.assertRaises(KernelError) as caught:
            state.update(1e308, 2)
        self.assertEqual(caught.exception.code, "TECHNICAL_VALUE_RANGE")
        with self.assertRaises(KernelError) as caught:
            ATRState().update(1e308, -1e308, None, 2)
        self.assertEqual(caught.exception.code, "TECHNICAL_VALUE_RANGE")

    def test_extrema_count_na_as_window_holes_and_publish_before_full_warmup(self):
        values = [None, 9., 2., None, -3., None, None, None, 8., 8., 7.]
        for highest in (True, False):
            for length in (1, 2, 3, 5, 17):
                state = ExtremumState(StorageBudget(), highest)
                expected = []
                for index, value in enumerate(values):
                    window = [v for v in values[max(0, index - length + 1):index + 1] if v is not None]
                    expected.append((max if highest else min)(window) if window else None)
                self.assertEqual([state.update(value, length) for value in values], expected)

    def test_extrema_random_non_power_of_two_windows_match_brute_force(self):
        rng = random.Random(3107)
        values = [None if rng.randrange(5) == 0 else float(rng.randrange(-100, 101)) for _ in range(700)]
        for length in (3, 7, 15, 31, 97):
            for highest in (True, False):
                state = ExtremumState(StorageBudget(), highest)
                for index, value in enumerate(values):
                    defined = [x for x in values[max(0, index + 1 - length):index + 1] if x is not None]
                    expected = (max if highest else min)(defined) if defined else None
                    self.assertEqual(state.update(value, length), expected)

    def test_extrema_memory_budget_and_maximum_length_are_bounded(self):
        budget = StorageBudget()
        state = ExtremumState(budget, True)
        self.assertEqual(state.update(3., 100000), 3.)
        self.assertEqual(budget.cells, 200000)
        for index in range(100):
            state.update(float(index), 100000)
        self.assertEqual(budget.cells, 200000)
        with self.assertRaises(KernelError) as caught:
            ExtremumState(StorageBudget(limit=5), True).update(1., 3)
        self.assertEqual(caught.exception.code, "HISTORY_STORAGE")

    def test_every_kernel_roundtrips_during_warmup_and_after_seed(self):
        values = [None, 4., 1., None, 9., -2., 3., None, 8.]
        for factory in (RMAState, RSIState, lambda: ExtremumState(StorageBudget(), True),
                        lambda: ExtremumState(StorageBudget(), False)):
            baseline = factory()
            expected = [baseline.update(value, 3) for value in values]
            for cut in range(1, len(values) + 1):
                first = factory()
                prefix = [first.update(value, 3) for value in values[:cut]]
                kind, state = export_technical(first)
                restored = restore_technical(kind, json.loads(json.dumps(state)), StorageBudget(), cut)
                self.assertEqual(prefix + [restored.update(value, 3) for value in values[cut:]], expected)
        first = ATRState()
        first.update(4., 1., None, 3)
        kind, state = export_technical(first)
        resumed = restore_technical(kind, state, StorageBudget(), 1)
        self.assertIs(type(resumed), ATRState)
        self.assertEqual(resumed.update(8., 2., 3., 3), first.update(8., 2., 3., 3))

    def test_malformed_state_shapes_numbers_counts_and_kinds_are_rejected(self):
        state = RMAState()
        state.update(4., 3)
        kind, saved = export_technical(state)
        mutations = ({**saved, "length": True}, {**saved, "samples": 4}, {**saved, "previous": 2.},
                     {**saved, "total": 1 << 3000}, {**saved, "extra": 0}, {**saved, "length": None})
        for bad in mutations:
            with self.assertRaises(ValueError):
                restore_technical(kind, bad, StorageBudget(), 2)
        with self.assertRaises(ValueError):
            restore_technical("unknown", saved, StorageBudget(), 2)
        state = ExtremumState(StorageBudget(), True)
        state.update(2., 3)
        kind, saved = export_technical(state)
        for bad in ({**saved, "count": 0}, {**saved, "count": 2}, {**saved, "leaves": [2., None, 3.]},
                    {**saved, "leaves": [float("nan"), None, None]}, {**saved, "leaves": [2., None]}):
            with self.assertRaises(ValueError):
                restore_technical(kind, bad, StorageBudget(), 1)

    def test_rsi_restore_rejects_asymmetric_nested_state(self):
        state = RSIState()
        for value in (1., 3., 2.):
            state.update(value, 3)
        kind, saved = export_technical(state)
        for field, replacement in (("samples", 1), ("length", 2), ("total", -1)):
            bad = copy.deepcopy(saved)
            bad["gains"][field] = replacement
            with self.assertRaises(ValueError):
                restore_technical(kind, bad, StorageBudget(), 3)
        atr = ATRState()
        atr.update(4., 1., None, 2)
        kind, saved = export_technical(atr)
        with self.assertRaises(ValueError):
            restore_technical(kind, {**saved, 'total': -saved['total']}, StorageBudget(), 1)
        atr.update(5., 2., 3., 2)
        kind, saved = export_technical(atr)
        with self.assertRaises(ValueError):
            restore_technical(kind, {**saved, 'previous': -1.}, StorageBudget(), 2)

    def test_preview_branches_have_private_scalars_and_logarithmic_changed_cells(self):
        for factory in (RMAState, RSIState):
            state = factory()
            for value in (1., 2., 4.):
                state.update(value, 2)
            before = export_technical(state)
            preview = fork_technical(state, StorageBudget())
            preview.update(90., 2)
            self.assertEqual(export_technical(state), before)
            self.assertNotEqual(export_technical(preview), before)
        budget = StorageBudget()
        state = ExtremumState(budget, True)
        for value in (1., 2., 4.):
            state.update(value, 100000)
        before = export_technical(state)
        preview = fork_technical(state, copy.copy(budget))
        self.assertIsInstance(preview.nodes, CellOverlay)
        self.assertIs(preview.nodes.base, state.nodes)
        self.assertEqual(preview.update(90., 100000), 90.)
        self.assertLessEqual(len(preview.nodes.changed), 18)
        self.assertEqual(export_technical(state), before)
        self.assertEqual(preview.budget.cells, budget.cells)

    def test_written_calls_and_contexts_get_independent_states(self):
        states, budget = {}, StorageBudget()
        first = KernelContext("one", "chart", states, budget)
        second = KernelContext("two", "chart", states, budget)
        requested = KernelContext("one", "requested", states, budget)
        kernel = KERNELS["series.rma"].function
        self.assertIsNone(kernel((2., 2), first))
        self.assertIsNone(kernel((20., 2), second))
        self.assertIsNone(kernel((200., 2), requested))
        self.assertEqual(kernel((4., 2), first), 3.)
        self.assertEqual(kernel((40., 2), second), 30.)
        self.assertEqual(kernel((400., 2), requested), 300.)
        self.assertEqual(len(states), 3)


if __name__ == "__main__":
    unittest.main()
