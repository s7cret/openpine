"""Real process death at both temporal publication boundaries."""
from pathlib import Path
import tempfile
import unittest
from tools.walkforward_soak import run


class WalkForwardRecoveryTests(unittest.TestCase):
    def test_real_kill_before_holdout_and_between_folds(self):
        for cut in ('decision','fold'):
            with self.subTest(cut=cut),tempfile.TemporaryDirectory() as directory:
                report=run(Path(directory)/'proof',cut)
                self.assertTrue(report['real_coordinator_kill'])
                self.assertFalse(report['completed_candidate_repeated'])
                self.assertEqual(report['holdout_scores'],['39.0','42.0'])
