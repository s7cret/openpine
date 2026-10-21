import unittest

from packages.compiler.semantic.specification import ROOT
from packages.contracts.application import CompileRequest, RunRequest, JobStatus, TERMINAL_STATUSES
from packages.execution.service import Application
from packages.marketdata import read_bars


class ApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Application()
        cls.request = CompileRequest((ROOT / "examples/pipeline/v6.pine").read_text(), "v6.pine")
        cls.data = read_bars(ROOT / "examples/pipeline/bars.json")

    def test_compile_and_repeated_run_share_identity_but_not_state(self):
        compiled = self.app.handle(self.request)
        first = self.app.handle(RunRequest(self.request, self.data))
        second = self.app.handle(RunRequest(self.request, self.data))
        self.assertEqual(compiled["status"], "compiled")
        self.assertEqual(compiled["program_sha256"], first["program_sha256"])
        self.assertEqual(first, second)
        self.assertEqual(first["result"]["outputs"][0]["values"], (10.0, 40.0, 85.0, 135.0))

    def test_errors_use_common_diagnostic_contract(self):
        result = self.app.handle(CompileRequest('//@version=6\nindicator("bad")\nplot(missing)', "bad.pine"))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["diagnostics"][0]["span"]["line"], 3)
        missing_data = self.app.handle(RunRequest(self.request))
        self.assertEqual(missing_data["diagnostics"][0]["code"], "BARS_REQUIRED")
        self.assertEqual(self.app.handle(CompileRequest("", profile_id="unknown"))["diagnostics"][0]["code"], "REQUEST_INVALID")

    def test_terminal_states(self):
        self.assertNotIn(JobStatus.RUNNING, TERMINAL_STATUSES)
        self.assertIn(JobStatus.CANCELLED, TERMINAL_STATUSES)
