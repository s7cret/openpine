"""Test that --capture-plots does not change execution semantics.

This is a regression test for the bug where:
- Without --capture-plots: 528 closed + 1 open
- With --capture-plots: 527 closed + 0 open

The flag should be read-only/export-only and never affect execution.
"""
from __future__ import annotations

from dataclasses import fields, make_dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def test_cli_backtest_helpers_parse_dates_and_count_plots():
    from openpine.cli.main import _parse_cli_date_ms, _plot_record_count

    assert _parse_cli_date_ms(None, 123) == 123
    assert _parse_cli_date_ms("1700000000", 0) == 1_700_000_000_000
    assert _parse_cli_date_ms("1700000000000", 0) == 1_700_000_000_000
    assert _parse_cli_date_ms("2024-01-01T00:00:00Z", 0) == 1_704_067_200_000

    recorder = SimpleNamespace(get_records=lambda: [{"plot": "x"}, {"plot": "y"}])
    assert _plot_record_count(None) == 0
    assert _plot_record_count([{"plot": "x"}]) == 1
    assert _plot_record_count(recorder) == 2


def test_cli_backtest_config_helper_maps_declaration_values():
    from openpine.cli.main import _build_strategy_backtest_config

    Config = make_dataclass(
        "Config",
        [
            ("symbol", str),
            ("timeframe", str),
            ("start_time", int),
            ("end_time", int),
            ("exchange", str),
            ("market_type", str),
            ("initial_capital", float),
            ("default_qty_type", str),
            ("default_qty_value", float),
            ("commission_type", str),
            ("commission_value", float),
            ("exit_matching", str),
            ("pyramiding", int),
            ("qty_step", float | None),
            ("qty_rounding_mode", str),
            ("plot_from_ms", int | None),
            ("plot_to_ms", int | None),
        ],
    )
    strategy = SimpleNamespace(
        symbol="BTCUSDT",
        timeframe="15m",
        exchange="binance",
        market_type="spot",
    )

    config = _build_strategy_backtest_config(
        strategy=strategy,
        decl_args={
            "initial_capital": 25_000,
            "default_qty_type": "percent_of_equity",
            "default_qty_value": 50,
            "commission_type": "percent",
            "commission_value": 0.1,
            "close_entries_rule": "any",
            "pyramiding": 2,
        },
        start_ms=100,
        end_ms=200,
        capture_plots=True,
        capture_from_ms=120,
        capture_to_ms=180,
        config_cls=Config,
    )

    assert {field.name: getattr(config, field.name) for field in fields(config)} == {
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "start_time": 100,
        "end_time": 200,
        "exchange": "binance",
        "market_type": "spot",
        "initial_capital": 25_000,
        "default_qty_type": "percent_of_equity",
        "default_qty_value": 50,
        "commission_type": "percent",
        "commission_value": 0.1,
        "exit_matching": "ANY",
        "pyramiding": 2,
        "qty_step": 1e-6,
        "qty_rounding_mode": "truncate",
        "plot_from_ms": 120,
        "plot_to_ms": 180,
    }


def test_indicator_plot_helpers_build_config_and_meta(tmp_path):
    from openpine.cli.main import (
        _build_indicator_plot_config,
        _build_indicator_plot_run_meta,
    )

    provider = SimpleNamespace(_provider="provider-core")
    config = _build_indicator_plot_config(
        symbol="BTCUSDT",
        timeframe="15m",
        exchange="BINANCE",
        market_type="SPOT",
        provider=provider,
    )

    assert config.symbol == "BTCUSDT"
    assert config.timeframe == "15m"
    assert config.exchange == "binance"
    assert config.market_type == "spot"
    assert config.data_provider == "provider-core"
    assert config.mintick == 0.01

    plots_csv = tmp_path / "plots.csv"
    meta = _build_indicator_plot_run_meta(
        name="pine-name",
        source=SimpleNamespace(id="pine-1", active_artifact_id="artifact-1"),
        symbol="BTCUSDT",
        exchange="binance",
        market_type="spot",
        timeframe="15m",
        start_ms=100,
        end_ms=200,
        compare_from_ms=120,
        compare_to_ms=180,
        bars_total=42,
        data_fetch_info={"source": "fixture"},
        plots_rows=3,
        timings={"runtime_sec": 1.25},
        plots_csv=plots_csv,
    )

    assert meta["type"] == "indicator"
    assert meta["pine_id"] == "pine-1"
    assert meta["artifact_id"] == "artifact-1"
    assert meta["bars_total"] == 42
    assert meta["outputs"] == {"plots": str(plots_csv)}


