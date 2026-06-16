"""Test that --capture-plots does not change execution semantics.

This is a regression test for the bug where:
- Without --capture-plots: 528 closed + 1 open
- With --capture-plots: 527 closed + 0 open

The flag should be read-only/export-only and never affect execution.
"""

from __future__ import annotations

from dataclasses import fields, make_dataclass
from types import SimpleNamespace
from typing import Any, cast
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
            ("slippage", float),
            ("slippage_type", str),
            ("exit_matching", str),
            ("pyramiding", int),
            ("margin_long", float),
            ("margin_short", float),
            ("process_orders_on_close", bool),
            ("calc_on_order_fills", bool),
            ("calc_on_every_tick", bool),
            ("use_bar_magnifier", bool),
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
            "slippage": 2,
            "slippage_type": "percent",
            "close_entries_rule": "any",
            "pyramiding": 2,
            "margin_long": 50,
            "margin_short": 60,
            "process_orders_on_close": True,
            "calc_on_order_fills": True,
            "calc_on_every_tick": True,
            "use_bar_magnifier": True,
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
        "slippage": 2,
        "slippage_type": "percent",
        "exit_matching": "ANY",
        "pyramiding": 2,
        "margin_long": 50,
        "margin_short": 60,
        "process_orders_on_close": True,
        "calc_on_order_fills": True,
        "calc_on_every_tick": True,
        "use_bar_magnifier": True,
        "qty_step": 1e-5,
        "qty_rounding_mode": "truncate",
        "plot_from_ms": 120,
        "plot_to_ms": 180,
    }


