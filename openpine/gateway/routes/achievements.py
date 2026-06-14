"""Achievement routes — read-only snapshot of the catalog + progress.

The engine is the source of truth for derived stats and unlock state.
The route is intentionally thin: a single GET that powers the
``/achievements`` page in the UI, plus a POST event-sink for
non-SQL metrics (ruin_recovery, shipped_lib, secret_*).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field

from openpine._compat import structlog
from openpine.gateway.deps import GatewayState, get_state
from openpine.gateway.schemas import (
    AchievementItem,
    AchievementSummary,
    AchievementsResponse,
)

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/achievements", tags=["achievements"])
STATE_DEP = Depends(get_state)


class AchievementEventIn(BaseModel):
    """Payload for ``POST /achievements/events``.

    ``event_type`` must match a metric in the engine (e.g. 'ruin_recovery',
    'shipped_lib', 'secret_buy_zero', 'secret_nuclear'). ``source_id``
    is optional and used for distinct-counting (e.g. one event per
    shipped library version). ``value`` defaults to 1.0 (count)."""

    event_type: str = Field(min_length=1, max_length=64)
    source_id: str | None = Field(default=None, max_length=128)
    value: float = 1.0
    payload: dict[str, Any] | None = None


def _to_item(state_row) -> AchievementItem:
    target = state_row.target
    pct = 0.0 if target <= 0 else min(100.0, (state_row.current / target) * 100.0)
    return AchievementItem(
        id=state_row.id,
        tier=state_row.tier,
        icon=state_row.icon,
        title=state_row.title,
        description=state_row.description,
        metric=state_row.metric,
        target=target,
        current=state_row.current,
        reward=state_row.reward,
        hidden=state_row.hidden,
        unlocked=state_row.unlocked,
        unlocked_at=state_row.unlocked_at,
        progress_pct=pct,
    )


@router.get("", response_model=AchievementsResponse)
async def list_achievements(
    state: GatewayState = STATE_DEP,
    locale: str = Query(default="en", max_length=8),
    include_hidden: bool = Query(default=False),
) -> AchievementsResponse:
    """Catalog + per-achievement progress + unlock status.

    ``locale`` selects per-locale copy from the ``achievement_i18n``
    table. Unknown locales fall back to the canonical English copy
    baked into the achievements catalog.
    """
    engine = state.achievement_engine
    rows = engine.get_state(locale=locale, include_hidden_locked=include_hidden)
    summary = engine.summary()
    items = [_to_item(r) for r in rows]
    return AchievementsResponse(
        summary=AchievementSummary(
            total=summary["total"],
            unlocked=summary["unlocked"],
            by_tier=summary["by_tier"],
        ),
        items=items,
    )


@router.post("/refresh", response_model=AchievementsResponse)
async def refresh_achievements(
    state: GatewayState = STATE_DEP,
    locale: str = Query(default="en", max_length=8),
) -> AchievementsResponse:
    """Force a recompute + unlock check.

    Intended for ops/admin: the background tick normally handles this.
    The optional ``locale`` query string selects copy for the returned
    snapshot — the recompute itself is locale-agnostic.
    """
    result = state.achievement_engine.refresh()
    log.info("achievements_force_refresh", **result)
    return await list_achievements(state, locale=locale, include_hidden=False)


@router.post("/events")
async def record_event(
    event: AchievementEventIn = Body(...),
    state: GatewayState = STATE_DEP,
) -> dict[str, Any]:
    """Append a row to ``achievement_events`` and recompute that metric.

    Use this for achievements that don't have a natural SQL source
    (ruin_recovery, shipped_lib, secret_*). The engine treats
    ``event_type`` 1:1 as a metric name. Returns the list of
    achievement ids that crossed their target as a result of this
    event.
    """
    unlocked = state.achievement_engine.record_event(
        event_type=event.event_type,
        source_id=event.source_id,
        value=event.value,
        payload=event.payload,
    )
    return {
        "event_type": event.event_type,
        "source_id": event.source_id,
        "newly_unlocked": unlocked,
    }