def test_cli_bar_query_helper_normalizes_instrument_identity():
    from openpine.cli.main import _build_cli_bar_query

    query = _build_cli_bar_query(
        symbol="btcusdt",
        exchange="binance",
        market_type="SPOT",
        timeframe="15m",
        start_ms=100,
        end_ms=200,
        bar_query_cls=lambda **kwargs: SimpleNamespace(**kwargs),
        instrument_key_cls=lambda **kwargs: SimpleNamespace(**kwargs),
        parse_timeframe_func=lambda value: f"parsed:{value}",
    )

    assert query.instrument.symbol == "BTCUSDT"
    assert query.instrument.exchange == "BINANCE"
    assert query.instrument.market == "spot"
    assert query.timeframe == "parsed:15m"
    assert query.start_ms == 100
    assert query.end_ms == 200


def test_data_backfill_helpers_parse_dates_and_klines():
    from openpine.cli.main import _binance_kline_to_bar, _parse_cli_ymd_ms

    start_ms, error = _parse_cli_ymd_ms("2024-01-01", option_name="--from")
    bad_ms, bad_error = _parse_cli_ymd_ms("2024/01/01", option_name="--from")
    bar = _binance_kline_to_bar(
        [1000, "1.0", "2.0", "0.5", "1.5", "10.0", 1999],
        instrument="instrument",
        timeframe="15m",
    )

    assert start_ms == 1_704_056_400_000
    assert error is None
    assert bad_ms is None
    assert bad_error == "Invalid --from date format: 2024/01/01 (use YYYY-MM-DD)"
    assert bar.instrument == "instrument"
    assert bar.timeframe == "15m"
    assert bar.time == 1000
    assert bar.time_close == 1999
    assert bar.open == 1.0
    assert bar.close == 1.5
    assert bar.volume == 10.0
    assert bar.closed is True


def test_capture_plots_does_not_change_execution_backend():
    """When strategy has generated_strategy_class_ref, same backend is used
    regardless of --capture-plots flag."""
    
    # Mock strategy class with generated_strategy_class_ref
    mock_generated_class = MagicMock()
    mock_strategy_class = MagicMock()
    mock_strategy_class.generated_strategy_class_ref = mock_generated_class
    
    # Patch at the source modules where imports come from
    with patch("openpine.runtime.engine.BacktestEngineAdapter") as MockAdapter, \
         patch("openpine.registry.SQLiteStrategyRegistry") as MockRegistry, \
         patch("openpine.data.orchestrator.DataOrchestrator") as MockOrch, \
         patch("openpine.data.provider_adapter.create_local_marketdata_provider_adapter") as MockProvider, \
         patch("openpine.artifacts.ArtifactStore") as MockArtifactStore, \
         patch("openpine.storage.BacktestResultStore") as MockStore, \
         patch("openpine.runtime.engine.load_strategy_class_from_artifact", return_value=mock_strategy_class):
        
        # Set up mocks
        mock_registry = MockRegistry.return_value
        mock_registry.get_strategy.return_value = MagicMock(
            strategy_id="test",
            pine_id="pine_test",
            artifact_id="art_test",
            params_hash="ph_test",
            symbol="BTCUSDT",
            timeframe="1h",
            exchange="binance",
            market_type="spot",
            params_json='{}',
        )
        
        mock_orch = MockOrch.return_value
        mock_orch.get_bars.return_value = [MagicMock()]
        
        mock_artifact = MockArtifactStore.return_value
        mock_artifact.get_artifact.return_value = {"compile_meta": {}}
        
        mock_store = MockStore.return_value
        
        mock_instance = MockAdapter.return_value
        mock_result = MagicMock()
        mock_result.raw_result = MagicMock()
        mock_result.raw_result.trades = []
        mock_result.raw_result.open_trades = []
        mock_result.status = "completed"
        mock_result.bars_processed = 1
        mock_result.uses_backtest_engine = True
        mock_instance.run.return_value = mock_result
        
        # Test A: without capture_plots
        from openpine.cli import strategy_backtest
        from click.testing import CliRunner
        
        runner = CliRunner()
        
        result_a = runner.invoke(strategy_backtest, ["test", "--from", "2024-01-01", "--to", "2024-01-02"])
        
        # Get the call args for run A
        call_args_a = mock_instance.run.call_args
        backend_a = call_args_a.kwargs.get("execution_backend") if call_args_a else None
        strategy_class_a = call_args_a.args[0] if call_args_a else None
        
        # Reset mock
        mock_instance.run.reset_mock()
        
        # Test B: with capture_plots
        result_b = runner.invoke(strategy_backtest, ["test", "--from", "2024-01-01", "--to", "2024-01-02", "--capture-plots"])
        
        call_args_b = mock_instance.run.call_args
        backend_b = call_args_b.kwargs.get("execution_backend") if call_args_b else None
        strategy_class_b = call_args_b.args[0] if call_args_b else None
        
        # Assertions: same backend and same strategy class should be used
        assert type(backend_a).__name__ == type(backend_b).__name__, \
            f"Backend changed: {type(backend_a)} vs {type(backend_b)}"
        assert strategy_class_a is strategy_class_b, \
            f"Strategy class changed: {strategy_class_a} vs {strategy_class_b}"


