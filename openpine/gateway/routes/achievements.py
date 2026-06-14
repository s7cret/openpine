"""Achievement routes — read-only snapshot of the catalog + progress.

The engine is the source of truth for derived stats and unlock state.
The route is intentionally thin: a single GET that powers the
``/achievements`` page in the UI.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

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
    include_hidden: bool = Query(default=False),
) -> AchievementsResponse:
    """Catalog + per-achievement progress + unlock status."""
    engine = state.achievement_engine
    rows = engine.get_state(include_hidden_locked=include_hidden)
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
) -> AchievementsResponse:
    """Force a recompute + unlock check.

    Intended for ops/admin: the background tick normally handles this.
    """
    result = state.achievement_engine.refresh()
    log.info("achievements_force_refresh", **result)
    return await list_achievements(state)
