"""Run generated artifact bytes in the isolated worker and replay the live tape.

The parent never imports generated source. Production class loaders stay
fail-closed; this is the side-by-side 5.0 execution path.
"""

from __future__ import annotations

from typing import Any

from backtest_engine.core.intent_replay import (
    IntentReplayError,
    ValidatedIntentTape,
    apply_live_intents_for_bar,
    require_live_tape,
)

from openpine.runtime.isolated_worker import (
    InteractiveWorkerSession,
    IsolatedWorkerError,
    evaluate_artifact,
)


class IsolatedRunError(RuntimeError):
    """Isolated artifact run could not produce a replayable live tape."""


def _semantic_profile(config: Any) -> str:
    from openpine_contracts import SemanticProfile

    raw = getattr(config, "semantic_profile", None)
    if raw is None or str(raw).strip() == "":
        raise IsolatedRunError("semantic_profile is required")
    if isinstance(raw, SemanticProfile):
        return raw.value
    try:
        return SemanticProfile(str(raw)).value
    except ValueError as exc:
        raise IsolatedRunError(f"semantic_profile {raw!r} is unknown") from exc


def _generated_semantic_profile(value: object | None) -> str:
    from openpine_contracts import SemanticProfile

    if value is None or str(value).strip() == "":
        raise IsolatedRunError("semantic_profile is required")
    if isinstance(value, SemanticProfile):
        return value.value
    try:
        return SemanticProfile(str(value)).value
    except ValueError as exc:
        raise IsolatedRunError(f"semantic_profile {value!r} is unknown") from exc


class _ReplayLive:
    def __init__(self, params: dict[str, Any], runtime: Any, ctx: Any) -> None:
        self.ctx = ctx
        self.tape = params["tape"]

    def _process_bar(self, bar: Any, bar_index: int) -> None:
        apply_live_intents_for_bar(self.ctx, self.tape, bar_index)

    def export_state(self) -> dict[str, Any]:
        return {"replay": True}

    def restore_state(self, state: Any) -> None:
        return


