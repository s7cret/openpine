"""Independent end-to-end expectations shared by every application surface."""

import json
import unittest

from packages.compiler.semantic.specification import ROOT
from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.execution.session import Session
from packages.marketdata import read_bars


class PipelineScenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.directory = ROOT / "examples" / "pipeline"
        cls.data = read_bars(cls.directory / "bars.json")

    def test_all_versions_have_independent_expected_values(self):
        expected = json.loads((self.directory / "expected.json").read_text())
        for version in range(1, 7):
            with self.subTest(version=version):
                path = self.directory / f"v{version}.pine"
                program, result = self.session.run(path.read_text(), path.name, data=self.data)
                self.assertEqual(program.identity.language_version, version)
                self.assertEqual([list(o.values) for o in result.outputs], expected["outputs"])
                self.assertEqual(result.observation_count, 4)
                self.assertEqual(result.data.times, (0, 60000, 120000, 180000))

    def test_invalid_source_preserves_position(self):
        with self.assertRaises(OpenPineError) as caught:
            self.session.compile('//@version=6\nindicator("bad")\nplot(missing)\n', "bad.pine")
        diagnostic = caught.exception.diagnostic
        self.assertEqual(diagnostic.span.source, "bad.pine")
        self.assertEqual(diagnostic.span.line, 3)
        self.assertEqual(diagnostic.language_version, 6)
