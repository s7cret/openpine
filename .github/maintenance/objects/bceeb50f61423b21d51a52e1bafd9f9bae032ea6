"""OP-03 transport and generated-intent configuration regressions."""

import pytest

from backtest_engine import BacktestConfig
from openpine.runtime.isolated_run import _bulk_engine_config


@pytest.mark.parametrize("name", ["initial_capital", "default_qty_value", "margin_long", "margin_short"])
def test_explicit_zero_is_not_replaced_in_transport(name):
    config = BacktestConfig("SOLUSDT", "1m", 0, 60_000)
    setattr(config, name, 0)
    assert _bulk_engine_config(config, "strict_5x")[name] == 0


def test_current_engine_rounding_is_not_replaced_by_legacy_alias_default():
    config = BacktestConfig("SOLUSDT", "1m", 0, 60_000, qty_rounding="ceil")
    assert _bulk_engine_config(config, "strict_5x")["qty_rounding"] == "ceil"


def test_transport_preserves_execution_and_warmup_settings():
    config = BacktestConfig(
        "SOLUSDT", "1m", 0, 60_000, currency="USD", force_close_on_end=True,
        allow_short=False, warmup_policy="CALC_ONLY", score_end_policy="LEAVE_OPEN",
        min_pre_bars=3, max_pre_bars=20, min_qty=0.01,
    )
    payload = _bulk_engine_config(config, "strict_5x")
    for name in ("currency", "force_close_on_end", "allow_short", "warmup_policy",
                 "score_end_policy", "min_pre_bars", "max_pre_bars", "min_qty"):
        assert payload[name] == getattr(config, name)


def test_resolved_config_is_hash_bound_and_preserves_sets_and_model():
    import json
    from backtest_engine.models.instrument import InstrumentModel
    from openpine.runtime.rc6_config import resolve_engine_config
    config = BacktestConfig("SOLUSDT", "1m", 0, 60_000,
                            instrument_model=InstrumentModel(mode="spot"),
                            required_outputs={"closed_trades", "equity_curve"})
    payload = json.loads(json.dumps(_bulk_engine_config(config, "strict_5x")))
    resolved = resolve_engine_config(payload, {})
    assert resolved.instrument_model == config.instrument_model
    assert resolved.required_outputs == config.required_outputs
    assert resolved.effective_config_hash == payload["effective_config_hash"]
    payload["default_qty_value"] = 100
    with pytest.raises(ValueError, match="hash mismatch"):
        resolve_engine_config(payload, {})


def test_unsupported_zero_margin_is_rejected_not_repaired_to_100():
    from backtest_engine.errors import ConfigError
    from openpine.runtime.rc6_config import resolve_engine_config
    config = BacktestConfig("SOLUSDT", "1m", 0, 60_000, margin_long=0)
    with pytest.raises(ConfigError, match="margin_long"):
        resolve_engine_config(_bulk_engine_config(config, "strict_5x"), {})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_config_is_rejected_before_worker_spawn(value):
    config = BacktestConfig("SOLUSDT", "1m", 0, 60_000, default_qty_value=value)
    with pytest.raises(ValueError):
        _bulk_engine_config(config, "strict_5x")


def test_process_local_provider_is_not_silently_dropped():
    config = BacktestConfig("SOLUSDT", "1m", 0, 60_000, realtime_tick_provider=object())
    with pytest.raises(ValueError, match="realtime_tick_provider"):
        _bulk_engine_config(config, "strict_5x")


def test_unknown_worker_setting_is_rejected():
    from openpine.runtime.rc6_config import resolve_engine_config
    with pytest.raises(ValueError, match="unknown RC6"):
        resolve_engine_config(dict(symbol="SOLUSDT", timeframe="1m", start_time=0,
                                   end_time=60_000, pyramidng=5), {})


@pytest.mark.parametrize("kind,value,equity,expected", [
    ("fixed", 7, None, "7"), ("cash", 202, None, "2"),
    ("percent_of_equity", 20, 1010, "2"),
])
def test_generated_strategy_uses_shared_effective_sizing(kind, value, equity, expected):
    from backtest_engine.core.intent_replay import IntentReplayIdentity
    from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
    from openpine.runtime.rc6_worker_runtime import RC6GeneratedScriptSession
    from pinelib import RuntimeLanguageContext
    from pinelib.runtime.metadata import BarValues, InstrumentContext, TimeframeContext
    compiled = NativeRC6CompilerAdapter().compile(
        '//@version=6\nstrategy("sizing")\nstrategy.entry("L", strategy.long)\n',
        module_name="review_sizing", source_name="sizing.pine",
        producer_commits={"pine2ast": "a" * 40, "ast2python": "b" * 40},
    )
    assert compiled.success, compiled.errors
    config = BacktestConfig("SOLUSDT", "1m", 0, 60_000, default_qty_type=kind,
                            default_qty_value=value, commission_type="none", commission_value=0)
    session = RC6GeneratedScriptSession(
        artifact={"generated_artifact": compiled.generated_artifact, "python_code": compiled.python_code},
        language=RuntimeLanguageContext(6, "2026-09-01", "pine-v6", "sha256:" + "e" * 64, "compiler_annotation"),
        instrument=InstrumentContext(ticker="SOLUSDT", tickerid="BINANCE:SOLUSDT",
                                     prefix="BINANCE", currency="USDT", basecurrency="SOL",
                                     timezone="UTC", instrument_type="crypto", mintick=0.01),
        timeframe=TimeframeContext.parse("1"),
        identity=IntentReplayIdentity(run_id="run-sizing", strategy_id="strategy-sizing",
                                      stack_id="sha256:" + "c" * 64, semantic_profile="strict_5x",
                                      series_id="series-sizing", instrument_id="BINANCE:SOLUSDT", timeframe="1m"),
        producer_commit="d" * 40, engine_config=config,
    )
    assert session.intent_config is config
    execution = session.execute_bar(
        BarValues(open=100, high=102, low=99, close=101, volume=1000, time=0, time_close=59_999),
        bar_index=0, last_bar_index=0, strategy_values={}, broker_equity=equity,
    )
    assert len(execution.intents) == 1
    assert execution.intents[0]["qty"] == expected
