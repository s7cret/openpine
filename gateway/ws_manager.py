"""WebSocket manager for real-time event and progress broadcasting."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import structlog
from fastapi import WebSocket

log = structlog.get_logger(__name__)


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts."""

    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}
        self._lock = asyncio.Lock()
        self._progress: dict[str, dict[str, Any]] = {}

    async def connect(self, ws: WebSocket, client_id: str | None = None) -> str:
        """Accept a WebSocket connection and return its id."""
        await ws.accept()
        cid = client_id or f"ws_{uuid.uuid4().hex[:12]}"
        async with self._lock:
            self._connections[cid] = ws
        log.info("ws_connected", client_id=cid)
        return cid

    async def disconnect(self, client_id: str) -> None:
        """Remove a disconnected client."""
        async with self._lock:
            self._connections.pop(client_id, None)
        log.info("ws_disconnected", client_id=client_id)

    async def send_personal(self, client_id: str, data: dict[str, Any]) -> None:
        """Send to a specific client."""
        ws = self._connections.get(client_id)
        if ws is None:
            return
        try:
            await ws.send_json(data)
        except Exception:
            await self.disconnect(client_id)

    async def broadcast(self, data: dict[str, Any]) -> None:
        """Broadcast to all connected clients."""
        payload = json.dumps(data, default=str)
        disconnected: list[str] = []
        async with self._lock:
            for cid, ws in list(self._connections.items()):
                try:
                    await ws.send_text(payload)
                except Exception:
                    disconnected.append(cid)
        for cid in disconnected:
            await self.disconnect(cid)

    @property
    def active_count(self) -> int:
        return len(self._connections)

    # ── Progress tracking ──────────────────────────────────────────────────

    def update_progress(
        self,
        operation_id: str,
        operation_type: str,
        status: str,
        pct: float = 0.0,
        message: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Store latest progress for an operation."""
        self._progress[operation_id] = {
            "operation_id": operation_id,
            "operation_type": operation_type,
            "status": status,
            "pct": pct,
            "message": message,
            "detail": detail or {},
            "updated_at": int(time.time() * 1000),
        }

    def get_progress(self, operation_id: str) -> dict[str, Any] | None:
        """Get latest progress for an operation."""
        return self._progress.get(operation_id)

    def get_all_progress(self) -> list[dict[str, Any]]:
        """Get all active progress updates."""
        return list(self._progress.values())

    def clear_progress(self, operation_id: str) -> None:
        """Remove a completed progress entry."""
        self._progress.pop(operation_id, None)

    async def broadcast_progress(self, operation_id: str) -> None:
        """Broadcast a progress update to all connected clients."""
        progress = self._progress.get(operation_id)
        if progress is not None:
            await self.broadcast({"type": "progress", "data": progress})


# Singleton — imported by routes
ws_manager = ConnectionManager()