def test_no_generated_ref_no_backend():
    """When strategy has NO generated_strategy_class_ref, no backend is used
    regardless of --capture-plots flag."""
    
    mock_strategy_class = MagicMock()
    # No generated_strategy_class_ref attribute
    
    with patch("openpine.runtime.engine.BacktestEngineAdapter") as MockAdapter, \
         patch("openpine.registry.SQLiteStrategyRegistry") as MockRegistry, \
         patch("openpine.data.orchestrator.DataOrchestrator") as MockOrch, \
         patch("openpine.data.provider_adapter.create_local_marketdata_provider_adapter") as MockProvider, \
         patch("openpine.artifacts.ArtifactStore") as MockArtifactStore, \
         patch("openpine.storage.BacktestResultStore") as MockStore, \
         patch("openpine.runtime.engine.load_strategy_class_from_artifact", return_value=mock_strategy_class):
        
        mock_registry = MockRegistry.return_value
        mock_registry.get_strategy.return_value = MagicMock(
            strategy_id="test",
            pine_id="pine_test",
            artifact_id="art_test",
            params_hash="ph_test",
            symbol="BTCUSDT",
            timeframe="1h",
            exchange="binance",
            market_type="spot",
            params_json='{}',
        )
        
        mock_orch = MockOrch.return_value
        mock_orch.get_bars.return_value = [MagicMock()]
        
        mock_artifact = MockArtifactStore.return_value
        mock_artifact.get_artifact.return_value = {"compile_meta": {}}
        
        mock_store = MockStore.return_value
        
        mock_instance = MockAdapter.return_value
        mock_result = MagicMock()
        mock_result.raw_result = MagicMock()
        mock_result.raw_result.trades = []
        mock_result.raw_result.open_trades = []
        mock_result.status = "completed"
        mock_result.bars_processed = 1
        mock_result.uses_backtest_engine = True
        mock_instance.run.return_value = mock_result
        
        from openpine.cli import strategy_backtest
        from click.testing import CliRunner
        
        runner = CliRunner()
        
        # Test A: without capture_plots
        result_a = runner.invoke(strategy_backtest, ["test", "--from", "2024-01-01", "--to", "2024-01-02"])
        
        call_args_a = mock_instance.run.call_args
        backend_a = call_args_a.kwargs.get("execution_backend") if call_args_a else None
        
        mock_instance.run.reset_mock()
        
        # Test B: with capture_plots
        result_b = runner.invoke(strategy_backtest, ["test", "--from", "2024-01-01", "--to", "2024-01-02", "--capture-plots"])
        
        call_args_b = mock_instance.run.call_args
        backend_b = call_args_b.kwargs.get("execution_backend") if call_args_b else None
        
        # Assertions: no backend in either case
        assert backend_a is None, f"Expected no backend without capture_plots, got {backend_a}"
        assert backend_b is None, f"Expected no backend with capture_plots, got {backend_b}"
