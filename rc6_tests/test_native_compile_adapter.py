from __future__ import annotations

import importlib
from pathlib import Path

from openpine_contracts import validate_payload


SOURCE = '//@version=6\nindicator("rc6-native")\nplot(close)\n'
COMMITS = {
    "pine2ast": "a" * 40,
    "ast2python": "b" * 40,
}


def _native_module():
    return importlib.import_module("openpine.compile.native_rc6")


def test_native_rc6_adapter_compiles_consumer_bundle_to_v3() -> None:
    native = _native_module()
    adapter = native.NativeRC6CompilerAdapter()

    result = adapter.compile(
        SOURCE,
        module_name="generated_rc6_strategy",
        source_name="rc6-native.pine",
        producer_commits=COMMITS,
    )

    assert result.success, result.errors
    assert result.python_code is not None
    assert "class GeneratedScript:" in result.python_code
    assert result.generated_artifact is not None
    assert result.generated_artifact["schema_id"] == "openpine.generated_artifact.v3"
    assert result.generated_artifact["entrypoint"] == {
        "module": "generated_rc6_strategy",
        "class": "GeneratedScript",
    }
    assert result.generated_artifact["producer"]["commit"] == COMMITS["ast2python"]
    validate_payload("openpine.generated_artifact.v3", result.generated_artifact)
    assert result.consumer_bundle is not None
    assert result.consumer_bundle["producer"]["commit"] == COMMITS["pine2ast"]
    assert result.source_map is not None
    assert result.source_map["schema_id"] == "openpine.source_map.v2"
    assert result.compile_meta["adapter"] == "native-rc6-python-library"
    assert result.compile_meta["artifact_schema_id"] == "openpine.generated_artifact.v3"
    assert result.compile_meta["producer_commits"] == COMMITS


def test_native_rc6_adapter_fails_closed_without_exact_producer_commits() -> None:
    native = _native_module()
    adapter = native.NativeRC6CompilerAdapter()

    result = adapter.compile(SOURCE, producer_commits={"pine2ast": "a" * 40})

    assert not result.success
    assert result.python_code is None
    assert result.generated_artifact is None
    assert result.errors == [
        "producer_commits must contain exact pine2ast and ast2python 40-character Git SHAs"
    ]


def test_native_rc6_module_has_no_legacy_compile_surface() -> None:
    native = _native_module()
    text = Path(native.__file__).read_text(encoding="utf-8")

    for forbidden in (
        "CompileProfile",
        "runtime_contract_v1_4_options",
        "translate_ast",
        "openpine.generated_artifact.v2",
        "OPENPINE_RC5_COMPILER_COMPAT",
    ):
        assert forbidden not in text


def test_production_compile_call_sites_have_no_rc5_adapter_surface() -> None:
    package = Path(__file__).parents[1] / "openpine"
    paths = (
        package / "cli" / "main.py",
        package / "batch" / "runner.py",
        package / "gateway" / "routes" / "pine_ops.py",
        package / "compile" / "pipeline.py",
    )
    forbidden = (
        "SubprocessCompilerAdapter",
        "CompileProfile",
        "runtime_contract_v1_4_options",
        "translate_ast",
        "compile.adapter",
    )
    hits = [
        f"{path.relative_to(package)}:{token}"
        for path in paths
        for token in forbidden
        if token in path.read_text(encoding="utf-8")
    ]
    assert hits == []
