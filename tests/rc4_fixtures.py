from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from marketdata_provider.canonical.bar import make_canonical_bar
from openpine_contracts import seal_content_hash

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64
STACK_HASH = "sha256:" + "d" * 64
STACK_COMPONENTS = (
    "openpine-contracts",
    "marketdata-provider",
    "pinelib",
    "pine2ast",
    "ast2python",
    "backtest_engine",
    "optimizer",
    "openpine",
)


def admitted_manifest() -> dict[str, Any]:
    return {
        "schema": "openpine.stack-candidate.v2",
        "stage": "wheel-bound",
        "id": "5.0.0-rc.4-test",
        "not_a_release": True,
        "manifest_hash": STACK_HASH,
        "components": {
            "openpine-contracts": {"sha": "a" * 40},
            "pine2ast": {"sha": "b" * 40},
            "ast2python": {"sha": "c" * 40},
            "pinelib": {"sha": "d" * 40},
            "marketdata-provider": {"sha": "e" * 40},
            "backtest_engine": {"sha": "f" * 40},
            "optimizer": {"sha": "2" * 40},
            "openpine": {"sha": "1" * 40},
        },
        "worker_policy": {
            "bubblewrap_path": "/usr/bin/bwrap",
            "python_path": "/usr/bin/python3",
            "worker_user": "openpine-worker",
            "tmpfs_bytes": 16 * 1024 * 1024,
            "memory_max_bytes": 128 * 1024 * 1024,
            "tasks_max": 32,
            "trusted_packages": [
                "ast2python",
                "attr",
                "attrs",
                "jsonschema",
                "jsonschema_specifications",
                "openpine_contracts",
                "pinelib",
                "referencing",
                "rpds",
            ],
        },
    }


def execution_context(
    *,
    generated_artifact_hash: str = HASH_B,
    emitted_module_hash: str = STACK_HASH,
    semantic_profile: str = "strict_5x",
    series_id: str = "test:S:1m",
    instrument_id: str = "test:S",
    exchange: str = "test",
    market: str = "spot",
    symbol: str = "S",
    timeframe: str = "1m",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_id": "openpine.execution_context.v1",
        "schema_version": "1.0.0",
        "producer": "openpine",
        "producer_version": "5.0.0-rc.4",
        "producer_commit": "1" * 40,
        "stack_id": STACK_HASH,
        "created_at_utc_ms": 0,
        "serializer_id": "openpine.canonical.json.v1",
        "content_hash_alg": "sha256",
        "run_id": "run-test",
        "strategy_id": "strategy-test",
        "session_id": "session-test",
        "stack_manifest_hash": STACK_HASH,
        "wheel_identities": [
            {"name": name, "version": "5.0.0rc4", "content_hash": HASH_B}
            for name in STACK_COMPONENTS
        ],
        "schema_hashes": {
            "openpine.execution_context.v1": HASH_A,
            "openpine.intent.v2": HASH_B,
            "openpine.worker.protocol.v2": HASH_C,
            "openpine.checkpoint.v1": STACK_HASH,
            "openpine.checkpoint.proof.v1": HASH_A,
        },
        "generated_artifact_hash": generated_artifact_hash,
        "source_hash": HASH_C,
        "emitted_module_hash": emitted_module_hash,
        "data_snapshot_hash": HASH_A,
        "series_id": series_id,
        "instrument_id": instrument_id,
        "exchange": exchange,
        "market": market,
        "symbol": symbol,
        "timeframe": timeframe,
        "timezone": "UTC",
        "currency": "USD",
        "mintick": "0.01",
        "pointvalue": "1",
        "session_policy": "24x7",
        "semantic_profile": semantic_profile,
        "finality_policy": "CLOSED_BAR_ONLY",
        "warmup_policy": "CALC_ONLY",
        "score_policy": "ALL_BARS",
        "end_policy": "LIQUIDATE_ON_LAST_BAR",
        "capabilities": ["closed_bar", "deterministic_clock"],
        "policy_registry_version": "openpine.policies.rc4.v1",
        "schema_registry_version": "openpine.schemas.rc4.v1",
        "capability_registry_version": "openpine.capabilities.rc4.v1",
        "producer_commits": {
            "openpine-contracts": "a" * 40,
            "pine2ast": "b" * 40,
            "ast2python": "c" * 40,
            "pinelib": "d" * 40,
            "marketdata-provider": "e" * 40,
            "backtest_engine": "f" * 40,
            "optimizer": "2" * 40,
            "openpine": "1" * 40,
        },
    }
    return seal_content_hash(payload, schema_id="openpine.execution_context.v1")


def canonical_bar_envelopes(
    bars: list[Any], context: dict[str, Any]
) -> list[dict[str, Any]]:
    return [
        make_canonical_bar(
            instrument_id=context["instrument_id"],
            timeframe=context["timeframe"],
            open_time_utc_ms=int(bar.time),
            open=str(bar.open),
            high=str(bar.high),
            low=str(bar.low),
            close=str(bar.close),
            volume=str(0 if bar.volume is None else bar.volume),
            snapshot_id="snapshot-test",
            provider="test",
            provider_revision={"known": True, "revision": "revision-test"},
            producer_commit=context["producer_commits"]["marketdata-provider"],
            stack_id=context["stack_manifest_hash"],
            finality="FINAL",
            created_at_utc_ms=0,
        )
        for bar in bars
    ]


def canonical_series(series: Any) -> Any:
    instrument = series.query.instrument
    timeframe = series.query.timeframe.canonical
    instrument_id = instrument.serialize()
    context = execution_context(
        series_id=f"{instrument_id}:{timeframe}",
        instrument_id=instrument_id,
        exchange=str(instrument.exchange).lower(),
        market=str(instrument.market).lower(),
        symbol=str(instrument.symbol).upper(),
        timeframe=timeframe,
    )
    return SimpleNamespace(
        query=series.query,
        bars=series.bars,
        coverage=series.coverage,
        canonical_bars=tuple(canonical_bar_envelopes(list(series.bars), context)),
        snapshot={},
    )
