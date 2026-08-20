from __future__ import annotations

from types import SimpleNamespace

import pytest
from openpine_contracts import AdmitError

from openpine.cli.runtime_helpers import (
    _build_strategy_backtest_config,
    _build_strategy_replay_config,
    _prepare_indicator_plot_inputs,
    _prepare_strategy_backtest_inputs,
    _prepare_strategy_replay_inputs,
    _run_indicator_plot_runtime,
    _run_strategy_backtest_adapter,
)
from openpine.runtime.engine import BacktestRunConfig
from openpine.runtime.isolated_run import _confirmed_htf_bars_for_timeframe


def test_cli_backtest_config_uses_strategy_semantic_profile() -> None:
    strategy = SimpleNamespace(
        symbol="BTCUSDT",
        timeframe="1m",
        exchange="binance",
        market_type="spot",
        semantic_profile="strict_5x",
    )
    config = _build_strategy_backtest_config(
        strategy=strategy,
        decl_args={},
        start_ms=0,
        end_ms=60_000,
        capture_plots=False,
        capture_from_ms=None,
        capture_to_ms=None,
        config_cls=BacktestRunConfig,
    )
    assert config.semantic_profile == "strict_5x"


def test_cli_replay_config_uses_strategy_semantic_profile() -> None:
    strategy = SimpleNamespace(
        symbol="BTCUSDT",
        timeframe="1m",
        exchange="binance",
        market_type="spot",
        semantic_profile="strict_5x",
    )
    config = _build_strategy_replay_config(
        strategy=strategy,
        decl_args={},
        start_ms=0,
        end_ms=60_000,
        config_cls=BacktestRunConfig,
    )
    assert config.semantic_profile == "strict_5x"


def test_cli_isolated_indicator_requires_semantic_profile(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(source, bars, *, semantic_profile=None, htf_bars=None):
        captured["semantic_profile"] = semantic_profile
        return SimpleNamespace(plots=())

    monkeypatch.setattr(
        "openpine.runtime.isolated_run.run_isolated_indicator", fake_run
    )
    kwargs = dict(
        generated_class=b"VALUE = 1\n",
        bars=[],
        symbol="BTCUSDT",
        timeframe="1m",
        exchange="binance",
        market_type="spot",
        provider=SimpleNamespace(),
        compare_from_ms=None,
        compare_to_ms=None,
        progress_every=1,
        console=SimpleNamespace(),
        perf_counter=lambda: 0.0,
    )
    with pytest.raises(TypeError):
        _run_indicator_plot_runtime(**kwargs)
    assert "semantic_profile" not in captured


def test_cli_isolated_indicator_forwards_admitted_profile(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(source, bars, *, semantic_profile=None, htf_bars=None):
        captured["semantic_profile"] = semantic_profile
        return SimpleNamespace(plots=())

    monkeypatch.setattr(
        "openpine.runtime.isolated_run.run_isolated_indicator", fake_run
    )
    _run_indicator_plot_runtime(
        generated_class=b"VALUE = 1\n",
        bars=[],
        symbol="BTCUSDT",
        timeframe="1m",
        exchange="binance",
        market_type="spot",
        provider=SimpleNamespace(),
        compare_from_ms=None,
        compare_to_ms=None,
        progress_every=1,
        console=SimpleNamespace(),
        perf_counter=lambda: 0.0,
        semantic_profile="strict_5x",
    )
    assert captured["semantic_profile"] == "strict_5x"


def test_cli_isolated_indicator_rejects_unknown_profile() -> None:
    with pytest.raises(AdmitError, match="unknown semantic profile"):
        _run_indicator_plot_runtime(
            generated_class=b"VALUE = 1\n",
            bars=[],
            symbol="BTCUSDT",
            timeframe="1m",
            exchange="binance",
            market_type="spot",
            provider=SimpleNamespace(),
            compare_from_ms=None,
            compare_to_ms=None,
            progress_every=1,
            console=SimpleNamespace(),
            perf_counter=lambda: 0.0,
            semantic_profile="nope",
        )


def test_cli_isolated_strategy_forwards_confirmed_htf_bars(monkeypatch) -> None:
    captured: dict[str, object] = {}
    htf_bars = [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": 0,
            "time_close": 86_399_999,
            "open": 40,
            "high": 43,
            "low": 39,
            "close": 42,
            "volume": 1,
        }
    ]

    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._prepare_strategy_backtest_runtime",
        lambda strategy_class, console: (b"STAMPED", None),
    )

    class Adapter:
        def run_isolated(self, source, bars, config, **kwargs):
            captured["htf_bars"] = kwargs.get("htf_bars")
            return SimpleNamespace(ok=True)

    result, _elapsed = _run_strategy_backtest_adapter(
        adapter_cls=Adapter,
        strategy_class=b"STAMPED",
        bars=[],
        config=object(),
        params={},
        provider=SimpleNamespace(),
        console=SimpleNamespace(),
        perf_counter=lambda: 0.0,
        htf_bars=htf_bars,
    )
    assert result.ok is True
    assert captured["htf_bars"] == htf_bars