def _stamp_confirmed_htf_bars(htf_bars: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not htf_bars:
        return []
    stamped: list[dict[str, Any]] = []
    for item in htf_bars:
        if not isinstance(item, dict):
            raise IsolatedRunError("HTF bar must be an object")
        required = ("symbol", "timeframe", "time", "time_close", "open", "high", "low", "close")
        for name in required:
            if name not in item or item[name] is None or (
                name in {"symbol", "timeframe"} and not str(item[name]).strip()
            ):
                detail = f"HTF bar required field {name} is missing"
                if name == "time_close":
                    detail += "; confirmed HTF bars require time_close"
                raise IsolatedRunError(detail)
        try:
            stamped.append(
                {
                    "symbol": str(item["symbol"]),
                    "timeframe": str(item["timeframe"]),
                    "time": int(item["time"]),
                    "time_close": int(item["time_close"]),
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"]),
                    "volume": float(item.get("volume") or 0),
                }
            )
        except (TypeError, ValueError) as exc:
            raise IsolatedRunError("HTF bar numeric fields are invalid") from exc
    return stamped


def _confirmed_htf_bars_from_provider_bars(bars, *, symbol: str, timeframe: str):
    if not bars:
        return None
    stamped: list[dict[str, object]] = []
    for bar in bars:
        if isinstance(bar, dict):
            time_close = bar.get("time_close")
            time = bar.get("time", 0)
            open_ = bar.get("open", 0)
            high = bar.get("high", 0)
            low = bar.get("low", 0)
            close = bar.get("close", 0)
            volume = bar.get("volume") or 0
        else:
            time_close = getattr(bar, "time_close", None)
            time = getattr(bar, "time", 0)
            open_ = getattr(bar, "open", 0)
            high = getattr(bar, "high", 0)
            low = getattr(bar, "low", 0)
            close = getattr(bar, "close", 0)
            volume = getattr(bar, "volume", 0) or 0
        if time_close is None:
            return None
        stamped.append(
            {
                "symbol": str(symbol),
                "timeframe": str(timeframe),
                "time": int(time),
                "time_close": int(time_close),
                "open": float(open_),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": float(volume),
            }
        )
    return stamped


def _confirmed_htf_bars_for_timeframe(
    *,
    chart_bars,
    symbol: str,
    chart_timeframe: str,
    requested_timeframe: str | None,
    fetched_htf_bars=None,
):
    if requested_timeframe and str(requested_timeframe) != str(chart_timeframe):
        return _confirmed_htf_bars_from_provider_bars(
            fetched_htf_bars,
            symbol=str(symbol),
            timeframe=str(requested_timeframe),
        )
    return _confirmed_htf_bars_from_provider_bars(
        chart_bars,
        symbol=str(symbol),
        timeframe=str(chart_timeframe),
    )


def _chart_bar_payload(bar: Any) -> dict[str, int | float]:
    def required_field(name: str) -> Any:
        if isinstance(bar, dict):
            if name not in bar or bar[name] is None:
                raise IsolatedRunError(f"chart bar required field {name} is missing")
            return bar[name]
        value = getattr(bar, name, None)
        if value is None:
            raise IsolatedRunError(f"chart bar required field {name} is missing")
        return value

    def optional_field(name: str, default: Any = None) -> Any:
        if isinstance(bar, dict):
            return bar.get(name, default)
        return getattr(bar, name, default)

    try:
        payload: dict[str, int | float] = {
            "time": int(required_field("time")),
            "open": float(required_field("open")),
            "high": float(required_field("high")),
            "low": float(required_field("low")),
            "close": float(required_field("close")),
            "volume": float(optional_field("volume", 0) or 0),
        }
    except (TypeError, ValueError) as exc:
        raise IsolatedRunError("chart bar required fields must be numeric") from exc
    time_close = optional_field("time_close")
    if time_close is not None:
        try:
            payload["time_close"] = int(time_close)
        except (TypeError, ValueError) as exc:
            raise IsolatedRunError("chart bar time_close must be numeric") from exc
    return payload


def _strategy_projection(callback: dict[str, Any]) -> dict[str, Any]:
    broker = callback.get("broker")
    ledger = callback.get("ledger")
    if not isinstance(broker, dict) or not isinstance(ledger, dict):
        raise IsolatedRunError("engine callback omitted broker/ledger projection")
    position = broker.get("position")
    if not isinstance(position, dict):
        raise IsolatedRunError("engine callback position projection is invalid")
    open_trades = ledger.get("open_trades")
    closed_trades = ledger.get("closed_trades")
    fills = ledger.get("fills")
    if not isinstance(open_trades, list) or not isinstance(closed_trades, list):
        raise IsolatedRunError("engine callback trade projection is invalid")
    if not isinstance(fills, list):
        raise IsolatedRunError("engine callback fill projection is invalid")
    profits = [float(item.get("profit") or 0) for item in closed_trades]
    gross_profit = sum(value for value in profits if value > 0)
    gross_loss = sum(abs(value) for value in profits if value < 0)
    position_size = float(position.get("size") or 0)
    return {
        "cash": float(broker.get("cash") or 0),
        "equity": float(broker.get("equity") or 0),
        "netprofit": float(position.get("realized_profit") or 0),
        "openprofit": float(position.get("open_profit") or 0),
        "grossprofit": gross_profit,
        "grossloss": gross_loss,
        "position_size": position_size,
        "position_avg_price": float(position.get("avg_price") or 0),
        "position_entry_name": (
            str(open_trades[-1].get("entry_id")) if open_trades else None
        ),
        "opentrades": len(open_trades),
        "closedtrades": len(closed_trades),
        "wintrades": sum(value > 0 for value in profits),
        "losstrades": sum(value < 0 for value in profits),
        "eventrades": sum(value == 0 for value in profits),
        "max_drawdown": float(broker.get("max_drawdown") or 0),
        "max_runup": float(broker.get("max_runup") or 0),
        "fills": fills,
        "open_trade_log": open_trades,
        "closed_trade_log": closed_trades,
    }


def run_isolated_artifact(
    source: bytes,
    *,
    bars: list[Any],
    config: Any,
    resume_state: Any | None = None,
    htf_bars: list[dict[str, Any]] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from backtest_engine import BacktestCallbacks, BacktestEngine

    semantic_profile = _semantic_profile(config)
    chart_timeframe = str(getattr(config, "timeframe", "") or "")
    if not chart_timeframe:
        raise IsolatedRunError("chart timeframe is required")
    accumulated_events: list[dict[str, Any]] = []
    callback_state: dict[str, Any] = {}

    def capture_projection(payload: dict[str, Any]) -> None:
        callback_state.clear()
        callback_state.update(payload)

    try:
        with InteractiveWorkerSession(
            source,
            semantic_profile=semantic_profile,
            chart_timeframe=chart_timeframe,
            htf_bars=_stamp_confirmed_htf_bars(htf_bars),
            params=params,
        ) as session:

            class _InteractiveStrategy:
                required_runtime_capabilities: tuple[str, ...] = ()

                def __init__(self, params: dict[str, Any], runtime: Any, ctx: Any) -> None:
                    del params, runtime
                    self.ctx = ctx

                def _process_bar(self, bar: Any, bar_index: int) -> None:
                    if not callback_state:
                        raise IsolatedRunError(
                            "engine did not provide broker projection before strategy callback"
                        )
                    response = session.evaluate_bar(
                        _chart_bar_payload(bar),
                        bar_index=bar_index,
                        phase=str(callback_state.get("phase") or "score"),
                        recalc_iteration=int(
                            callback_state.get("recalc_iteration") or 0
                        ),
                        projection=_strategy_projection(callback_state),
                    )
                    batch = response.get("intent_batch")
                    if not isinstance(batch, list):
                        raise IsolatedRunError("worker INTENT_BATCH payload is invalid")
                    if not batch:
                        return
                    accumulated_events.extend(dict(item) for item in batch)
                    validated = require_live_tape(accumulated_events)
                    current = ValidatedIntentTape(
                        events=tuple(dict(item) for item in batch),
                        identity=validated.identity,
                    )
                    apply_live_intents_for_bar(
                        self.ctx,
                        current,
                        bar_index,
                        bar_open_time_utc_ms=int(getattr(bar, "time")),
                    )

                def export_state(self) -> dict[str, Any]:
                    return {"interactive_worker": True}

                def restore_state(self, state: Any) -> None:
                    del state

            result = BacktestEngine(config).run(
                _InteractiveStrategy,
                bars=bars,
                callbacks=BacktestCallbacks(on_strategy_callback=capture_projection),
                resume_state=resume_state,
            )
            isolation = session.hello.get("isolation")
    except (IsolatedWorkerError, IntentReplayError) as exc:
        raise IsolatedRunError(str(exc)) from exc

    if accumulated_events:
        try:
            tape = require_live_tape(accumulated_events)
        except IntentReplayError as exc:
            raise IsolatedRunError(str(exc)) from exc
        tape_events = list(tape.events)
    elif resume_state is not None:
        tape_events = []
    else:
        raise IsolatedRunError("live pinelib tape is empty")
    return {
        "ok": True,
        "intent_tape": tape_events,
        "score_ledger_hash": result.score_ledger_hash,
        "raw_result": result,
        "isolation": isolation,
        "execution_protocol": "openpine.worker.protocol.v2",
    }


def capture_generated_source(source_id: str, artifact_id: str) -> bytes:
    from openpine.artifacts import ArtifactStore
    from openpine.runtime.engine import BacktestArtifactError

    try:
        artifact = ArtifactStore().get_artifact(artifact_id, source_id)
    except (FileNotFoundError, BacktestArtifactError, ValueError) as exc:
        raise IsolatedRunError(str(exc)) from exc
    source = artifact.get("python_code")
    if not isinstance(source, str) or not source:
        raise IsolatedRunError(f"Artifact {artifact_id} has no generated_strategy.py")
    return source.encode("utf-8")


def run_isolated_from_store(
    source_id: str,
    artifact_id: str,
    *,
    bars: list[Any],
    config: Any,
    htf_bars: list[dict[str, Any]] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return run_isolated_artifact(
        capture_generated_source(source_id, artifact_id),
        bars=bars,
        config=config,
        htf_bars=htf_bars,
        params=params,
    )


class IsolatedPlotResult:
    def __init__(self, plots: list[tuple]) -> None:
        self.plots = plots


def run_isolated_indicator(
    source: bytes,
    bars: list[Any],
    *,
    semantic_profile: object | None = None,
    htf_bars: list[dict[str, Any]] | None = None,
) -> IsolatedPlotResult:
    bar_payloads = [_chart_bar_payload(bar) for bar in bars]
    try:
        payload = evaluate_artifact(
            source,
            bars=bar_payloads,
            semantic_profile=_generated_semantic_profile(semantic_profile),
            htf_bars=_stamp_confirmed_htf_bars(htf_bars),
        )
    except IsolatedWorkerError as exc:
        raise IsolatedRunError(str(exc)) from exc
    records: list[tuple] = []
    for item in payload.get("plots") or []:
        records.append(
            (
                int(item["bar_time"]),
                int(item["bar_index"]),
                item["value"],
                item["title"],
            )
        )
    return IsolatedPlotResult(plots=records)
