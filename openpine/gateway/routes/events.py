"""Event routes — history + live WebSocket stream."""

from __future__ import annotations

import json

from openpine._compat import structlog
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from openpine.gateway.deps import GatewayState, get_state
from openpine.gateway.schemas import EventResponse
from openpine.gateway.ws_manager import ws_manager

log = structlog.get_logger(__name__)
router = APIRouter(tags=["events"])


def _decode_event_payload(raw_payload: object, event_id: object) -> dict[str, object]:
    if not raw_payload:
        return {}
    try:
        payload = json.loads(str(raw_payload))
    except (TypeError, ValueError) as exc:
        log.warning("event_payload_decode_failed", event_id=event_id, error=str(exc))
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


@router.get("/events", response_model=list[EventResponse])
async def list_events(
    limit: int = Query(100, ge=1, le=1000),
    event_type: str | None = None,
    strategy_id: str | None = None,
    state: GatewayState = Depends(get_state),
) -> list[EventResponse]:
    """List persisted events from the EventBus."""
    # Detect schema version: old events table has payload_json + created_at,
    # new schema has payload + timestamp_ms.
    try:
        cols = {
            row[1]
            for row in state.storage.execute("PRAGMA table_info(events)").fetchall()
        }
    except Exception:
        return []

    has_new_schema = "timestamp_ms" in cols
    payload_col = "payload" if "payload" in cols else "payload_json"
    ts_col = "timestamp_ms" if has_new_schema else "created_at"

    where_clauses = []
    params: list[object] = []
    if event_type:
        where_clauses.append("event_type = ?")
        params.append(event_type)
    if strategy_id:
        where_clauses.append(f"json_extract({payload_col}, '$.strategy_id') = ?")
        params.append(strategy_id)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    sql = f"SELECT event_id, event_type, {payload_col}, {ts_col} FROM events {where_sql} ORDER BY {ts_col} DESC LIMIT ?"
    params.append(limit)

    rows = state.storage.execute(sql, tuple(params)).fetchall()
    return [
        EventResponse(
            event_id=r[0],
            event_type=r[1],
            timestamp_ms=r[3],
            payload=_decode_event_payload(r[2], r[0]),
        )
        for r in rows
    ]


@router.websocket("/ws/events")
async def websocket_events(ws: WebSocket) -> None:
    """WebSocket endpoint for real-time event and progress streaming.

    Clients receive:
    - {"type": "event", "data": {...}} — from EventBus
    - {"type": "progress", "data": {...}} — from operation progress tracking
    - {"type": "ping"} — keepalive
    """
    client_id = await ws_manager.connect(ws)
    try:
        while True:
            # Keep connection alive; clients can send commands here too
            try:
                msg = await ws.receive_text()
                # Client can send {"type": "subscribe", "filter": "..."}
                # For now, just acknowledge
                await ws.send_json({"type": "ack", "data": msg})
            except WebSocketDisconnect:
                break
    except Exception as exc:
        log.warning("ws_error", client_id=client_id, error=str(exc))
    finally:
        await ws_manager.disconnect(client_id)
