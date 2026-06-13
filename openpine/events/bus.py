"""Durable EventBus for OpenPine.

Section 7.9 + 18 + 33.1 + 33.6 of OpenPine TZ v3.

Rules:
- SQLiteEventBus is required for MVP (section 7.9).
- Events are persisted to SQLite before emit.
- CandleClosed emit ONLY after DataOrchestrator.on_candle_closed durable write succeeds (section 33.1).
- StrategyRuntimeError is a required durable event (section 33.6).
- Event handlers must be idempotent.
"""

from __future__ import annotations

import json
from openpine._compat import structlog
from collections import defaultdict
from typing import Any, Callable
from dataclasses import asdict, is_dataclass

from openpine.events.types import (
    CandleClosedEventPayload,
    Event,
    EventType,
    StrategyRuntimeErrorPayload,
)
from openpine.storage import SQLiteStorage
from openpine.storage.schema_compat import ensure_events_compat_schema, table_columns

log = structlog.get_logger(__name__)


def _event_payload_dict(value: Any) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if is_dataclass(value):
        return asdict(value)
    return dict(value)


class EventBus:
    """Section 7.9 + 33.1: durable event bus.

    Events are persisted to SQLite before emit.
    Raises if durable write fails (no emit without persistence).
    """

    _TABLE = "events"

    def __init__(self, storage: SQLiteStorage) -> None:
        """Initialize EventBus with SQLite storage.

        Args:
            storage: SQLiteStorage instance for event persistence.
        """
        self.storage = storage
        self._subscribers: dict[EventType, list[Callable[[Event], None]]] = defaultdict(
            list
        )
        self._setup_tables()

    def _setup_tables(self) -> None:
        """Create/normalize the durable events table."""

        ensure_events_compat_schema(self.storage)

    # -------------------------------------------------------------------------
    # Subscription management
    # -------------------------------------------------------------------------

    def subscribe(
        self,
        event_type: EventType,
        handler: Callable[[Event], None],
    ) -> None:
        """Register a handler for event_type.

        Args:
            event_type: Type of event to subscribe to.
            handler: Callable that receives the Event.
        """
        self._subscribers[event_type].append(handler)
        log.debug("eventbus.subscribe", event_type=event_type, handler=repr(handler))

    def unsubscribe(
        self,
        event_type: EventType,
        handler: Callable[[Event], None],
    ) -> None:
        """Remove a handler from event_type subscribers.

        Args:
            event_type: Type of event to unsubscribe from.
            handler: Handler to remove.
        """
        try:
            self._subscribers[event_type].remove(handler)
            log.debug("eventbus.unsubscribe", event_type=event_type)
        except ValueError:
            log.warning("eventbus.unsubscribe.not_found", event_type=event_type)

    def _notify_subscribers(self, event: Event) -> None:
        """Synchronously notify all subscribers of an event.

        Args:
            event: The event to dispatch.
        """
        handlers = list(self._subscribers.get(event.event_type, []))
        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:
                log.error(
                    "eventbus.handler_error", event_id=event.event_id, error=str(exc)
                )

    # -------------------------------------------------------------------------
    # Emit
    # -------------------------------------------------------------------------

    def _persist(self, event: Event) -> None:
        """Persist event to SQLite.

        Raises:
            RuntimeError: if the event is durable and write fails.
        """
        if not event.durable:
            return

        payload_json = json.dumps(event.payload)
        values: dict[str, object] = {
            "event_id": event.event_id,
            "event_type": event.event_type.value,
            "payload_json": payload_json,
            "payload": payload_json,
            "created_at": event.timestamp_ms,
            "timestamp_ms": event.timestamp_ms,
            "durable": 1,
            "status": "NEW",
        }
        columns = table_columns(self.storage, self._TABLE)
        insert_columns = [name for name in values if name in columns]
        placeholders = ", ".join("?" for _ in insert_columns)
        column_sql = ", ".join(insert_columns)
        self.storage.execute(
            f"INSERT INTO {self._TABLE} ({column_sql}) VALUES ({placeholders})",
            tuple(values[name] for name in insert_columns),
        )
        self.storage.commit()

    def emit(self, event: Event) -> None:
        """Persist event to DB then notify subscribers.

        Section 33.1: Raises if durable write fails (no emit without persistence).

        Args:
            event: Event to emit.

        Raises:
            RuntimeError: if durable write fails.
        """
        try:
            self._persist(event)
        except Exception as exc:
            log.error(
                "eventbus.persist_failed", event_id=event.event_id, error=str(exc)
            )
            raise RuntimeError(
                f"EventBus: failed to persist durable event: {exc}"
            ) from exc

        log.debug("eventbus.emit", event_id=event.event_id, event_type=event.event_type)
        self._notify_subscribers(event)

    def emit_candle_closed(
        self,
        bar: Any,
        instrument_key: Any,
        timeframe: Any,
    ) -> None:
        """Emit CandleClosed event after DataOrchestrator.on_candle_closed succeeds.

        Section 33.1: CandleClosed emit ONLY after on_candle_closed durable write.

        This is called by the caller (LiveDataFeedAdapter / LiveDataLoop) ONLY AFTER
        DataOrchestrator.on_candle_closed has successfully completed.

        Args:
            bar: The confirmed closed Bar.
            instrument_key: InstrumentKey instance.
            timeframe: Timeframe instance.
        """
        payload = CandleClosedEventPayload(
            instrument_key=_event_payload_dict(instrument_key),
            timeframe=_event_payload_dict(timeframe),
            bar_timestamp=int(
                bar.open_time_ms if hasattr(bar, "open_time_ms") else (bar.timestamp if hasattr(bar, "timestamp") else bar.time)
            ),
            bar=_event_payload_dict(bar),
        )
        event = Event.create(
            event_type=EventType.CANDLE_CLOSED,
            payload=payload.__dict__,
            durable=True,
        )
        self.emit(event)

    def emit_strategy_runtime_error(
        self,
        error: StrategyRuntimeErrorPayload,
    ) -> None:
        """Emit StrategyRuntimeError durable event.

        Section 33.6: required durable event.

        Args:
            error: StrategyRuntimeErrorPayload with all 11 required fields.
        """
        event = Event.create(
            event_type=EventType.STRATEGY_RUNTIME_ERROR,
            payload={
                "strategy_id": error.strategy_id,
                "artifact_id": error.artifact_id,
                "params_hash": error.params_hash,
                "instrument_key": error.instrument_key,
                "timeframe": error.timeframe,
                "bar_time": error.bar_time,
                "error_type": error.error_type,
                "message": error.message,
                "traceback_id": error.traceback_id,
                "job_id": error.job_id,
                "strategy_status_after": error.strategy_status_after,
            },
            durable=True,
        )
        self.emit(event)

    # -------------------------------------------------------------------------
    # Query
    # -------------------------------------------------------------------------

    def get_events(
        self,
        event_type: EventType | None = None,
        since_ms: int | None = None,
        limit: int = 100,
    ) -> list[Event]:
        """Query persisted events from SQLite.

        Args:
            event_type: Filter by event type (optional).
            since_ms: Filter events newer than this timestamp (optional).
            limit: Maximum number of events to return (default 100).

        Returns:
            List of Event objects matching the query.
        """
        conditions = []
        params: list[Any] = []

        if event_type is not None:
            conditions.append("event_type = ?")
            params.append(event_type.value)

        if since_ms is not None:
            conditions.append("timestamp_ms >= ?")
            params.append(since_ms)

        where = " AND ".join(conditions) if conditions else "1=1"
        columns = table_columns(self.storage, self._TABLE)
        payload_expr = "payload" if "payload" in columns else "payload_json"
        time_expr = "timestamp_ms" if "timestamp_ms" in columns else "created_at"
        durable_expr = "durable" if "durable" in columns else "1"
        sql = f"SELECT event_id, event_type, {payload_expr}, {time_expr}, {durable_expr} FROM {self._TABLE} WHERE {where} ORDER BY {time_expr} DESC LIMIT ?"
        params.append(limit)

        cursor = self.storage.execute(sql, tuple(params))
        rows = cursor.fetchall()

        events: list[Event] = []
        for row in rows:
            event_id, ev_type, payload_json, timestamp_ms, durable_flag = row
            events.append(
                Event(
                    event_id=event_id,
                    event_type=EventType(ev_type),
                    payload=json.loads(payload_json),
                    timestamp_ms=timestamp_ms,
                    durable=bool(durable_flag),
                )
            )
        return events