def test_cli_isolated_indicator_forwards_confirmed_htf_bars(monkeypatch) -> None:
    captured: dict[str, object] = {}
    htf_bars = [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": 0,
            "time_close": 86_399_999,
            "open": 40,
            "high": 43,
            "low": 39,
            "close": 42,
            "volume": 1,
        }
    ]

    def fake_run(source, bars, *, semantic_profile=None, htf_bars=None):
        captured["htf_bars"] = htf_bars
        return SimpleNamespace(plots=())

    monkeypatch.setattr(
        "openpine.runtime.isolated_run.run_isolated_indicator", fake_run
    )
    _run_indicator_plot_runtime(
        generated_class=b"VALUE = 1\n",
        bars=[],
        symbol="BTCUSDT",
        timeframe="1m",
        exchange="binance",
        market_type="spot",
        provider=SimpleNamespace(),
        compare_from_ms=None,
        compare_to_ms=None,
        progress_every=1,
        console=SimpleNamespace(),
        perf_counter=lambda: 0.0,
        semantic_profile="strict_5x",
        htf_bars=htf_bars,
    )
    assert captured["htf_bars"] == htf_bars


def test_cli_strategy_replay_forwards_confirmed_htf_bars(monkeypatch) -> None:
    import importlib

    from click.testing import CliRunner

    cli_main = importlib.import_module("openpine.cli.main")

    captured: dict[str, object] = {}
    htf_bars = [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": 0,
            "time_close": 86_399_999,
            "open": 40,
            "high": 43,
            "low": 39,
            "close": 42,
            "volume": 1,
        }
    ]

    class Registry:
        def get_strategy(self, strategy_id):
            return SimpleNamespace(strategy_id=strategy_id)

        def update_status(self, strategy_id, status):
            return None

        def close(self):
            return None

    class Adapter:
        def run_isolated(self, source, bars, config, **kwargs):
            captured["htf_bars"] = kwargs.get("htf_bars")
            return SimpleNamespace(status="ok", bars_processed=1, uses_backtest_engine=True)

    monkeypatch.setattr("openpine.registry.SQLiteStrategyRegistry", Registry)
    monkeypatch.setattr(cli_main, "_get_strategy_or_exit", lambda **kwargs: SimpleNamespace())
    monkeypatch.setattr(cli_main, "_print_strategy_command_header", lambda **kwargs: None)
    monkeypatch.setattr(cli_main, "_strategy_backtest_readiness_error", lambda strategy: None)
    monkeypatch.setattr(
        cli_main,
        "_prepare_strategy_replay_inputs",
        lambda **kwargs: SimpleNamespace(
            strategy_class=b"STAMPED",
            bars=[],
            config=object(),
            htf_bars=htf_bars,
        ),
    )
    monkeypatch.setattr("openpine.runtime.engine.BacktestEngineAdapter", Adapter)

    result = CliRunner().invoke(cli_main.cli, ["strategy", "replay", "s1"])
    assert result.exit_code == 0, result.output
    assert captured["htf_bars"] == htf_bars


