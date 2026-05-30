from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from openpine.compile import CompileProfile, SubprocessCompilerAdapter
from openpine.compile import adapter as adapter_module


def test_pine2ast_subprocess_errors_prefer_stderr_then_stdout() -> None:
    stderr_result = subprocess.CompletedProcess(
        args=["pine2ast"],
        returncode=3,
        stdout="stdout details",
        stderr="stderr details",
    )
    stdout_result = subprocess.CompletedProcess(
        args=["pine2ast"],
        returncode=4,
        stdout="stdout details",
        stderr="",
    )

    assert adapter_module._pine2ast_subprocess_errors(stderr_result) == [
        "pine2ast failed (exit 3)",
        "stderr details",
    ]
    assert adapter_module._pine2ast_subprocess_errors(stdout_result) == [
        "pine2ast failed (exit 4)",
        "stdout details",
    ]


def test_subprocess_compile_meta_records_tool_paths_and_profile() -> None:
    meta = adapter_module._subprocess_compile_meta(
        profile=CompileProfile.diagnostic(),
        module_name="custom_module",
        strict=True,
        pine2ast_path=adapter_module.Path("/tools/pine2ast"),
        ast2python_path=adapter_module.Path("/tools/ast2python"),
        adapter_status="selected",
    )

    assert meta["adapter"] == "subprocess"
    assert meta["adapter_status"] == "selected"
    assert meta["module_name"] == "custom_module"
    assert meta["strict"] is True
    assert meta["compile_profile"] == "diagnostic"
    assert meta["tool_paths"] == {
        "pine2ast": "/tools/pine2ast",
        "ast2python": "/tools/ast2python",
    }


def test_resolve_subprocess_tools_reports_missing_tools(monkeypatch) -> None:
    def fake_find_tool(name: str):
        return adapter_module.Path("/bin/pine2ast") if name == "pine2ast" else None

    monkeypatch.setattr(adapter_module, "_find_tool", fake_find_tool)

    tools, errors = adapter_module._resolve_subprocess_tools()

    assert tools is None
    assert errors == ["ast2python not found in PATH or ~/.local/bin"]


def test_parse_with_pine2ast_subprocess_retries_v5_rejection(
    monkeypatch, tmp_path
) -> None:
    src_path = tmp_path / "source.pine"
    src_path.write_text("//@version=5\nindicator('x')\n")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, src_path.read_text()))
        if len(calls) == 1:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="",
                stderr="P2A0103: unsupported Pine version 5",
            )
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout='{"kind":"Program"}',
            stderr="",
        )

    monkeypatch.setattr(adapter_module.subprocess, "run", fake_run)
    compile_meta = {}

    result, error = adapter_module._parse_with_pine2ast_subprocess(
        pine2ast_path=adapter_module.Path("/bin/pine2ast"),
        src_path=src_path,
        source_text="//@version=5\nindicator('x')\n",
        profile=CompileProfile.diagnostic(allow_implicit_version_rewrite=True),
        timeout=10,
        compile_meta=compile_meta,
    )

    assert error is None
    assert result is not None
    assert result.returncode == 0
    assert len(calls) == 2
    assert calls[1][1].startswith("//@version=6")
    assert compile_meta["unsafe"] is True
    assert compile_meta["compatibility_fallback"]["pine_version_to"] == 6


def _fake_library_apis(metadata: dict):
    return adapter_module._LibraryApis(
        parse_code=lambda _source, _options: SimpleNamespace(
            ast={"kind": "Program"},
            diagnostics=[],
            ok=True,
        ),
        parse_options=lambda **_kwargs: SimpleNamespace(),
        ast_to_json=lambda _ast: json.dumps({"kind": "Program"}),
        translate_ast=lambda *_args, **_kwargs: SimpleNamespace(
            code="# generated\n",
            metadata=metadata,
            source_map=[],
        ),
        versions={
            "pine2ast_version": "test",
            "ast2python_version": "test",
            "pinelib_contract_version": "test",
            "pinelib_version": "test",
        },
    )


def _fake_library_apis_with_v5_retry(metadata: dict):
    calls = {"count": 0}

    def parse_code(_source, _options):
        calls["count"] += 1
        if calls["count"] == 1:
            return SimpleNamespace(
                ast=None,
                diagnostics=[
                    SimpleNamespace(
                        severity=SimpleNamespace(value="error"),
                        code="P2A0103",
                        message="unsupported Pine version 5",
                    )
                ],
                ok=False,
            )
        return SimpleNamespace(ast={"kind": "Program"}, diagnostics=[], ok=True)

    return adapter_module._LibraryApis(
        parse_code=parse_code,
        parse_options=lambda **_kwargs: SimpleNamespace(),
        ast_to_json=lambda _ast: json.dumps({"kind": "Program"}),
        translate_ast=lambda *_args, **_kwargs: SimpleNamespace(
            code="# generated\n",
            metadata=metadata,
            source_map=[],
        ),
        versions={
            "pine2ast_version": "test",
            "ast2python_version": "test",
            "pinelib_contract_version": "test",
            "pinelib_version": "test",
        },
    )


