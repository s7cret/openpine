from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from backtest_engine import BacktestConfig, Bar
from marketdata_provider.canonical.bar import make_canonical_bar
from openpine.admission import DeploymentAdmissionIdentity
from openpine_contracts import Finality
from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
from openpine.run_identity import execution_context_from_admission
from openpine.runtime.isolated_run import run_isolated_artifact
from openpine.runtime.isolated_worker import (
    IsolatedWorkerError,
    _TRUSTED_NAMES,
    _validate_interactive_generated_artifact,
)


SOURCE = '//@version=6\nstrategy("rc6-worker")\nstrategy.entry("L", strategy.long, qty=1)\n'
COMMITS = {"pine2ast": "a" * 40, "ast2python": "b" * 40}
COMPONENTS = (
    "openpine-contracts",
    "pine2ast",
    "ast2python",
    "pinelib",
    "marketdata-provider",
    "backtest_engine",
    "optimizer",
    "openpine",
)
ALL_COMMITS = {
    name: f"{index:x}" * 40 for index, name in enumerate(COMPONENTS, start=1)
}
STACK_HASH = "sha256:" + "d" * 64


def _compiled():
    result = NativeRC6CompilerAdapter().compile(
        SOURCE,
        module_name="generated_rc6_worker",
        source_name="rc6-worker.pine",
        producer_commits=COMMITS,
    )
    assert result.success, result.errors
    assert result.generated_artifact is not None
    return result


def test_interactive_worker_admits_only_exact_v3_generated_script_identity() -> None:
    result = _compiled()
    generated = result.generated_artifact
    context = {
        "generated_artifact_hash": generated["content_hash"],
        "source_hash": generated["source_hash"],
        "emitted_module_hash": generated["emitted_module_hash"],
        "producer_commits": {"ast2python": COMMITS["ast2python"]},
    }

    identity = _validate_interactive_generated_artifact(
        result.python_code.encode("utf-8"), generated, context
    )

    assert identity == {
        "artifact_hash": generated["content_hash"],
        "module_hash": generated["emitted_module_hash"],
        "entrypoint_module": "generated_rc6_worker",
        "entrypoint_class": "GeneratedScript",
    }


@pytest.mark.parametrize("field", ["source_hash", "emitted_module_hash"])
def test_interactive_worker_rejects_v3_execution_context_drift(field: str) -> None:
    result = _compiled()
    generated = result.generated_artifact
    context = {
        "generated_artifact_hash": generated["content_hash"],
        "source_hash": generated["source_hash"],
        "emitted_module_hash": generated["emitted_module_hash"],
        "producer_commits": {"ast2python": COMMITS["ast2python"]},
    }
    context[field] = "sha256:" + "f" * 64

    with pytest.raises(IsolatedWorkerError, match="admission identity"):
        _validate_interactive_generated_artifact(
            result.python_code.encode("utf-8"), deepcopy(generated), context
        )


def _deployment() -> DeploymentAdmissionIdentity:
    schema_ids = (
        "openpine.execution_context.v1",
        "openpine.intent.v2",
        "openpine.worker.protocol.v2",
        "openpine.checkpoint.v1",
        "openpine.checkpoint.proof.v1",
    )
    return DeploymentAdmissionIdentity(
        stack_id="openpine-5.0",
        stack_manifest_hash=STACK_HASH,
        wheel_identities=tuple(
            (name, "5.0.0rc6", "sha256:" + f"{index:x}" * 64)
            for index, name in enumerate(COMPONENTS, start=1)
        ),
        schema_hashes={schema_id: "sha256:" + "e" * 64 for schema_id in schema_ids},
        capabilities=frozenset(
            {"closed_bar", "deterministic_clock", "checkpoint_v1", "sealed_artifact_refs"}
        ),
        semantic_profiles=frozenset({"strict_5x"}),
        finality_policies=frozenset({"CLOSED_BAR_ONLY"}),
        warmup_policies=frozenset({"CALC_ONLY"}),
        score_policies=frozenset({"ALL_BARS"}),
    )


def _manifest() -> dict[str, object]:
    return {
        "manifest_hash": STACK_HASH,
        "components": {name: {"sha": ALL_COMMITS[name]} for name in COMPONENTS},
        "worker_policy": {
            "bubblewrap_path": "/usr/bin/bwrap",
            "python_path": "candidate-python",
            "worker_user": "openpine-worker",
            "tmpfs_bytes": 16 * 1024 * 1024,
            "memory_max_bytes": 128 * 1024 * 1024,
            "tasks_max": 32,
            "trusted_packages": list(_TRUSTED_NAMES),
        },
    }