def test_prepare_strategy_replay_stamps_confirmed_provider_htf_bars(monkeypatch) -> None:
    bars = [
        SimpleNamespace(
            time=0,
            time_close=59_999,
            open=1,
            high=2,
            low=0.5,
            close=1.5,
            volume=3,
        )
    ]
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._parse_strategy_backtest_window",
        lambda **kwargs: (0, 60_000, None, None),
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._load_strategy_backtest_class",
        lambda **kwargs: (b"STAMPED", 0.0),
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._load_strategy_backtest_bars",
        lambda **kwargs: (bars, 0.0, None, None),
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._exit_if_no_strategy_bars",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._strategy_backtest_declaration_args",
        lambda **kwargs: {},
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._build_strategy_replay_config",
        lambda **kwargs: object(),
    )
    prepared = _prepare_strategy_replay_inputs(
        strategy=SimpleNamespace(symbol="BTCUSDT", timeframe="1m", params_json="{}"),
        strategy_id="s1",
        from_date=None,
        to_date=None,
        now_ms=0,
        registry=SimpleNamespace(),
        load_strategy_class=None,
        artifact_error_cls=Exception,
        artifact_store_cls=None,
        bar_query_cls=None,
        instrument_key_cls=None,
        parse_timeframe_func=None,
        orchestrator_cls=None,
        config_cls=None,
        perf_counter=lambda: 0.0,
        console=SimpleNamespace(),
    )
    assert prepared.htf_bars == [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "time": 0,
            "time_close": 59_999,
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 3.0,
        }
    ]


def test_prepare_strategy_replay_does_not_invent_time_close(monkeypatch) -> None:
    bars = [SimpleNamespace(time=1, open=1, high=1, low=1, close=1, volume=1)]
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._parse_strategy_backtest_window",
        lambda **kwargs: (0, 60_000, None, None),
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._load_strategy_backtest_class",
        lambda **kwargs: (b"STAMPED", 0.0),
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._load_strategy_backtest_bars",
        lambda **kwargs: (bars, 0.0, None, None),
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._exit_if_no_strategy_bars",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._strategy_backtest_declaration_args",
        lambda **kwargs: {},
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._build_strategy_replay_config",
        lambda **kwargs: object(),
    )
    prepared = _prepare_strategy_replay_inputs(
        strategy=SimpleNamespace(symbol="BTCUSDT", timeframe="1m", params_json="{}"),
        strategy_id="s1",
        from_date=None,
        to_date=None,
        now_ms=0,
        registry=SimpleNamespace(),
        load_strategy_class=None,
        artifact_error_cls=Exception,
        artifact_store_cls=None,
        bar_query_cls=None,
        instrument_key_cls=None,
        parse_timeframe_func=None,
        orchestrator_cls=None,
        config_cls=None,
        perf_counter=lambda: 0.0,
        console=SimpleNamespace(),
    )
    assert prepared.htf_bars is None


