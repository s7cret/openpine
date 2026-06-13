"""Compile pipeline — orchestrates Pine compilation via CompilerAdapter."""

from __future__ import annotations

import hashlib
import time

from openpine.compile.adapter import CompilerAdapter, CompileProfile
from openpine.pine.source import PineSource


def compile_pipeline(
    source: PineSource,
    adapter: CompilerAdapter,
    params_hash: str = "default",
    extra_options: dict | None = None,
) -> dict:
    """Compile a PineSource via the given adapter and save to ArtifactStore.

    Args:
        source: PineSource to compile.
        adapter: CompilerAdapter instance (e.g. SubprocessCompilerAdapter).
        params_hash: Parameter hash for this artifact.
        extra_options: Extra compile options passed to the adapter.

    Returns:
        dict with keys: artifact_id, source_id, params_hash, success,
        compile_meta, artifact_path.
    """
    from openpine.artifacts.store import ArtifactStore

    extra_options = {"profile": CompileProfile.production(), **(extra_options or {})}

    result = adapter.compile(source.source_text, **extra_options)

    # Derive artifact_id from source hash + params_hash + compile_meta
    compile_key = (
        f"{source.id}"
        f"|{params_hash}"
        f"|{result.compile_meta.get('pine2ast_version', '?')}"
        f"|{result.compile_meta.get('ast2python_version', '?')}"
    )
    art_hash = hashlib.sha256(compile_key.encode()).hexdigest()[:16]
    artifact_id = f"art_{art_hash}"

    compile_meta = {
        **result.compile_meta,
        "source_id": source.id,
        "source_name": source.name,
        "params_hash": params_hash,
        "artifact_id": artifact_id,
        "schema_version": "openpine.compile_meta.v1",
        "compile_status": "OK" if result.success else "FAILED",
        "errors": result.errors,
        "created_at": int(time.time() * 1000),
    }

    if result.success:
        if not result.python_code:
            raise RuntimeError(
                "successful compile result did not include generated Python code"
            )
        store = ArtifactStore()
        artifact_path = store.save_artifact(
            artifact_id=artifact_id,
            source_id=source.id,
            params_hash=params_hash,
            python_code=result.python_code,
            compile_meta=compile_meta,
            source_text=source.source_text,
            ast_json=result.ast_json,
            diagnostics="",
        )
    else:
        # Save failed artifact for diagnostics
        store = ArtifactStore()
        compile_meta["compile_status"] = "FAILED"
        artifact_path = store.save_artifact(
            artifact_id=artifact_id,
            source_id=source.id,
            params_hash=params_hash,
            python_code=None,
            compile_meta=compile_meta,
            source_text=source.source_text,
            ast_json=result.ast_json,
            diagnostics="\n".join(result.errors),
        )

    return {
        "artifact_id": artifact_id,
        "source_id": source.id,
        "params_hash": params_hash,
        "success": result.success,
        "errors": result.errors,
        "compile_meta": compile_meta,
        "artifact_path": str(artifact_path),
        "python_code": result.python_code if result.success else None,
    }
