from __future__ import annotations

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