def test_prepare_strategy_replay_fetches_explicit_htf_timeframe(monkeypatch) -> None:
    chart = [
        SimpleNamespace(
            time=0,
            time_close=59_999,
            open=1,
            high=2,
            low=0.5,
            close=1.5,
            volume=3,
        )
    ]
    fetched = [
        SimpleNamespace(
            time=0,
            time_close=86_399_999,
            open=40,
            high=43,
            low=39,
            close=42,
            volume=1,
        )
    ]
    loaded: list[str] = []

    def load_bars(**kwargs):
        timeframe = str(kwargs["strategy"].timeframe)
        loaded.append(timeframe)
        bars = fetched if timeframe == "1D" else chart
        return bars, None, None, 0.0

    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._parse_strategy_backtest_window",
        lambda **kwargs: (0, 60_000, None, None),
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._load_strategy_backtest_class",
        lambda **kwargs: (b"STAMPED", 0.0),
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._load_strategy_backtest_bars",
        load_bars,
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._exit_if_no_strategy_bars",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._strategy_backtest_declaration_args",
        lambda **kwargs: {},
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._build_strategy_replay_config",
        lambda **kwargs: object(),
    )
    prepared = _prepare_strategy_replay_inputs(
        strategy=SimpleNamespace(
            symbol="BTCUSDT",
            timeframe="1m",
            params_json="{}",
            exchange="binance",
            market_type="spot",
        ),
        strategy_id="s1",
        from_date=None,
        to_date=None,
        now_ms=0,
        registry=SimpleNamespace(),
        load_strategy_class=None,
        artifact_error_cls=Exception,
        artifact_store_cls=None,
        bar_query_cls=None,
        instrument_key_cls=None,
        parse_timeframe_func=None,
        orchestrator_cls=None,
        config_cls=None,
        perf_counter=lambda: 0.0,
        console=SimpleNamespace(),
        htf_timeframe="1D",
    )
    assert loaded == ["1m", "1D"]
    assert prepared.htf_bars == [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": 0,
            "time_close": 86_399_999,
            "open": 40.0,
            "high": 43.0,
            "low": 39.0,
            "close": 42.0,
            "volume": 1.0,
        }
    ]


def test_prepare_strategy_replay_same_htf_timeframe_does_not_refetch(monkeypatch) -> None:
    bars = [
        SimpleNamespace(
            time=0,
            time_close=59_999,
            open=1,
            high=2,
            low=0.5,
            close=1.5,
            volume=3,
        )
    ]
    loaded: list[str] = []

    def load_bars(**kwargs):
        loaded.append(str(kwargs["strategy"].timeframe))
        return bars, None, None, 0.0

    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._parse_strategy_backtest_window",
        lambda **kwargs: (0, 60_000, None, None),
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._load_strategy_backtest_class",
        lambda **kwargs: (b"STAMPED", 0.0),
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._load_strategy_backtest_bars",
        load_bars,
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._exit_if_no_strategy_bars",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._strategy_backtest_declaration_args",
        lambda **kwargs: {},
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._build_strategy_replay_config",
        lambda **kwargs: object(),
    )
    prepared = _prepare_strategy_replay_inputs(
        strategy=SimpleNamespace(symbol="BTCUSDT", timeframe="1m", params_json="{}"),
        strategy_id="s1",
        from_date=None,
        to_date=None,
        now_ms=0,
        registry=SimpleNamespace(),
        load_strategy_class=None,
        artifact_error_cls=Exception,
        artifact_store_cls=None,
        bar_query_cls=None,
        instrument_key_cls=None,
        parse_timeframe_func=None,
        orchestrator_cls=None,
        config_cls=None,
        perf_counter=lambda: 0.0,
        console=SimpleNamespace(),
        htf_timeframe="1m",
    )
    assert loaded == ["1m"]
    assert prepared.htf_bars[0]["timeframe"] == "1m"


def test_cli_replay_help_lists_htf_timeframe() -> None:
    import importlib

    from click.testing import CliRunner

    cli_main = importlib.import_module("openpine.cli.main")
    result = CliRunner().invoke(cli_main.cli, ["strategy", "replay", "--help"])
    assert result.exit_code == 0, result.output
    assert "--htf-timeframe" in result.output
    assert "--mtf-series" in result.output


def _backtest_prepare_deps() -> SimpleNamespace:
    return SimpleNamespace(
        capture_generated_source=object(),
        BacktestArtifactError=Exception,
        BarQuery=object,
        InstrumentKey=object,
        parse_timeframe=object(),
        DataOrchestrator=object,
        create_local_marketdata_provider_adapter=object(),
        ArtifactStore=object,
        BacktestRunConfig=object,
    )


