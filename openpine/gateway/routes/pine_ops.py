"""Pine operations routes — compile, validate, artifacts, inspect."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

import structlog
from openpine.artifacts.store import ArtifactStore
from openpine.gateway.deps import GatewayState, get_state
from openpine.gateway.ws_manager import ws_manager

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/pine", tags=["pine-operations"])


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _compile_native_rc6(source: object, *, producer_commits: dict[str, str]):
    from openpine.compile import NativeRC6CompilerAdapter

    name = str(getattr(source, "name"))
    source_path = getattr(source, "source_path", None)
    return NativeRC6CompilerAdapter().compile(
        str(getattr(source, "source_text")),
        module_name=name,
        source_name=str(source_path or f"{name}.pine"),
        producer_commits=producer_commits,
    )


def _validate_native_rc6(
    source: object, *, producer_commit: str
) -> dict[str, object]:
    from pine2ast.hardening.consumer_bundle import (
        build_consumer_bundle,
        verify_consumer_bundle,
    )

    name = str(getattr(source, "name"))
    source_text = str(getattr(source, "source_text"))
    source_path = getattr(source, "source_path", None)
    bundle = build_consumer_bundle(
        source_text,
        source_name=str(source_path or f"{name}.pine"),
        producer_commit=producer_commit,
    )
    verify_consumer_bundle(
        bundle,
        source=source_text,
        expected_producer_commit=producer_commit,
    )
    return bundle


def _artifact_dir_for_inspect(
    state: GatewayState,
    source_id: str,
    artifact_id: str,
    artifact: dict[str, object],
) -> Path:
    artifact_dir_fn = getattr(state.artifact_store, "_artifact_dir", None)
    try:
        if callable(artifact_dir_fn):
            artifact_dir = Path(str(artifact_dir_fn(source_id, artifact_id)))
        else:
            artifact_dir = Path(str(artifact["artifact_dir"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, f"Invalid artifact path: {artifact_id}") from exc

    root = getattr(state.artifact_store, "_root", None)
    if root is None:
        return artifact_dir
    root_resolved = Path(root).expanduser().resolve()
    artifact_resolved = artifact_dir.expanduser().resolve(strict=False)
    if not _path_is_under(artifact_resolved, root_resolved):
        log.warning(
            "unsafe_pine_artifact_inspect_path",
            source_id=source_id,
            artifact_id=artifact_id,
            path=str(artifact_dir),
            allowed_root=str(root_resolved),
        )
        raise HTTPException(400, f"Invalid artifact path: {artifact_id}")
    return artifact_dir


@router.post("/{source_id}/compile")
async def compile_pine(
    source_id: str,
    background_tasks: BackgroundTasks,
    state: GatewayState = Depends(get_state),
) -> dict[str, str]:
    """Compile a Pine source into an artifact (async with progress)."""
    from openpine.gateway.side_effects import persist_gateway_job, require_http_admit

    require_http_admit(state, "compile")
    try:
        src = state.pine_registry.get_source(source_id)
    except KeyError:
        raise HTTPException(404, f"Pine source not found: {source_id}")

    operation_id = f"compile_{source_id}_{int(__import__('time').time() * 1000)}"
    persist_gateway_job(
        state,
        job_id=operation_id,
        kind="compile",
        actor="gateway",
        input_artifact_refs=[source_id],
    )

    async def _run_compile():
        try:
            ws_manager.update_progress(
                operation_id, "compile", "running", 0.1, "Building RC6 consumer bundle..."
            )
            await ws_manager.broadcast_progress(operation_id)
            from openpine.build_identity import compiler_producer_commits
            producer_commits = compiler_producer_commits()
            compilation = _compile_native_rc6(
                src, producer_commits=producer_commits
            )
            if not compilation.success:
                ws_manager.update_progress(
                    operation_id,
                    "compile",
                    "failed",
                    0.6,
                    f"RC6 compile failed: {compilation.errors[:3]}",
                )
                await ws_manager.broadcast_progress(operation_id)
                return

            ws_manager.update_progress(
                operation_id, "compile", "running", 0.7, "Saving artifact..."
            )
            await ws_manager.broadcast_progress(operation_id)
            generated_artifact = compilation.generated_artifact
            consumer_bundle = compilation.consumer_bundle
            source_map = compilation.source_map
            if (
                generated_artifact is None
                or consumer_bundle is None
                or source_map is None
                or compilation.python_code is None
                or compilation.ast_json is None
            ):
                raise RuntimeError("successful RC6 compile returned incomplete artifacts")
            artifact_id = state.artifact_store.artifact_id_for_envelope(
                generated_artifact
            )
            compile_meta = {
                **compilation.compile_meta,
                "compile_status": "OK",
                "source_id": source_id,
                "artifact_id": artifact_id,
                "generated_artifact_hash": generated_artifact["content_hash"],
            }
            state.artifact_store.save_artifact(
                artifact_id=artifact_id,
                source_id=source_id,
                params_hash="",
                python_code=compilation.python_code,
                compile_meta=compile_meta,
                source_text=src.source_text,
                ast_json=compilation.ast_json,
                source_map=source_map,
                generated_artifact=generated_artifact,
                consumer_bundle=consumer_bundle,
                frontend_artifact=compilation.frontend_artifact,
                support_profile=compilation.support_profile,
                ast_artifact=compilation.ast_artifact,
            )
            state.pine_registry.set_active_artifact(source_id, artifact_id)

            ws_manager.update_progress(
                operation_id, "compile", "completed", 1.0, f"Compiled: {artifact_id}"
            )
            await ws_manager.broadcast_progress(operation_id)
            log.info("pine_compiled", source_id=source_id, artifact_id=artifact_id)

        except Exception as exc:
            log.error("compile_failed", source_id=source_id, error=str(exc))
            ws_manager.update_progress(operation_id, "compile", "failed", 0.0, str(exc))
            await ws_manager.broadcast_progress(operation_id)

    background_tasks.add_task(_run_compile)
    return {"operation_id": operation_id, "status": "queued", "source_id": source_id}


@router.post("/{source_id}/validate")
async def validate_pine(
    source_id: str,
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    """Validate a Pine source without compiling."""
    try:
        src = state.pine_registry.get_source(source_id)
    except KeyError:
        raise HTTPException(404, f"Pine source not found: {source_id}")

    try:
        from openpine.build_identity import compiler_producer_commits

        commits = compiler_producer_commits()
        bundle = _validate_native_rc6(
            src, producer_commit=commits["pine2ast"]
        )
        return {
            "source_id": source_id,
            "valid": True,
            "diagnostics": [],
            "consumer_bundle_hash": bundle["content_hash"],
            "release_axes": bundle["release_axes"],
        }
    except Exception as exc:
        return {"source_id": source_id, "valid": False, "error": str(exc)}


@router.get("/{source_id}/artifacts")
async def list_artifacts(
    source_id: str,
    state: GatewayState = Depends(get_state),
) -> list[dict[str, object]]:
    """List compiled artifacts for a Pine source."""
    try:
        state.pine_registry.get_source(source_id)
    except KeyError:
        raise HTTPException(404, f"Pine source not found: {source_id}")

    try:
        ArtifactStore._validate_path_component(source_id)
        artifacts_dir = state.artifact_store._root / source_id
        root = state.artifact_store._root.resolve()
        artifacts_dir.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid source id: {source_id}") from exc
    if not artifacts_dir.exists():
        return []

    results = []
    for artifact_dir in sorted(artifacts_dir.iterdir()):
        if not artifact_dir.is_dir():
            continue
        meta_path = artifact_dir / "compile_meta.json"
        if meta_path.exists():
            import json

            try:
                meta = json.loads(meta_path.read_text())
            except (OSError, ValueError) as exc:
                log.warning(
                    "pine_artifact_compile_meta_read_failed",
                    source_id=source_id,
                    artifact_id=artifact_dir.name,
                    error=str(exc),
                )
                continue
            results.append(
                {
                    "artifact_id": artifact_dir.name,
                    "compile_status": meta.get("compile_status", "unknown"),
                    "source_id": source_id,
                    "has_generated_strategy": (
                        artifact_dir / "generated_strategy.py"
                    ).exists(),
                    "unsafe": meta.get("unsafe", False),
                }
            )
    return results


@router.get("/{source_id}/artifacts/{artifact_id}")
async def inspect_artifact(
    source_id: str,
    artifact_id: str,
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    """Inspect a specific artifact."""
    try:
        artifact = state.artifact_store.get_artifact(artifact_id, source_id)
    except FileNotFoundError:
        raise HTTPException(404, f"Artifact not found: {artifact_id}")

    artifact_dir = _artifact_dir_for_inspect(state, source_id, artifact_id, artifact)

    result = {
        "artifact_id": artifact_id,
        "source_id": source_id,
        "compile_meta": artifact.get("compile_meta", {}),
    }

    # Read generated Python if exists
    py_path = artifact_dir / "generated_strategy.py"
    if py_path.exists():
        result["generated_python_lines"] = len(py_path.read_text().splitlines())

    # Read diagnostics if exists
    diag_path = artifact_dir / "diagnostics.log"
    if diag_path.exists():
        result["diagnostics"] = diag_path.read_text()[:2000]

    return result


@router.get("/compile/progress/{operation_id}")
async def compile_progress(operation_id: str) -> dict[str, object] | None:
    """Get compile operation progress."""
    return ws_manager.get_progress(operation_id)
