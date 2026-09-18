import importlib.util
from pathlib import Path
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('exact_stage2',ROOT/'run_exact_stage2.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class ExactRunnerTests(unittest.TestCase):
    def test_empty_failure_and_skip_are_not_passes(self):
        cases=[('<testsuite/>',False),
        ('<testsuite tests="1"><testcase name="x"/></testsuite>',True),
        ('<testsuite tests="1"><testcase name="x"><skipped/></testcase></testsuite>',False),
        ('<testsuite tests="1"><testcase name="x"><failure/></testcase></testsuite>',False),
        ('<testsuite errors="1"><testcase name="x"/></testsuite>',False)]
        with tempfile.TemporaryDirectory() as d:
            for text,expected in cases:
                p=Path(d)/'junit.xml';p.write_text(text)
                self.assertEqual(m.clean_junit(p)['ok'],expected)
    def test_source_drift_and_build_exclusions(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for c in m.COMPONENTS:
                p=root/'sources'/c;p.mkdir(parents=True);(p/'source.py').write_text('pass\n')
            first=m.snapshot(root)['source_hash']
            cache=root/'sources'/'pine2ast'/'__pycache__';cache.mkdir();(cache/'x.pyc').write_bytes(b'cache')
            self.assertEqual(m.snapshot(root)['source_hash'],first)
            (root/'sources'/'pine2ast'/'source.py').write_text('changed\n')
            self.assertNotEqual(m.snapshot(root)['source_hash'],first)
    def test_interpreter_cannot_be_silently_substituted(self):
        import sys
        required='3.11' if sys.version_info[:2]!=(3,11) else '3.13'
        self.assertEqual(m.interpreter_identity(sys.executable,required)['status'],'blocked')
    def test_symlink_is_not_hidden_from_source_identity(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for c in m.COMPONENTS:
                p=root/'sources'/c;p.mkdir(parents=True);(p/'source.py').write_text('pass')
            (root/'sources'/'pinelib'/'alias.py').symlink_to('source.py')
            with self.assertRaises(ValueError):m.snapshot(root)
if __name__=='__main__':unittest.main(verbosity=2)
