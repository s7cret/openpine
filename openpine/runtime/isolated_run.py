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


def run_isolated_artifact(
    source: bytes,
    *,
    bars: list[Any],
    config: Any,
    resume_state: Any | None = None,
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
) -> dict[str, Any]:
    return run_isolated_artifact(
        capture_generated_source(source_id, artifact_id),
        bars=bars,
        config=config,
    )


class IsolatedPlotResult:
    def __init__(self, plots: list[tuple]) -> None:
        self.plots = plots


def run_isolated_indicator(
    source: bytes,
    bars: list[Any],
    *,
    semantic_profile: object | None = None,
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
