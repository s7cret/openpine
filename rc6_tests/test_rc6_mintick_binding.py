"""Admitted ticks override inference, never explicit conflicting settings."""

import pytest


@pytest.mark.parametrize("bad_tick", [None, True, "0", "-1", "NaN", "Infinity", "1e-999", "1e999"])
def test_admitted_tick_is_not_silently_repaired(bad_tick):
    from openpine.runtime.rc6_config import resolve_engine_config, serialize_engine_config

    from backtest_engine import BacktestConfig

    cfg = BacktestConfig("S", "1m", 0, 60000)
    with pytest.raises(ValueError, match="mintick"):
        resolve_engine_config(serialize_engine_config(cfg, "strict_5x"), {"mintick": bad_tick})


def test_tick_default_is_bound_to_resolved_hash_without_mutating_submission():
    from copy import deepcopy
    from openpine.runtime.rc6_config import resolve_engine_config, serialize_engine_config

    from backtest_engine import BacktestConfig

    config = BacktestConfig("S", "1m", 0, 60000)
    payload = serialize_engine_config(config, "strict_5x")
    before = deepcopy(payload)
    one = resolve_engine_config(payload, {"mintick": "0.01"})
    two = resolve_engine_config(payload, {"mintick": "0.02"})
    assert one.mintick == 0.01 and two.mintick == 0.02
    assert one.effective_config_hash != two.effective_config_hash
    assert config.mintick is None and payload == before
    assert (
        resolve_engine_config(
            serialize_engine_config(one, "strict_5x"), {"mintick": "0.010"}
        ).mintick
        == 0.01
    )
    with pytest.raises(ValueError, match="mintick"):
        resolve_engine_config(serialize_engine_config(one, "strict_5x"), {"mintick": "0.02"})
