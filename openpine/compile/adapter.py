"""CompilerAdapter — pine2ast + ast2python compilation adapters."""

from __future__ import annotations

import importlib
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Protocol

from ast2python.profiles import CompileProfile

# Common installation locations for pine2ast/ast2python
TOOL_SEARCH_PATHS = [
    Path.home() / ".local" / "bin",
    Path("/usr/local/bin"),
    Path("/usr/bin"),
]

COMPILER_PACKAGES = ("pine2ast", "ast2python", "pinelib")

_PINE_V5_DIRECTIVE_RE = re.compile(r"^(\s*//\s*@version\s*=\s*)5(\b.*)$")
_PINE_V5_FALLBACK_WARNING = (
    "Pine v5 compatibility fallback: parser rejected //@version=5; "
    "retried as //@version=6"
)
_PINE_V5_FALLBACK_UNSAFE_REASON = "implicit_pine_version_rewrite"

_VISUAL_CONTRACT_BUILTINS = (
    "plot",
    "plotshape",
    "plotchar",
    "plotbar",
    "plotcandle",
    "hline",
    "fill",
    "barcolor",
    "bgcolor",
    "color.new",
    "color.rgb",
    "label.new",
)
_SUPPORTED_REQUEST_CALLS = {"security", "security_lower_tf"}


def _is_visual_contract_diagnostic(message: str) -> bool:
    lowered = message.lower()
    if (
        "p2a1507" not in lowered
        and "not lowerable under runtime_contract" not in lowered
    ):
        return False
    return any(f"builtin {name}" in lowered for name in _VISUAL_CONTRACT_BUILTINS)


def _unsupported_request_in_source_error(source_text: str) -> str | None:
    for name in sorted(
        set(re.findall(r"\brequest\.([A-Za-z_][A-Za-z0-9_]*)", source_text))
    ):
        if name not in _SUPPORTED_REQUEST_CALLS:
            return (
                f"unsupported request call is not production lowerable: request.{name}"
            )
    return None


def _find_tool(name: str) -> Path | None:
    """Find a tool in PATH or common locations."""
    # First check PATH
    path = shutil.which(name)
    if path:
        return Path(path)
    # Then check common locations
    for search_dir in TOOL_SEARCH_PATHS:
        candidate = search_dir / name
        if candidate.exists():
            return candidate
    return None


def _version_from_module(module: ModuleType, *names: str) -> str:
    for name in names:
        value = getattr(module, name, None)
        if isinstance(value, str):
            return value
    return "unknown"


def _diagnostic_message(diagnostic: Any) -> str:
    code = getattr(diagnostic, "code", None)
    severity = getattr(getattr(diagnostic, "severity", None), "value", None)
    message = getattr(diagnostic, "message", None)
    parts = [str(part) for part in (severity, code, message) if part]
    return ": ".join(parts) if parts else str(diagnostic)


def _normalize_pine_v5_directive(source_text: str) -> tuple[str, bool]:
    """Rewrite only a Pine version directive from v5 to v6."""
    lines = source_text.splitlines(keepends=True)
    for idx, line in enumerate(lines):
        body = line.rstrip("\r\n")
        newline = line[len(body) :]
        match = _PINE_V5_DIRECTIVE_RE.match(body)
        if match:
            lines[idx] = f"{match.group(1)}6{match.group(2)}{newline}"
            return "".join(lines), True
    return source_text, False


def _is_pine_v5_compatibility_diagnostic(message: str) -> bool:
    lowered = str(message).lower()
    return "p2a0103" in lowered and "compatibility mode" in lowered


def _is_non_blocking_parse_diagnostic(message: str) -> bool:
    return _is_visual_contract_diagnostic(message) or _is_pine_v5_compatibility_diagnostic(
        message
    )


