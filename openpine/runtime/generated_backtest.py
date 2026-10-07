"""Concrete generated-script bytes bridge over existing broker and checkpoint owners."""

from __future__ import annotations

from copy import copy
from decimal import Decimal
from typing import Any, Callable, TYPE_CHECKING

from backtest_engine import BacktestEngine
from backtest_engine.core.engine_validation import validate_backtest_config
from backtest_engine.core.realtime import BarTickSlice, cumulative_tick_bar
from backtest_engine.core.state_snapshot import BrokerSnapshot
from backtest_engine.core.strategy_capabilities import strategy_values_from_state
from backtest_engine.errors import ResumeUnsupportedError
from backtest_engine.execution_backends.base import PreparedNativeExecution
from backtest_engine.models import BacktestCallbacks, BacktestResumeState, Bar, BarSeries
from backtest_engine.results import BacktestResult
from openpine_contracts import ExecutionEvent, decimal_string
from pinelib.runtime.metadata import BarValues

from openpine.runtime.generated_checkpoint import (
    PreparedGeneratedCheckpoint,
    generated_owner_config,
)

if TYPE_CHECKING:
    from openpine.runtime.rc6_worker_runtime import RC6GeneratedScriptSession


def _primary_values(bar: Bar) -> BarValues:
    """Use the same broker-to-runtime normalization for execution and admission."""
    return BarValues(
        open=float(bar.open),
        high=float(bar.high),
        low=float(bar.low),
        close=float(bar.close),
        volume=float(bar.volume or 0),
        time=int(bar.time),
        time_close=int(bar.time_close or bar.time),
    )


def _validate_primary_prefix(
    prepared: PreparedGeneratedCheckpoint,
    series: BarSeries,
    cursor: int,
    tick_schedule: tuple[BarTickSlice, ...] | None = None,
) -> None:
    """Bind the admitted owner's primary history to the independent broker input."""
    names = ("open", "high", "low", "close", "volume", "time", "time_close")
    histories = {}
    for name in names:
        storage = prepared.runtime.series.get(name)
        if storage is None or len(storage.committed) != cursor + 1:
            raise ResumeUnsupportedError(
                "generated primary history differs from broker prefix: " + name
            )
        histories[name] = storage.committed
    for index in range(cursor + 1):
        parent = series.get_bar(index)
        expected = _primary_values(
            parent
            if tick_schedule is None
            else cumulative_tick_bar(parent, tick_schedule[index].ticks)
        )
        for name in names:
            value = histories[name][index]
            kinds = (int,) if name in ("time", "time_close") else (int, float)
            if type(value) not in kinds or value != getattr(expected, name):
                raise ResumeUnsupportedError(
                    "generated primary history differs from broker prefix: "
                    + name
                    + " at bar "
                    + str(index)
                )


def _validate_tick_receipts(
    prepared: PreparedGeneratedCheckpoint,
    schedule: tuple[BarTickSlice, ...],
    cursor: int,
) -> None:
    """Bind every acknowledged callback coordinate to the admitted tick stream."""
    visited: dict[int, set[int]] = {}
    for receipt in prepared.receipts:
        raw = receipt["event"]
        if raw is None:
            raise ResumeUnsupportedError("generated tick checkpoint contains direct callbacks")
        event = ExecutionEvent.from_dict(raw)
        if not 0 <= event.bar_index <= cursor:
            raise ResumeUnsupportedError("generated tick callback is outside committed input")
        ticks = schedule[event.bar_index].ticks
        if (
            not event.realtime
            or not 0 <= event.tick_index < len(ticks)
            or event.final_tick != (event.tick_index == len(ticks) - 1)
            or event.last_bar_index != event.bar_index
            or event.last_historical_bar_index != -1
        ):
            raise ResumeUnsupportedError(
                "generated callback differs from admitted tick coordinates"
            )
        if event.cause == "TICK":
            seen = visited.setdefault(event.bar_index, set())
            if event.tick_index in seen:
                raise ResumeUnsupportedError("generated tick callback was acknowledged twice")
            seen.add(event.tick_index)
    if any(visited.get(i) != set(range(len(schedule[i].ticks))) for i in range(cursor + 1)):
        raise ResumeUnsupportedError("generated checkpoint omitted admitted tick callbacks")


