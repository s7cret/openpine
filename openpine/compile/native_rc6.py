"""Native RC6 Pine2AST consumer-bundle to Ast2Python V3 compilation."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from ast2python.api import compile_consumer_bundle
from ast2python.errors import BundleInvariantError
from ast2python.lowering import load_pinelib_target_manifest
from openpine_contracts import SchemaValidationError, validate_payload
from pine2ast.hardening.consumer_bundle import ConsumerBundleError, build_consumer_bundle
from .source_context import PreparedSource, prepare_source, projected_source_map
from .library_inputs import requested_lock_hash
from .diagnostics import exception_diagnostics, frontend_diagnostics, format_diagnostic


_SHA40 = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class LibraryAvailability:
    """Availability and identity of the native local RC6 compiler stack."""

    available: bool
    errors: list[str] = field(default_factory=list)
    versions: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CompileResult:
    """Result returned to the OpenPine compile pipeline."""

    success: bool
    python_code: str | None = None
    errors: list[str] = field(default_factory=list)
    compile_meta: dict[str, Any] = field(default_factory=dict)
    ast_json: str | None = None
    generated_artifact: dict[str, Any] | None = None
    source_map: dict[str, Any] | None = None
    consumer_bundle: dict[str, Any] | None = None
    frontend_artifact: dict[str, Any] | None = None
    support_profile: dict[str, Any] | None = None
    ast_artifact: dict[str, Any] | None = None
    diagnostics: list[dict[str, Any]] = field(default_factory=list)


class CompilerAdapter(Protocol):
    def compile(self, source_text: str, **kwargs: Any) -> CompileResult: ...


def _producer_commits(value: object) -> tuple[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    pine2ast_commit = value.get("pine2ast")
    ast2python_commit = value.get("ast2python")
    if (
        not isinstance(pine2ast_commit, str)
        or _SHA40.fullmatch(pine2ast_commit) is None
        or not isinstance(ast2python_commit, str)
        or _SHA40.fullmatch(ast2python_commit) is None
    ):
        return None
    return pine2ast_commit, ast2python_commit


@dataclass
class NativeRC6CompilerAdapter:
    """Compile source through the exact native RC6 producer and target contracts."""

    def library_status(self) -> LibraryAvailability:
        try:
            import ast2python
            import pine2ast
            import pinelib

            return LibraryAvailability(
                available=True,
                versions={
                    "pine2ast_version": str(pine2ast.__version__),
                    "ast2python_version": str(ast2python.__version__),
                    "pinelib_version": str(pinelib.__version__),
                },
            )
        except (AttributeError, ImportError) as exc:
            return LibraryAvailability(available=False, errors=[str(exc)])

    def compile(self, source_text: str, **kwargs: Any) -> CompileResult:
        commits = _producer_commits(kwargs.get("producer_commits"))
        if commits is None:
            return CompileResult(
                success=False,
                errors=[
                    "producer_commits must contain exact pine2ast and ast2python "
                    "40-character Git SHAs"
                ],
                compile_meta={"adapter": "native-rc6-python-library"},
            )
        pine2ast_commit, ast2python_commit = commits
        module_name = str(kwargs.get("module_name", "generated_strategy"))
        source_name = str(kwargs.get("source_name", "<memory>"))
        compile_meta: dict[str, Any] = {
            "adapter": "native-rc6-python-library",
            "module_name": module_name,
            "artifact_schema_id": "openpine.generated_artifact.v3",
            "producer_commits": {
                "pine2ast": pine2ast_commit,
                "ast2python": ast2python_commit,
            },
        }
        compile_meta.update(self.library_status().versions)

        requested = requested_lock_hash(library_store=kwargs.get("library_store"), library_payload=kwargs.get("library_payload"))
        if requested is not None:
            compile_meta["requested_library_lock_hash"] = requested
        bundle = None
        prepared = PreparedSource(source_text, source_name)
        phase = "library_admission"
        compile_meta.update(prepared.metadata())
        try:
            prepared = prepare_source(source_text, source_name,
                library_store=kwargs.get("library_store"), library_payload=kwargs.get("library_payload"))
            compile_meta.update(prepared.metadata())
            linked_source = prepared.linked
            phase = "frontend"
            bundle = build_consumer_bundle(
                prepared.code, source_name=source_name, producer_commit=pine2ast_commit,
                require_clean_frontend=True, linked_source=linked_source,
            )
            diagnostics = frontend_diagnostics(bundle.get("diagnostics", []), prepared)
            phase = "compiler"
            target = load_pinelib_target_manifest()
            compiled = compile_consumer_bundle(
                bundle,
                target=target,
                module_name=module_name,
                producer_commit=ast2python_commit,
                expected_pine2ast_commit=pine2ast_commit,
                **({"linked_source": linked_source} if linked_source is not None else {}),
            )
            from openpine.verification.capabilities import (
                effective_target_identity, require_plan_bindings,
            )
            require_plan_bindings(compiled.plan, target)
            compile_meta["effective_target"] = effective_target_identity(
                compiled.plan.pine_version, target.content_hash,
                str(kwargs.get("execution_mode", "interactive")),
            )
            generated_artifact = compiled.artifact.to_dict()
            validate_payload("openpine.generated_artifact.v3", generated_artifact)
            source_map = compiled.emitted.source_map.to_dict()
            from openpine.runtime.strategy_host import validate_strategy_host
            phase = "host_admission"
            host_evidence = validate_strategy_host(compiled.emitted.code, generated_artifact["version_context"]["pine_version"])
            linked = bundle.get("linked_artifacts")
            linked_artifacts = linked if isinstance(linked, Mapping) else {}
            compile_meta.update(
                {
                    "strategy_host": host_evidence,
                    "bundle_hash": bundle["content_hash"],
                    "target_manifest_hash": target.content_hash,
                    "normalized_target_schema": target.schema_id,
                    "argument_qualifier_admission": "producer_and_exact_target",
                    "lowering_plan_hash": compiled.plan.content_hash,
                    "emitted_module_hash": compiled.emitted.code_hash,
                    "source_map_hash": compiled.emitted.source_map.content_hash,
                    "input_descriptors": list(compiled.emitted.script_metadata.get("inputs", {}).values()),
                }
            )
            projection = projected_source_map(source_map, prepared)
            if projection is not None:
                compile_meta["original_source_projection"] = projection
            return CompileResult(
                success=True,
                diagnostics=diagnostics,
                python_code=compiled.emitted.code,
                compile_meta=compile_meta,
                ast_json=json.dumps(bundle["ast"], ensure_ascii=False, sort_keys=True),
                generated_artifact=generated_artifact,
                source_map=source_map,
                consumer_bundle=dict(bundle),
                frontend_artifact=_dict_or_none(linked_artifacts.get("frontend_artifact")),
                support_profile=_dict_or_none(linked_artifacts.get("support_profile")),
                ast_artifact=_dict_or_none(linked_artifacts.get("ast_artifact")),
            )
        except (
            BundleInvariantError,
            ConsumerBundleError,
            ImportError,
            OSError,
            SchemaValidationError,
            TypeError,
            ValueError,
        ) as exc:
            diagnostics = exception_diagnostics(exc, prepared, phase=phase, bundle=bundle)
            return CompileResult(
                success=False, errors=[format_diagnostic(d) for d in diagnostics],
                compile_meta=compile_meta, diagnostics=diagnostics,
            )

    def validate(self, source_text: str, **kwargs: Any) -> CompileResult:
        """Use the same explicit inputs and clean producer gate without emitting code."""
        commits = _producer_commits(kwargs.get("producer_commits"))
        if commits is None:
            return CompileResult(success=False, errors=["exact producer_commits required"])
        name = str(kwargs.get("source_name", "<memory>"))
        prepared = PreparedSource(source_text, name)
        meta = {"adapter": "native-rc6-python-library", "producer_commits": dict(zip(("pine2ast", "ast2python"), commits)),
                **prepared.metadata()}
        requested = requested_lock_hash(library_store=kwargs.get("library_store"), library_payload=kwargs.get("library_payload"))
        if requested is not None:
            meta["requested_library_lock_hash"] = requested
        phase = "library_admission"
        try:
            prepared = prepare_source(source_text, name, library_store=kwargs.get("library_store"),
                                      library_payload=kwargs.get("library_payload"))
            meta.update(prepared.metadata())
            phase = "frontend"
            bundle = build_consumer_bundle(prepared.code, source_name=name, producer_commit=commits[0],
                                           require_clean_frontend=True, linked_source=prepared.linked)
            return CompileResult(success=True, consumer_bundle=bundle, compile_meta=meta,
                                 diagnostics=frontend_diagnostics(bundle.get("diagnostics", []), prepared))
        except (ConsumerBundleError, ImportError, OSError, TypeError, ValueError) as exc:
            diagnostics = exception_diagnostics(exc, prepared, phase=phase)
            return CompileResult(success=False, errors=[format_diagnostic(d) for d in diagnostics],
                                 compile_meta=meta, diagnostics=diagnostics)


def _dict_or_none(value: object) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, Mapping) else None


__all__ = [
    "CompileResult",
    "CompilerAdapter",
    "LibraryAvailability",
    "NativeRC6CompilerAdapter",
]
