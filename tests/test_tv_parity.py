from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient
import pytest

from openpine.gateway import server
from openpine.gateway.routes import tv_parity


class FakeTrade:
    def __init__(self, **values):
        for key, value in values.items():
            setattr(self, key, value)


class FakeEquityPoint:
    def __init__(self, **values):
        for key, value in values.items():
            setattr(self, key, value)


def _json_strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _json_strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _json_strings(item)
    elif isinstance(value, str):
        yield value


def test_parse_tradingview_candles_csv_builds_bar_series(tmp_path: Path):
    candles = tmp_path / "tv_candles.csv"
    candles.write_text(
        "time,open,high,low,close,Volume\n"
        "2024-01-01T00:00:00Z,100,110,90,105,12.5\n"
        "2024-01-01T00:01:00Z,105,112,101,108,8\n",
        encoding="utf-8",
    )

    parsed = tv_parity.parse_tradingview_candles_csv(
        candles,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1m",
    )

    assert parsed.summary["valid_bars"] == 2
    assert parsed.summary["invalid_rows"] == 0
    assert parsed.summary["from_time"] == 1704067200000
    assert parsed.summary["to_time"] == 1704067320000
    assert parsed.series.coverage.source_mix == ("tradingview_csv",)
    assert parsed.bars[0].instrument.exchange == "binance"
    assert parsed.bars[0].instrument.market == "spot"
    assert parsed.bars[0].instrument.symbol == "BTCUSDT"
    assert parsed.bars[0].open == 100.0
    assert parsed.bars[1].time_close == 1704067320000


def test_parse_tradingview_candles_csv_rejects_missing_ohlc(tmp_path: Path):
    candles = tmp_path / "bad.csv"
    candles.write_text("time,open,high,close\n1,1,1,1\n", encoding="utf-8")

    try:
        tv_parity.parse_tradingview_candles_csv(
            candles,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="1m",
        )
    except ValueError as exc:
        assert "missing required candle columns" in str(exc)
    else:
        raise AssertionError("missing low column must fail")


def test_write_tv_parity_exports_and_comparison_generates_three_way_report(tmp_path: Path):
    tv_chart = tmp_path / "tv_chart.csv"
    tv_chart.write_text(
        "time,my_plot\n"
        "1704067200000,5\n"
        "1704067260000,6\n",
        encoding="utf-8",
    )
    tv_equity = tmp_path / "tv_equity.csv"
    tv_equity.write_text(
        "time,equity\n"
        "1704067200000,10000\n"
        "1704067260000,10010\n",
        encoding="utf-8",
    )
    tv_trades = tmp_path / "tv_trades.csv"
    tv_trades.write_text(
        "Trade #,Type,Date/Time,Price,Qty,Net Profit\n"
        "1,Entry Long,2024-01-01T00:00:00Z,100,1,\n"
        "1,Exit Long,2024-01-01T00:01:00Z,110,1,10\n",
        encoding="utf-8",
    )
    raw_result = SimpleNamespace(
        symbol="BTCUSDT",
        timeframe="1m",
        plots=[
            (1704067200000, 0, 5.0, "my_plot"),
            (1704067260000, 1, 6.0, "my_plot"),
        ],
        trades=[
            FakeTrade(
                id="1",
                direction="long",
                entry_time=1704067200000,
                exit_time=1704067260000,
                entry_price=100.0,
                exit_price=110.0,
                qty=1.0,
                gross_profit=10.0,
                net_profit=10.0,
                max_runup=None,
                max_drawdown=None,
            )
        ],
        equity_curve=[
            FakeEquityPoint(time=1704067200000, equity=10000.0, cash=10000.0),
            FakeEquityPoint(time=1704067260000, equity=10010.0, cash=10010.0),
        ],
        initial_capital=10000.0,
        final_equity=10010.0,
        net_profit=10.0,
        total_trades=1,
    )

    payload = tv_parity.write_tv_parity_exports_and_comparison(
        strategy_id="strat_1",
        run_id="run_1",
        raw_result=raw_result,
        output_root=tmp_path / "parity",
        compare_from_ms=1704067200000,
        compare_to_ms=1704067320000,
        tv_chart_path=tv_chart,
        tv_trades_path=tv_trades,
        tv_equity_path=tv_equity,
        abs_tol=1e-9,
        rel_tol=1e-9,
        include_base_columns=False,
    )

    assert (tmp_path / "parity/openpine_outputs/plots.csv").exists()
    assert (tmp_path / "parity/openpine_outputs/trades.csv").exists()
    assert (tmp_path / "parity/openpine_outputs/equity_curve.csv").exists()
    assert (tmp_path / "parity/comparison/comparison_summary.json").exists()
    assert payload["comparison"]["failures"] == []
    assert {row["type"] for row in payload["comparison"]["comparisons"]} == {
        "plots",
        "trades",
        "equity",
    }
    assert not any(str(tmp_path) in value for value in _json_strings(payload))


def test_tv_parity_routes_are_registered_under_api_prefix():
    app = server.create_app()
    routes = {getattr(route, "path", "") for route in app.routes}

    assert "/api/tv-parity/preview-candles" in routes
    assert "/api/tv-parity/run" in routes
    assert "/api/tv-parity/runs/{run_id}" in routes
    assert "/api/tv-parity/runs/{run_id}/artifacts/{artifact_name}" in routes


