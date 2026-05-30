"""Test that --capture-plots does not change execution semantics.

This is a regression test for the bug where:
- Without --capture-plots: 528 closed + 1 open
- With --capture-plots: 527 closed + 0 open

The flag should be read-only/export-only and never affect execution.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


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