def _validate_fill_receipts(
    prepared: PreparedGeneratedCheckpoint,
    state: BacktestResumeState,
    *,
    calc_on_order_fills: bool,
    tick_schedule: tuple[BarTickSlice, ...] | None = None,
) -> None:
    """Bind owner fill recalculations to ordered native fills in the same cut.

    Native close-activated scans may coalesce several fills into one callback.
    Each recorded cause must still identify a distinct ordered actual fill, and
    each filled parent bar must have its required recalculation when enabled.
    """
    broker = state.broker_state
    if not isinstance(broker, BrokerSnapshot):
        raise ResumeUnsupportedError("generated cut requires the admitted native broker owner")
    fills = broker.fills
    next_fill = 0
    covered_bars: set[int] = set()
    for receipt in prepared.receipts:
        if receipt["event"] is None:
            continue
        event = ExecutionEvent.from_dict(receipt["event"])
        if event.cause != "ORDER_FILL":
            continue
        if not calc_on_order_fills:
            raise ResumeUnsupportedError(
                "generated fill receipt conflicts with broker recalc config"
            )
        fill_time = event.bar_open_time_utc_ms
        if event.realtime:
            if tick_schedule is None:
                raise ResumeUnsupportedError("generated fill receipt has no admitted tick stream")
            fill_time = tick_schedule[event.bar_index].ticks[event.tick_index].time
        while next_fill < len(fills):
            fill = fills[next_fill]
            next_fill += 1
            if (
                fill.bar_index == event.bar_index
                and fill.time == fill_time
                and str(fill.order_id) == event.fill_order_id
                and decimal_string(Decimal(repr(float(fill.price)))) == event.fill_price
            ):
                covered_bars.add(fill.bar_index)
                break
        else:
            raise ResumeUnsupportedError("generated fill receipt differs from broker fill history")
    if calc_on_order_fills and {fill.bar_index for fill in fills} != covered_bars:
        raise ResumeUnsupportedError("generated checkpoint omitted broker fill recalculations")


def generated_strategy(
    session: RC6GeneratedScriptSession,
    tape_events: list[dict[str, Any]],
    *,
    engine: BacktestEngine | None = None,
    validate_resume: Callable[..., None] | None = None,
    on_intents: Callable[[ExecutionEvent, list[dict[str, Any]]], None] | None = None,
) -> type:
    """Share the existing bulk callback/intent adapter with the bytes bridge."""
    from backtest_engine.core.intent_replay import (
        admit_sealed_intent_tape,
        apply_live_intents_for_bar,
    )

    class _GeneratedStrategy:
        required_runtime_capabilities: tuple[str, ...] = ()
        realtime_resume_runtime = "strategy"

        @staticmethod
        def validate_resume_state(state: Any, *, bar_index: int, committed_bar: Bar) -> None:
            if validate_resume is None:
                raise ResumeUnsupportedError("generated tick owner is missing admission context")
            validate_resume(state, bar_index=bar_index, committed_bar=committed_bar)

        def __init__(self, params: dict[str, Any], runtime: Any, ctx: Any) -> None:
            del runtime
            if params != dict(session.inputs.values):
                raise ValueError("bulk broker parameters differ from applied Pine inputs")
            self.ctx = ctx

        def run_callback(self, bar: Any, event: ExecutionEvent) -> None:
            values = _primary_values(bar)
            execution = session.execute_callback(
                values,
                event,
                strategy_values=strategy_values_from_state(self.ctx.state, self.ctx.config),
                open_entry_ids=tuple(trade.entry_id for trade in self.ctx.state._open_trades_ref),
                broker_equity=self.ctx.state.equity,
            )
            batch = [dict(intent) for intent in execution.intents]
            tape_events.extend(batch)
            if batch:
                current = admit_sealed_intent_tape(batch, sequence_origin=int(batch[0]["sequence"]))
                apply_live_intents_for_bar(
                    self.ctx,
                    current,
                    event.bar_index,
                    bar_open_time_utc_ms=int(bar.time),
                )
            if on_intents is not None:
                on_intents(event, batch)

        def export_state(self) -> dict[str, Any]:
            return session.export_state()

        def export_protocol_state(self) -> dict[str, Any]:
            # The protocol's canonical scalar domain excludes native floats.
            # Preserve the complete owner's JSON bytes rather than changing its
            # values or introducing a second graph representation.
            import base64
            from backtest_engine.core.state_snapshot import JsonStateSerializer

            return {
                "schema": "openpine.generated-checkpoint.transport.v1",
                "codec": "json-v1",
                "inline_base64": base64.b64encode(
                    JsonStateSerializer().dumps(session.export_state())
                ).decode("ascii"),
            }

        def _commit_bar(self, index: int) -> None:
            session.finalize_bar(index)

        def restore_state(self, state: Any) -> None:
            session.restore_state(state)
            # The generated owner proves this sequence through callback receipts.
            # Resume the broker's event producer at that same committed boundary.
            if engine is not None:
                last = session.execution_cursor.last
                engine._execution_callback_sequence = -1 if last is None else last.sequence

    return _GeneratedStrategy