def test_preview_candles_endpoint_returns_locked_period(tmp_path: Path):
    state = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    app = FastAPI()
    app.include_router(tv_parity.router, prefix="/api")
    from openpine.gateway.deps import get_state

    app.dependency_overrides[get_state] = lambda: state
    client = TestClient(app)

    response = client.post(
        "/api/tv-parity/preview-candles",
        data={
            "exchange": "binance",
            "market_type": "spot",
            "symbol": "BTCUSDT",
            "timeframe": "1m",
        },
        files={
            "candles_file": (
                "candles.csv",
                "time,open,high,low,close,volume\n"
                "1704067200000,1,2,0.5,1.5,10\n"
                "1704067260000,1.5,2.5,1,2,11\n",
                "text/csv",
            )
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid_bars"] == 2
    assert payload["locked_period"] == {
        "from_time": 1704067200000,
        "to_time": 1704067320000,
    }
    assert "stored_path" not in payload
    assert not any(str(tmp_path) in value for value in _json_strings(payload))


def test_run_endpoint_queues_tv_candle_replay_without_server_side_paths(tmp_path: Path, monkeypatch):
    class FakeStrategyRegistry:
        def get_strategy(self, strategy_id: str):
            assert strategy_id == "strat_1"
            return SimpleNamespace(
                strategy_id="strat_1",
                pine_id="pine_1",
                artifact_id="art_1",
                params_hash="params_1",
                exchange="binance",
                market_type="spot",
                symbol="BTCUSDT",
                timeframe="1m",
                params_json='{"length": 14}',
            )

    class FakeBacktestStore:
        def __init__(self):
            self.requests = []

        def create_run(self, request):
            self.requests.append(request)
            return "run_tv_1"

    async def fake_background(**kwargs):
        result_path = kwargs["run_root"] / "tv_parity_result.json"
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        payload["status"] = "done"
        result_path.write_text(json.dumps(payload), encoding="utf-8")

    fake_store = FakeBacktestStore()
    state = SimpleNamespace(
        config=SimpleNamespace(data_dir=tmp_path),
        strategy_registry=FakeStrategyRegistry(),
        backtest_store=fake_store,
    )
    app = FastAPI()
    app.include_router(tv_parity.router, prefix="/api")
    from openpine.gateway.deps import get_state

    app.dependency_overrides[get_state] = lambda: state
    monkeypatch.setattr(tv_parity, "_run_tv_parity_background", fake_background)
    client = TestClient(app)

    response = client.post(
        "/api/tv-parity/run",
        data={
            "strategy_id": "strat_1",
            "capture_plots": "true",
            "warmup_bars": "2",
            "abs_tol": "0.01",
        },
        files={
            "candles_file": (
                "candles.csv",
                "time,open,high,low,close,volume\n"
                "1704067200000,1,2,0.5,1.5,10\n"
                "1704067260000,1.5,2.5,1,2,11\n",
                "text/csv",
            ),
            "tv_chart_file": (
                "chart.csv",
                "time,my_plot\n1704067200000,1\n",
                "text/csv",
            ),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == "run_tv_1"
    assert payload["status"] == "queued"
    assert payload["source"] == "tradingview_csv"
    assert payload["locked_period"] == {"from_time": 1704067200000, "to_time": 1704067320000}
    assert fake_store.requests[0].price_type == "tradingview_csv"
    run_root = tmp_path / "tv-parity" / "run_tv_1"
    assert (run_root / "uploads" / "candles.csv").exists()
    request_payload = json.loads((run_root / "request.json").read_text(encoding="utf-8"))
    assert "stored_path" not in request_payload
    assert not any(str(tmp_path) in value for value in _json_strings(request_payload))
    result_payload = json.loads((run_root / "tv_parity_result.json").read_text(encoding="utf-8"))
    assert not any(str(tmp_path) in value for value in _json_strings(result_payload))
    assert request_payload["capture_plots"] is True


def test_run_endpoint_treats_csv_from_time_as_visible_window_not_calculation_window(
    tmp_path: Path, monkeypatch
):
    class FakeStrategyRegistry:
        def get_strategy(self, strategy_id: str):
            assert strategy_id == "strat_csv_window"
            return SimpleNamespace(
                strategy_id="strat_csv_window",
                pine_id="pine_1",
                artifact_id="art_1",
                params_hash="params_1",
                exchange="binance",
                market_type="spot",
                symbol="BTCUSDT",
                timeframe="1m",
                params_json=None,
            )

    class FakeBacktestStore:
        def __init__(self):
            self.requests = []

        def create_run(self, request):
            self.requests.append(request)
            return "run_csv_window"

    seen = {}

    async def fake_background(**kwargs):
        parsed = kwargs["parsed"]
        seen["bars"] = len(parsed.bars)
        seen["summary"] = dict(parsed.summary)
        result_path = kwargs["run_root"] / "tv_parity_result.json"
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        payload["status"] = "done"
        result_path.write_text(json.dumps(payload), encoding="utf-8")

    fake_store = FakeBacktestStore()
    state = SimpleNamespace(
        config=SimpleNamespace(data_dir=tmp_path),
        strategy_registry=FakeStrategyRegistry(),
        backtest_store=fake_store,
    )
    app = FastAPI()
    app.include_router(tv_parity.router, prefix="/api")
    from openpine.gateway.deps import get_state

    app.dependency_overrides[get_state] = lambda: state
    monkeypatch.setattr(tv_parity, "_run_tv_parity_background", fake_background)
    client = TestClient(app)

    response = client.post(
        "/api/tv-parity/run",
        data={
            "strategy_id": "strat_csv_window",
            "from_time": "1704067260000",
            "to_time": "1704067380000",
            "capture_plots": "true",
        },
        files={
            "candles_file": (
                "candles.csv",
                "time,open,high,low,close,volume\n"
                "1704067200000,1,2,0.5,1.5,10\n"
                "1704067260000,1.5,2.5,1,2,11\n"
                "1704067320000,2,3,1.5,2.5,12\n",
                "text/csv",
            )
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid_bars"] == 3
    assert payload["locked_period"] == {
        "from_time": 1704067260000,
        "to_time": 1704067380000,
    }
    assert seen["bars"] == 3
    assert seen["summary"]["valid_bars"] == 3
    assert seen["summary"]["from_time"] == 1704067200000
    assert seen["summary"]["to_time"] == 1704067380000
    assert seen["summary"]["compare_from"] == 1704067260000
    assert seen["summary"]["compare_to"] == 1704067380000
    assert seen["summary"]["effective_pre_bars"] == 1
    request_payload = json.loads(
        (tmp_path / "tv-parity" / "run_csv_window" / "request.json").read_text(encoding="utf-8")
    )
    assert request_payload["requested_from_ms"] == 1704067260000
    assert request_payload["requested_to_ms"] == 1704067380000
    assert fake_store.requests[0].from_time == 1704067200000
    assert fake_store.requests[0].to_time == 1704067380000


def test_parser_filters_invalid_duplicate_and_window_rows(tmp_path: Path):
    candles = tmp_path / "mixed.csv"
    candles.write_text(
        "time,open,high,low,close\n"
        "bad,1,2,0,1\n"
        "1704067140000,1,2,0,1\n"
        "1704067200000,1,2,0,1\n"
        "1704067200000,1,2,0,1\n"
        "1704067260000,na,2,0,1\n"
        "1704067320000,1,2,0,1\n",
        encoding="utf-8",
    )

    parsed = tv_parity.parse_tradingview_candles_csv(
        candles,
        exchange="BINANCE",
        market_type="SPOT",
        symbol="btcusdt",
        timeframe="1m",
        from_ms=1704067200000,
        to_ms=1704067320000,
    )

    assert parsed.summary["total_rows"] == 6
    assert parsed.summary["valid_bars"] == 2
    assert parsed.summary["invalid_rows"] == 2
    assert parsed.summary["duplicate_timestamps"] == 1
    assert parsed.bars[0].volume is None


def test_parser_rejects_empty_valid_window(tmp_path: Path):
    candles = tmp_path / "empty.csv"
    candles.write_text(
        "time,open,high,low,close\n1704067200000,1,2,0,1\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="no valid TradingView candle rows"):
        tv_parity.parse_tradingview_candles_csv(
            candles,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="1m",
            from_ms=1704068000000,
        )


def test_helper_functions_cover_safe_paths_and_empty_uploads(tmp_path: Path):
    state = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    run_root = tv_parity._run_root(state, "../run 1")

    assert run_root.name == "run_1"
    assert tv_parity._safe_upload_filename("../bad name.csv", "fallback.csv") == "bad_name.csv"
    assert tv_parity._path_is_under(run_root / "ok.csv", run_root)
    assert not tv_parity._path_is_under(run_root / "../escape.csv", run_root)
    assert tv_parity._artifact_catalog("run_1", run_root) == []


@pytest.mark.asyncio
async def test_store_upload_sanitizes_filename_and_handles_none(tmp_path: Path):
    upload = UploadFile(filename="../candles bad.csv", file=__import__("io").BytesIO(b"a,b\n1,2\n"))

    assert await tv_parity._store_upload(None, upload_root=tmp_path, fallback_name="none.csv") is None
    stored = await tv_parity._store_upload(upload, upload_root=tmp_path, fallback_name="candles.csv")

    assert stored is not None
    assert stored.name == "candles.csv"
    assert stored.read_text(encoding="utf-8") == "a,b\n1,2\n"


def test_write_exports_without_tv_files_skips_comparison(tmp_path: Path):
    raw_result = SimpleNamespace(
        symbol="BTCUSDT",
        timeframe="1m",
        plots=[],
        trades=[],
        equity_curve=[],
        initial_capital=10000.0,
        final_equity=10000.0,
        net_profit=0.0,
        total_trades=0,
    )

    payload = tv_parity.write_tv_parity_exports_and_comparison(
        strategy_id="strat_1",
        run_id="run_1",
        raw_result=raw_result,
        output_root=tmp_path,
        compare_from_ms=1,
        compare_to_ms=2,
        tv_chart_path=None,
        tv_trades_path=None,
        tv_equity_path=None,
        abs_tol=0.0,
        rel_tol=0.0,
        include_base_columns=False,
    )

    assert payload["comparison"] is None
    assert payload["rows"] == {"plots": 0, "trades": 0, "all_trades": 0, "equity": 0}


def test_strategy_decl_args_and_backtest_config_map_defaults_and_commission():
    strategy = SimpleNamespace(
        pine_id="pine_1",
        artifact_id="art_1",
        symbol="BTCUSDT",
        timeframe="1m",
        exchange="binance",
        market_type="spot",
    )
    state = SimpleNamespace(
        artifact_store=SimpleNamespace(
            get_artifact=lambda artifact_id, pine_id: {
                "compile_meta": {
                    "translation_metadata": {
                        "declaration": {
                            "arguments": {
                                "initial_capital": 12345,
                                "commission_type": "cash_per_order",
                                "commission_value": 2.5,
                                "close_entries_rule": "any",
                                "capture_plots": True,
                            }
                        }
                    }
                }
            }
        )
    )

    decl_args = tv_parity._strategy_decl_args(state, strategy)
    config = tv_parity._backtest_config_for_tv_replay(
        strategy=strategy,
        from_ms=1,
        to_ms=2,
        warmup_bars=7,
        capture_plots=True,
        decl_args=decl_args,
    )

    assert config.initial_capital == 12345
    assert config.commission_type == "fixed_per_order"
    assert config.exit_matching == "ANY"
    assert config.capture_plots is True
    assert config.qty_step == 0.00001
    assert config.qty_rounding_mode == "truncate"
    assert config.mintick == 0.01
    assert tv_parity._strategy_decl_args(SimpleNamespace(artifact_store=SimpleNamespace(get_artifact=lambda *_: (_ for _ in ()).throw(RuntimeError("x")))), strategy) == {}


def test_tv_compare_prefers_chart_diagnostic_trades_over_stale_trade_report(tmp_path: Path):
    from openpine.cli.compare import _compare_strategy_run_with_tv_exports

    tv_chart = tmp_path / "chart.csv"
    tv_chart.write_text(
        "time,P092_LOCAL_BAR,P092_CLOSED_TRADES,P092_DIAG_NEW_CLOSED_TRADE,"
        "P092_DIAG_LAST_CLOSED_PROFIT,P092_DIAG_LAST_CLOSED_SIZE,"
        "P092_DIAG_LAST_CLOSED_ENTRY_PRICE,P092_DIAG_LAST_CLOSED_EXIT_PRICE,"
        "P092_DIAG_LAST_CLOSED_ENTRY_BAR,P092_DIAG_LAST_CLOSED_EXIT_BAR\n"
        "1704067200000,10,0,0,,,,,10,10\n"
        "1704153600000,11,1,1,283.72,-0.012772,113135.75,90809.0,10,11\n",
        encoding="utf-8",
    )
    stale_tv_trades = tmp_path / "trades_report.csv"
    stale_tv_trades.write_text(
        "Trade #,Type,Date/Time,Price,Qty,Net Profit\n"
        "1,Entry Short,2024-01-01 00:00,113135.75,0.012401,\n"
        "1,Exit Short,2024-01-02 00:00,90809.0,0.012401,275.48\n",
        encoding="utf-8",
    )
    op_plots = tmp_path / "openpine_plots.csv"
    op_plots.write_text(
        "bar_time,P092_LOCAL_BAR,P092_CLOSED_TRADES,P092_DIAG_NEW_CLOSED_TRADE,"
        "P092_DIAG_LAST_CLOSED_PROFIT,P092_DIAG_LAST_CLOSED_SIZE,"
        "P092_DIAG_LAST_CLOSED_ENTRY_PRICE,P092_DIAG_LAST_CLOSED_EXIT_PRICE,"
        "P092_DIAG_LAST_CLOSED_ENTRY_BAR,P092_DIAG_LAST_CLOSED_EXIT_BAR\n"
        "1704067200000,10,0,0,,,,,10,10\n"
        "1704153600000,11,1,1,283.72,0.012772,113135.75,90809.0,10,11\n",
        encoding="utf-8",
    )
    op_trades = tmp_path / "openpine_trades.csv"
    op_trades.write_text(
        "trade_id,status,direction,entry_time_ms,exit_time_ms,entry_price,exit_price,qty,gross_profit,commission,net_profit\n"
        "S,closed,short,1704067200000,1704153600000,113135.75,90809.0,0.012772,283.72,,283.72\n",
        encoding="utf-8",
    )

    result = _compare_strategy_run_with_tv_exports(
        strategy_id="strat_daily",
        run=SimpleNamespace(run_id="run_daily"),
        exported={"plots": str(op_plots), "trades": str(op_trades)},
        output_path=tmp_path / "comparison",
        tv_chart=str(tv_chart),
        tv_trades=str(stale_tv_trades),
        tv_equity=None,
        abs_tol=0.000001,
        rel_tol=0.000000001,
        include_base_columns=False,
        compare_from_ms=1704067200000,
        compare_to_ms=1704240000000,
    )

    trades = next(row for row in result["comparisons"] if row["type"] == "trades")
    assert trades["status"] == "match"
    normalized = tmp_path / "comparison" / "tradingview_trades_normalized.csv"
    assert "0.012772" in normalized.read_text(encoding="utf-8")
    assert "0.012401" not in normalized.read_text(encoding="utf-8")


def test_tv_compare_ignores_chart_rows_with_blank_strategy_plot_values(tmp_path: Path):
    from openpine.cli.compare import _compare_strategy_run_with_tv_exports

    tv_chart = tmp_path / "chart.csv"
    tv_chart.write_text(
        "time,open,high,low,close,P092_NETPROFIT,P092_EQUITY\n"
        "1704067200000,1,2,0.5,1.5,10,10010\n"
        "1704153600000,1.5,2.5,1,2,,\n",
        encoding="utf-8",
    )
    op_plots = tmp_path / "plots.csv"
    op_plots.write_text(
        "bar_time,bar_index,open,high,low,close,P092_NETPROFIT,P092_EQUITY\n"
        "1704067200000,0,1,2,0.5,1.5,10,10010\n"
        "1704153600000,1,1.5,2.5,1,2,20,10020\n",
        encoding="utf-8",
    )

    result = _compare_strategy_run_with_tv_exports(
        strategy_id="strat_daily",
        run=SimpleNamespace(run_id="run_daily"),
        exported={"plots": str(op_plots)},
        output_path=tmp_path / "comparison",
        tv_chart=str(tv_chart),
        tv_trades=None,
        tv_equity=None,
        abs_tol=0.000001,
        rel_tol=0.000000001,
        include_base_columns=False,
        compare_from_ms=1704067200000,
        compare_to_ms=1704240000000,
    )

    plots = next(row for row in result["comparisons"] if row["type"] == "plots")
    assert plots["status"] == "match"
    assert plots["tv_rows"] == 1
    assert plots["openpine_rows"] == 1


@pytest.mark.asyncio
async def test_background_success_saves_result_and_tv_parity_payload(tmp_path: Path, monkeypatch):
    candles = tmp_path / "candles.csv"
    candles.write_text(
        "time,open,high,low,close,volume\n1704067200000,1,2,0,1,10\n",
        encoding="utf-8",
    )
    parsed = tv_parity.parse_tradingview_candles_csv(
        candles,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1m",
    )
    strategy = SimpleNamespace(
        pine_id="pine_1",
        artifact_id="art_1",
        params_hash="params_1",
        symbol="BTCUSDT",
        timeframe="1m",
        exchange="binance",
        market_type="spot",
        params_json='{"length": 14}',
    )
    saved = {}

    class FakeBacktestStore:
        def save_result(self, **kwargs):
            saved.update(kwargs)

        def mark_failed(self, *args):
            raise AssertionError("must not fail")

    class FakeWs:
        def update_progress(self, *args, **kwargs):
            saved.setdefault("progress", []).append((args, kwargs))

        async def broadcast_progress(self, run_id):
            saved.setdefault("broadcast", []).append(run_id)

    def fake_run(*args):
        args[-1](1, 1)
        raw_result = SimpleNamespace(
            trades=[FakeTrade(id="T1", direction="long", entry_time=1, entry_price=1, qty=1)],
            equity_curve=[FakeEquityPoint(time=1, equity=10000)],
            plots=[(1, 0, 1.0, "plot")],
        )
        return SimpleNamespace(raw_result=raw_result, bars_processed=1)

    monkeypatch.setattr(tv_parity, "ws_manager", FakeWs())
    monkeypatch.setattr(tv_parity, "_bar_series_fingerprint", lambda series: "fp_1")
    monkeypatch.setattr(tv_parity, "_save_backtest_data_fingerprint", lambda *args: saved.setdefault("fingerprint_saved", True))
    monkeypatch.setattr(tv_parity, "_run_backtest_in_process", fake_run)
    monkeypatch.setattr(
        "openpine.runtime.engine.load_strategy_class_from_artifact",
        lambda *args, **kwargs: object,
    )
    monkeypatch.setattr(
        "openpine.data.provider_adapter.create_local_runtime_data_provider_adapter",
        lambda **kwargs: "runtime_provider",
    )
    monkeypatch.setattr(
        tv_parity,
        "write_tv_parity_exports_and_comparison",
        lambda **kwargs: {"run_id": kwargs["run_id"], "comparison": {"failures": []}, "outputs": {}},
    )
    state = SimpleNamespace(
        config=SimpleNamespace(data_dir=tmp_path, data_cache_root=tmp_path / "cache"),
        strategy_registry=SimpleNamespace(get_strategy=lambda strategy_id: strategy),
        artifact_store=SimpleNamespace(get_artifact=lambda *args: {}),
        backtest_store=FakeBacktestStore(),
    )

    await tv_parity._run_tv_parity_background(
        state=state,
        strategy_id="strat_1",
        run_id="run_1",
        parsed=parsed,
        run_root=tmp_path / "run_1",
        uploads={"chart": None, "trades": None, "equity": None},
        params_override=None,
        warmup_bars=0,
        capture_plots=True,
        compare_from_ms=1,
        compare_to_ms=2,
        abs_tol=0.0,
        rel_tol=0.0,
        include_base_columns=False,
    )

    payload = json.loads((tmp_path / "run_1" / "tv_parity_result.json").read_text(encoding="utf-8"))
    assert payload["status"] == "done"
    assert payload["bars_processed"] == 1
    assert saved["fingerprint_saved"] is True
    assert saved["plots"] == [(1, 0, 1.0, "plot")]


@pytest.mark.asyncio
async def test_background_marks_artifact_load_failures(tmp_path: Path, monkeypatch):
    from openpine.runtime.engine import BacktestArtifactError

    candles = tmp_path / "candles.csv"
    candles.write_text("time,open,high,low,close\n1704067200000,1,2,0,1\n", encoding="utf-8")
    parsed = tv_parity.parse_tradingview_candles_csv(candles, exchange="binance", market_type="spot", symbol="BTCUSDT", timeframe="1m")
    marked = []

    class FakeWs:
        def update_progress(self, *args, **kwargs):
            pass

        async def broadcast_progress(self, run_id):
            pass

    monkeypatch.setattr(tv_parity, "ws_manager", FakeWs())
    monkeypatch.setattr(
        "openpine.runtime.engine.load_strategy_class_from_artifact",
        lambda *args, **kwargs: (_ for _ in ()).throw(BacktestArtifactError("artifact missing")),
    )
    state = SimpleNamespace(
        config=SimpleNamespace(data_dir=tmp_path),
        strategy_registry=SimpleNamespace(get_strategy=lambda strategy_id: SimpleNamespace(pine_id="pine_1", artifact_id="art_1", symbol="BTCUSDT", timeframe="1m")),
        backtest_store=SimpleNamespace(mark_failed=lambda run_id, error: marked.append((run_id, error))),
    )

    await tv_parity._run_tv_parity_background(
        state=state,
        strategy_id="strat_1",
        run_id="run_fail",
        parsed=parsed,
        run_root=tmp_path / "run_fail",
        uploads={"candles": "candles.csv"},
        params_override={},
        warmup_bars=0,
        capture_plots=False,
        compare_from_ms=1,
        compare_to_ms=2,
        abs_tol=0.0,
        rel_tol=0.0,
        include_base_columns=False,
    )

    assert marked == [("run_fail", "artifact missing")]
    payload = json.loads((tmp_path / "run_fail" / "tv_parity_result.json").read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["error"] == "artifact missing"
    assert payload["uploads"] == {"candles": "candles.csv"}


@pytest.mark.asyncio
async def test_background_outer_failure_writes_failed_payload_even_if_mark_failed_fails(tmp_path: Path, monkeypatch):
    parsed = SimpleNamespace(summary={"from_time": 1, "to_time": 2}, bars=(), series=None)

    class FakeWs:
        def update_progress(self, *args, **kwargs):
            pass

        async def broadcast_progress(self, run_id):
            pass

    monkeypatch.setattr(tv_parity, "ws_manager", FakeWs())
    state = SimpleNamespace(
        config=SimpleNamespace(data_dir=tmp_path),
        strategy_registry=SimpleNamespace(get_strategy=lambda strategy_id: (_ for _ in ()).throw(RuntimeError("boom"))),
        backtest_store=SimpleNamespace(mark_failed=lambda *args: (_ for _ in ()).throw(RuntimeError("mark fail"))),
    )

    await tv_parity._run_tv_parity_background(
        state=state,
        strategy_id="strat_1",
        run_id="run_failed",
        parsed=parsed,
        run_root=tmp_path / "run_failed",
        uploads={"candles": "x"},
        params_override=None,
        warmup_bars=0,
        capture_plots=False,
        compare_from_ms=1,
        compare_to_ms=2,
        abs_tol=0.0,
        rel_tol=0.0,
        include_base_columns=False,
    )

    payload = json.loads((tmp_path / "run_failed" / "tv_parity_result.json").read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["error"] == "boom"


def test_artifact_download_rejects_unknown_names(tmp_path: Path):
    state = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    app = FastAPI()
    app.include_router(tv_parity.router, prefix="/api")
    from openpine.gateway.deps import get_state

    app.dependency_overrides[get_state] = lambda: state
    client = TestClient(app)

    (tmp_path / "tv-parity" / "run_1").mkdir(parents=True)
    (tmp_path / "tv-parity" / "run_1" / "tv_parity_result.json").write_text(
        json.dumps({"run_id": "run_1"}), encoding="utf-8"
    )

    response = client.get("/api/tv-parity/runs/run_1/artifacts/../../secret")

    assert response.status_code == 404


def _upload_csv(text: str, filename: str = "candles.csv") -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(text.encode("utf-8")))


def _strategy(**overrides):
    data = {
        "strategy_id": "strat_1",
        "pine_id": "pine_1",
        "artifact_id": "art_1",
        "params_hash": "params_1",
        "exchange": "binance",
        "market_type": "spot",
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "params_json": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


async def _run_tv_direct(*, candles_file, strategy_id: str, state, **overrides):
    params = {
        "tv_chart_file": None,
        "tv_trades_file": None,
        "tv_equity_file": None,
        "from_time": None,
        "to_time": None,
        "compare_from_time": None,
        "compare_to_time": None,
        "params_override_json": None,
        "warmup_bars": 0,
        "capture_plots": True,
        "abs_tol": 1e-6,
        "rel_tol": 1e-9,
        "include_base_columns": False,
        "source": "tradingview_csv",
        "full_prehistory": False,
    }
    params.update(overrides)
    return await tv_parity.run_tv_parity(
        BackgroundTasks(),
        candles_file=candles_file,
        strategy_id=strategy_id,
        state=state,
        **params,
    )


def _bar_series_for_window(exchange: str, market_type: str, symbol: str, timeframe: str, start_ms: int, end_ms: int):
    from marketdata_provider.contracts import Bar, BarQuery, BarSeries, CoverageReport, InstrumentKey, parse_timeframe

    timeframe_obj = parse_timeframe(timeframe)
    duration = int(timeframe_obj.duration_ms or 60_000)
    instrument = InstrumentKey(exchange=exchange, market=market_type, symbol=symbol)
    bars = []
    for open_ms in range(start_ms, end_ms, duration):
        bars.append(
            Bar(
                instrument=instrument,
                timeframe=timeframe_obj,
                time=open_ms,
                time_close=open_ms + duration,
                open=1.0,
                high=2.0,
                low=0.5,
                close=1.5,
                volume=10.0,
                closed=True,
            )
        )
    query = BarQuery(instrument=instrument, timeframe=timeframe_obj, start_ms=start_ms, end_ms=end_ms, gap_policy="allow_with_metadata")
    coverage = CoverageReport(
        requested_start_ms=start_ms,
        requested_end_ms=end_ms,
        delivered_start_ms=bars[0].time,
        delivered_end_ms=bars[-1].time_close,
        source_mix=("exchange_data",),
    )
    return BarSeries(query, tuple(bars), coverage)


@pytest.mark.asyncio
async def test_run_endpoint_queues_exchange_data_with_full_prehistory(tmp_path: Path, monkeypatch):
    captured = {}

    class Store:
        def __init__(self):
            self.requests = []

        def create_run(self, request):
            self.requests.append(request)
            return "run_exchange_full"

    class Orchestrator:
        def load_bars(self, query, progress_callback=None):
            captured["query"] = query
            return _bar_series_for_window(query.instrument.exchange, query.instrument.market, query.instrument.symbol, query.timeframe.canonical, query.start_ms, query.end_ms)

    async def fake_background(**kwargs):
        captured["background"] = kwargs

    store = Store()
    state = SimpleNamespace(
        config=SimpleNamespace(data_dir=tmp_path),
        strategy_registry=SimpleNamespace(get_strategy=lambda strategy_id: _strategy()),
        backtest_store=store,
        orchestrator=Orchestrator(),
    )
    monkeypatch.setattr(tv_parity, "_run_tv_parity_background", fake_background)
    monkeypatch.setattr(tv_parity.ws_manager, "update_progress", lambda *args, **kwargs: None)

    payload = await _run_tv_direct(
        candles_file=None,
        strategy_id="strat_1",
        source="exchange_data",
        full_prehistory=True,
        from_time="1704067080000",
        compare_from_time="1704067200000",
        compare_to_time="1704067320000",
        state=state,
    )

    assert payload["source"] == "exchange_data"
    assert payload["locked_period"] == {"from_time": 1704067200000, "to_time": 1704067320000}
    assert captured["query"].start_ms == 1704067080000
    assert captured["query"].end_ms == 1704067320000
    assert store.requests[0].price_type == "exchange_data"
    assert store.requests[0].from_time == 1704067080000
    assert store.requests[0].to_time == 1704067320000
    request_payload = json.loads((tmp_path / "tv-parity" / "run_exchange_full" / "request.json").read_text(encoding="utf-8"))
    assert request_payload["full_prehistory"] is True
    assert request_payload["candle_summary"]["effective_pre_bars"] == 2


@pytest.mark.asyncio
async def test_run_endpoint_exchange_data_without_full_prehistory_uses_warmup_window(tmp_path: Path, monkeypatch):
    captured = {}

    class Orchestrator:
        def load_bars(self, query, progress_callback=None):
            captured["query"] = query
            return _bar_series_for_window(query.instrument.exchange, query.instrument.market, query.instrument.symbol, query.timeframe.canonical, query.start_ms, query.end_ms)

    state = SimpleNamespace(
        config=SimpleNamespace(data_dir=tmp_path),
        strategy_registry=SimpleNamespace(get_strategy=lambda strategy_id: _strategy()),
        backtest_store=SimpleNamespace(create_run=lambda request: "run_exchange_warmup"),
        orchestrator=Orchestrator(),
    )
    monkeypatch.setattr(tv_parity, "_run_tv_parity_background", lambda **kwargs: None)
    monkeypatch.setattr(tv_parity.ws_manager, "update_progress", lambda *args, **kwargs: None)

    payload = await _run_tv_direct(
        candles_file=None,
        strategy_id="strat_1",
        source="exchange_data",
        warmup_bars=2,
        compare_from_time="1704067200000",
        compare_to_time="1704067320000",
        state=state,
    )

    assert payload["source"] == "exchange_data"
    assert captured["query"].start_ms == 1704067080000
    assert captured["query"].end_ms == 1704067320000


@pytest.mark.asyncio
async def test_exchange_data_source_requires_compare_window(tmp_path: Path):
    state = SimpleNamespace(
        config=SimpleNamespace(data_dir=tmp_path),
        strategy_registry=SimpleNamespace(get_strategy=lambda strategy_id: _strategy()),
        backtest_store=SimpleNamespace(create_run=lambda request: "run_1"),
        orchestrator=SimpleNamespace(load_bars=lambda query, progress_callback=None: None),
    )

    with pytest.raises(HTTPException) as missing_window:
        await _run_tv_direct(
            candles_file=None,
            strategy_id="strat_1",
            source="exchange_data",
            state=state,
        )

    assert missing_window.value.status_code == 400
    assert "exchange_data source requires" in missing_window.value.detail



def test_exchange_data_source_error_branches(tmp_path: Path):
    strategy = _strategy(timeframe="1m")

    with pytest.raises(HTTPException) as bad_source:
        tv_parity._normalize_tv_parity_source("unknown")
    assert bad_source.value.status_code == 400

    with pytest.raises(HTTPException) as bad_timeframe:
        tv_parity._fixed_timeframe_duration_ms("1M")
    assert bad_timeframe.value.status_code == 400

    error_cases = [
        ({"requested_from_ms": None, "requested_to_ms": 20, "compare_from_ms": 30, "compare_to_ms": 20}, False),
        ({"requested_from_ms": None, "requested_to_ms": 10, "compare_from_ms": 20, "compare_to_ms": 30}, False),
        ({"requested_from_ms": 25, "requested_to_ms": 30, "compare_from_ms": 20, "compare_to_ms": 30}, True),
        ({"requested_from_ms": 20, "requested_to_ms": 20, "compare_from_ms": None, "compare_to_ms": None}, False),
    ]
    for kwargs, full in error_cases:
        with pytest.raises(HTTPException):
            tv_parity._exchange_data_window(strategy=strategy, warmup_bars=0, full_prehistory=full, **kwargs)

    assert tv_parity._exchange_data_window(
        strategy=strategy,
        requested_from_ms=10,
        requested_to_ms=20,
        compare_from_ms=None,
        compare_to_ms=None,
        warmup_bars=0,
        full_prehistory=False,
    ) == (10, 20, 10, 20)
    assert tv_parity._exchange_data_window(
        strategy=strategy,
        requested_from_ms=None,
        requested_to_ms=20,
        compare_from_ms=10,
        compare_to_ms=20,
        warmup_bars=0,
        full_prehistory=True,
    ) == (0, 20, 10, 20)

    with pytest.raises(HTTPException) as no_provider:
        tv_parity.load_exchange_data_candles(
            state=SimpleNamespace(),
            strategy=strategy,
            calculation_from_ms=0,
            calculation_to_ms=10,
            compare_from_ms=0,
            compare_to_ms=10,
            full_prehistory=False,
        )
    assert no_provider.value.status_code == 500

    with pytest.raises(HTTPException) as empty_bars:
        tv_parity.load_exchange_data_candles(
            state=SimpleNamespace(orchestrator=SimpleNamespace(load_bars=lambda query: SimpleNamespace(bars=[]))),
            strategy=strategy,
            calculation_from_ms=0,
            calculation_to_ms=10,
            compare_from_ms=0,
            compare_to_ms=10,
            full_prehistory=False,
        )
    assert empty_bars.value.status_code == 400


def test_backtest_process_entry_passes_effective_pre_bars():
    from openpine.gateway.routes import backtest as backtest_routes

    seen = {}

    class Out:
        def put_nowait(self, value):
            seen.setdefault("progress", []).append(value)

        def put(self, value):
            seen["result"] = value

    class Adapter:
        def run(self, strategy_class, bars, config, **kwargs):
            seen["kwargs"] = kwargs
            return "done"

    backtest_routes._backtest_process_entry(Out(), Adapter(), object, [], object(), {}, None, effective_pre_bars=3)

    assert seen["kwargs"]["effective_pre_bars"] == 3
    assert seen["result"] == ("ok", "done")


@pytest.mark.asyncio
async def test_background_passes_effective_pre_bars_to_worker(tmp_path: Path, monkeypatch):
    candles = tmp_path / "candles.csv"
    candles.write_text(
        "time,open,high,low,close\n"
        "1704067140000,1,2,0,1\n"
        "1704067200000,1,2,0,1\n",
        encoding="utf-8",
    )
    parsed = tv_parity.parse_tradingview_candles_csv(candles, exchange="binance", market_type="spot", symbol="BTCUSDT", timeframe="1m")
    parsed.summary["effective_pre_bars"] = 1
    captured = {}

    class FakeStore:
        def save_result(self, **kwargs):
            pass

        def mark_completed(self, *args, **kwargs):
            pass

        def mark_failed(self, *args, **kwargs):
            raise AssertionError("must not fail")

    class FakeWs:
        def update_progress(self, *args, **kwargs):
            pass

        async def broadcast_progress(self, run_id):
            pass

    def fake_run(*args):
        captured["effective_pre_bars"] = args[-1]
        raw_result = SimpleNamespace(trades=[], equity_curve=[], plots=[])
        return SimpleNamespace(raw_result=raw_result, bars_processed=2)

    monkeypatch.setattr(tv_parity, "ws_manager", FakeWs())
    monkeypatch.setattr(tv_parity, "_run_backtest_in_process", fake_run)
    monkeypatch.setattr(tv_parity, "_bar_series_fingerprint", lambda series: "fp")
    monkeypatch.setattr(tv_parity, "_save_backtest_data_fingerprint", lambda *args: None)
    monkeypatch.setattr("openpine.runtime.engine.load_strategy_class_from_artifact", lambda *args, **kwargs: object)
    monkeypatch.setattr("openpine.data.provider_adapter.create_local_runtime_data_provider_adapter", lambda **kwargs: "runtime_provider")
    monkeypatch.setattr(tv_parity, "write_tv_parity_exports_and_comparison", lambda **kwargs: {"run_id": kwargs["run_id"], "comparison": {}, "outputs": {}})
    state = SimpleNamespace(
        config=SimpleNamespace(data_dir=tmp_path, data_cache_root=tmp_path / "cache"),
        strategy_registry=SimpleNamespace(get_strategy=lambda strategy_id: _strategy()),
        backtest_store=FakeStore(),
    )

    await tv_parity._run_tv_parity_background(
        state=state,
        strategy_id="strat_1",
        run_id="run_effective",
        parsed=parsed,
        run_root=tmp_path / "run_effective",
        uploads={"chart": None, "trades": None, "equity": None},
        params_override=None,
        warmup_bars=0,
        capture_plots=True,
        compare_from_ms=1704067200000,
        compare_to_ms=1704067260000,
        abs_tol=0.0,
        rel_tol=0.0,
        include_base_columns=False,
    )

    assert captured["effective_pre_bars"] == 1


def test_parser_rejects_missing_time_and_variable_duration_timeframe(tmp_path: Path):
    no_time = tmp_path / "no_time.csv"
    no_time.write_text("open,high,low,close\n1,2,0,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required candle columns"):
        tv_parity.parse_tradingview_candles_csv(
            no_time,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="1m",
        )

    monthly = tmp_path / "monthly.csv"
    monthly.write_text("time,open,high,low,close\n1704067200000,1,2,0,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="fixed duration"):
        tv_parity.parse_tradingview_candles_csv(
            monthly,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="1M",
        )


def test_upload_helpers_cover_fallback_suffix_and_artifact_catalog(tmp_path: Path):
    assert tv_parity._safe_upload_filename(".", "fallback.csv") == "fallback.csv"

    run_root = tmp_path / "run_1"
    plots = run_root / "openpine_outputs" / "plots.csv"
    plots.parent.mkdir(parents=True)
    plots.write_text("time,value\n", encoding="utf-8")

    catalog = tv_parity._artifact_catalog("run_1", run_root)
    assert catalog == [
        {
            "name": "openpine_plots",
            "filename": "plots.csv",
            "size_bytes": len("time,value\n"),
            "download_url": "/api/tv-parity/runs/run_1/artifacts/openpine_plots",
        }
    ]


@pytest.mark.asyncio
async def test_store_upload_preserves_non_default_suffix(tmp_path: Path):
    stored = await tv_parity._store_upload(
        _upload_csv("a,b\n1,2\n", "candles.txt"),
        upload_root=tmp_path,
        fallback_name="candles.csv",
    )

    assert stored is not None
    assert stored.name == "candles.txt"


@pytest.mark.asyncio
async def test_store_upload_rejects_oversized_files_and_removes_partial(tmp_path: Path):
    with pytest.raises(HTTPException) as too_large:
        await tv_parity._store_upload(
            _upload_csv("abcdef", "big.csv"),
            upload_root=tmp_path,
            fallback_name="big.csv",
            max_bytes=5,
        )

    assert too_large.value.status_code == 413
    assert not (tmp_path / "big.csv").exists()


def test_public_payload_paths_scrubs_absolute_paths_inside_tuples(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    payload = {"files": (str(run_root / "comparison" / "summary.csv"),)}

    assert tv_parity._public_payload_paths(payload, run_root) == {"files": ("comparison/summary.csv",)}


def test_write_exports_aliases_equity_curve_for_comparison(tmp_path: Path, monkeypatch):
    import openpine.cli.compare as compare_mod

    captured = {}

    def fake_export_strategy_result(**_kwargs):
        return SimpleNamespace(
            outputs={"equity_curve": "equity_curve.csv"},
            plots_rows=0,
            trades_rows=0,
            all_trades_rows=0,
            equity_rows=1,
            initial_equity_at_export_start=10000.0,
        )

    def fake_compare(**kwargs):
        captured.update(kwargs)
        return {"failures": []}

    tv_chart = tmp_path / "chart.csv"
    tv_chart.write_text("time,value\n", encoding="utf-8")
    monkeypatch.setattr(tv_parity, "export_strategy_result", fake_export_strategy_result)
    monkeypatch.setattr(compare_mod, "_compare_strategy_run_with_tv_exports", fake_compare)

    payload = tv_parity.write_tv_parity_exports_and_comparison(
        strategy_id="strat_1",
        run_id="run_1",
        raw_result=SimpleNamespace(),
        output_root=tmp_path,
        compare_from_ms=1,
        compare_to_ms=2,
        tv_chart_path=tv_chart,
        tv_trades_path=None,
        tv_equity_path=None,
        abs_tol=0.0,
        rel_tol=0.0,
        include_base_columns=False,
    )

    assert captured["exported"]["equity"] == "equity_curve.csv"
    assert payload["comparison"] == {"failures": []}


def test_write_exports_leaves_outputs_without_equity_curve_alias(tmp_path: Path, monkeypatch):
    def fake_export_strategy_result(**_kwargs):
        return SimpleNamespace(
            outputs={"trades": "trades.csv"},
            plots_rows=0,
            trades_rows=1,
            all_trades_rows=1,
            equity_rows=0,
            initial_equity_at_export_start=None,
        )

    monkeypatch.setattr(tv_parity, "export_strategy_result", fake_export_strategy_result)

    payload = tv_parity.write_tv_parity_exports_and_comparison(
        strategy_id="strat_1",
        run_id="run_1",
        raw_result=SimpleNamespace(),
        output_root=tmp_path,
        compare_from_ms=1,
        compare_to_ms=2,
        tv_chart_path=None,
        tv_trades_path=None,
        tv_equity_path=None,
        abs_tol=0.0,
        rel_tol=0.0,
        include_base_columns=False,
    )

    assert payload["outputs"] == {"trades": "trades.csv"}


@pytest.mark.asyncio
async def test_preview_candles_rejects_missing_and_invalid_uploads(tmp_path: Path):
    state = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))

    with pytest.raises(HTTPException) as missing:
        await tv_parity.preview_candles(
            candles_file=None,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="1m",
            state=state,
        )
    assert missing.value.status_code == 400

    with pytest.raises(HTTPException) as invalid:
        await tv_parity.preview_candles(
            candles_file=_upload_csv("time,open,high,close\n1,1,1,1\n"),
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="1m",
            state=state,
        )
    assert invalid.value.status_code == 400
    assert "missing required candle columns" in invalid.value.detail


@pytest.mark.asyncio
async def test_run_tv_parity_validation_errors(tmp_path: Path):
    class Registry:
        def __init__(self, strategy):
            self.strategy = strategy

        def get_strategy(self, strategy_id):
            if self.strategy is KeyError:
                raise KeyError(strategy_id)
            return self.strategy

    base_state = lambda strategy: SimpleNamespace(
        config=SimpleNamespace(data_dir=tmp_path),
        strategy_registry=Registry(strategy),
        backtest_store=SimpleNamespace(create_run=lambda request: "run_1"),
    )
    valid_csv = "time,open,high,low,close\n1704067200000,1,2,0,1\n"

    with pytest.raises(HTTPException) as not_found:
        await _run_tv_direct(
            candles_file=_upload_csv(valid_csv),
            strategy_id="missing",
            state=base_state(KeyError),
        )
    assert not_found.value.status_code == 404

    with pytest.raises(HTTPException) as no_artifact:
        await _run_tv_direct(
            candles_file=_upload_csv(valid_csv),
            strategy_id="strat_1",
            state=base_state(_strategy(artifact_id=None)),
        )
    assert no_artifact.value.status_code == 400

    with pytest.raises(HTTPException) as bad_json:
        await _run_tv_direct(
            candles_file=_upload_csv(valid_csv),
            strategy_id="strat_1",
            params_override_json="{bad",
            state=base_state(_strategy()),
        )
    assert bad_json.value.status_code == 400

    with pytest.raises(HTTPException) as non_object:
        await _run_tv_direct(
            candles_file=_upload_csv(valid_csv),
            strategy_id="strat_1",
            params_override_json="[]",
            state=base_state(_strategy()),
        )
    assert non_object.value.status_code == 400

    with pytest.raises(HTTPException) as missing_candles:
        await _run_tv_direct(
            candles_file=None,
            strategy_id="strat_1",
            state=base_state(_strategy()),
        )
    assert missing_candles.value.status_code == 400

    with pytest.raises(HTTPException) as bad_csv:
        await _run_tv_direct(
            candles_file=_upload_csv("time,open,high,close\n1,1,1,1\n"),
            strategy_id="strat_1",
            state=base_state(_strategy()),
        )
    assert bad_csv.value.status_code == 400

    with pytest.raises(HTTPException) as bad_period:
        await _run_tv_direct(
            candles_file=_upload_csv(valid_csv),
            strategy_id="strat_1",
            compare_from_time="1704067200000",
            compare_to_time="1704067200000",
            state=base_state(_strategy()),
        )
    assert bad_period.value.status_code == 400


@pytest.mark.asyncio
async def test_run_tv_parity_replaces_existing_run_root(tmp_path: Path, monkeypatch):
    class Store:
        def create_run(self, request):
            return "run_existing"

    stale = tmp_path / "tv-parity" / "run_existing" / "stale.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("old", encoding="utf-8")
    state = SimpleNamespace(
        config=SimpleNamespace(data_dir=tmp_path),
        strategy_registry=SimpleNamespace(get_strategy=lambda strategy_id: _strategy()),
        backtest_store=Store(),
    )
    monkeypatch.setattr(tv_parity.ws_manager, "update_progress", lambda *args, **kwargs: None)

    payload = await _run_tv_direct(
        candles_file=_upload_csv("time,open,high,low,close\n1704067200000,1,2,0,1\n"),
        strategy_id="strat_1",
        params_override_json='{"length": 7}',
        state=state,
    )

    assert payload["run_id"] == "run_existing"
    assert not stale.exists()
    assert (tmp_path / "tv-parity" / "run_existing" / "request.json").exists()


@pytest.mark.asyncio
async def test_background_success_handles_params_override_and_runtime_provider_failure(tmp_path: Path, monkeypatch):
    candles = tmp_path / "candles.csv"
    candles.write_text("time,open,high,low,close\n1704067200000,1,2,0,1\n", encoding="utf-8")
    parsed = tv_parity.parse_tradingview_candles_csv(
        candles,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1m",
    )
    saved = {}

    class Ws:
        def update_progress(self, *args, **kwargs):
            pass

        async def broadcast_progress(self, run_id):
            pass

    monkeypatch.setattr(tv_parity, "ws_manager", Ws())
    monkeypatch.setattr(tv_parity, "_bar_series_fingerprint", lambda series: "fp")
    monkeypatch.setattr(tv_parity, "_save_backtest_data_fingerprint", lambda *args: None)
    monkeypatch.setattr("openpine.runtime.engine.load_strategy_class_from_artifact", lambda *args, **kwargs: object)
    monkeypatch.setattr(
        "openpine.data.provider_adapter.create_local_runtime_data_provider_adapter",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("provider down")),
    )
    monkeypatch.setattr(
        tv_parity,
        "_run_backtest_in_process",
        lambda *args: SimpleNamespace(raw_result=SimpleNamespace(trades=[], equity_curve=[], plots=[])),
    )
    monkeypatch.setattr(tv_parity, "write_tv_parity_exports_and_comparison", lambda **kwargs: {"outputs": {}, "comparison": None})
    state = SimpleNamespace(
        config=SimpleNamespace(data_dir=tmp_path, data_cache_root=None),
        strategy_registry=SimpleNamespace(get_strategy=lambda strategy_id: _strategy(params_json='{"ignored": true}')),
        artifact_store=SimpleNamespace(get_artifact=lambda *args: {}),
        backtest_store=SimpleNamespace(save_result=lambda **kwargs: saved.update(kwargs), mark_failed=lambda *args: None),
    )

    await tv_parity._run_tv_parity_background(
        state=state,
        strategy_id="strat_1",
        run_id="run_provider_warning",
        parsed=parsed,
        run_root=tmp_path / "run_provider_warning",
        uploads={"chart": None, "trades": None, "equity": None},
        params_override={"length": 7},
        warmup_bars=0,
        capture_plots=False,
        compare_from_ms=1,
        compare_to_ms=2,
        abs_tol=0.0,
        rel_tol=0.0,
        include_base_columns=False,
    )

    payload = json.loads((tmp_path / "run_provider_warning" / "tv_parity_result.json").read_text(encoding="utf-8"))
    assert payload["status"] == "done"
    assert saved["plots"] is None


def test_get_tv_parity_run_and_artifact_download_paths(tmp_path: Path):
    state = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    run_root = tmp_path / "tv-parity" / "run_1"
    (run_root / "openpine_outputs").mkdir(parents=True)
    (run_root / "comparison").mkdir(parents=True)
    (run_root / "openpine_outputs" / "plots.csv").write_text("time,value\n", encoding="utf-8")
    (run_root / "comparison" / "comparison_report.md").write_text("# report\n", encoding="utf-8")
    (run_root / "tv_parity_result.json").write_text(json.dumps({"run_id": "run_1"}), encoding="utf-8")

    payload = __import__("asyncio").run(tv_parity.get_tv_parity_run("run_1", state))
    assert payload["artifacts"][0]["name"] == "openpine_plots"

    csv_response = __import__("asyncio").run(tv_parity.download_tv_parity_artifact("run_1", "openpine_plots", state))
    assert csv_response.media_type == "text/csv"
    md_response = __import__("asyncio").run(tv_parity.download_tv_parity_artifact("run_1", "comparison_report", state))
    assert md_response.media_type == "text/markdown"

    with pytest.raises(HTTPException) as missing_run:
        __import__("asyncio").run(tv_parity.get_tv_parity_run("missing", state))
    assert missing_run.value.status_code == 404

    corrupt = tmp_path / "tv-parity" / "bad" / "tv_parity_result.json"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("{", encoding="utf-8")
    with pytest.raises(HTTPException) as corrupt_result:
        __import__("asyncio").run(tv_parity.get_tv_parity_run("bad", state))
    assert corrupt_result.value.status_code == 500

    with pytest.raises(HTTPException) as missing_artifact:
        __import__("asyncio").run(tv_parity.download_tv_parity_artifact("run_1", "comparison_json", state))
    assert missing_artifact.value.status_code == 404

    with pytest.raises(HTTPException) as unknown_artifact:
        __import__("asyncio").run(tv_parity.download_tv_parity_artifact("run_1", "unknown", state))
    assert unknown_artifact.value.status_code == 404


def test_list_tv_parity_runs_returns_disk_history_newest_first(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from openpine.gateway.deps import get_state

    state = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    base = tmp_path / "tv-parity"

    def _seed(run_id: str, queued_at: int, source: str, strategy_id: str, status: str) -> None:
        rd = base / run_id
        rd.mkdir(parents=True)
        payload = {
            "run_id": run_id,
            "strategy_id": strategy_id,
            "source": source,
            "status": status,
            "queued_at": queued_at,
            "compare_from": 1_700_000_000_000,
            "compare_to": 1_700_003_600_000,
            "candle_summary": {
                "symbol": "BTCUSDT",
                "exchange": "binance",
                "market_type": "spot",
                "timeframe": "1h",
                "valid_bars": 24,
                "from_time": 1_700_000_000_000,
                "to_time": 1_700_003_600_000,
            },
        }
        (rd / "tv_parity_result.json").write_text(json.dumps(payload), encoding="utf-8")

    _seed("older", 1_000, "tradingview_csv", "strat_a", "completed")
    _seed("newer", 9_000, "exchange_data", "strat_b", "running")
    _seed("newest", 5_000, "tradingview_csv", "strat_a", "failed")

    app = FastAPI()
    app.include_router(tv_parity.router, prefix="/api")
    app.dependency_overrides[get_state] = lambda: state
    client = TestClient(app)

    response = client.get("/api/tv-parity/runs")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["limit"] == 50
    run_ids = [e["run_id"] for e in payload["items"]]
    assert run_ids == ["newer", "newest", "older"]
    first = payload["items"][0]
    assert first["symbol"] == "BTCUSDT"
    assert first["source"] == "exchange_data"
    assert first["status"] == "running"


def test_list_tv_parity_runs_hides_seeded_demo_rows_by_default(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from openpine.gateway.deps import get_state

    state = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    base = tmp_path / "tv-parity"

    def _seed(run_id: str, queued_at: int, status: str) -> None:
        rd = base / run_id
        rd.mkdir(parents=True)
        payload = {
            "run_id": run_id,
            "strategy_id": "strat_a",
            "source": "tradingview_csv",
            "status": status,
            "queued_at": queued_at,
            "compare_from": 1,
            "compare_to": 2,
            "candle_summary": {
                "symbol": "BTCUSDT",
                "exchange": "binance",
                "market_type": "spot",
                "timeframe": "1h",
                "valid_bars": 24,
                "from_time": 1,
                "to_time": 2,
            },
        }
        (rd / "tv_parity_result.json").write_text(json.dumps(payload), encoding="utf-8")

    _seed("tvpar_demo_002", 9_000, "running")
    _seed("run_real_001", 8_000, "done")

    app = FastAPI()
    app.include_router(tv_parity.router, prefix="/api")
    app.dependency_overrides[get_state] = lambda: state
    client = TestClient(app)

    default_payload = client.get("/api/tv-parity/runs").json()
    assert default_payload["total"] == 1
    assert [e["run_id"] for e in default_payload["items"]] == ["run_real_001"]
    assert default_payload["include_demo"] is False

    debug_payload = client.get("/api/tv-parity/runs", params={"include_demo": True}).json()
    assert debug_payload["total"] == 2
    assert [e["run_id"] for e in debug_payload["items"]] == ["tvpar_demo_002", "run_real_001"]
    assert debug_payload["include_demo"] is True


def test_list_tv_parity_runs_filters_by_strategy_and_source(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from openpine.gateway.deps import get_state

    state = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    base = tmp_path / "tv-parity"

    def _seed(run_id: str, queued_at: int, source: str, strategy_id: str) -> None:
        rd = base / run_id
        rd.mkdir(parents=True)
        payload = {
            "run_id": run_id,
            "strategy_id": strategy_id,
            "source": source,
            "status": "completed",
            "queued_at": queued_at,
            "compare_from": 1,
            "compare_to": 2,
            "candle_summary": {
                "symbol": "ETHUSDT",
                "exchange": "bybit",
                "market_type": "perp",
                "timeframe": "15m",
                "valid_bars": 100,
                "from_time": 1,
                "to_time": 2,
            },
        }
        (rd / "tv_parity_result.json").write_text(json.dumps(payload), encoding="utf-8")

    _seed("a", 1, "tradingview_csv", "strat_a")
    _seed("b", 2, "exchange_data", "strat_a")
    _seed("c", 3, "tradingview_csv", "strat_b")

    app = FastAPI()
    app.include_router(tv_parity.router, prefix="/api")
    app.dependency_overrides[get_state] = lambda: state
    client = TestClient(app)

    by_strategy = client.get("/api/tv-parity/runs", params={"strategy_id": "strat_a"}).json()
    assert [e["run_id"] for e in by_strategy["items"]] == ["b", "a"]
    assert by_strategy["total"] == 2

    by_source = client.get("/api/tv-parity/runs", params={"source": "tradingview_csv"}).json()
    assert [e["run_id"] for e in by_source["items"]] == ["c", "a"]
    assert by_source["total"] == 2

    limited = client.get("/api/tv-parity/runs", params={"limit": 1}).json()
    assert len(limited["items"]) == 1
    assert limited["total"] == 3
    assert limited["limit"] == 1


def test_list_tv_parity_runs_skips_corrupt_and_orphan_dirs(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from openpine.gateway.deps import get_state

    state = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    base = tmp_path / "tv-parity"

    (base / "good").mkdir(parents=True)
    (base / "good" / "tv_parity_result.json").write_text(
        json.dumps({
            "run_id": "good",
            "strategy_id": "s1",
            "source": "tradingview_csv",
            "status": "completed",
            "queued_at": 100,
            "compare_from": 1,
            "compare_to": 2,
            "candle_summary": {
                "symbol": "BTCUSDT",
                "exchange": "binance",
                "market_type": "spot",
                "timeframe": "1h",
                "valid_bars": 1,
                "from_time": 1,
                "to_time": 2,
            },
        }),
        encoding="utf-8",
    )
    (base / "orphan").mkdir(parents=True)
    (base / "corrupt").mkdir(parents=True)
    (base / "corrupt" / "tv_parity_result.json").write_text("{not json", encoding="utf-8")
    (base / "stray.txt").write_text("ignore me", encoding="utf-8")

    app = FastAPI()
    app.include_router(tv_parity.router, prefix="/api")
    app.dependency_overrides[get_state] = lambda: state
    client = TestClient(app)

    response = client.get("/api/tv-parity/runs")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["run_id"] == "good"


def test_delete_tv_parity_run_removes_directory_and_404s_after(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from openpine.gateway.deps import get_state

    state = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    run_root = tmp_path / "tv-parity" / "run_42"
    (run_root / "openpine_outputs").mkdir(parents=True)
    (run_root / "openpine_outputs" / "plots.csv").write_text("time,value\n", encoding="utf-8")
    (run_root / "tv_parity_result.json").write_text(json.dumps({"run_id": "run_42"}), encoding="utf-8")

    app = FastAPI()
    app.include_router(tv_parity.router, prefix="/api")
    app.dependency_overrides[get_state] = lambda: state
    client = TestClient(app)

    response = client.delete("/api/tv-parity/runs/run_42")
    assert response.status_code == 204
    assert not run_root.exists()

    response = client.delete("/api/tv-parity/runs/run_42")
    assert response.status_code == 404

    response = client.delete("/api/tv-parity/runs/ghost")
    assert response.status_code == 404
