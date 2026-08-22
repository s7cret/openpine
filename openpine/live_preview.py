"""Immutable live-start preview. Start is never implied from a GET."""

from __future__ import annotations

import hashlib
import json
from typing import Any

CONFIRMATION_LIVE = "LIVE"
PREVIEW_TTL_MS = 60_000


class LiveConfirmError(ValueError):
    """Typed confirmation / preview failure."""


def preview_hash(*, strategy_id: str, expires_at_utc_ms: int, stack_id: str) -> str:
    payload = json.dumps(
        {
            "expires_at_utc_ms": expires_at_utc_ms,
            "mode": "LIVE",
            "stack_id": stack_id,
            "strategy_id": strategy_id,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def make_live_preview(
    strategy_id: str, *, now_ms: int, stack_id: str
) -> dict[str, Any]:
    if not strategy_id.strip():
        raise LiveConfirmError("strategy_id required")
    expires = now_ms + PREVIEW_TTL_MS
    digest = preview_hash(
        strategy_id=strategy_id, expires_at_utc_ms=expires, stack_id=stack_id
    )
    return {
        "strategy_id": strategy_id,
        "mode": "LIVE",
        "stack_id": stack_id,
        "expires_at_utc_ms": expires,
        "preview_hash": digest,
        "confirmation_required": CONFIRMATION_LIVE,
        "mutating": False,
    }


def require_live_confirmation(
    *,
    strategy_id: str,
    preview_hash_value: str,
    confirmation: str,
    expires_at_utc_ms: int | None,
    now_ms: int,
    stack_id: str,
) -> None:
    if confirmation != CONFIRMATION_LIVE:
        raise LiveConfirmError("typed confirmation LIVE required")
    if not preview_hash_value:
        raise LiveConfirmError("preview_hash required")
    if expires_at_utc_ms is None:
        raise LiveConfirmError("preview expiry required")
    if now_ms >= int(expires_at_utc_ms):
        raise LiveConfirmError("preview expired")
    expected = preview_hash(
        strategy_id=strategy_id,
        expires_at_utc_ms=int(expires_at_utc_ms),
        stack_id=stack_id,
    )
    if preview_hash_value != expected:
        raise LiveConfirmError("preview_hash mismatch")