def _patch_backtest_prepare(monkeypatch, bars) -> None:
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._parse_valid_strategy_backtest_window",
        lambda **kwargs: (0, 60_000, None, None),
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._load_strategy_backtest_class_or_exit",
        lambda **kwargs: (b"STAMPED", 0.0),
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._load_strategy_backtest_bars",
        lambda **kwargs: (bars, SimpleNamespace(), None, 0.0),
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._exit_if_no_strategy_bars",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._strategy_backtest_declaration_args",
        lambda **kwargs: {},
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._build_strategy_backtest_params_and_config",
        lambda **kwargs: ({}, object()),
    )


def test_prepare_strategy_backtest_stamps_confirmed_provider_htf_bars(monkeypatch) -> None:
    bars = [
        SimpleNamespace(
            time=0,
            time_close=59_999,
            open=1,
            high=2,
            low=0.5,
            close=1.5,
            volume=3,
        )
    ]
    _patch_backtest_prepare(monkeypatch, bars)
    prepared = _prepare_strategy_backtest_inputs(
        strategy=SimpleNamespace(symbol="BTCUSDT", timeframe="1m", params_json="{}"),
        strategy_id="s1",
        from_date=None,
        to_date=None,
        capture_plots=False,
        capture_from=None,
        capture_to=None,
        history_from=None,
        warmup_bars=0,
        gap_policy="fail",
        now_ms=0,
        registry=SimpleNamespace(),
        deps=_backtest_prepare_deps(),
        perf_counter=lambda: 0.0,
        console=SimpleNamespace(),
    )
    assert prepared.htf_bars == [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "time": 0,
            "time_close": 59_999,
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 3.0,
        }
    ]


def test_prepare_strategy_backtest_does_not_invent_time_close(monkeypatch) -> None:
    _patch_backtest_prepare(
        monkeypatch,
        [SimpleNamespace(time=1, open=1, high=1, low=1, close=1, volume=1)],
    )
    prepared = _prepare_strategy_backtest_inputs(
        strategy=SimpleNamespace(symbol="BTCUSDT", timeframe="1m", params_json="{}"),
        strategy_id="s1",
        from_date=None,
        to_date=None,
        capture_plots=False,
        capture_from=None,
        capture_to=None,
        history_from=None,
        warmup_bars=0,
        gap_policy="fail",
        now_ms=0,
        registry=SimpleNamespace(),
        deps=_backtest_prepare_deps(),
        perf_counter=lambda: 0.0,
        console=SimpleNamespace(),
    )
    assert prepared.htf_bars is None


def _patch_indicator_prepare(monkeypatch, bars) -> None:
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._load_pine_source_or_exit",
        lambda **kwargs: SimpleNamespace(id="pine"),
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._require_active_pine_artifact",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._parse_indicator_plot_window",
        lambda **kwargs: (0, 60_000, None, None),
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._load_generated_class_timed",
        lambda **kwargs: (b"STAMPED", 0.0),
    )
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._load_indicator_plot_bars",
        lambda **kwargs: (bars, SimpleNamespace(), None, 0.0),
    )


def test_prepare_indicator_plot_stamps_confirmed_provider_htf_bars(monkeypatch) -> None:
    bars = [
        SimpleNamespace(
            time=0,
            time_close=59_999,
            open=1,
            high=2,
            low=0.5,
            close=1.5,
            volume=3,
        )
    ]
    _patch_indicator_prepare(monkeypatch, bars)
    prepared = _prepare_indicator_plot_inputs(
        name="pine",
        symbol="BTCUSDT",
        timeframe="1m",
        exchange="binance",
        market_type="spot",
        from_date="1",
        to_date="2",
        compare_from=None,
        compare_to=None,
        now_ms=0,
        registry_cls=object,
        parse_time_ms_func=int,
        load_generated_class=object(),
        artifact_error_cls=Exception,
        bar_query_cls=object,
        instrument_key_cls=object,
        parse_timeframe_func=object(),
        orchestrator_cls=object,
        provider_factory=object(),
        perf_counter=lambda: 0.0,
        console=SimpleNamespace(),
    )
    assert prepared.htf_bars == [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "time": 0,
            "time_close": 59_999,
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 3.0,
        }
    ]


