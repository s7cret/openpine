"""Run generated artifact bytes in the isolated worker and replay the live tape.

The parent never imports generated source. Production class loaders stay
fail-closed; this is the side-by-side 5.0 execution path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backtest_engine.core.intent_replay import (
    IntentReplayError,
    apply_live_intents_for_bar,
    require_live_tape,
)

from openpine.runtime.isolated_worker import IsolatedWorkerError, evaluate_artifact


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
        if not isinstance(item, dict) or item.get("time_close") is None:
            raise IsolatedRunError("request.security requires confirmed HTF bars")
        stamped.append(
            {
                "symbol": str(item.get("symbol") or ""),
                "timeframe": str(item.get("timeframe") or ""),
                "time": int(item.get("time", 0)),
                "time_close": int(item["time_close"]),
                "open": float(item.get("open", 0)),
                "high": float(item.get("high", 0)),
                "low": float(item.get("low", 0)),
                "close": float(item.get("close", 0)),
                "volume": float(item.get("volume") or 0),
            }
        )
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


def run_isolated_artifact(
    source: bytes,
    *,
    bars: list[Any],
    config: Any,
    resume_state: Any | None = None,
    htf_bars: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        bar_payloads = [
            {
                "time": int(getattr(bar, "time")),
                "open": float(getattr(bar, "open")),
                "high": float(getattr(bar, "high")),
                "low": float(getattr(bar, "low")),
                "close": float(getattr(bar, "close")),
            }
            for bar in bars
        ]
        payload = evaluate_artifact(
            source,
            bars=bar_payloads,
            semantic_profile=_semantic_profile(config),
            htf_bars=_stamp_confirmed_htf_bars(htf_bars),
        )
        tape = require_live_tape(list(payload.get("intent_tape") or []))
    except (IsolatedWorkerError, IntentReplayError) as exc:
        raise IsolatedRunError(str(exc)) from exc

    from backtest_engine import BacktestEngine

    result = BacktestEngine(config).run(
        _ReplayLive,
        {"tape": tape},
        bars=bars,
        resume_state=resume_state,
    )
    return {
        "ok": True,
        "intent_tape": tape,
        "score_ledger_hash": result.score_ledger_hash,
        "raw_result": result,
        "isolation": payload.get("isolation"),
    }


def capture_generated_source(source_id: str, artifact_id: str) -> bytes:
    from openpine.artifacts import ArtifactStore
    from openpine.runtime.engine import BacktestArtifactError

    try:
        artifact = ArtifactStore().get_artifact(artifact_id, source_id)
    except (FileNotFoundError, BacktestArtifactError) as exc:
        raise IsolatedRunError(str(exc)) from exc
    path = Path(str(artifact["artifact_dir"])) / "generated_strategy.py"
    if not path.is_file():
        raise IsolatedRunError(f"Artifact {artifact_id} has no generated_strategy.py")
    return path.read_bytes()


def run_isolated_from_store(
    source_id: str,
    artifact_id: str,
    *,
    bars: list[Any],
    config: Any,
    htf_bars: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return run_isolated_artifact(
        capture_generated_source(source_id, artifact_id),
        bars=bars,
        config=config,
        htf_bars=htf_bars,
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
    bar_payloads = [
        {
            "time": int(getattr(bar, "time") if not isinstance(bar, dict) else bar["time"]),
            "open": float(getattr(bar, "open") if not isinstance(bar, dict) else bar["open"]),
            "high": float(getattr(bar, "high") if not isinstance(bar, dict) else bar["high"]),
            "low": float(getattr(bar, "low") if not isinstance(bar, dict) else bar["low"]),
            "close": float(getattr(bar, "close") if not isinstance(bar, dict) else bar["close"]),
            "volume": float(
                (getattr(bar, "volume", 0) if not isinstance(bar, dict) else bar.get("volume"))
                or 0
            ),
        }
        for bar in bars
    ]
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
