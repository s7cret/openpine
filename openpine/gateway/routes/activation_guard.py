"""Shared fail-closed guard for strategy activation routes."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException

from openpine.gateway.deps import GatewayState
from openpine.gateway.worker_supervisor import worker_accepts_strategy_activation

_FALLBACK_ACTIVATION_LOCK = threading.RLock()


def require_worker_ready(state: GatewayState) -> None:
    allowed, status = worker_accepts_strategy_activation(state)
    if not allowed:
        raise HTTPException(
            503,
            f"Background worker is not ready: {status.get('reason') or 'unavailable'}",
        )


@contextmanager
def guarded_strategy_activation(state: GatewayState) -> Iterator[None]:
    """Serialize the worker guard and the caller's registry mutation."""

    lock = getattr(state, "strategy_activation_lock", _FALLBACK_ACTIVATION_LOCK)
    with lock:
        require_worker_ready(state)
        yield