class RC6GeneratedExecutionBackend:
    """Caller-selected generated owner for the existing public native engine path.

    The broker admits transport, accounting, config and input prefix first. This
    trusted owner then admits the complete generated graph and its committed cut.
    It does not select imports from JSON or execute a protected worker in-process.
    """

    name = "rc6-generated"

    def __init__(
        self,
        session: RC6GeneratedScriptSession,
        tape_events: list[dict[str, Any]] | None = None,
        *,
        on_intents: Callable[[ExecutionEvent, list[dict[str, Any]]], None] | None = None,
    ) -> None:
        from openpine.runtime.rc6_worker_runtime import RC6GeneratedScriptSession

        if not isinstance(session, RC6GeneratedScriptSession):
            raise ResumeUnsupportedError("generated bytes bridge requires an RC6 session")
        self.session = session
        self.tape_events = tape_events if tape_events is not None else []
        self.on_intents = on_intents

    def prepare_native_execution(
        self,
        *,
        engine: BacktestEngine,
        strategy_class: type,
        params: dict[str, Any] | None,
        series: BarSeries,
        resume_state: BacktestResumeState | None,
        callbacks: BacktestCallbacks | None,
        tick_schedule: tuple[BarTickSlice, ...] | None = None,
    ) -> PreparedNativeExecution:
        session = self.session
        config = engine.config
        if (
            config.resume_validation_policy != "strict"
            or config.runtime is not None
            or config.warmup_policy
            or config.force_close_on_end
        ):
            raise ResumeUnsupportedError(
                "generated bytes bridge requires strict admission, no external runtime, "
                "warmup or forced final close"
            )
        admission_config = copy(config)
        validate_backtest_config(admission_config)
        session_config = copy(session.intent_config)
        validate_backtest_config(session_config)
        profile = session.identity.semantic_profile
        if (
            admission_config.semantic_profile != profile
            or session_config.semantic_profile != profile
        ):
            raise ResumeUnsupportedError("generated session and broker semantic profiles differ")
        if generated_owner_config(admission_config, profile) != generated_owner_config(
            session_config, profile
        ):
            raise ResumeUnsupportedError(
                "generated session engine config differs from broker config"
            )
        if strategy_class is not self.session.generated_class:
            raise ResumeUnsupportedError(
                "generated backend strategy class differs from selected session"
            )
        selected_params = dict(session.inputs.values)
        if params is not None and params != selected_params:
            raise ResumeUnsupportedError(
                "generated backend parameters differ from applied Pine inputs"
            )
        state = resume_state
        if config.calc_on_every_tick and tick_schedule is None:
            raise ResumeUnsupportedError(
                "generated tick owner requires the admitted native schedule"
            )
        if state is None:
            # Admission must also reject a provisional/reused owner before the
            # broker resets. Fresh callback execution starts at sequence zero.
            session.export_state()
            if session.execution_cursor.last is not None:
                raise ResumeUnsupportedError("fresh generated execution requires an unused session")
        else:
            if state.runtime_state is not None:
                raise ResumeUnsupportedError("generated bytes bridge has no external runtime owner")

        def validate_owner(payload: Any, *, bar_index: int, committed_bar: Bar) -> None:
            if bar_index < 0 or not isinstance(payload, dict):
                raise ResumeUnsupportedError(
                    "generated bytes resume requires a committed strategy checkpoint"
                )
            try:
                prepared = session.prepare_restore(payload)
            except Exception as error:
                raise ResumeUnsupportedError(
                    "generated owner preflight failed: " + str(error)
                ) from error
            last = prepared.cursor.last
            bar = committed_bar
            if (
                last is None
                or last.realtime != config.calc_on_every_tick
                or not last.final_tick
                or last.bar_index != bar_index
                or last.bar_open_time_utc_ms != int(bar.time)
                or last.last_bar_index
                != (bar_index if config.calc_on_every_tick else len(series) - 1)
                or last.last_historical_bar_index
                != (-1 if config.calc_on_every_tick else len(series) - 1)
            ):
                raise ResumeUnsupportedError(
                    "generated checkpoint and broker committed boundary differ"
                )
            _validate_primary_prefix(prepared, series, bar_index, tick_schedule)
            if tick_schedule is not None:
                _validate_tick_receipts(prepared, tick_schedule, bar_index)
            if state is not None:
                _validate_fill_receipts(
                    prepared,
                    state,
                    calc_on_order_fills=admission_config.calc_on_order_fills,
                    tick_schedule=tick_schedule,
                )

        if state is not None and not config.calc_on_every_tick:
            validate_owner(
                state.strategy_state,
                bar_index=state.bar_index,
                committed_bar=series.get_bar(state.bar_index),
            )

        return PreparedNativeExecution(
            generated_strategy(
                session,
                self.tape_events,
                engine=engine,
                validate_resume=validate_owner,
                on_intents=self.on_intents,
            ),
            selected_params,
            callbacks,
        )


def run_generated_backtest(
    engine: BacktestEngine,
    session: RC6GeneratedScriptSession,
    bars: BarSeries | list[Bar],
    *,
    resume_state: bytes | None = None,
    callbacks: BacktestCallbacks | None = None,
    tape_events: list[dict[str, Any]] | None = None,
) -> BacktestResult:
    """Compatibility entrypoint delegating to the generic public backend API."""
    backend = RC6GeneratedExecutionBackend(session, tape_events)
    return engine.run(
        session.generated_class,
        params=dict(session.inputs.values),
        bars=bars,
        callbacks=callbacks,
        resume_state=resume_state,
        execution_backend=backend,
    )
