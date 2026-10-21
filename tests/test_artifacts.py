import copy
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from packages.compiler.semantic.specification import ROOT
from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.execution.artifacts import export_program, import_program
from packages.execution.session import Session
from packages.marketdata import read_bars
from packages.runtime.engine import program_hash


class ArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data = read_bars(ROOT / "examples/pipeline/bars.json")

    def test_roundtrip_all_versions_execute_without_compile(self):
        for version in range(1, 7):
            with self.subTest(version=version):
                program = self.session.compile((ROOT / f"examples/pipeline/v{version}.pine").read_text(), f"v{version}.pine")
                restored = import_program(json.loads(json.dumps(export_program(program))))
                self.assertEqual(restored, program)
                with patch("packages.execution.session.compile_source", side_effect=AssertionError("recompiled")):
                    one = self.session.execute(restored, data=self.data)
                    two = self.session.execute(restored, data=replace(self.data, bars=self.data.bars[:2]))
                self.assertEqual(one.outputs[0].values, (10., 40., 85., 135.))
                self.assertEqual(two.outputs[0].values, (10., 40.))
                self.assertEqual(one.program_sha256, two.program_sha256)
                self.assertNotEqual(one.data.sha256, two.data.sha256)

    def test_tamper_and_primitive_types_rejected(self):
        program = self.session.compile((ROOT / "examples/pipeline/v6.pine").read_text())
        payload = export_program(program)
        for change in (lambda p: p.update(extra=True), lambda p: p["ir"].update(schema_version="old"),
                       lambda p: p["ir"]["identity"].update(language_version=True),
                       lambda p: p["ir"].update(statements={}),
                       lambda p: p["ir"]["identity"].update(extra=True),
                       lambda p: p["ir"]["identity"].update(**{"$type": "os.system"})):
            forged = copy.deepcopy(payload); change(forged)
            with self.assertRaises(OpenPineError):
                import_program(forged)

    def test_json_transport_cannot_erase_float_identity(self):
        source = '//@version=6\nindicator("float")\nplot(1.0)\nplot(-0.0)\nplot(9007199254740993)'
        program = self.session.compile(source)
        wire = export_program(program)
        def normalize_js(value):
            if type(value) is float and value.is_integer():
                return int(value)
            if type(value) is int and abs(value) > 2**53 - 1:
                return int(float(value))
            if type(value) is dict:
                return {k: normalize_js(v) for k, v in value.items()}
            if type(value) is list:
                return [normalize_js(v) for v in value]
            return value
        restored = import_program(normalize_js(wire))
        self.assertEqual(program_hash(restored), program_hash(program))
        self.assertIn('"$float"', json.dumps(wire))

    def test_identity_and_forged_operation_checked_before_effects(self):
        original = self.session.compile((ROOT / "examples/pipeline/v6.pine").read_text())
        for field in ("spec_lock_sha256", "implementation_sha256", "semantic_sha256"):
            program = replace(original, identity=replace(original.identity, **{field: "0" * 64}))
            restored = import_program(export_program(program))
            with patch("packages.runtime.engine.Runtime.prepare", side_effect=AssertionError("effects reached")):
                with self.assertRaises(OpenPineError):
                    self.session.execute(restored, data=self.data)
        bad_call = replace(original.statements[0], operation="not.allowed")
        forged = replace(original, statements=(bad_call, *original.statements[1:]))
        with self.assertRaises(OpenPineError):
            self.session.execute(import_program(export_program(forged)), data=self.data)