def _is_pine_v5_version_rejection(messages: list[str]) -> bool:
    """Return True only for the known pine2ast v5-version rejection."""
    combined = "\n".join(str(message) for message in messages if message)
    lowered = combined.lower()
    has_v5_marker = "p2a0103" in lowered or (
        "unsupported pine version" in lowered and "5" in lowered
    )
    if not has_v5_marker:
        return False

    diagnostic_codes = set(re.findall(r"\bP2A\d+\b", combined, flags=re.IGNORECASE))
    return not diagnostic_codes or diagnostic_codes == {"P2A0103"}


def _production_metadata_blockers(metadata: dict[str, Any]) -> list[str]:
    """Return unsafe translation metadata reasons that production must reject."""
    blockers: list[str] = []

    for key in ("codegen_safe", "runtime_contract_safe", "parity_safe"):
        if metadata.get(key) is False:
            blockers.append(f"translation metadata reports {key}=False")

    if metadata.get("unsafe") is True:
        blockers.append("translation metadata reports unsafe=True")

    for key in (
        "unsupported_features",
        "unsupported_nodes",
        "unsupported_declaration_args",
    ):
        values = metadata.get(key)
        if values:
            blockers.append(f"translation metadata reports {key}: {values}")

    import_aliases = metadata.get("import_aliases")
    if import_aliases:
        blockers.append(
            "external library imports require stubs/resolution and are not production-safe: "
            f"{import_aliases}"
        )

    compile_profile = metadata.get("compile_profile")
    if compile_profile not in (None, "production"):
        blockers.append(
            f"translation metadata compile_profile is {compile_profile!r}, not 'production'"
        )

    return blockers


def _unsupported_request_error(exc: Exception) -> str | None:
    message = str(exc).strip("'\"")
    if re.fullmatch(r"request\.[A-Za-z_][A-Za-z0-9_]*", message):
        return f"unsupported request call is not production lowerable: {message}"
    return None