@pytest.mark.parametrize("mode,quantity", [
    ("interactive", None), ("interactive", 2), ("interactive", 7),
    ("bulk_backtest", 2), ("bulk_backtest", 7),
])
def test_isolated_rc6_worker_emits_intent_consumed_by_engine(
    tmp_path: Path, mode: str, quantity: int | None,
) -> None:
    source = SOURCE if quantity is None else (
        '//@version=6\nstrategy("input-worker")\nn=input.int(1,minval=1)\n'
        'strategy.entry("L",strategy.long,qty=n)\n')
    result = NativeRC6CompilerAdapter().compile(
        source,
        module_name="generated_rc6_worker",
        source_name="rc6-worker.pine",
        producer_commits={
            "pine2ast": ALL_COMMITS["pine2ast"],
            "ast2python": ALL_COMMITS["ast2python"],
        },
    )
    assert result.success, result.errors
    artifact = {
        "generated_artifact": result.generated_artifact,
        "python_code": result.python_code,
        "consumer_bundle": result.consumer_bundle,
        "source_map": result.source_map,
        "compile_meta": result.compile_meta,
    }
    context = execution_context_from_admission(
        _deployment(),
        _manifest(),
        run_id="run-rc6-worker",
        strategy_id="strategy-rc6-worker",
        artifact=artifact,
        data_snapshot_hash="sha256:" + "f" * 64,
        series_id="binance:spot:SOLUSDT:1m",
        instrument_id="binance:spot:SOLUSDT",
        exchange="binance",
        market="spot",
        symbol="SOLUSDT",
        timeframe="1m",
        semantic_profile="strict_5x",
        created_at_utc_ms=0,
    )
    opened = 1_725_145_620_000
    canonical_bar = make_canonical_bar(
        instrument_id="binance:spot:SOLUSDT",
        timeframe="1m",
        open_time_utc_ms=opened,
        close_time_utc_ms=opened + 59_999,
        open=100,
        high=102,
        low=99,
        close=101,
        volume=1000,
        snapshot_id="snapshot-rc6-worker",
        provider="binance",
        provider_revision={"known": True, "revision": "1"},
        producer_commit=ALL_COMMITS["marketdata-provider"],
        stack_id=STACK_HASH,
        finality="FINAL",
        created_at_utc_ms=0,
    )
    second_bar = make_canonical_bar(
        instrument_id="binance:spot:SOLUSDT",
        timeframe="1m",
        open_time_utc_ms=opened + 60_000,
        close_time_utc_ms=opened + 119_999,
        open=101,
        high=103,
        low=100,
        close=102,
        volume=1100,
        snapshot_id="snapshot-rc6-worker",
        provider="binance",
        provider_revision={"known": True, "revision": "1"},
        producer_commit=ALL_COMMITS["marketdata-provider"],
        stack_id=STACK_HASH,
        finality="FINAL",
        created_at_utc_ms=0,
    )
    config = BacktestConfig(
        symbol="SOLUSDT",
        timeframe="1m",
        start_time=opened,
        end_time=opened + 60_000,
        initial_capital=100_000,
        commission_type="none",
        semantic_profile="strict_5x",
        finality_policy="CLOSED_BAR_ONLY",
        force_close_on_end=False,
    )
    for name, value in {
        "execution_context": context,
        "admitted_manifest": _manifest(),
        "instrument_id": "binance:spot:SOLUSDT",
        "generated_artifact": result.generated_artifact,
        "bar_envelopes": [canonical_bar, second_bar],
        "run_hash": "sha256:" + "1" * 64,
        "protocol_artifact_dir": str(tmp_path / "protocol"),
        "isolated_protocol": mode,
    }.items():
        object.__setattr__(config, name, value)

    isolated = run_isolated_artifact(
        result.python_code.encode("utf-8"),
        bars=[
            Bar(
                opened,
                100,
                102,
                99,
                101,
                volume=1000,
                time_close=opened + 59_999,
                finality=Finality.FINAL,
            ),
            Bar(
                opened + 60_000,
                101,
                103,
                100,
                102,
                volume=1100,
                time_close=opened + 119_999,
                finality=Finality.FINAL,
            ),
        ],
        config=config,
        params={} if quantity is None else {"n": quantity},
    )

    assert isolated["intent_tape"][0]["qty"] == str(quantity or 1)
    assert isolated["ok"] is True
    assert len(isolated["intent_tape"]) == 2
    assert isolated["intent_tape"][0]["kind"] == "entry"
    assert isolated["raw_result"].open_trades