def test_prepare_indicator_plot_does_not_invent_time_close(monkeypatch) -> None:
    _patch_indicator_prepare(
        monkeypatch,
        [SimpleNamespace(time=1, open=1, high=1, low=1, close=1, volume=1)],
    )
    prepared = _prepare_indicator_plot_inputs(
        name="pine",
        symbol="BTCUSDT",
        timeframe="1m",
        exchange="binance",
        market_type="spot",
        from_date="1",
        to_date="2",
        compare_from=None,
        compare_to=None,
        now_ms=0,
        registry_cls=object,
        parse_time_ms_func=int,
        load_generated_class=object(),
        artifact_error_cls=Exception,
        bar_query_cls=object,
        instrument_key_cls=object,
        parse_timeframe_func=object(),
        orchestrator_cls=object,
        provider_factory=object(),
        perf_counter=lambda: 0.0,
        console=SimpleNamespace(),
    )
    assert prepared.htf_bars is None


def test_prepare_indicator_plot_fetches_explicit_htf_timeframe(monkeypatch) -> None:
    chart = [
        SimpleNamespace(
            time=0,
            time_close=59_999,
            open=1,
            high=2,
            low=0.5,
            close=1.5,
            volume=3,
        )
    ]
    fetched = [
        SimpleNamespace(
            time=0,
            time_close=86_399_999,
            open=40,
            high=43,
            low=39,
            close=42,
            volume=1,
        )
    ]
    loaded: list[str] = []

    def load_bars(**kwargs):
        timeframe = str(kwargs["timeframe"])
        loaded.append(timeframe)
        bars = fetched if timeframe == "1D" else chart
        return bars, SimpleNamespace(), None, 0.0

    _patch_indicator_prepare(monkeypatch, chart)
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._load_indicator_plot_bars",
        load_bars,
    )
    prepared = _prepare_indicator_plot_inputs(
        name="pine",
        symbol="BTCUSDT",
        timeframe="1m",
        exchange="binance",
        market_type="spot",
        from_date="1",
        to_date="2",
        compare_from=None,
        compare_to=None,
        now_ms=0,
        registry_cls=object,
        parse_time_ms_func=int,
        load_generated_class=object(),
        artifact_error_cls=Exception,
        bar_query_cls=object,
        instrument_key_cls=object,
        parse_timeframe_func=object(),
        orchestrator_cls=object,
        provider_factory=object(),
        perf_counter=lambda: 0.0,
        console=SimpleNamespace(),
        htf_timeframe="1D",
    )
    assert loaded == ["1m", "1D"]
    assert prepared.htf_bars == [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": 0,
            "time_close": 86_399_999,
            "open": 40.0,
            "high": 43.0,
            "low": 39.0,
            "close": 42.0,
            "volume": 1.0,
        }
    ]


def test_prepare_indicator_plot_same_htf_timeframe_does_not_refetch(monkeypatch) -> None:
    bars = [
        SimpleNamespace(
            time=0,
            time_close=59_999,
            open=1,
            high=2,
            low=0.5,
            close=1.5,
            volume=3,
        )
    ]
    loaded: list[str] = []

    def load_bars(**kwargs):
        loaded.append(str(kwargs["timeframe"]))
        return bars, SimpleNamespace(), None, 0.0

    _patch_indicator_prepare(monkeypatch, bars)
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._load_indicator_plot_bars",
        load_bars,
    )
    prepared = _prepare_indicator_plot_inputs(
        name="pine",
        symbol="BTCUSDT",
        timeframe="1m",
        exchange="binance",
        market_type="spot",
        from_date="1",
        to_date="2",
        compare_from=None,
        compare_to=None,
        now_ms=0,
        registry_cls=object,
        parse_time_ms_func=int,
        load_generated_class=object(),
        artifact_error_cls=Exception,
        bar_query_cls=object,
        instrument_key_cls=object,
        parse_timeframe_func=object(),
        orchestrator_cls=object,
        provider_factory=object(),
        perf_counter=lambda: 0.0,
        console=SimpleNamespace(),
        htf_timeframe="1m",
    )
    assert loaded == ["1m"]
    assert prepared.htf_bars[0]["timeframe"] == "1m"