def test_production_compile_rejects_unsafe_translation_metadata(monkeypatch) -> None:
    metadata = {
        "compile_profile": "production",
        "codegen_safe": True,
        "runtime_contract_safe": False,
        "parity_safe": True,
        "unsupported_features": ["request.financial"],
        "unsupported_nodes": [],
        "unsupported_declaration_args": [],
        "import_aliases": [],
    }
    status = adapter_module.LibraryAvailability(available=True)
    monkeypatch.setattr(
        adapter_module,
        "_load_library_apis",
        lambda: (_fake_library_apis(metadata), status),
    )

    result = SubprocessCompilerAdapter().compile(
        "//@version=6\nindicator('x')\nplot(close)\n",
        profile=CompileProfile.production(),
    )

    assert not result.success
    assert result.python_code is None
    assert "runtime_contract_safe=False" in "\n".join(result.errors)
    assert "unsupported_features" in "\n".join(result.errors)
    assert result.compile_meta["production_blockers"] == result.errors
    assert result.compile_meta["translation_metadata"] == metadata


def test_diagnostic_version_rewrite_marks_compile_meta_unsafe(monkeypatch) -> None:
    metadata = {
        "compile_profile": "diagnostic",
        "codegen_safe": False,
        "runtime_contract_safe": False,
        "parity_safe": False,
        "unsafe": True,
        "unsupported_features": [],
        "unsupported_nodes": [],
        "unsupported_declaration_args": [],
        "import_aliases": [],
    }
    status = adapter_module.LibraryAvailability(available=True)
    monkeypatch.setattr(
        adapter_module,
        "_load_library_apis",
        lambda: (_fake_library_apis_with_v5_retry(metadata), status),
    )

    result = SubprocessCompilerAdapter().compile(
        "//@version=5\nindicator('x')\nplot(close)\n",
        profile="diagnostic",
    )

    assert result.success
    assert result.compile_meta["unsafe"] is True
    assert "implicit_pine_version_rewrite" in result.compile_meta["unsafe_reasons"]
    assert result.compile_meta["compatibility_fallback"]["pine_version_from"] == 5
    assert result.compile_meta["translation_metadata"] == metadata


def test_production_compile_rejects_external_import_metadata(monkeypatch) -> None:
    metadata = {
        "compile_profile": "production",
        "codegen_safe": True,
        "runtime_contract_safe": True,
        "parity_safe": True,
        "unsupported_features": [],
        "unsupported_nodes": [],
        "unsupported_declaration_args": [],
        "import_aliases": [{"alias": "lib", "path": "user/Lib/1"}],
    }
    status = adapter_module.LibraryAvailability(available=True)
    monkeypatch.setattr(
        adapter_module,
        "_load_library_apis",
        lambda: (_fake_library_apis(metadata), status),
    )

    result = SubprocessCompilerAdapter().compile(
        "//@version=6\nindicator('x')\nplot(close)\n",
        profile=CompileProfile.production(),
    )

    assert not result.success
    assert "external library imports" in result.errors[0]
    assert result.compile_meta["production_blockers"] == result.errors


def test_production_compile_rejects_explicit_unsafe_metadata(monkeypatch) -> None:
    metadata = {
        "compile_profile": "production",
        "codegen_safe": True,
        "runtime_contract_safe": True,
        "parity_safe": True,
        "unsafe": True,
        "unsupported_features": [],
        "unsupported_nodes": [],
        "unsupported_declaration_args": [],
        "import_aliases": [],
    }
    status = adapter_module.LibraryAvailability(available=True)
    monkeypatch.setattr(
        adapter_module,
        "_load_library_apis",
        lambda: (_fake_library_apis(metadata), status),
    )

    result = SubprocessCompilerAdapter().compile(
        "//@version=6\nindicator('x')\nplot(close)\n",
        profile=CompileProfile.production(),
    )

    assert not result.success
    assert result.errors == ["translation metadata reports unsafe=True"]
    assert result.compile_meta["production_blockers"] == result.errors


def test_diagnostic_compile_can_return_unsafe_metadata(monkeypatch) -> None:
    metadata = {
        "compile_profile": "diagnostic",
        "codegen_safe": False,
        "runtime_contract_safe": False,
        "parity_safe": False,
        "unsupported_features": ["request.financial"],
        "unsupported_nodes": [{"kind": "ExternalCall"}],
        "unsupported_declaration_args": [],
        "import_aliases": [{"alias": "lib", "path": "user/Lib/1"}],
    }
    status = adapter_module.LibraryAvailability(available=True)
    monkeypatch.setattr(
        adapter_module,
        "_load_library_apis",
        lambda: (_fake_library_apis(metadata), status),
    )

    result = SubprocessCompilerAdapter().compile(
        "//@version=6\nindicator('x')\nplot(close)\n",
        profile=CompileProfile.diagnostic(),
    )

    assert result.success
    assert result.python_code == "# generated\n"
    assert result.compile_meta["translation_metadata"] == metadata
    assert "production_blockers" not in result.compile_meta


def test_live_production_compile_rejects_external_library_import() -> None:
    adapter = SubprocessCompilerAdapter()
    if not adapter.library_status().available:
        pytest.skip("local compiler Python APIs are unavailable")

    result = adapter.compile(
        "//@version=6\n"
        "indicator('x')\n"
        "import user/Lib/1 as lib\n"
        "y = lib.custom(close)\n"
        "plot(y)\n",
        profile=CompileProfile.production(),
    )

    assert not result.success
    assert "import" in "\n".join(result.errors).lower()


def test_live_production_compile_rejects_unsupported_request() -> None:
    adapter = SubprocessCompilerAdapter()
    if not adapter.library_status().available:
        pytest.skip("local compiler Python APIs are unavailable")

    result = adapter.compile(
        "//@version=6\n"
        "indicator('x')\n"
        "y = request.financial(syminfo.tickerid, 'TOTAL_SHARES_OUTSTANDING', 'FY')\n"
        "plot(y)\n",
        profile=CompileProfile.production(),
    )

    assert not result.success
    assert "unsupported request" in "\n".join(result.errors).lower()
