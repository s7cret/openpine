"""Historical execution, independent EMA arithmetic, isolation and input admission."""

import copy
import json
import socket
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from packages.compiler.semantic.specification import ROOT, SpecError
from packages.contracts import BarFrame, ConvertIR, HISTORICAL_PROFILE_ID, OpenPineError, ValueType
from packages.execution.session import Session
from packages.marketdata import data_identity, from_payload, read_bars
from packages.runtime.engine import Runtime
from packages.runtime.state import KernelContext
from tools.conformance import BAR_CASES, run_cases

SOURCE = '//@version=6\nindicator("EMA")\nplot(ta.ema(close, 3), "ema")\n'


def payload(values=(10, 20, 30, 40), context="test.fixed"):
    return {"schema_version": "openpine.historical-bars.1", "context_id": context, "symbol": "TEST:OWN",
            "timeframe": "1", "bars": [{"time": index * 60000, "close": value} for index, value in enumerate(values)]}


class HistoricalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.version_info[:3] != (3, 14, 8):
            raise RuntimeError("Tests require Python 3.14.8")
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data = from_payload(payload())

    def temporary(self):
        parent = ROOT / ".runtime/historical-tests"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="case-", dir=parent)
        if not Path(temporary.name).resolve().is_relative_to(parent.resolve()):
            raise RuntimeError("Unexpected temporary path")
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def cli(self, *arguments):
        bootstrap = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('apps.openpine',run_name='__main__')"
        return subprocess.run([sys.executable, "-X", "utf8", "-c", bootstrap, str(ROOT), *arguments],
                              cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)

    def test_independent_historical_corpus(self):
        report = run_cases(self.session, cases_path=BAR_CASES)
        self.assertEqual((report["executed_programs"], report["expected_rejections"]), (6, 5))
        self.assertIn("series.ema", report["exercised_operations"])
        self.assertIn("context.close", report["exercised_operations"])

    def test_close_reference_is_resolved_and_result_bound_to_data(self):
        source = SOURCE.replace('ta.ema(close, 3)', 'close')
        program, result = self.session.run(source, data=self.data)
        reference = program.statements[1].arguments[0].expression
        self.assertEqual(reference.contract.symbol_id, "symbol.variable.close")
        self.assertEqual(reference.contract.assertion_id, "assertion.symbol.variable.close.value")
        self.assertEqual(reference.value_type, ValueType("float", "series"))
        self.assertEqual(result.outputs[0].values, (10.0, 20.0, 30.0, 40.0))
        self.assertEqual(result.observation_count, 4)
        self.assertEqual(result.data, data_identity(self.data))
        self.assertEqual(result.data.times, (0, 60000, 120000, 180000))

    def test_ema_matches_independent_closed_form_fraction_oracle(self):
        values = (2, -3, 17, 0, 21, -11, 5, 39, 1)
        for length in (1, 2, 3, 7, 100):
            with self.subTest(length=length):
                alpha = Fraction(2, length + 1)
                ratio = 1 - alpha
                expected = [ratio**i * values[0] + alpha * sum(
                    ratio**(i-j) * values[j] for j in range(1, i+1)) for i in range(len(values))]
                _, result = self.session.run(SOURCE.replace('close, 3', f'close, {length}'), data=from_payload(payload(values)))
                for actual, rational in zip(result.outputs[0].values, expected, strict=True):
                    self.assertAlmostEqual(actual, float(rational), delta=1e-12)

    def test_missing_source_seed_and_gaps(self):
        for values, expected in (
            ((None, None), (None, None)),
            ((None, None, 8, None, 4, None, 0), (None, None, 8.0, 8.0, 6.0, 6.0, 3.0)),
        ):
            _, result = self.session.run(SOURCE, data=from_payload(payload(values)))
            self.assertEqual(result.outputs[0].values, expected)
        _, result = self.session.run(SOURCE.replace('close, 3', 'math.abs(-close), 3'),
                                    data=from_payload(payload((None, -8, None, 4))))
        self.assertEqual(result.outputs[0].values, (None, 8.0, 8.0, 6.0))

    def test_state_is_fresh_between_runs_and_contexts(self):
        first_program, first = self.session.run(SOURCE, data=self.data)
        second_program, second = self.session.run(SOURCE, data=from_payload(payload((80, 40), "other.context")))
        _, repeated = self.session.run(SOURCE, data=self.data)
        self.assertEqual(first_program, second_program)  # Compilation is independent of input data.
        self.assertEqual(first, repeated)
        self.assertEqual(second.outputs[0].values, (80.0, 60.0))
        self.assertNotEqual(first.data.sha256, second.data.sha256)
        states = {}
        a, b = KernelContext("site", "a", states), KernelContext("site", "b", states)
        self.assertEqual(a.ema().update(10.0, 3), 10.0)
        self.assertEqual(b.ema().update(90.0, 3), 90.0)
        self.assertEqual(a.ema().update(20.0, 3), 15.0)

    def test_future_bars_do_not_change_an_executed_prefix(self):
        _, prefix = self.session.run(SOURCE, data=from_payload(payload((10, 20))))
        for future in ((99999, -99999), (-100, 100), (None, None)):
            _, extended = self.session.run(SOURCE, data=from_payload(payload((10, 20, *future))))
            self.assertEqual(extended.outputs[0].values[:2], prefix.outputs[0].values)

    def test_invalid_length_is_runtime_error_and_does_not_leak_state(self):
        source = SOURCE.replace('close, 3', 'close, 0')
        self.session.compile(source)  # Positive length is a runtime rule in the pinned spec.
        with self.assertRaises(OpenPineError) as caught:
            self.session.run(source, data=self.data)
        diagnostic = caught.exception.diagnostic
        self.assertEqual((diagnostic.category, diagnostic.code, diagnostic.rule_id, diagnostic.language_version),
                         ("RUNTIME_ERROR", "EMA_LENGTH", "rule.ema.length", 6))
        self.assertEqual(source[diagnostic.span.start:diagnostic.span.end], 'ta.ema(close, 0)')
        partial_source = source.replace('plot(ta.ema', 'plot(close)\nplot(ta.ema')
        program = self.session.compile(partial_source)
        runtime = Runtime(program.identity, self.session.catalog.contracts.values(), self.session.catalog.references.values())
        evaluation = runtime.prepare(program)
        with self.assertRaises(OpenPineError):
            evaluation.step(BarFrame("x", 0, 0, 10.0))
        with self.assertRaises(OpenPineError):
            evaluation.finish(data_identity(from_payload(payload((10,), "x"))))
        with self.assertRaises(OpenPineError):
            evaluation.step(BarFrame("x", 0, 0, 10.0))
        _, result = self.session.run(SOURCE, data=self.data)
        self.assertEqual(result.outputs[0].values, (10.0, 15.0, 22.5, 31.25))

    def test_profiles_require_data_and_select_version_contracts(self):
        with self.assertRaises(OpenPineError) as caught:
            self.session.run(SOURCE)
        self.assertEqual(caught.exception.diagnostic.code, "BARS_REQUIRED")
        for version in range(1, 6):
            source = SOURCE.replace('version=6', f'version={version}')
            if version < 5:
                source = source.replace('indicator(', 'study(').replace('ta.ema(', 'ema(')
            program, result = self.session.run(source, data=self.data)
            self.assertEqual(program.identity.language_version, version)
            self.assertEqual(result.outputs[0].values, (None, None, 20.0, 30.0) if version < 5 else (10.0, 15.0, 22.5, 31.25))
        literal = Session(snapshot=self.session.snapshot)
        with self.assertRaises(OpenPineError) as caught:
            literal.run('//@version=6\nindicator("literal")\nplot(1)', data=self.data)
        self.assertEqual(caught.exception.diagnostic.code, "BAR_PROFILE")

    def test_input_rejects_bad_order_types_and_unsupported_fields(self):
        invalid = []
        for times in ((0, 0), (60000, 0), (-1, 0), (False, 60000), (0, 2**63), (0, 1.5)):
            changed = payload((1, 2))
            for bar, time in zip(changed["bars"], times, strict=True):
                bar["time"] = time
            invalid.append(changed)
        for values in ((), (True,), ("10",), (float("nan"),), (float("inf"),), (10**400,)):
            invalid.append(payload(values))
        invalid += [dict(payload(), realtime=True), dict(payload(), schema_version="wrong"),
                    dict(payload(), context_id=""), dict(payload(), bars="not bars")]
        for changed in invalid:
            with self.subTest(changed=str(changed)[:100]), self.assertRaises(OpenPineError) as caught:
                from_payload(changed)
            self.assertEqual(caught.exception.diagnostic.category, "INPUT_INVALID")
        with self.assertRaises(OpenPineError):
            data_identity(replace(self.data, bars=list(self.data.bars)))

    def test_data_hash_uses_normalized_values_and_context_metadata(self):
        integers = from_payload(payload((10, 20)))
        floats = from_payload(payload((10.0, 20.0)))
        self.assertEqual(data_identity(integers), data_identity(floats))
        self.assertNotEqual(data_identity(integers).sha256, data_identity(replace(integers, symbol="TEST:OTHER")).sha256)
        self.assertNotEqual(data_identity(integers).sha256, data_identity(from_payload(payload((10, 21)))).sha256)

    def test_json_duplicates_nonfinite_and_overflow_are_rejected(self):
        path = self.temporary() / "bars.json"
        for raw in ('{"bars":[],"bars":[]}', '{', json.dumps(payload((1,))).replace('"close": 1', '"close": NaN'),
                    json.dumps(payload((1,))).replace('"close": 1', '"close": 1e999')):
            path.write_text(raw, encoding="utf-8")
            with self.subTest(raw=raw), self.assertRaises(OpenPineError) as caught:
                read_bars(path)
            self.assertEqual(caught.exception.diagnostic.code, "BARS_INVALID")

    def test_runtime_rejects_forged_reference_and_conversion_before_effects(self):
        program = self.session.compile(SOURCE)
        output = program.statements[1]
        ema = output.arguments[0].expression
        reference = ema.arguments[0].expression
        for changed, code in (
            (replace(reference, contract=replace(reference.contract, slot="future_close")), "IR_REFERENCE"),
            (replace(reference, value_type=ValueType("float", "simple")), "IR_REFERENCE"),
            (ConvertIR(reference, ValueType("int", "series"), reference.span), "IR_CONVERSION"),
        ):
            forged_ema = replace(ema, arguments=(replace(ema.arguments[0], expression=changed), ema.arguments[1]))
            forged_output = replace(output, arguments=(replace(output.arguments[0], expression=forged_ema), output.arguments[1]))
            forged = replace(program, statements=(program.statements[0], forged_output))
            runtime = Runtime(program.identity, self.session.catalog.contracts.values(), self.session.catalog.references.values())
            with self.assertRaises(OpenPineError) as caught:
                runtime.prepare(forged)
            self.assertEqual(caught.exception.diagnostic.code, code)

    def test_bar_order_context_and_single_completion_are_enforced(self):
        program = self.session.compile(SOURCE)
        runtime = Runtime(program.identity, self.session.catalog.contracts.values(), self.session.catalog.references.values())
        evaluation = runtime.prepare(program)
        evaluation.step(BarFrame("x", 0, 0, 10.0))
        for frame, code in ((BarFrame("x", 0, 0, 10.0), "BAR_ORDER"),
                            (BarFrame("y", 1, 1, 10.0), "BAR_CONTEXT")):
            with self.assertRaises(OpenPineError) as caught:
                evaluation.step(frame)
            self.assertEqual(caught.exception.diagnostic.code, code)
        identity = data_identity(from_payload(payload((10,), "x")))
        evaluation.finish(identity)
        with self.assertRaises(OpenPineError):
            evaluation.step(BarFrame("x", 1, 1, 20.0))

    def test_changed_semantics_are_not_silently_implemented(self):
        snapshot = self.session.snapshot
        resolve = snapshot.resolve

        def changed(assertion_id, *args):
            value = resolve(assertion_id, *args)
            if assertion_id == "assertion.rule.ema.semantics":
                value = copy.deepcopy(value)
                value["seed"] = "different_seed"
            return value

        with patch.object(type(snapshot), "resolve", side_effect=changed), self.assertRaises(SpecError) as caught:
            Session(snapshot=snapshot, profile_id=HISTORICAL_PROFILE_ID)
        self.assertEqual(caught.exception.code, "IMPLEMENTATION_MISSING")

    def test_historical_cli_compile_run_and_missing_bars(self):
        source = "conformance/historical-bars/ema.pine"
        compiled = self.cli("compile", "--source", source, "--profile", "historical")
        executed = self.cli("run", "--source", source, "--bars", "conformance/historical-bars/fixed.json")
        self.assertEqual((compiled.returncode, executed.returncode), (0, 0), compiled.stderr + executed.stderr)
        left, right = json.loads(compiled.stdout), json.loads(executed.stdout)
        self.assertEqual(left["program_sha256"], right["program_sha256"])
        self.assertEqual(right["result"]["outputs"][0]["values"], [10.0, 15.0, 22.5, 31.25])
        self.assertEqual(right["result"]["observation_count"], 4)
        self.assertEqual(right["identity"]["implementation_profile_id"], HISTORICAL_PROFILE_ID)
        missing = self.cli("run", "--source", source, "--profile", "historical")
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(json.loads(missing.stdout)["diagnostics"][0]["code"], "BARS_REQUIRED")

    def test_cli_rejects_invalid_input_and_protects_bar_file(self):
        path = self.temporary() / "bars.json"
        raw = '{"bars":[],"bars":[]}'
        path.write_text(raw, encoding="utf-8")
        rejected = self.cli("run", "--source", "conformance/historical-bars/ema.pine", "--bars", str(path))
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(json.loads(rejected.stdout)["diagnostics"][0]["code"], "BARS_INVALID")
        protected = self.cli("run", "--source", "conformance/historical-bars/ema.pine", "--bars", str(path), "--output", str(path))
        self.assertEqual(protected.returncode, 2)
        self.assertEqual(path.read_text(encoding="utf-8"), raw)

    def test_historical_execution_is_offline(self):
        with patch.object(socket, "create_connection", side_effect=AssertionError("network")), \
             patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS")):
            _, result = self.session.run(SOURCE, data=self.data)
        self.assertEqual(result.outputs[0].values, (10.0, 15.0, 22.5, 31.25))


if __name__ == "__main__":
    unittest.main()
