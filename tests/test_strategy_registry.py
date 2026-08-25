from __future__ import annotations

import pytest

from openpine.registry.strategies import SQLiteStrategyRegistry, StrategyInstance


def test_strategy_instance_from_dict_defaults_match_registered_strategy_defaults() -> (
    None
):
    strategy = StrategyInstance.from_dict(
        {
            "strategy_id": "strategy-1",
            "name": "Strategy",
            "pine_id": "",
            "artifact_id": "artifact-1",
            "params_json": "{}",
            "params_hash": "params-1",
            "symbol": "BTCUSDT",
            "timeframe": "1h",
        }
    )

    assert strategy.exchange == "binance"
    assert strategy.market_type == "spot"
    assert strategy.price_type == "trade"
    assert strategy.mode == "paper"


def test_paper_execution_epoch_is_stable_across_metadata_patch_and_resets_on_resume(
    tmp_path, monkeypatch
) -> None:
    registry = SQLiteStrategyRegistry(db_path=tmp_path / "openpine.sqlite")
    strategy = registry.register_strategy(
        artifact_id="artifact-1",
        symbol="BTCUSDT",
        timeframe="1m",
        params={},
        name="paper-epoch",
        mode="paper",
    )
    timestamps = iter((100.0, 101.0, 102.0, 103.0, 104.0, 105.0))
    monkeypatch.setattr(
        "openpine.registry.strategies.time.time", lambda: next(timestamps)
    )
    try:
        registry.activate_strategy(
            strategy.strategy_id, status="running", mode="paper"
        )
        first = registry.execution_epoch_started_at(strategy.strategy_id)
        registry.patch_strategy_atomic(strategy.strategy_id, {"name": "renamed"})
        assert registry.execution_epoch_started_at(strategy.strategy_id) == first

        registry.patch_strategy_atomic(
            strategy.strategy_id, {"params_json": "{}"}
        )
        assert registry.execution_epoch_started_at(strategy.strategy_id) == first

        registry.patch_strategy_atomic(
            strategy.strategy_id, {"params_json": '{"length": 21}'}
        )
        params_epoch = registry.execution_epoch_started_at(strategy.strategy_id)
        patched = registry.get_strategy(strategy.strategy_id)
        assert params_epoch == 103_000
        assert patched.params_json == '{"length":21}'
        assert patched.params_hash != strategy.params_hash
        with pytest.raises(ValueError, match="derived"):
            registry.patch_strategy_atomic(
                strategy.strategy_id, {"params_hash": "forged"}
            )

        registry.transition_strategy(
            strategy.strategy_id, enabled=False, status="paused"
        )
        registry.transition_strategy(
            strategy.strategy_id, enabled=True, status="running", mode="paper"
        )
        second = registry.execution_epoch_started_at(strategy.strategy_id)
    finally:
        registry.close()

    assert first == 100_000
    assert second == 105_000


def test_paper_execution_epoch_advances_monotonically_within_same_millisecond(
    tmp_path, monkeypatch
) -> None:
    registry = SQLiteStrategyRegistry(db_path=tmp_path / "openpine.sqlite")
    strategy = registry.register_strategy(
        artifact_id="artifact-1",
        symbol="BTCUSDT",
        timeframe="1m",
        params={},
        name="paper-epoch-same-ms",
        mode="paper",
    )
    monkeypatch.setattr("openpine.registry.strategies.time.time", lambda: 100.0)
    try:
        registry.activate_strategy(
            strategy.strategy_id, status="running", mode="paper"
        )
        first = registry.execution_epoch_started_at(strategy.strategy_id)
        registry.transition_strategy(
            strategy.strategy_id, enabled=False, status="paused"
        )
        registry.transition_strategy(
            strategy.strategy_id, enabled=True, status="running", mode="paper"
        )
        second = registry.execution_epoch_started_at(strategy.strategy_id)
    finally:
        registry.close()

    assert first == 100_000
    assert second == 100_001


def test_sqlite_strategy_registry_persists_registered_strategy(tmp_path) -> None:
    db_path = tmp_path / "openpine.sqlite"
    registry = SQLiteStrategyRegistry(db_path=db_path)
    strategy = registry.register_strategy(
        artifact_id="artifact-1",
        symbol="BTCUSDT",
        timeframe="1h",
        params={"length": 14},
        name="ema-cross",
    )
    registry.close()

    reloaded = SQLiteStrategyRegistry(db_path=db_path)
    try:
        loaded = reloaded.get_strategy(strategy.strategy_id)
    finally:
        reloaded.close()

    assert loaded.name == "ema-cross"
    assert loaded.market_type == "spot"
    assert loaded.params_json == '{"length": 14}'


def test_sqlite_strategy_registry_persists_registered_strategy_mode(tmp_path) -> None:
    db_path = tmp_path / "openpine.sqlite"
    registry = SQLiteStrategyRegistry(db_path=db_path)
    strategy = registry.register_strategy(
        artifact_id="artifact-1",
        symbol="BTCUSDT",
        timeframe="15m",
        params={},
        name="backtest-strategy",
        mode="backtest",
    )
    registry.close()

    reloaded = SQLiteStrategyRegistry(db_path=db_path)
    try:
        loaded = reloaded.get_strategy(strategy.strategy_id)
    finally:
        reloaded.close()

    assert loaded.mode == "backtest"


def test_sqlite_strategy_registry_persists_semantic_profile(tmp_path) -> None:
    db_path = tmp_path / "openpine.sqlite"
    registry = SQLiteStrategyRegistry(db_path=db_path)
    strategy = registry.register_strategy(
        artifact_id="artifact-1",
        symbol="BTCUSDT",
        timeframe="15m",
        params={},
        name="live-strategy",
        mode="paper",
    )
    assert strategy.semantic_profile is None
    registry.set_semantic_profile(strategy.strategy_id, "strict_5x")
    registry.activate_strategy(strategy.strategy_id, status="running", mode="live")
    assert registry.get_strategy(strategy.strategy_id).semantic_profile == "strict_5x"
    registry.close()

    reloaded = SQLiteStrategyRegistry(db_path=db_path)
    try:
        loaded = reloaded.get_strategy(strategy.strategy_id)
    finally:
        reloaded.close()

    assert loaded.mode == "live"
    assert loaded.semantic_profile == "strict_5x"


def test_sqlite_strategy_registry_persists_mtf_series_for_delegated_worker(tmp_path) -> None:
    db_path = tmp_path / "openpine.sqlite"
    registry = SQLiteStrategyRegistry(db_path=db_path)
    strategy = registry.register_strategy(
        artifact_id="artifact-1",
        symbol="BTCUSDT",
        timeframe="15m",
        params={},
        name="mtf-live-strategy",
        mode="paper",
    )
    registry.set_mtf_series(
        strategy.strategy_id,
        [
            {"symbol": "BTCUSDT", "timeframe": "1D"},
            {"symbol": "ETHUSDT", "timeframe": "4h"},
        ],
    )
    registry.close()

    reloaded = SQLiteStrategyRegistry(db_path=db_path)
    try:
        loaded = reloaded.get_strategy(strategy.strategy_id)
    finally:
        reloaded.close()

    assert loaded.mtf_series_json == (
        '[{"symbol":"BTCUSDT","timeframe":"1D"},'
        '{"symbol":"ETHUSDT","timeframe":"4h"}]'
    )
    assert loaded.to_dict()["mtf_series"] == [
        {"symbol": "BTCUSDT", "timeframe": "1D"},
        {"symbol": "ETHUSDT", "timeframe": "4h"},
    ]

