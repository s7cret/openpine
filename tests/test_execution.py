"""Source-to-result regressions for the explicitly bounded literal slice."""

import ast
import hashlib
import json
import socket
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from packages.compiler.semantic.specification import ROOT, read_json
from packages.contracts import LiteralIR, OpenPineError, ValueType
from packages.execution.session import PROFILE_ID, Session
from packages.runtime.engine import Runtime, program_hash
from tools.conformance import run_cases

SOURCE = '//@version=6\nindicator("R6 first run")\nplot(math.abs(-2.5))\n'


class ExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.version_info[:3] != (3, 14, 8):
            raise RuntimeError("Tests require Python 3.14.8")
        cls.session = Session()

    def temporary(self):
        parent = ROOT / ".runtime/execution-tests"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="case-", dir=parent)
        if not Path(temporary.name).resolve().is_relative_to(parent.resolve()):
            raise RuntimeError("Unexpected temporary path")
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def reject(self, source, category, code=None):
        with self.assertRaises(OpenPineError) as caught:
            self.session.run(source, "test.pine")
        diagnostic = caught.exception.diagnostic
        self.assertEqual(diagnostic.category, category)
        if code is not None:
            self.assertEqual(diagnostic.code, code)
        return diagnostic

    def runtime_reject(self, program, changed, code):
        runtime = Runtime(program.identity, self.session.catalog.contracts.values())
        with self.assertRaises(OpenPineError) as caught:
            runtime.run(changed)
        self.assertEqual(caught.exception.diagnostic.category, "ARTIFACT_INCOMPATIBLE")
        self.assertEqual(caught.exception.diagnostic.code, code)

    def cli(self, *arguments):
        bootstrap = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('apps.openpine',run_name='__main__')"
        return subprocess.run([sys.executable, "-X", "utf8", "-c", bootstrap, str(ROOT), *arguments],
                              cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)

    def test_independent_corpus_and_recorded_outcomes(self):
        report = run_cases(self.session)
        self.assertEqual(report["executed_programs"], 5)
        self.assertEqual(report["expected_rejections"], 3)
        self.assertTrue(all(row["passed"] for row in report["results"]))

    def test_first_program_is_typed_resolved_and_deterministic(self):
        program, result = self.session.run(SOURCE, "first.pine")
        other = self.session.compile(SOURCE, "first.pine")
        self.assertEqual(program, other)
        self.assertEqual(program_hash(program), program_hash(other))
        self.assertEqual(program.identity.source_sha256, hashlib.sha256(SOURCE.encode()).hexdigest())
        self.assertEqual(program.identity.implementation_profile_id, PROFILE_ID)
        self.assertEqual(result.outputs[0].values, (2.5,))
        self.assertEqual(result.outputs[0].value_type, ValueType("float"))
        self.assertEqual(result.observation_count, 1)
        absolute = program.statements[1].arguments[0].expression
        self.assertEqual(absolute.operation, "numeric.abs")
        self.assertEqual(absolute.overload_id, "overload.math.abs.number")
        self.assertEqual(absolute.arguments[0].parameter_id, "parameter.overload.math.abs.number.number")

    def test_defaults_are_materialized_once_and_named_order_is_preserved(self):
        source = '//@version=6\nindicator("title")\nplot(title="value", series=math.abs(number=-2.5))\n'
        program = self.session.compile(source)
        declaration = program.statements[0]
        self.assertEqual([argument.origin for argument in declaration.arguments], ["source", "default", "default"])
        self.assertEqual([argument.expression.value for argument in declaration.arguments], ["title", "", False])
        output = program.statements[1]
        self.assertEqual(output.evaluation_order, (1, 0))
        self.assertEqual([argument.source_index for argument in output.arguments], [1, 0])

    def test_integer_float_and_duplicate_titles_remain_distinct(self):
        source = '//@version=6\nindicator("types")\nplot(math.abs(-7), "same")\nplot(math.abs(-2.5), "same")\n'
        _, result = self.session.run(source)
        self.assertEqual([output.values for output in result.outputs], [(7,), (2.5,)])
        self.assertEqual([output.value_type.base for output in result.outputs], ["int", "float"])
        self.assertEqual(len({output.output_id for output in result.outputs}), 2)

    def test_binding_failures_do_not_reach_runtime(self):
        examples = {
            'math.abs(true)': 'ARGUMENT_TYPE', 'math.abs("-2.5")': 'ARGUMENT_TYPE',
            'math.abs()': 'MISSING_ARGUMENT', 'math.abs(-2.5, -3.0)': 'TOO_MANY_ARGUMENTS',
            'math.abs(value=-2.5)': 'UNKNOWN_ARGUMENT',
            'math.abs(-2.5, number=-3.0)': 'DUPLICATE_ARGUMENT',
            'math.abs(number=-2.5, -3.0)': 'POSITIONAL_AFTER_NAMED',
        }
        with patch("packages.runtime.engine.Runtime.run", side_effect=AssertionError("runtime reached")):
            for expression, code in examples.items():
                with self.subTest(expression=expression):
                    self.reject(f'//@version=6\nindicator("bad")\nplot({expression})\n', "LANGUAGE_INVALID", code)

    def test_versions_v1_to_v5_have_explicit_numeric_profiles(self):
        for version in range(1, 6):
            with self.subTest(version=version):
                declaration = "study" if version < 5 else "indicator"
                program, result = self.session.run(f'//@version={version}\n{declaration}("old")\nplot(-2.5)')
                self.assertEqual(program.identity.language_version, version)
                self.assertEqual(result.outputs[0].values, (-2.5,))
        program, result = self.session.run('study("old")\nplot(abs(-2.5))')
        self.assertEqual(program.identity.language_version, 1)
        self.assertEqual(result.outputs[0].values, (2.5,))

    def test_directives_in_strings_and_comments_are_not_selected(self):
        source = 'indicator("//@version=6")\nplot(-2.5)'
        self.assertEqual(self.reject(source, "LANGUAGE_INVALID").language_version, 1)
        source = '// an ordinary //@version=5 comment\n' + SOURCE.replace('R6 first run', '//@version=3')
        _, result = self.session.run(source)
        self.assertEqual(result.identity.language_version, 6)
        for number in ("7", "0", "9" * 5000):
            self.reject(f'//@version={number}\nindicator("bad")\nplot(1)', "LANGUAGE_INVALID", "LANGUAGE_VERSION")

    def test_unicode_crlf_and_error_spans_use_character_columns(self):
        source = '//@version=6\r\nindicator("Пайн 🎄")\r\nplot(math.abs(true))\r\n'
        error = self.reject(source, "LANGUAGE_INVALID", "ARGUMENT_TYPE")
        self.assertEqual((error.span.source, error.span.line, error.span.column), ("test.pine", 3, 15))
        self.assertEqual(source[error.span.start:error.span.end], "true")
        self.assertEqual(error.rule_id, "rule.types")
        valid = source.replace("true", "-2.5")
        program, result = self.session.run(valid)
        self.assertEqual(result.declaration.title, "Пайн 🎄")
        self.assertEqual(program.identity.source_sha256, hashlib.sha256(valid.encode()).hexdigest())

    def test_known_unimplemented_and_unknown_operations_are_distinguished(self):
        self.reject(SOURCE.replace('math.abs(-2.5)', 'math.round(-2.5)'), "IMPLEMENTATION_MISSING", "OPERATION_UNAVAILABLE")
        self.reject(SOURCE.replace('math.abs(-2.5)', 'ta.sma(-2.5, 3)'), "SPEC_UNRESOLVED", "SIGNATURE_UNAVAILABLE")
        self.reject(SOURCE.replace('math.abs(-2.5)', '__import__("os")'), "LANGUAGE_INVALID", "SIGNATURE_UNAVAILABLE")

    def test_uncovered_syntax_and_limits_produce_diagnostics(self):
        for body in ('varip float value = -2.5', 'plot(close)', 'plot(1 % 2)', 'plot("""text""")'):
            with self.subTest(body=body):
                self.reject('//@version=6\nindicator("slice")\n' + body, "PROFILE_UNCOVERED")
        for expression in ('(' * 60 + '1' + ')' * 60, '9' * 5000, '1e999'):
            self.reject('//@version=6\nindicator("limit")\nplot(' + expression + ')', "PROFILE_UNCOVERED")
        self.reject(SOURCE + ' ' * 65536, "PROFILE_UNCOVERED", "SOURCE_LIMIT")

    def test_malformed_programs_and_harness_requirements(self):
        self.reject('//@version=6\nindicator("a")\nplot(math.abs(-2.5)', "LANGUAGE_INVALID", "EXPECTED_TOKEN")
        self.reject('//@version=6\nplot(-2.5)', "LANGUAGE_INVALID", "DECLARATION_COUNT")
        self.reject('//@version=6\nindicator("a")', "LANGUAGE_INVALID", "OUTPUT_REQUIRED")
        self.reject('//@version=6\nindicator("a")\nindicator("b")\nplot(1)', "LANGUAGE_INVALID", "DECLARATION_COUNT")
        self.reject('//@version=6\nindicator("unterminated)\nplot(1)', "LANGUAGE_INVALID", "UNTERMINATED_STRING")

    def test_runtime_rejects_incompatible_identity_and_forged_calls(self):
        program = self.session.compile(SOURCE)
        self.runtime_reject(program, replace(program, identity=replace(program.identity, semantic_sha256="0" * 64)), "IR_IDENTITY")
        output = program.statements[1]
        forged = replace(output, operation="system.exec")
        self.runtime_reject(program, replace(program, statements=(program.statements[0], forged)), "IR_CALL_CONTRACT")
        argument = replace(output.arguments[0], parameter_id="parameter.wrong")
        forged = replace(output, arguments=(argument, *output.arguments[1:]))
        self.runtime_reject(program, replace(program, statements=(program.statements[0], forged)), "IR_PARAMETER_IDS")

    def test_runtime_checks_types_and_evaluation_order_before_any_execution(self):
        program = self.session.compile('//@version=6\nindicator("order")\nplot(title="x",series=math.abs(-2.5))')
        output = program.statements[1]
        forged = replace(output, evaluation_order=tuple(reversed(output.evaluation_order)))
        self.runtime_reject(program, replace(program, statements=(program.statements[0], forged)), "IR_EVALUATION_ORDER")
        numeric = LiteralIR(True, ValueType("int"), output.span)
        forged = replace(output, arguments=(replace(output.arguments[0], expression=numeric), output.arguments[1]))
        self.runtime_reject(program, replace(program, statements=(program.statements[0], forged)), "IR_LITERAL_TYPE")

    def test_source_to_result_is_offline(self):
        with patch.object(socket, "create_connection", side_effect=AssertionError("network")), \
             patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS")):
            _, result = Session().run(SOURCE)
            self.assertEqual(result.outputs[0].values, (2.5,))

    def test_cli_compile_and_run_are_real_processes(self):
        source = "conformance/literal-harness/abs-float.pine"
        compiled = self.cli("compile", "--source", source)
        executed = self.cli("run", "--source", source)
        self.assertEqual((compiled.returncode, executed.returncode), (0, 0), compiled.stderr + executed.stderr)
        left, right = json.loads(compiled.stdout), json.loads(executed.stdout)
        self.assertEqual(left["status"], "compiled")
        self.assertIsNone(left["result"])
        self.assertEqual(left["program_sha256"], right["program_sha256"])
        self.assertEqual(right["status"], "executed")
        self.assertEqual(right["python"], "3.14.8")
        self.assertEqual(right["result"]["outputs"][0]["values"], [2.5])
        self.assertEqual(right["identity"]["language_version"], 6)

    def test_cli_rejection_and_utf8_file_output(self):
        rejected = self.cli("run", "--source", "conformance/literal-harness/reject-bool.pine")
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(json.loads(rejected.stdout)["diagnostics"][0]["code"], "ARGUMENT_TYPE")
        output = self.temporary() / "result.json"
        completed = self.cli("run", "--source", "conformance/literal-harness/named-outputs.pine", "--output", str(output))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(read_json(output)["result"]["declaration"]["title"], "Имена 🎄")

    def test_package_dependency_boundaries(self):
        forbidden = {
            "contracts": ("packages.compiler", "packages.runtime", "packages.execution", "apps", "tools"),
            "runtime": ("packages.compiler", "packages.execution", "apps", "tools"),
            "compiler": ("packages.runtime", "packages.execution", "apps", "tools"),
        }
        for package, prefixes in forbidden.items():
            for path in (ROOT / "packages" / package).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
                imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
                self.assertFalse(any(name == prefix or name.startswith(prefix + ".") for name in imports for prefix in prefixes), path)


if __name__ == "__main__":
    unittest.main()
