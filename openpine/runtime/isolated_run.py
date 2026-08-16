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


class _ReplayLive:
    def __init__(self, params: dict[str, Any], runtime: Any, ctx: Any) -> None:
        self.ctx = ctx
        self.tape = params["tape"]

    def _process_bar(self, bar: Any, bar_index: int) -> None:
        apply_live_intents_for_bar(self.ctx, self.tape, bar_index)


def run_isolated_artifact(
    source: bytes,
    *,
    bars: list[Any],
    config: Any,
) -> dict[str, Any]:
    try:
        payload = evaluate_artifact(source)
        tape = require_live_tape(list(payload.get("intent_tape") or []))
    except (IsolatedWorkerError, IntentReplayError) as exc:
        raise IsolatedRunError(str(exc)) from exc

    from backtest_engine import BacktestEngine

    result = BacktestEngine(config).run(_ReplayLive, {"tape": tape}, bars=bars)
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
