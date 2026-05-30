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
from typing import Any, Callable, Literal, Protocol

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
        newline = line[len(body):]
        match = _PINE_V5_DIRECTIVE_RE.match(body)
        if match:
            lines[idx] = f"{match.group(1)}6{match.group(2)}{newline}"
            return "".join(lines), True
    return source_text, False


def _is_pine_v5_version_rejection(messages: list[str]) -> bool:
    """Return True only for the known pine2ast v5-version rejection."""
    combined = "\n".join(str(message) for message in messages if message)
    lowered = combined.lower()
    has_v5_marker = (
        "p2a0103" in lowered
        or ("unsupported pine version" in lowered and "5" in lowered)
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
        except Exception as exc:  # pragma: no cover - exact import failures are environment-specific
            errors.append(f"{name} unavailable: {exc}")

    try:
        pine2ast_api = importlib.import_module("pine2ast.api")
    except Exception as exc:  # pragma: no cover - exact import failures are environment-specific
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
        "pinelib_version": _version_from_module(pinelib, "PACKAGE_VERSION", "__version__"),
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


def _profile_from_kwargs(kwargs: dict[str, Any]) -> CompileProfile:
    raw = kwargs.get("profile")
    if isinstance(raw, CompileProfile):
        profile = raw
    elif raw == "diagnostic":
        unsafe = True
        profile = CompileProfile(
            name="diagnostic",
            allow_external_library_stubs=unsafe,
            allow_unsupported_request_stubs=unsafe,
            allow_invalid_ast=unsafe,
            allow_subprocess_fallback=unsafe,
            allow_implicit_version_rewrite=unsafe,
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
        raise ValueError("production CompileProfile cannot enable unsafe compile allowances")
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
            parse_result = apis.parse_code(source_text, options)
            ast = getattr(parse_result, "ast", None)
            diagnostics = list(getattr(parse_result, "diagnostics", []) or [])
            ok = bool(getattr(parse_result, "ok", False))
            if ast is None or not ok:
                parse_errors = [
                    _diagnostic_message(diagnostic) for diagnostic in diagnostics
                ] or ["pine2ast returned no AST"]
                normalized_source, normalized = _normalize_pine_v5_directive(source_text)
                if normalized and _is_pine_v5_version_rejection(parse_errors):
                    if not profile.allow_implicit_version_rewrite:
                        return CompileResult(
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
                        return CompileResult(
                            success=False,
                            errors=retry_errors,
                            compile_meta=compile_meta,
                        )

                if ast is None or not ok:
                    return CompileResult(
                        success=False,
                        errors=parse_errors,
                        compile_meta=compile_meta,
                    )

            if ast is None or not ok:
                return CompileResult(
                    success=False,
                    errors=[_diagnostic_message(diagnostic) for diagnostic in diagnostics]
                    or ["pine2ast returned no AST"],
                    compile_meta=compile_meta,
                )

            ast_json = apis.ast_to_json(ast)
            ast_payload = json.loads(ast_json)
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
                    "allow_unsupported_request_stubs", profile.allow_unsupported_request_stubs
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
        pine2ast_path = _find_tool("pine2ast")
        ast2python_path = _find_tool("ast2python")

        errors: list[str] = []
        if pine2ast_path is None:
            errors.append("pine2ast not found in PATH or ~/.local/bin")
        if ast2python_path is None:
            errors.append("ast2python not found in PATH or ~/.local/bin")

        if errors:
            return CompileResult(success=False, errors=errors)

        # Write source to temp file for pine2ast
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".pine", delete=False
            ) as src_f:
                src_f.write(source_text)
                src_path = Path(src_f.name)
        except OSError as e:
            return CompileResult(success=False, errors=[f"Failed to write temp source: {e}"])

        try:
            module_name = kwargs.get("module_name", "generated_strategy")
            strict = kwargs.get("strict", False)
            compile_meta = {
                "pine2ast_version": "unknown",
                "ast2python_version": "unknown",
                "pinelib_contract_version": "unknown",
                "adapter": "subprocess",
                "adapter_status": "fallback" if self.prefer_library else "selected",
                "module_name": module_name,
                "strict": strict,
                "compile_profile": profile.name,
                "tool_paths": {
                    "pine2ast": str(pine2ast_path),
                    "ast2python": str(ast2python_path),
                },
            }

            # Step 1: pine2ast parse
            result_p2a = subprocess.run(
                [str(pine2ast_path), "parse", str(src_path)],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            if result_p2a.returncode != 0:
                parse_errors = [
                    f"pine2ast failed (exit {result_p2a.returncode})",
                    result_p2a.stderr or result_p2a.stdout,
                ]
                normalized_source, normalized = _normalize_pine_v5_directive(source_text)
                if normalized and _is_pine_v5_version_rejection(parse_errors):
                    if not profile.allow_implicit_version_rewrite:
                        return CompileResult(
                            success=False,
                            errors=parse_errors,
                            compile_meta=compile_meta,
                        )
                    src_path.write_text(normalized_source)
                    result_p2a = subprocess.run(
                        [str(pine2ast_path), "parse", str(src_path)],
                        capture_output=True,
                        text=True,
                        timeout=self.timeout,
                    )
                    warnings = list(compile_meta.get("warnings", []))
                    warnings.append(_PINE_V5_FALLBACK_WARNING)
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
                        return CompileResult(
                            success=False,
                            errors=[
                                f"pine2ast failed (exit {result_p2a.returncode})",
                                result_p2a.stderr or result_p2a.stdout,
                            ],
                            compile_meta=compile_meta,
                        )
                else:
                    return CompileResult(
                        success=False,
                        errors=parse_errors,
                    )

            if result_p2a.returncode != 0:
                return CompileResult(
                    success=False,
                    errors=[
                        f"pine2ast failed (exit {result_p2a.returncode})",
                        result_p2a.stderr or result_p2a.stdout,
                    ],
                )

            ast_json = result_p2a.stdout

            # Parse AST to verify it's valid JSON
            try:
                json.loads(ast_json)
            except json.JSONDecodeError as e:
                return CompileResult(
                    success=False,
                    errors=[f"pine2ast produced invalid JSON: {e}"],
                )

            # Step 2: ast2python translate
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as ast_f:
                ast_f.write(ast_json)
                ast_path = Path(ast_f.name)

            cmd = [
                str(ast2python_path), "translate",
                str(ast_path),
                "-o", "/dev/stdout",
                "--module-name", module_name,
            ]
            if strict:
                cmd.append("--strict")

            result_a2p = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            if result_a2p.returncode != 0:
                return CompileResult(
                    success=False,
                    errors=[
                        f"ast2python failed (exit {result_a2p.returncode})",
                        result_a2p.stderr or result_a2p.stdout,
                    ],
                    ast_json=ast_json,
                )

            python_code = result_a2p.stdout

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
