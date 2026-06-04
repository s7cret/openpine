"""Pine source routes — CRUD for .pine files.

Pine sources are raw scripts without timeframe/symbol assignment.
They get compiled into artifacts, which then get attached to strategies.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException

from openpine.gateway.deps import GatewayState, get_pine_registry, get_state
from openpine.gateway.schemas import (
    PineSourceCreate,
    PineSourceDetailResponse,
    PineSourceResponse,
    PineSourceUpdate,
)
from openpine.pine.registry import SQLitePineSourceRegistry

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/pine-sources", tags=["pine-sources"])


@router.get("", response_model=list[PineSourceResponse])
async def list_sources(
    registry: SQLitePineSourceRegistry = Depends(get_pine_registry),
) -> list[PineSourceResponse]:
    """List all Pine sources (without source_text for brevity)."""
    return [
        PineSourceResponse(
            id=src.id,
            name=src.name,
            source_type=src.source_type,
            version=src.version,
            active_artifact_id=src.active_artifact_id,
            created_at=src.created_at,
            updated_at=src.updated_at,
        )
        for src in registry.list_sources()
    ]


@router.post("", response_model=PineSourceDetailResponse, status_code=201)
async def create_source(
    body: PineSourceCreate,
    registry: SQLitePineSourceRegistry = Depends(get_pine_registry),
) -> PineSourceDetailResponse:
    """Upload a new Pine source."""
    # Check name uniqueness
    try:
        existing = registry.get_source(body.name)
        if existing:
            raise HTTPException(409, f"Pine source with name '{body.name}' already exists")
    except KeyError:
        pass

    src = registry.add_source(body.source_text, body.name)
    src.source_type = body.source_type
    log.info("pine_source_created", source_id=src.id, name=src.name)
    return PineSourceDetailResponse(
        id=src.id,
        name=src.name,
        source_type=src.source_type,
        version=src.version,
        source_text=src.source_text,
        active_artifact_id=src.active_artifact_id,
        created_at=src.created_at,
        updated_at=src.updated_at,
    )


@router.get("/{source_id}", response_model=PineSourceDetailResponse)
async def get_source(
    source_id: str,
    registry: SQLitePineSourceRegistry = Depends(get_pine_registry),
) -> PineSourceDetailResponse:
    """Get a Pine source by id or name."""
    try:
        src = registry.get_source(source_id)
    except KeyError:
        raise HTTPException(404, f"Pine source not found: {source_id}")
    return PineSourceDetailResponse(
        id=src.id,
        name=src.name,
        source_type=src.source_type,
        version=src.version,
        source_text=src.source_text,
        active_artifact_id=src.active_artifact_id,
        created_at=src.created_at,
        updated_at=src.updated_at,
    )


@router.patch("/{source_id}", response_model=PineSourceDetailResponse)
async def update_source(
    source_id: str,
    body: PineSourceUpdate,
    registry: SQLitePineSourceRegistry = Depends(get_pine_registry),
) -> PineSourceDetailResponse:
    """Update a Pine source (name, source_text, source_type)."""
    try:
        src = registry.get_source(source_id)
    except KeyError:
        raise HTTPException(404, f"Pine source not found: {source_id}")

    if body.source_text is not None:
        src.source_text = body.source_text
        import hashlib
        src.source_hash = hashlib.sha256(body.source_text.encode()).hexdigest()
    if body.name is not None:
        src.name = body.name
    if body.source_type is not None:
        src.source_type = body.source_type

    src.updated_at = int(__import__("time").time() * 1000)
    # Persist via registry internals
    registry._conn.execute(
        "UPDATE pine_sources SET name=?, source_text=?, source_hash=?, source_type=?, updated_at=? WHERE id=?",
        (src.name, src.source_text, src.source_hash, src.source_type, src.updated_at, src.id),
    )
    registry._conn.commit()
    registry._mem[src.id] = src

    return PineSourceDetailResponse(
        id=src.id,
        name=src.name,
        source_type=src.source_type,
        version=src.version,
        source_text=src.source_text,
        active_artifact_id=src.active_artifact_id,
        created_at=src.created_at,
        updated_at=src.updated_at,
    )


@router.delete("/{source_id}", status_code=204)
async def delete_source(
    source_id: str,
    state: GatewayState = Depends(get_state),
) -> None:
    """Delete a Pine source."""
    try:
        source = state.pine_registry.get_source(source_id)
    except KeyError:
        raise HTTPException(404, f"Pine source not found: {source_id}")
    source_id = source.id
    state.pine_registry.remove_source(source_id)
    try:
        state.storage.execute("DELETE FROM compile_artifacts WHERE pine_id = ?", (source_id,))
        state.storage.execute("DELETE FROM pine_artifacts WHERE source_id = ?", (source_id,))
        state.storage.commit()
    except Exception as exc:
        log.warning("pine_source_artifact_rows_cleanup_failed", source_id=source_id, error=str(exc))
    try:
        import shutil

        source_dir = state.artifact_store._source_dir(source_id)
        if source_dir.exists():
            shutil.rmtree(source_dir, ignore_errors=True)
    except Exception as exc:
        log.warning("pine_source_artifact_dir_cleanup_failed", source_id=source_id, error=str(exc))
    log.info("pine_source_deleted", source_id=source_id)