def test_cli_run_plots_help_lists_htf_timeframe() -> None:
    import importlib

    from click.testing import CliRunner

    cli_main = importlib.import_module("openpine.cli.main")
    result = CliRunner().invoke(cli_main.cli, ["pine", "run-plots", "--help"])
    assert result.exit_code == 0, result.output
    assert "--htf-timeframe" in result.output
    assert "--mtf-series" in result.output


def test_confirmed_htf_selector_uses_fetched_series_for_other_timeframe() -> None:
    chart = [SimpleNamespace(time=0, time_close=59_999, open=1, high=1, low=1, close=1, volume=1)]
    fetched = [
        SimpleNamespace(
            time=0,
            time_close=86_399_999,
            open=40,
            high=43,
            low=39,
            close=42,
            volume=1,
        )
    ]
    stamped = _confirmed_htf_bars_for_timeframe(
        chart_bars=chart,
        symbol="BTCUSDT",
        chart_timeframe="1m",
        requested_timeframe="1D",
        fetched_htf_bars=fetched,
    )
    assert stamped == [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": 0,
            "time_close": 86_399_999,
            "open": 40.0,
            "high": 43.0,
            "low": 39.0,
            "close": 42.0,
            "volume": 1.0,
        }
    ]


def test_confirmed_htf_selector_does_not_invent_unconfirmed_htf() -> None:
    chart = [SimpleNamespace(time=0, time_close=59_999, open=1, high=1, low=1, close=1, volume=1)]
    stamped = _confirmed_htf_bars_for_timeframe(
        chart_bars=chart,
        symbol="BTCUSDT",
        chart_timeframe="1m",
        requested_timeframe="1D",
        fetched_htf_bars=[SimpleNamespace(time=0, open=40, high=43, low=39, close=42, volume=1)],
    )
    assert stamped is None


def test_prepare_strategy_backtest_fetches_explicit_htf_timeframe(monkeypatch) -> None:
    chart = [
        SimpleNamespace(
            time=0,
            time_close=59_999,
            open=1,
            high=2,
            low=0.5,
            close=1.5,
            volume=3,
        )
    ]
    fetched = [
        SimpleNamespace(
            time=0,
            time_close=86_399_999,
            open=40,
            high=43,
            low=39,
            close=42,
            volume=1,
        )
    ]
    loaded: list[str] = []

    def load_bars(**kwargs):
        timeframe = str(kwargs["strategy"].timeframe)
        loaded.append(timeframe)
        bars = fetched if timeframe == "1D" else chart
        return bars, SimpleNamespace(), None, 0.0

    _patch_backtest_prepare(monkeypatch, chart)
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._load_strategy_backtest_bars",
        load_bars,
    )
    prepared = _prepare_strategy_backtest_inputs(
        strategy=SimpleNamespace(
            symbol="BTCUSDT",
            timeframe="1m",
            params_json="{}",
            exchange="binance",
            market_type="spot",
        ),
        strategy_id="s1",
        from_date=None,
        to_date=None,
        capture_plots=False,
        capture_from=None,
        capture_to=None,
        history_from=None,
        warmup_bars=0,
        gap_policy="fail",
        now_ms=0,
        registry=SimpleNamespace(),
        deps=_backtest_prepare_deps(),
        perf_counter=lambda: 0.0,
        console=SimpleNamespace(),
        htf_timeframe="1D",
    )
    assert loaded == ["1m", "1D"]
    assert prepared.htf_bars == [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": 0,
            "time_close": 86_399_999,
            "open": 40.0,
            "high": 43.0,
            "low": 39.0,
            "close": 42.0,
            "volume": 1.0,
        }
    ]


