"""One compiler-result persistence path for CLI, Python API and HTTP."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from openpine.compile.native_rc6 import CompilerAdapter, CompileResult
from openpine.pine.source import PineSource


def persist_compile_result(
    source: PineSource,
    result: CompileResult,
    params_hash: str = "default",
    *,
    artifact_store: Any = None,
) -> dict:
    """Save successes and failures with original Pine and structured diagnostics.

    Failures have a separate identity including exact source, admitted dependencies,
    producer identities and diagnostics. A failed recompile never activates output.
    """
    from openpine.artifacts.store import ArtifactStore

    if result.success:
        if not result.python_code:
            raise RuntimeError("successful compile result did not include generated Python code")
        if result.generated_artifact is None:
            raise RuntimeError(
                "successful compile result did not include sealed generated artifact"
            )
        artifact_id = ArtifactStore.artifact_id_for_envelope(result.generated_artifact)
    else:
        identity = {
            "schema_id": "openpine.failed_compile_identity.v2",
            "source_id": source.id,
            "source": source.source_text,
            "params_hash": params_hash,
            "compile_meta": result.compile_meta,
            "errors": result.errors,
            "diagnostics": result.diagnostics,
        }
        encoded = json.dumps(
            identity, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        artifact_id = "err_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
    meta = {
        **result.compile_meta,
        "source_id": source.id,
        "source_name": source.name,
        "params_hash": params_hash,
        "artifact_id": artifact_id,
        "schema_version": "openpine.compile_meta.v1",
        "compile_status": "OK" if result.success else "FAILED",
        "errors": result.errors,
        "diagnostics": result.diagnostics,
        "created_at": int(time.time() * 1000),
    }
    store = artifact_store if artifact_store is not None else ArtifactStore()
    payloads = {}
    if result.success:
        payloads = {
            key: getattr(result, key)
            for key in (
                "source_map",
                "generated_artifact",
                "consumer_bundle",
                "frontend_artifact",
                "support_profile",
                "ast_artifact",
            )
        }
    artifact_path = store.save_artifact(
        artifact_id=artifact_id,
        source_id=source.id,
        params_hash=params_hash,
        python_code=result.python_code if result.success else None,
        compile_meta=meta,
        source_text=source.source_text,
        ast_json=result.ast_json,
        diagnostics="\n".join(result.errors),
        **payloads,
    )
    return {
        "artifact_id": artifact_id,
        "source_id": source.id,
        "params_hash": params_hash,
        "success": result.success,
        "errors": result.errors,
        "diagnostics": result.diagnostics,
        "compile_meta": meta,
        "artifact_path": str(artifact_path),
        "python_code": result.python_code if result.success else None,
        "generated_artifact": result.generated_artifact if result.success else None,
    }


def compile_pipeline(
    source: PineSource,
    adapter: CompilerAdapter,
    params_hash: str = "default",
    extra_options: dict | None = None,
    *,
    artifact_store: Any = None,
) -> dict:
    options = dict(extra_options or {})
    options.setdefault("source_name", source.source_path or f"{source.name}.pine")
    result = adapter.compile(source.source_text, **options)
    return persist_compile_result(source, result, params_hash, artifact_store=artifact_store)
