"""Native coordinator/worker death, joint restore and portable offline equality."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from tools.session_soak import run


class SessionContinuityTests(unittest.TestCase):
    def test_two_versions_keep_outputs_and_broker_after_real_process_deaths(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt=run(SimpleNamespace(directory=Path(directory)/'acceptance',minutes=120,versions=[1,6]))
            self.assertTrue(receipt['real_coordinator_kill'])
            self.assertTrue(receipt['real_worker_kill'])
            for cut in receipt['coordinator_cut']['sessions']:
                self.assertGreater(cut['normalized_pending'],0)
                self.assertGreater(cut['raw_pending'],0)
            self.assertTrue(receipt['all_offline_outputs_equal'])
            self.assertEqual(receipt['independently_verified_output_values'],240*3*2)
            self.assertEqual(receipt['shared_feeds'],3)
            self.assertEqual([r['expected']['confirmed_bars'] for r in receipt['results']],[240,240])
            self.assertGreater(receipt['results'][1]['expected']['ledger']['rows'],0)


if __name__=='__main__':unittest.main()