def test_prepare_strategy_backtest_fetches_two_mtf_series(monkeypatch) -> None:
    chart = [
        SimpleNamespace(
            time=0,
            time_close=59_999,
            open=1,
            high=2,
            low=0.5,
            close=1.5,
            volume=3,
        )
    ]
    loaded: list[tuple[str, str]] = []

    def load_bars(**kwargs):
        strategy = kwargs["strategy"]
        key = (str(strategy.symbol), str(strategy.timeframe))
        loaded.append(key)
        if key == ("BTCUSDT", "1m"):
            return chart, SimpleNamespace(), None, 0.0
        duration = 86_400_000 if key[1] == "1D" else 14_400_000
        return [
            SimpleNamespace(
                time=0,
                time_close=duration - 1,
                open=2,
                high=3,
                low=1,
                close=2,
                volume=1,
            )
        ], SimpleNamespace(), None, 0.0

    _patch_backtest_prepare(monkeypatch, chart)
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._load_strategy_backtest_bars",
        load_bars,
    )
    prepared = _prepare_strategy_backtest_inputs(
        strategy=SimpleNamespace(
            symbol="BTCUSDT",
            timeframe="1m",
            params_json="{}",
            exchange="binance",
            market_type="spot",
        ),
        strategy_id="s1",
        from_date=None,
        to_date=None,
        capture_plots=False,
        capture_from=None,
        capture_to=None,
        history_from=None,
        warmup_bars=0,
        gap_policy="fail",
        now_ms=0,
        registry=SimpleNamespace(),
        deps=_backtest_prepare_deps(),
        perf_counter=lambda: 0.0,
        console=SimpleNamespace(),
        mtf_series=("BTCUSDT:1D", "ETHUSDT:4h"),
    )

    assert loaded == [
        ("BTCUSDT", "1m"),
        ("BTCUSDT", "1D"),
        ("ETHUSDT", "4h"),
    ]
    assert {(item["symbol"], item["timeframe"]) for item in prepared.htf_bars} == {
        ("BTCUSDT", "1D"),
        ("ETHUSDT", "4h"),
    }


def test_prepare_strategy_backtest_same_htf_timeframe_does_not_refetch(monkeypatch) -> None:
    bars = [
        SimpleNamespace(
            time=0,
            time_close=59_999,
            open=1,
            high=2,
            low=0.5,
            close=1.5,
            volume=3,
        )
    ]
    loaded: list[str] = []

    def load_bars(**kwargs):
        loaded.append(str(kwargs["strategy"].timeframe))
        return bars, SimpleNamespace(), None, 0.0

    _patch_backtest_prepare(monkeypatch, bars)
    monkeypatch.setattr(
        "openpine.cli.runtime_helpers._load_strategy_backtest_bars",
        load_bars,
    )
    prepared = _prepare_strategy_backtest_inputs(
        strategy=SimpleNamespace(symbol="BTCUSDT", timeframe="1m", params_json="{}"),
        strategy_id="s1",
        from_date=None,
        to_date=None,
        capture_plots=False,
        capture_from=None,
        capture_to=None,
        history_from=None,
        warmup_bars=0,
        gap_policy="fail",
        now_ms=0,
        registry=SimpleNamespace(),
        deps=_backtest_prepare_deps(),
        perf_counter=lambda: 0.0,
        console=SimpleNamespace(),
        htf_timeframe="1m",
    )
    assert loaded == ["1m"]
    assert prepared.htf_bars[0]["timeframe"] == "1m"


def test_cli_backtest_help_lists_htf_timeframe() -> None:
    import importlib

    from click.testing import CliRunner

    cli_main = importlib.import_module("openpine.cli.main")
    result = CliRunner().invoke(cli_main.cli, ["strategy", "backtest", "--help"])
    assert result.exit_code == 0, result.output
    assert "--htf-timeframe" in result.output
    assert "--mtf-series" in result.output
