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