def test_cli_backtest_config_maps_tradingview_cash_commission_names():
    from openpine.cli.main import _build_strategy_backtest_config

    Config = make_dataclass(
        "Config",
        [
            ("symbol", str),
            ("timeframe", str),
            ("start_time", int),
            ("end_time", int),
            ("commission_type", str),
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
        decl_args={"commission_type": "cash_per_contract"},
        start_ms=100,
        end_ms=200,
        capture_plots=False,
        capture_from_ms=None,
        capture_to_ms=None,
        config_cls=Config,
    )

    assert config.commission_type == "fixed_per_contract"


def test_compare_rows_by_time_reports_value_mismatch(tmp_path):
    from openpine.cli.main import _compare_rows_by_time

    tv = tmp_path / "tv.csv"
    op = tmp_path / "op.csv"
    tv.write_text("time,open,PLOT\n1000,1,10\n2000,2,20\n", encoding="utf-8")
    op.write_text("bar_time,bar_index,PLOT\n1000,0,10\n2000,1,21\n", encoding="utf-8")

    summary, top_columns = _compare_rows_by_time(
        tv_path=tv,
        op_path=op,
        tv_time_column="time",
        op_time_column="bar_time",
        exclude_columns={"time", "bar_time", "bar_index", "open"},
        abs_tol=1e-6,
        rel_tol=1e-9,
    )

    assert summary["status"] == "mismatch"
    assert summary["classification"] == "value_mismatch"
    assert summary["common_times"] == 2
    assert summary["mismatch_cells"] == 1
    assert summary["worst_column"] == "PLOT"
    assert top_columns[0]["column"] == "PLOT"


def test_strategy_tv_chart_compare_filters_to_compare_window(tmp_path):
    from openpine.cli.main import _compare_strategy_run_with_tv_exports

    tv = tmp_path / "tv_chart.csv"
    op = tmp_path / "openpine_plots.csv"
    t0 = 1_704_067_200_000
    t1 = 1_704_153_600_000
    t2 = 1_704_240_000_000
    tv.write_text(
        f"time,open,PLOT\n{t0},1,10\n{t1},2,20\n{t2},3,30\n",
        encoding="utf-8",
    )
    op.write_text(f"bar_time,bar_index,PLOT\n{t1},0,20\n", encoding="utf-8")

    comparison = cast(
        dict[str, Any],
        _compare_strategy_run_with_tv_exports(
            strategy_id="strategy-1",
            run=SimpleNamespace(run_id="run-1"),
            exported={"plots": str(op)},
            output_path=tmp_path / "comparison",
            tv_chart=str(tv),
            tv_trades=None,
            tv_equity=None,
            abs_tol=1e-6,
            rel_tol=1e-9,
            include_base_columns=False,
            compare_from_ms=t1,
            compare_to_ms=t2,
        ),
    )

    comparisons = cast(list[dict[str, Any]], comparison["comparisons"])
    summary = comparisons[0]
    assert summary["type"] == "plots"
    assert summary["status"] == "match"
    assert summary["classification"] == "match"
    assert summary["tv_rows"] == 1
    assert summary["openpine_rows"] == 1
    assert summary["common_times"] == 1
    assert summary["missing_times_in_openpine"] == 0


def test_normalized_tv_trades_supports_localized_headers(tmp_path):
    from openpine.cli import compare as cli_compare
    from openpine.cli.main import _write_normalized_tv_trades

    assert cli_compare._compare_csv_time_ms("2026-04-01") == 1775001600000
    assert cli_compare._compare_csv_time_ms("2026-04-01 10:00") == 1775026800000

    tv = tmp_path / "tv_trades.csv"
    normalized = tmp_path / "normalized.csv"
    tv.write_text(
        "\n".join(
            [
                "Номер сделки,Тип,Дата и время,Сигнал,Цена USDT,Размер (кол-во),Чистая ПР/УБ USDT,Благоприятное отклонение USDT,Неблагоприятное отклонение USDT",
                "1,Выход из длинной позиции,2026-04-01 12:00,XL,110,0.5,5.5,8,-1",
                "1,Вход в длинную позицию,2026-04-01 10:00,L,100,0.5,5.5,8,-1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    _write_normalized_tv_trades(
        tv_path=tv,
        output_path=normalized,
        compare_from_ms=None,
        compare_to_ms=None,
    )

    text = normalized.read_text(encoding="utf-8")
    assert "trade_id,status,direction,entry_time_ms,exit_time_ms" in text
    assert "1,closed,long,1775026800000,1775034000000,100,110,0.5,5.5,8,-1,L,XL" in text


def test_cli_backtest_runtime_meta_and_progress_helpers():
    from openpine.cli.main import (
        _build_progress_callback,
        _build_strategy_backtest_run_meta,
        _print_strategy_plot_capture_status,
    )

    strategy = SimpleNamespace(
        strategy_id="strategy-1",
        name="Test Strategy",
        pine_id="pine-1",
        artifact_id="artifact-1",
        symbol="BTCUSDT",
        timeframe="15m",
    )
    raw_result = SimpleNamespace(
        trades=[{"id": 1}],
        open_trades=[{"id": 2}],
        plots=SimpleNamespace(get_records=lambda: [{"plot": "a"}, {"plot": "b"}]),
    )
    result = SimpleNamespace(
        raw_result=raw_result,
        bars_processed=99,
        process_next_bar_available=True,
    )
    meta = _build_strategy_backtest_run_meta(
        strategy=strategy,
        start_ms=100,
        end_ms=200,
        bars_total=10,
        data_fetch_info={"provider": "local"},
        result=result,
        capture_plots=True,
        timings={"backtest_sec": 1.5},
    )

    assert meta["strategy_id"] == "strategy-1"
    assert meta["bars_processed"] == 99
    assert meta["trades_rows"] == 1
    assert meta["open_trades"] == 1
    assert meta["plots_records"] == 2

    messages: list[tuple] = []
    console = SimpleNamespace(print=lambda *args, **kwargs: messages.append(args))
    progress = _build_progress_callback(bars_total=100, console=console)
    progress(1, 100)
    progress(5, 100)
    progress(100, 100)
    _print_strategy_plot_capture_status(
        raw_result=raw_result,
        capture_plots=True,
        console=console,
    )

    assert "[dim]runtime: 5/100 bars[/dim]" in messages[0][0]
    assert "[dim]runtime: 100/100 bars[/dim]" in messages[1][0]
    assert "2 plot records captured" in messages[-1][0]


def test_strategy_backtest_readiness_window_and_load_helpers():
    from openpine.cli.main import (
        _load_strategy_backtest_class,
        _parse_strategy_backtest_window,
        _strategy_backtest_readiness_error,
    )

    ready = SimpleNamespace(
        pine_id="pine-1",
        artifact_id="artifact-1",
        symbol="BTCUSDT",
        timeframe="15m",
    )
    missing_pine = SimpleNamespace(pine_id="", artifact_id="artifact-1")
    missing_artifact = SimpleNamespace(pine_id="pine-1", artifact_id="")

    assert _strategy_backtest_readiness_error(ready) is None
    assert "no pine_id" in _strategy_backtest_readiness_error(missing_pine)
    assert "no compiled artifact" in _strategy_backtest_readiness_error(
        missing_artifact
    )

    window = _parse_strategy_backtest_window(
        from_date="1700000000",
        to_date=None,
        capture_from="1700000010",
        capture_to=None,
        now_ms=1_700_000_100_000,
    )
    assert window == (1_700_000_000_000, 1_700_000_100_000, 1_700_000_010_000, None)

    strategy_class, elapsed = _load_strategy_backtest_class(
        strategy=ready,
        load_strategy_class=lambda pine_id, artifact_id, **kwargs: {
            "pine": pine_id,
            "artifact": artifact_id,
            **kwargs,
        },
        perf_counter=iter([10.0, 12.5]).__next__,
    )
    assert strategy_class == {
        "pine": "pine-1",
        "artifact": "artifact-1",
        "symbol": "BTCUSDT",
        "timeframe": "15m",
    }
    assert elapsed == 2.5


def test_strategy_backtest_adapter_helper_runs_selected_backend():
    from openpine.cli.main import _run_strategy_backtest_adapter

    captured = {}

    class FakeAdapter:
        def run(self, strategy_class, bars, config, **kwargs):
            captured["strategy_class"] = strategy_class
            captured["bars"] = bars
            captured["config"] = config
            captured["kwargs"] = kwargs
            return "result"

    messages: list[tuple] = []
    result, elapsed = _run_strategy_backtest_adapter(
        adapter_cls=FakeAdapter,
        strategy_class=SimpleNamespace(),
        bars=[1, 2],
        config="config",
        params={"p": 1},
        provider=SimpleNamespace(_provider="provider-core"),
        console=SimpleNamespace(print=lambda *args, **kwargs: messages.append(args)),
        perf_counter=iter([20.0, 22.25]).__next__,
    )

    assert result == "result"
    assert elapsed == 2.25
    assert captured["bars"] == [1, 2]
    assert captured["config"] == "config"
    assert captured["kwargs"]["params"] == {"p": 1}
    assert captured["kwargs"]["runtime_data_provider"] == "provider-core"


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


def test_indicator_plot_window_helper_uses_now_default():
    from openpine.cli.main import _parse_indicator_plot_window

    values = {
        "from": 100,
        "compare-from": 120,
        "compare-to": 180,
    }
    start_ms, end_ms, compare_from_ms, compare_to_ms = _parse_indicator_plot_window(
        from_date="from",
        to_date=None,
        compare_from="compare-from",
        compare_to="compare-to",
        parse_time_ms_func=lambda value: values.get(value),
        now_ms=200,
    )

    assert (start_ms, end_ms, compare_from_ms, compare_to_ms) == (100, 200, 120, 180)


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
    assert query.gap_policy == "fail"

    allow_query = _build_cli_bar_query(
        symbol="btcusdt",
        exchange="binance",
        market_type="SPOT",
        timeframe="15m",
        start_ms=100,
        end_ms=200,
        bar_query_cls=lambda **kwargs: SimpleNamespace(**kwargs),
        instrument_key_cls=lambda **kwargs: SimpleNamespace(**kwargs),
        parse_timeframe_func=lambda value: f"parsed:{value}",
        gap_policy="allow_with_metadata",
    )

    assert allow_query.gap_policy == "allow_with_metadata"


def test_strategy_backtest_data_and_declaration_helpers():
    from openpine.cli.main import (
        _load_strategy_backtest_bars,
        _strategy_backtest_declaration_args,
    )

    bars_fixture = [SimpleNamespace(time=100)]
    provider_core = SimpleNamespace(last_fetch_info={"source": "fixture"})
    provider = SimpleNamespace(_provider=provider_core)
    captured_query = {}

    class FakeOrchestrator:
        def set_provider(self, value):
            self.provider = value

        def get_bars(self, query):
            captured_query["query"] = query
            return bars_fixture

    class FakeStore:
        def get_artifact(self, artifact_id, pine_id):
            assert (artifact_id, pine_id) == ("artifact-1", "pine-1")
            return {
                "compile_meta": {
                    "translation_metadata": {
                        "declaration": {"arguments": {"initial_capital": 50_000}}
                    }
                }
            }

    messages: list[tuple] = []
    console = SimpleNamespace(print=lambda *args, **kwargs: messages.append(args))
    strategy = SimpleNamespace(
        symbol="BTCUSDT",
        exchange="binance",
        market_type="spot",
        timeframe="15m",
        artifact_id="artifact-1",
        pine_id="pine-1",
    )

    bars, loaded_provider, data_fetch, load_sec = _load_strategy_backtest_bars(
        strategy=strategy,
        start_ms=100,
        end_ms=200,
        bar_query_cls=lambda **kwargs: SimpleNamespace(**kwargs),
        instrument_key_cls=lambda **kwargs: SimpleNamespace(**kwargs),
        parse_timeframe_func=lambda value: f"tf:{value}",
        orchestrator_cls=FakeOrchestrator,
        provider_factory=lambda: provider,
        console=console,
    )
    decl_args = _strategy_backtest_declaration_args(
        artifact_store_cls=FakeStore,
        strategy=strategy,
    )

    assert bars == bars_fixture
    assert loaded_provider is provider
    assert data_fetch == {"source": "fixture"}
    assert load_sec >= 0
    assert captured_query["query"].timeframe == "tf:15m"
    assert decl_args == {"initial_capital": 50_000}


def test_data_backfill_helpers_parse_dates():
    from openpine.cli.data import (
        _parse_cli_ymd_ms,
        _parse_data_backfill_window,
    )

    start_ms, error = _parse_cli_ymd_ms("2024-01-01", option_name="--from")
    bad_ms, bad_error = _parse_cli_ymd_ms("2024/01/01", option_name="--from")
    window = _parse_data_backfill_window(
        from_date="2024-01-01",
        to_date=None,
        now_ms=1_800_000_000_000,
    )

    assert start_ms == 1_704_056_400_000
    assert error is None
    assert bad_ms is None
    assert bad_error == "Invalid --from date format: 2024/01/01 (use YYYY-MM-DD)"
    assert window == (1_704_056_400_000, 1_800_000_000_000, None)


def test_data_backfill_wait_uses_marketdata_orchestrator(monkeypatch):
    from openpine.cli.data import _run_sync_marketdata_backfill

    provider = object()
    captured = {}

    class FakeOrchestrator:
        def __init__(self, provider=None):
            captured["provider"] = provider

        def load_bars(self, query):
            captured["query"] = query
            return SimpleNamespace(bars=[object(), object()])

    monkeypatch.setattr(
        "openpine.data.provider_adapter.create_local_marketdata_provider_adapter",
        lambda: provider,
    )
    monkeypatch.setattr("openpine.data.orchestrator.DataOrchestrator", FakeOrchestrator)

    messages: list[tuple] = []
    ok = _run_sync_marketdata_backfill(
        symbol="btcusdt",
        timeframe="15m",
        exchange="BINANCE",
        market="USDM",
        start_ms=100,
        end_ms=200,
        timeout=0,
        console=SimpleNamespace(print=lambda *args, **kwargs: messages.append(args)),
    )

    assert ok is True
    assert captured["provider"] is provider
    assert captured["query"].instrument.exchange == "binance"
    assert captured["query"].instrument.market == "usdm"
    assert captured["query"].instrument.symbol == "BTCUSDT"
    assert captured["query"].source == "auto"
    assert captured["query"].gap_policy == "fail"
    assert "2 candles available" in messages[-1][0]


def test_doctor_writable_dir_helper_reports_success_and_failure(tmp_path):
    from openpine.cli.main import _check_writable_dir

    messages: list[tuple] = []
    console = SimpleNamespace(print=lambda *args, **kwargs: messages.append(args))

    assert _check_writable_dir(tmp_path / "ok", "Test dir", console) is True
    assert (tmp_path / "ok").is_dir()
    assert not (tmp_path / "ok" / ".write_test").exists()
    assert "Test dir writable" in messages[-1][0]

    blocked_file = tmp_path / "not-a-dir"
    blocked_file.write_text("already here")
    assert _check_writable_dir(blocked_file, "Blocked dir", console) is False
    assert "Blocked dir" in messages[-1][0]


def test_generated_strategy_adapter_is_not_unwrapped_to_runtime_backend():
    """CLI backtest must keep the BacktestEngine adapter selected by the loader."""

    # Mock strategy class with generated_strategy_class_ref
    mock_generated_class = MagicMock()
    mock_strategy_class = MagicMock()
    mock_strategy_class.generated_strategy_class_ref = mock_generated_class

    # Patch at the source modules where imports come from
    with patch("openpine.runtime.engine.BacktestEngineAdapter") as MockAdapter, patch(
        "openpine.registry.SQLiteStrategyRegistry"
    ) as MockRegistry, patch(
        "openpine.data.orchestrator.DataOrchestrator"
    ) as MockOrch, patch(
        "openpine.data.provider_adapter.create_local_marketdata_provider_adapter"
    ), patch(
        "openpine.artifacts.ArtifactStore"
    ) as MockArtifactStore, patch(
        "openpine.storage.BacktestResultStore"
    ), patch(
        "openpine.runtime.engine.load_strategy_class_from_artifact",
        return_value=mock_strategy_class,
    ):

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
            params_json="{}",
        )

        mock_orch = MockOrch.return_value
        mock_orch.get_bars.return_value = [MagicMock()]

        mock_artifact = MockArtifactStore.return_value
        mock_artifact.get_artifact.return_value = {"compile_meta": {}}

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

        runner.invoke(
            strategy_backtest, ["test", "--from", "2024-01-01", "--to", "2024-01-02"]
        )

        # Get the call args for run A
        call_args_a = mock_instance.run.call_args
        backend_a = call_args_a.kwargs.get("execution_backend") if call_args_a else None
        strategy_class_a = call_args_a.args[0] if call_args_a else None

        # Reset mock
        mock_instance.run.reset_mock()

        # Test B: with capture_plots
        runner.invoke(
            strategy_backtest,
            ["test", "--from", "2024-01-01", "--to", "2024-01-02", "--capture-plots"],
        )

        call_args_b = mock_instance.run.call_args
        backend_b = call_args_b.kwargs.get("execution_backend") if call_args_b else None
        strategy_class_b = call_args_b.args[0] if call_args_b else None

        assert backend_a is None
        assert backend_b is None
        assert strategy_class_a is mock_strategy_class
        assert strategy_class_b is mock_strategy_class


def test_no_generated_ref_no_backend():
    """When strategy has NO generated_strategy_class_ref, no backend is used
    regardless of --capture-plots flag."""

    mock_strategy_class = MagicMock()
    # No generated_strategy_class_ref attribute

    with patch("openpine.runtime.engine.BacktestEngineAdapter") as MockAdapter, patch(
        "openpine.registry.SQLiteStrategyRegistry"
    ) as MockRegistry, patch(
        "openpine.data.orchestrator.DataOrchestrator"
    ) as MockOrch, patch(
        "openpine.data.provider_adapter.create_local_marketdata_provider_adapter"
    ), patch(
        "openpine.artifacts.ArtifactStore"
    ) as MockArtifactStore, patch(
        "openpine.storage.BacktestResultStore"
    ), patch(
        "openpine.runtime.engine.load_strategy_class_from_artifact",
        return_value=mock_strategy_class,
    ):

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
            params_json="{}",
        )

        mock_orch = MockOrch.return_value
        mock_orch.get_bars.return_value = [MagicMock()]

        mock_artifact = MockArtifactStore.return_value
        mock_artifact.get_artifact.return_value = {"compile_meta": {}}

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
        runner.invoke(
            strategy_backtest, ["test", "--from", "2024-01-01", "--to", "2024-01-02"]
        )

        call_args_a = mock_instance.run.call_args
        backend_a = call_args_a.kwargs.get("execution_backend") if call_args_a else None

        mock_instance.run.reset_mock()

        # Test B: with capture_plots
        runner.invoke(
            strategy_backtest,
            ["test", "--from", "2024-01-01", "--to", "2024-01-02", "--capture-plots"],
        )

        call_args_b = mock_instance.run.call_args
        backend_b = call_args_b.kwargs.get("execution_backend") if call_args_b else None

        # Assertions: no backend in either case
        assert (
            backend_a is None
        ), f"Expected no backend without capture_plots, got {backend_a}"
        assert (
            backend_b is None
        ), f"Expected no backend with capture_plots, got {backend_b}"
