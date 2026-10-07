"""Concrete generated-script bytes bridge over existing broker and checkpoint owners."""

from __future__ import annotations

from copy import copy
from typing import Any, TYPE_CHECKING

from backtest_engine import BacktestEngine
from backtest_engine.core.engine_validation import validate_backtest_config
from backtest_engine.core.strategy_capabilities import strategy_values_from_state
from backtest_engine.errors import ResumeUnsupportedError
from backtest_engine.execution_backends.base import PreparedNativeExecution
from backtest_engine.models import BacktestCallbacks, BacktestResumeState, Bar, BarSeries
from backtest_engine.results import BacktestResult
from openpine_contracts import ExecutionEvent
from pinelib.runtime.metadata import BarValues

from openpine.runtime.rc6_config import serialize_engine_config
from openpine.runtime.generated_checkpoint import PreparedGeneratedCheckpoint

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
        expected = _primary_values(series.get_bar(index))
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


def generated_strategy(
    session: RC6GeneratedScriptSession,
    tape_events: list[dict[str, Any]],
    *,
    engine: BacktestEngine | None = None,
) -> type:
    """Share the existing bulk callback/intent adapter with the bytes bridge."""
    from backtest_engine.core.intent_replay import (
        admit_sealed_intent_tape,
        apply_live_intents_for_bar,
    )

    class _GeneratedStrategy:
        required_runtime_capabilities: tuple[str, ...] = ()

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

        def export_state(self) -> dict[str, Any]:
            return session.export_state()

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
    ) -> None:
        from openpine.runtime.rc6_worker_runtime import RC6GeneratedScriptSession

        if not isinstance(session, RC6GeneratedScriptSession):
            raise ResumeUnsupportedError("generated bytes bridge requires an RC6 session")
        self.session = session
        self.tape_events = tape_events if tape_events is not None else []

    def prepare_native_execution(
        self,
        *,
        engine: BacktestEngine,
        strategy_class: type,
        params: dict[str, Any] | None,
        series: BarSeries,
        resume_state: BacktestResumeState | None,
        callbacks: BacktestCallbacks | None,
    ) -> PreparedNativeExecution:
        session = self.session
        config = engine.config
        if (
            config.resume_validation_policy != "strict"
            or config.calc_on_every_tick
            or config.runtime is not None
            or config.warmup_policy
            or config.force_close_on_end
        ):
            raise ResumeUnsupportedError(
                "generated bytes bridge requires strict historical bars, no external runtime, "
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
        if serialize_engine_config(admission_config, profile) != serialize_engine_config(
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
        if state is not None:
            if (
                state.runtime_state is not None
                or "realtime_tick_schedule_fingerprint" in state.metadata
            ):
                raise ResumeUnsupportedError(
                    "generated bytes bridge has no external/tick runtime owner"
                )
            if state.bar_index < 0 or not isinstance(state.strategy_state, dict):
                raise ResumeUnsupportedError(
                    "generated bytes resume requires a committed strategy checkpoint"
                )
            try:
                prepared = session.prepare_restore(state.strategy_state)
            except Exception as error:
                raise ResumeUnsupportedError(
                    "generated owner preflight failed: " + str(error)
                ) from error
            last = prepared.cursor.last
            bar = series.get_bar(state.bar_index)
            if (
                last is None
                or last.realtime
                or not last.final_tick
                or last.bar_index != state.bar_index
                or last.bar_open_time_utc_ms != int(bar.time)
                or last.last_bar_index != len(series) - 1
                or last.last_historical_bar_index != len(series) - 1
            ):
                raise ResumeUnsupportedError(
                    "generated checkpoint and broker committed boundary differ"
                )
            _validate_primary_prefix(prepared, series, state.bar_index)

        return PreparedNativeExecution(
            generated_strategy(session, self.tape_events, engine=engine),
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
