"""Independent temporal expectations, leakage boundaries and global budgets."""
import unittest
from packages.contracts.walkforward import fold_windows

POLICY = {'mode': 'rolling', 'start_ms': 20, 'train_ms': 100, 'test_ms': 30,
          'step_ms': 30, 'warmup_ms': 20, 'folds': 3, 'max_jobs': 12}


class WalkForwardContractTests(unittest.TestCase):
    def test_rolling_and_expanding_have_nonoverlapping_test_intervals(self):
        rolling = fold_windows(POLICY, 3)
        expanding = fold_windows({**POLICY, 'mode': 'expanding'}, 3)
        self.assertEqual([(f['selection']['score_start_ms'], f['selection']['end_ms']) for f in rolling], [(20,120),(50,150),(80,180)])
        self.assertEqual([(f['selection']['score_start_ms'], f['selection']['end_ms']) for f in expanding], [(20,120),(20,150),(20,180)])
        self.assertEqual([(f['holdout']['score_start_ms'], f['holdout']['end_ms']) for f in rolling], [(120,150),(150,180),(180,210)])
        self.assertEqual(rolling[0]['holdout']['warmup_start_ms'], 100)

    def test_invalid_or_unbounded_policies_are_rejected_before_work(self):
        for change in ({'step_ms':29}, {'max_jobs':11}, {'folds':17}, {'train_ms':0}, {'warmup_ms':21},
                       {'start_ms':True}, {'mode':'unknown'}, {'start_ms':2**63-1}):
            with self.subTest(change=change), self.assertRaises(ValueError):fold_windows({**POLICY, **change}, 3)
        for budget in (0, 257, True, '3'):
            with self.subTest(budget=budget), self.assertRaises((TypeError,ValueError)):fold_windows(POLICY, budget)
