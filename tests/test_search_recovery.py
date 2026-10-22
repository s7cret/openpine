"""Actual process death at both search publication boundaries, with backup/restore."""
from pathlib import Path
import tempfile
import unittest
from tools.search_soak import run


class SearchRecoveryTests(unittest.TestCase):
    def test_kill_during_selection_preserves_completed_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            report=run(Path(directory)/'proof','selection')
            self.assertTrue(report['real_coordinator_kill']);self.assertFalse(report['completed_candidate_repeated']);self.assertEqual(report['jobs'],4)

    def test_kill_after_decision_keeps_one_fixed_holdout(self):
        with tempfile.TemporaryDirectory() as directory:
            report=run(Path(directory)/'proof','holdout')
            self.assertTrue(report['decision_preserved']);self.assertEqual(report['jobs'],4)


if __name__=='__main__':unittest.main()
