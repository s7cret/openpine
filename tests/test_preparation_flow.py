"""A combined independent six-version consumer across actual isolated workers."""
import tempfile,unittest
from pathlib import Path
from tools.preparation_flow import run

class PreparationFlowTests(unittest.TestCase):
    def test_file_ir_selected_context_udf_lower_resume_restart_and_offline_all_versions(self):
        with tempfile.TemporaryDirectory() as d:
            result=run(Path(d)/'flow')
        self.assertEqual([r['version'] for r in result['versions']],list(range(1,7)))
        self.assertTrue(all(r['historical_pause_resume'] and r['replay_restart'] for r in result['versions']))

if __name__=='__main__':unittest.main()