def _pine2ast_subprocess_errors(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [
        f"pine2ast failed (exit {result.returncode})",
        result.stderr or result.stdout,
    ]


def _subprocess_compile_meta(
    *,
    profile: CompileProfile,
    module_name: str,
    strict: bool,
    pine2ast_path: Path,
    ast2python_path: Path,
    adapter_status: str,
) -> dict[str, Any]:
    return {
        "pine2ast_version": "unknown",
        "ast2python_version": "unknown",
        "pinelib_contract_version": "unknown",
        "adapter": "subprocess",
        "adapter_status": adapter_status,
        "module_name": module_name,
        "strict": strict,
        "compile_profile": profile.name,
        "tool_paths": {
            "pine2ast": str(pine2ast_path),
            "ast2python": str(ast2python_path),
        },
    }


def _mark_compile_meta_unsafe(compile_meta: dict[str, Any], reason: str) -> None:
    compile_meta["unsafe"] = True
    reasons = list(compile_meta.get("unsafe_reasons", []))
    if reason not in reasons:
        reasons.append(reason)
    compile_meta["unsafe_reasons"] = reasons


def _allow_visual_only_producer_gates(
    ast_payload: dict[str, Any], compile_meta: dict[str, Any]
) -> None:
    """Treat filtered frontend diagnostics as non-blocking for codegen.

    Pine2AST correctly records parser/semantic gates as failed when production
    sees unsupported visual outputs (for example ``plot`` under runtime_contract
    v1.4) or v5 compatibility diagnostics. OpenPine filters those diagnostics
    before strategy codegen, so the downstream ast2python producer-metadata gate
    must see passed gates for this already-whitelisted case. Non-whitelisted
    diagnostics never reach this helper because compile metadata is set only
    after the parse-error whitelist.
    """
    if not (
        compile_meta.get("filtered_visual_diagnostics")
        or compile_meta.get("filtered_compatibility_diagnostics")
    ):
        return
    producer = ast_payload.setdefault("producer_metadata", {})
    if not isinstance(producer, dict):
        return
    original = {
        "parser_gate": producer.get("parser_gate"),
        "semantic_gate": producer.get("semantic_gate"),
    }
    if original != {"parser_gate": "pass", "semantic_gate": "pass"}:
        compile_meta["filtered_visual_original_producer_gates"] = original
    producer.setdefault("contract", "pine.ast_contract.v1")
    producer.setdefault("runtime_contract", "1.4")
    producer.setdefault("runtime_contract_profile", "runtime_contract_v1_4")
    producer["parser_gate"] = "pass"
    producer["semantic_gate"] = "pass"


def _diagnostic_payload_message(diagnostic: Any) -> str:
    if isinstance(diagnostic, dict):
        severity = diagnostic.get("severity")
        code = diagnostic.get("code")
        message = diagnostic.get("message")
        parts = [str(part) for part in (severity, code, message) if part]
        return ": ".join(parts) if parts else str(diagnostic)
    return _diagnostic_message(diagnostic)


def _drop_filtered_ast_diagnostics(
    ast_payload: dict[str, Any], compile_meta: dict[str, Any]
) -> None:
    if not (
        compile_meta.get("filtered_visual_diagnostics")
        or compile_meta.get("filtered_compatibility_diagnostics")
    ):
        return
    diagnostics = ast_payload.get("diagnostics")
    if not isinstance(diagnostics, list):
        return
    kept: list[Any] = []
    removed = 0
    for diagnostic in diagnostics:
        message = _diagnostic_payload_message(diagnostic)
        if _is_non_blocking_parse_diagnostic(message):
            removed += 1
        else:
            kept.append(diagnostic)
    if removed:
        ast_payload["diagnostics"] = kept
        compile_meta["filtered_ast_diagnostics"] = removed


@dataclass(frozen=True)
class LibraryAvailability:
    """Availability status for the local Python compiler stack."""

    available: bool
    errors: list[str] = field(default_factory=list)
    paths: dict[str, str] = field(default_factory=dict)
    versions: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _LibraryApis:
    parse_code: Callable[..., Any]
    parse_options: Callable[..., Any]
    ast_to_json: Callable[..., str]
    translate_ast: Callable[..., Any]
    versions: dict[str, str]


def _import_local_module(name: str) -> ModuleType:
    if name not in COMPILER_PACKAGES:
        raise ValueError(f"Unsupported compiler package: {name}")
    return importlib.import_module(name)


def _load_library_apis() -> tuple[_LibraryApis | None, LibraryAvailability]:
    errors: list[str] = []
    modules: dict[str, ModuleType] = {}

    for name in COMPILER_PACKAGES:
        try:
            modules[name] = _import_local_module(name)
        except (
            Exception
        ) as exc:  # pragma: no cover - exact import failures are environment-specific
            errors.append(f"{name} unavailable: {exc}")

    try:
        pine2ast_api = importlib.import_module("pine2ast.api")
    except (
        Exception
    ) as exc:  # pragma: no cover - exact import failures are environment-specific
        pine2ast_api = None
        errors.append(f"pine2ast.api unavailable: {exc}")

    if errors:
        return None, LibraryAvailability(
            available=False,
            errors=errors,
            paths={name: "installed-package" for name in COMPILER_PACKAGES},
        )

    pine2ast = modules["pine2ast"]
    ast2python = modules["ast2python"]
    pinelib = modules["pinelib"]

    missing: list[str] = []
    api_modules = {
        "pine2ast.api": pine2ast_api,
        "ast2python": ast2python,
    }
    for module_name, attr in (
        ("pine2ast.api", "parse_code"),
        ("pine2ast.api", "runtime_contract_v1_4_options"),
        ("pine2ast.api", "ast_to_json"),
        ("ast2python", "translate_ast"),
    ):
        module = api_modules[module_name]
        if module is None or not hasattr(module, attr):
            missing.append(f"{module_name}.{attr}")

    versions = {
        "pine2ast_version": _version_from_module(pine2ast, "__version__"),
        "ast2python_version": _version_from_module(ast2python, "__version__"),
        "pinelib_contract_version": _version_from_module(
            pinelib, "RUNTIME_CONTRACT_VERSION"
        ),
        "pinelib_version": _version_from_module(
            pinelib, "PACKAGE_VERSION", "__version__"
        ),
    }

    status = LibraryAvailability(
        available=not missing,
        errors=[f"missing Python API: {name}" for name in missing],
        paths={name: "installed-package" for name in COMPILER_PACKAGES},
        versions=versions,
    )
    if missing:
        return None, status

    return (
        _LibraryApis(
            parse_code=pine2ast_api.parse_code,
            parse_options=pine2ast_api.runtime_contract_v1_4_options,
            ast_to_json=pine2ast_api.ast_to_json,
            translate_ast=ast2python.translate_ast,
            versions=versions,
        ),
        status,
    )


@dataclass(frozen=True)
class CompileResult:
    """Result of a compile operation."""

    success: bool
    python_code: str | None = None
    errors: list[str] = field(default_factory=list)
    compile_meta: dict = field(default_factory=dict)
    ast_json: str | None = None


@dataclass(frozen=True)
class _SubprocessTools:
    pine2ast_path: Path
    ast2python_path: Path


def _resolve_subprocess_tools() -> tuple[_SubprocessTools | None, list[str]]:
    pine2ast_path = _find_tool("pine2ast")
    ast2python_path = _find_tool("ast2python")

    errors: list[str] = []
    if pine2ast_path is None:
        errors.append("pine2ast not found in PATH or ~/.local/bin")
    if ast2python_path is None:
        errors.append("ast2python not found in PATH or ~/.local/bin")

    if errors or pine2ast_path is None or ast2python_path is None:
        return None, errors
    return (
        _SubprocessTools(
            pine2ast_path=pine2ast_path,
            ast2python_path=ast2python_path,
        ),
        [],
    )


def _write_temp_pine_source(source_text: str) -> Path:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pine", delete=False) as src_f:
        src_f.write(source_text)
        return Path(src_f.name)


def _parse_with_pine2ast_subprocess(
    *,
    pine2ast_path: Path,
    src_path: Path,
    source_text: str,
    profile: CompileProfile,
    timeout: int,
    compile_meta: dict[str, Any],
) -> tuple[subprocess.CompletedProcess[str] | None, CompileResult | None]:
    result_p2a = subprocess.run(
        [str(pine2ast_path), "parse", str(src_path)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result_p2a.returncode == 0:
        return result_p2a, None

    parse_errors = _pine2ast_subprocess_errors(result_p2a)
    normalized_source, normalized = _normalize_pine_v5_directive(source_text)
    if not (normalized and _is_pine_v5_version_rejection(parse_errors)):
        return None, CompileResult(success=False, errors=parse_errors)

    if not profile.allow_implicit_version_rewrite:
        return None, CompileResult(
            success=False,
            errors=parse_errors,
            compile_meta=compile_meta,
        )

    src_path.write_text(normalized_source)
    result_p2a = subprocess.run(
        [str(pine2ast_path), "parse", str(src_path)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    warnings = list(compile_meta.get("warnings", []))
    warnings.append(_PINE_V5_FALLBACK_WARNING)
    _mark_compile_meta_unsafe(compile_meta, _PINE_V5_FALLBACK_UNSAFE_REASON)
    compile_meta.update(
        {
            "warnings": warnings,
            "compatibility_fallback": {
                "pine_version_from": 5,
                "pine_version_to": 6,
                "reason": "pine2ast_version_rejection",
            },
            "original_parse_errors": parse_errors,
        }
    )
    if result_p2a.returncode != 0:
        return None, CompileResult(
            success=False,
            errors=_pine2ast_subprocess_errors(result_p2a),
            compile_meta=compile_meta,
        )
    return result_p2a, None


def _translate_ast_with_subprocess(
    *,
    ast2python_path: Path,
    ast_json: str,
    module_name: str,
    strict: bool,
    timeout: int,
) -> tuple[str | None, CompileResult | None, Path]:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as ast_f:
        ast_f.write(ast_json)
        ast_path = Path(ast_f.name)

    cmd = [
        str(ast2python_path),
        "translate",
        str(ast_path),
        "-o",
        "/dev/stdout",
        "--module-name",
        module_name,
    ]
    if strict:
        cmd.append("--strict")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        return (
            None,
            CompileResult(
                success=False,
                errors=[
                    f"ast2python failed (exit {result.returncode})",
                    result.stderr or result.stdout,
                ],
                ast_json=ast_json,
            ),
            ast_path,
        )

    return result.stdout, None, ast_path


def _subprocess_ast_json_or_error(
    *,
    pine2ast_path: Path,
    src_path: Path,
    source_text: str,
    profile: CompileProfile,
    timeout: int,
    compile_meta: dict[str, Any],
) -> tuple[str | None, CompileResult | None]:
    result_p2a, parse_error = _parse_with_pine2ast_subprocess(
        pine2ast_path=pine2ast_path,
        src_path=src_path,
        source_text=source_text,
        profile=profile,
        timeout=timeout,
        compile_meta=compile_meta,
    )
    if parse_error is not None:
        return None, parse_error
    if result_p2a is None:
        return None, CompileResult(
            success=False,
            errors=["pine2ast subprocess returned no result"],
            compile_meta=compile_meta,
        )

    ast_json = result_p2a.stdout
    try:
        json.loads(ast_json)
    except json.JSONDecodeError as e:
        return None, CompileResult(
            success=False,
            errors=[f"pine2ast produced invalid JSON: {e}"],
        )
    return ast_json, None


def _parse_with_library_api(
    *,
    apis: _LibraryApis,
    source_text: str,
    options: Any,
    profile: CompileProfile,
    compile_meta: dict[str, Any],
) -> tuple[Any | None, CompileResult | None]:
    parse_result = apis.parse_code(source_text, options)
    ast = getattr(parse_result, "ast", None)
    diagnostics = list(getattr(parse_result, "diagnostics", []) or [])
    ok = bool(getattr(parse_result, "ok", False))
    parse_errors = [_diagnostic_message(diagnostic) for diagnostic in diagnostics]
    if ast is not None and (
        ok
        or (
            parse_errors
            and all(_is_non_blocking_parse_diagnostic(error) for error in parse_errors)
        )
    ):
        if parse_errors and not ok:
            visual_errors = [
                error for error in parse_errors if _is_visual_contract_diagnostic(error)
            ]
            compatibility_errors = [
                error
                for error in parse_errors
                if _is_pine_v5_compatibility_diagnostic(error)
            ]
            if visual_errors:
                compile_meta["filtered_visual_diagnostics"] = visual_errors
            if compatibility_errors:
                compile_meta["filtered_compatibility_diagnostics"] = compatibility_errors
        return ast, None

    parse_errors = parse_errors or ["pine2ast returned no AST"]
    normalized_source, normalized = _normalize_pine_v5_directive(source_text)
    if not (normalized and _is_pine_v5_version_rejection(parse_errors)):
        return None, CompileResult(
            success=False,
            errors=parse_errors,
            compile_meta=compile_meta,
        )

    if not profile.allow_implicit_version_rewrite:
        return None, CompileResult(
            success=False,
            errors=parse_errors,
            compile_meta=compile_meta,
        )

    parse_result = apis.parse_code(normalized_source, options)
    ast = getattr(parse_result, "ast", None)
    diagnostics = list(getattr(parse_result, "diagnostics", []) or [])
    ok = bool(getattr(parse_result, "ok", False))
    warnings = list(compile_meta.get("warnings", []))
    warnings.append(_PINE_V5_FALLBACK_WARNING)
    _mark_compile_meta_unsafe(compile_meta, _PINE_V5_FALLBACK_UNSAFE_REASON)
    compile_meta.update(
        {
            "warnings": warnings,
            "compatibility_fallback": {
                "pine_version_from": 5,
                "pine_version_to": 6,
                "reason": "pine2ast_version_rejection",
            },
            "original_parse_errors": parse_errors,
        }
    )
    if ast is None or not ok:
        retry_errors = [
            _diagnostic_message(diagnostic) for diagnostic in diagnostics
        ] or ["pine2ast returned no AST"]
        return None, CompileResult(
            success=False,
            errors=retry_errors,
            compile_meta=compile_meta,
        )
    return ast, None


def _translate_ast_with_library_api(
    *,
    apis: _LibraryApis,
    ast: Any,
    module_name: str,
    strict: bool,
    profile: CompileProfile,
    compile_meta: dict[str, Any],
    kwargs: dict[str, Any],
) -> CompileResult:
    ast_json = apis.ast_to_json(ast)
    ast_payload = json.loads(ast_json)
    _allow_visual_only_producer_gates(ast_payload, compile_meta)
    _drop_filtered_ast_diagnostics(ast_payload, compile_meta)
    ast_json = json.dumps(ast_payload, ensure_ascii=False)
    translation = apis.translate_ast(
        ast_payload,
        module_name=module_name,
        strict=strict,
        emit_source_comments=kwargs.get("emit_source_comments", True),
        allow_invalid_ast=kwargs.get("allow_invalid_ast", profile.allow_invalid_ast),
        allow_contract_mismatch=kwargs.get("allow_contract_mismatch", False),
        allow_external_library_stubs=kwargs.get(
            "allow_external_library_stubs", profile.allow_external_library_stubs
        ),
        allow_unsupported_request_stubs=kwargs.get(
            "allow_unsupported_request_stubs",
            profile.allow_unsupported_request_stubs,
        ),
        allow_realtime_local_simulation=kwargs.get(
            "allow_realtime_local_simulation", False
        ),
    )
    translation_meta = getattr(translation, "metadata", {}) or {}
    compile_meta.update(
        {
            "translation_metadata": translation_meta,
            "source_map_entries": len(getattr(translation, "source_map", []) or []),
        }
    )
    if profile.name == "production":
        blockers = _production_metadata_blockers(translation_meta)
        if blockers:
            compile_meta["production_blockers"] = blockers
            return CompileResult(
                success=False,
                errors=blockers,
                ast_json=ast_json,
                compile_meta=compile_meta,
            )
    return CompileResult(
        success=True,
        python_code=getattr(translation, "code"),
        ast_json=ast_json,
        compile_meta=compile_meta,
    )


def _profile_from_kwargs(kwargs: dict[str, Any]) -> CompileProfile:
    raw = kwargs.get("profile")
    if isinstance(raw, CompileProfile):
        profile = raw
    elif raw == "diagnostic":
        unsafe_diagnostic = True
        profile = CompileProfile.diagnostic(
            allow_external_library_stubs=unsafe_diagnostic,
            allow_unsupported_request_stubs=unsafe_diagnostic,
            allow_invalid_ast=unsafe_diagnostic,
            allow_subprocess_fallback=unsafe_diagnostic,
            allow_implicit_version_rewrite=unsafe_diagnostic,
        )
    else:
        profile = CompileProfile.production()

    if profile.name == "production" and (
        profile.allow_external_library_stubs
        or profile.allow_unsupported_request_stubs
        or profile.allow_invalid_ast
        or profile.allow_subprocess_fallback
        or profile.allow_implicit_version_rewrite
    ):
        raise ValueError(
            "production CompileProfile cannot enable unsafe compile allowances"
        )
    return profile


class CompilerAdapter(Protocol):
    """Protocol for Pine compiler adapters — section 30.1."""

    def compile(self, source_text: str, **kwargs) -> CompileResult:
        """Compile Pine source text to Python. Returns CompileResult."""
        ...


@dataclass
class SubprocessCompilerAdapter:
    """Compiler adapter for the local pine2ast + ast2python + pinelib stack.

    Uses the local Python APIs first when available, then falls back to the
    original pine2ast/ast2python subprocess pipeline. Missing tools or libraries
    return CompileResult errors instead of crashing.
    """

    timeout: int = 60
    prefer_library: bool = True
    fallback_to_subprocess: bool = False

    def library_status(self) -> LibraryAvailability:
        """Return import/API availability for the local Python compiler stack."""
        _, status = _load_library_apis()
        return status

    def compile(self, source_text: str, **kwargs) -> CompileResult:
        """Compile Pine source text via Python APIs when possible.

        Args:
            source_text: Raw Pine script source.
            **kwargs: Extra options (module_name, strict, etc.)

        Returns:
            CompileResult with success=True and python_code on success,
            or success=False with errors list.
        """
        try:
            profile = _profile_from_kwargs(kwargs)
        except ValueError as exc:
            return CompileResult(success=False, errors=[str(exc)])
        if profile.name == "production" and (
            kwargs.get("allow_external_library_stubs")
            or kwargs.get("allow_unsupported_request_stubs")
            or kwargs.get("fallback_to_subprocess")
            or kwargs.get("allow_implicit_version_rewrite")
        ):
            return CompileResult(
                success=False,
                errors=["production compile cannot enable unsafe compile allowances"],
            )
        kwargs["profile"] = profile

        if self.prefer_library:
            apis, status = _load_library_apis()
            if apis is not None:
                return self._compile_with_library(apis, source_text, **kwargs)
            if not (self.fallback_to_subprocess and profile.allow_subprocess_fallback):
                return CompileResult(
                    success=False,
                    errors=status.errors or ["Python compiler APIs unavailable"],
                    compile_meta={
                        "adapter": "python-library",
                        "adapter_status": "unavailable",
                        "library_paths": status.paths,
                        **status.versions,
                    },
                )

        if not profile.allow_subprocess_fallback:
            return CompileResult(
                success=False,
                errors=["subprocess compile fallback is disabled by compile profile"],
                compile_meta={"compile_profile": profile.name},
            )
        return self._compile_with_subprocess(source_text, **kwargs)

    def _compile_with_library(
        self, apis: _LibraryApis, source_text: str, **kwargs
    ) -> CompileResult:
        module_name = kwargs.get("module_name", "generated_strategy")
        strict = kwargs.get("strict", False)
        profile = _profile_from_kwargs(kwargs)
        if profile.name == "production" and (
            kwargs.get("allow_external_library_stubs")
            or kwargs.get("allow_unsupported_request_stubs")
            or kwargs.get("allow_implicit_version_rewrite")
        ):
            return CompileResult(
                success=False,
                errors=["production compile cannot enable unsafe compile allowances"],
                compile_meta={"compile_profile": profile.name},
            )
        compile_meta = {
            **apis.versions,
            "adapter": "python-library",
            "adapter_status": "available",
            "module_name": module_name,
            "strict": strict,
            "compile_profile": profile.name,
            "library_paths": {name: "installed-package" for name in COMPILER_PACKAGES},
        }

        try:
            options = apis.parse_options(
                source_name=kwargs.get("source_name", "<memory>"),
            )
            ast, parse_error = _parse_with_library_api(
                apis=apis,
                source_text=source_text,
                options=options,
                profile=profile,
                compile_meta=compile_meta,
            )
            if parse_error is not None:
                if profile.name == "production":
                    request_error = _unsupported_request_in_source_error(source_text)
                    if request_error:
                        compile_meta["production_blockers"] = [request_error]
                        return CompileResult(
                            success=False,
                            errors=[request_error],
                            compile_meta=compile_meta,
                        )
                return parse_error
            if ast is None:
                return CompileResult(
                    success=False,
                    errors=["Python compiler API returned no AST"],
                    compile_meta=compile_meta,
                )

            if profile.name == "production":
                request_error = _unsupported_request_in_source_error(source_text)
                if request_error:
                    compile_meta["production_blockers"] = [request_error]
                    return CompileResult(
                        success=False, errors=[request_error], compile_meta=compile_meta
                    )

            return _translate_ast_with_library_api(
                apis=apis,
                ast=ast,
                module_name=module_name,
                strict=strict,
                profile=profile,
                compile_meta=compile_meta,
                kwargs=kwargs,
            )
        except Exception as exc:
            production_error = (
                _unsupported_request_error(exc)
                if _profile_from_kwargs(kwargs).name == "production"
                else None
            )
            if production_error:
                compile_meta["production_blockers"] = [production_error]
                return CompileResult(
                    success=False,
                    errors=[production_error],
                    compile_meta=compile_meta,
                )
            return CompileResult(
                success=False,
                errors=[f"Python compiler API failed: {exc}"],
                compile_meta=compile_meta,
            )

    def _compile_with_subprocess(self, source_text: str, **kwargs) -> CompileResult:
        """Compile Pine source text via the original subprocess pipeline."""
        profile = _profile_from_kwargs(kwargs)
        if not profile.allow_subprocess_fallback:
            return CompileResult(
                success=False,
                errors=["subprocess compile fallback is disabled by compile profile"],
                compile_meta={"compile_profile": profile.name},
            )
        tools, errors = _resolve_subprocess_tools()
        if errors:
            return CompileResult(success=False, errors=errors)
        if tools is None:
            return CompileResult(
                success=False,
                errors=["subprocess compiler tools resolver returned no tools"],
                compile_meta={"compile_profile": profile.name},
            )

        try:
            src_path = _write_temp_pine_source(source_text)
        except OSError as e:
            return CompileResult(
                success=False, errors=[f"Failed to write temp source: {e}"]
            )

        try:
            module_name = kwargs.get("module_name", "generated_strategy")
            strict = kwargs.get("strict", False)
            compile_meta = _subprocess_compile_meta(
                profile=profile,
                module_name=module_name,
                strict=strict,
                pine2ast_path=tools.pine2ast_path,
                ast2python_path=tools.ast2python_path,
                adapter_status="fallback" if self.prefer_library else "selected",
            )

            ast_json, ast_error = _subprocess_ast_json_or_error(
                pine2ast_path=tools.pine2ast_path,
                src_path=src_path,
                source_text=source_text,
                profile=profile,
                timeout=self.timeout,
                compile_meta=compile_meta,
            )
            if ast_error is not None:
                return ast_error
            if ast_json is None:
                return CompileResult(
                    success=False,
                    errors=["pine2ast subprocess returned no AST JSON"],
                    compile_meta=compile_meta,
                )

            python_code, translate_error, ast_path = _translate_ast_with_subprocess(
                ast2python_path=tools.ast2python_path,
                ast_json=ast_json,
                module_name=module_name,
                strict=strict,
                timeout=self.timeout,
            )
            if translate_error is not None:
                return translate_error
            if python_code is None:
                return CompileResult(
                    success=False,
                    errors=["ast2python subprocess returned no Python code"],
                    ast_json=ast_json,
                    compile_meta=compile_meta,
                )

            return CompileResult(
                success=True,
                python_code=python_code,
                ast_json=ast_json,
                compile_meta=compile_meta,
            )

        except subprocess.TimeoutExpired:
            return CompileResult(
                success=False,
                errors=[f"Compile timed out after {self.timeout}s"],
            )
        except OSError as e:
            return CompileResult(
                success=False,
                errors=[f"Subprocess OSError: {e}"],
            )
        finally:
            # Cleanup temp files
            try:
                src_path.unlink(missing_ok=True)
            except OSError:
                pass
            try:
                ast_path.unlink(missing_ok=True)
            except NameError:
                pass
            except OSError:
                pass
