from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from marketdata_provider.contracts import Bar, InstrumentKey, parse_timeframe

from openpine.cli import runtime_helpers as rh
from openpine.registry.strategies import StrategyInstance


class Console:
    def __init__(self) -> None: self.messages: list[str] = []
    def print(self, *parts, **kwargs): self.messages.append(" ".join(str(p) for p in parts))


class Registry:
    def __init__(self, strategy=None) -> None: self.strategy = strategy; self.statuses=[]
    def get_strategy(self, strategy_id):
        if self.strategy is None: raise KeyError(strategy_id)
        return self.strategy
    def update_status(self, strategy_id, status): self.statuses.append((strategy_id, status))


def _strategy(**kw) -> StrategyInstance:
    values = dict(
        strategy_id="s1", name="S", pine_id="pine", artifact_id="artifact", params_json='{"p":1}', params_hash="ph",
        exchange="binance", market_type="spot", symbol="BTCUSDT", price_type="trade", timeframe="1m", mode="paper", status="paused", enabled=False, created_at=1, updated_at=2,
    )
    values.update(kw)
    return StrategyInstance(**values)


def _bar(t: int) -> Bar:
    tf = parse_timeframe("1m")
    return Bar(instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"), timeframe=tf, time=t, time_close=t+60000, open=1, high=2, low=0, close=1.5, volume=5, closed=True)


class Config:
    def __init__(self, **kw): self.kw = kw

_CONFIG_KEYS = [
    "symbol", "timeframe", "start_time", "end_time", "exchange", "market_type",
    "initial_capital", "default_qty_type", "default_qty_value", "commission_type",
    "commission_value", "slippage", "slippage_type", "exit_matching", "pyramiding",
    "margin_long", "margin_short", "process_orders_on_close", "calc_on_order_fills",
    "calc_on_every_tick", "use_bar_magnifier", "qty_step", "qty_rounding_mode",
    "max_bars_back", "score_start_time", "score_end_time", "max_pre_bars",
    "warmup_metadata", "export_resume_state", "content_hash_enabled", "collect_events",
    "collect_order_lifecycle", "capture_plots", "plot_from_ms", "plot_to_ms",
]
Config.__signature__ = inspect.Signature([
    inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, default=None)
    for name in _CONFIG_KEYS
])


def test_runtime_helper_config_window_readiness_and_progress(tmp_path: Path):
    console = Console(); strategy = _strategy()
    assert rh._plot_record_count(None) == 0
    assert rh._plot_record_count([1, 2]) == 2
    assert rh._plot_record_count(SimpleNamespace(get_records=lambda: [1])) == 1
    assert rh._bars_data_fingerprint([_bar(0)]) == rh._bars_data_fingerprint([_bar(0)])
    cfg = rh._build_strategy_backtest_config(strategy=strategy, decl_args={"commission_type": "cash_per_order", "close_entries_rule": "any", "initial_capital": 123}, start_ms=10, end_ms=20, requested_start_ms=11, warmup_bars=2, effective_pre_bars=1, capture_plots=True, capture_from_ms=12, capture_to_ms=18, config_cls=Config)
    assert cfg.kw["commission_type"] == "fixed_per_order" and cfg.kw["score_start_time"] == 11
    replay = rh._build_strategy_replay_config(strategy=strategy, decl_args={"close_entries_rule": "lifo"}, start_ms=1, end_ms=2, config_cls=Config)
    assert replay.kw["exit_matching"] == "LIFO"
    assert rh._strategy_backtest_readiness_error(_strategy(pine_id=""))
    assert rh._strategy_backtest_readiness_error(_strategy(artifact_id=""))
    assert rh._strategy_backtest_readiness_error(strategy) is None
    reg = Registry(strategy)
    assert rh._get_strategy_or_exit(registry=reg, strategy_id="s1", console=console) is strategy
    with pytest.raises(SystemExit): rh._get_strategy_or_exit(registry=Registry(), strategy_id="missing", console=console)
    with pytest.raises(SystemExit): rh._exit_if_strategy_not_ready_for_backtest(strategy=_strategy(pine_id=""), strategy_id="s1", registry=reg, console=console)
    assert reg.statuses[-1] == ("s1", "paused")
    start, end, cf, ct = rh._parse_strategy_backtest_window(from_date="1", to_date="2", capture_from="1", capture_to="2", now_ms=9)
    assert (start, end, cf, ct) == (1000, 2000, 1000, 2000)
    with pytest.raises(SystemExit): rh._parse_valid_strategy_backtest_window(from_date="2", to_date="1", capture_from=None, capture_to=None, now_ms=9, registry=reg, strategy_id="s1", console=console)
    progress = rh._build_progress_callback(bars_total=10, console=console, progress_every=3)
    progress(1, 10); progress(3, 10); progress(10, 10)
    assert any("runtime" in msg for msg in console.messages)
    assert rh._build_progress_callback(bars_total=10, console=console, progress_every=0) is None
    rh._print_strategy_command_header(label="L", strategy_id="s1", strategy=strategy, from_date=None, to_date=None, console=console)
    rh._print_backtest_result_summary(SimpleNamespace(status="ok", bars_processed=4, uses_backtest_engine=True), console=console)
    assert rh._ensure_output_dir(str(tmp_path / "out")).exists()


def test_runtime_helper_strategy_and_indicator_io_paths(tmp_path: Path):
    console = Console(); strategy = _strategy()
    class ArtifactError(Exception): pass
    def loader(*args, **kwargs): return object()
    cls, elapsed = rh._load_strategy_backtest_class(strategy=strategy, load_strategy_class=loader, perf_counter=lambda: 1.0)
    assert cls is not None and elapsed == 0
    def bad_loader(*args, **kwargs): raise ArtifactError("bad")
    with pytest.raises(SystemExit): rh._load_strategy_backtest_class_or_exit(strategy=strategy, strategy_id="s1", registry=Registry(strategy), load_strategy_class=bad_loader, artifact_error_cls=ArtifactError, perf_counter=lambda: 1.0, console=console)
    with pytest.raises(SystemExit): rh._exit_if_no_strategy_bars(bars=[], strategy=strategy, start_ms=0, end_ms=1, from_date=None, to_date=None, registry=Registry(strategy), strategy_id="s1", console=console)

    params, cfg = rh._build_strategy_backtest_params_and_config(strategy=strategy, decl_args={}, params_json='{"x": 2}', start_ms=0, end_ms=1, requested_start_ms=0, warmup_bars=0, effective_pre_bars=0, capture_plots=False, capture_from_ms=None, capture_to_ms=None, config_cls=Config)
    assert params == {"x": 2} and cfg.kw["plot_from_ms"] is None
    req = rh._build_strategy_backtest_run_request(strategy=strategy, start_ms=1, end_ms=2, request_cls=lambda **kw: SimpleNamespace(**kw))
    assert req.strategy_id == "s1" and req.from_time == 1
    assert rh._prepare_strategy_backtest_runtime(object, console)[1] is None

    source = SimpleNamespace(id="pine", active_artifact_id="artifact")
    class SourceRegistry:
        def __init__(self, ok=True): self.ok = ok; self.closed = False
        def get_source(self, name):
            if not self.ok: raise KeyError(name)
            return source
        def close(self): self.closed = True
    assert rh._load_pine_source_or_exit(registry_cls=SourceRegistry, name="x", console=console) is source
    class MissingRegistry(SourceRegistry):
        def __init__(self): super().__init__(False)
    with pytest.raises(SystemExit): rh._load_pine_source_or_exit(registry_cls=MissingRegistry, name="x", console=console)
    with pytest.raises(SystemExit): rh._require_active_pine_artifact(SimpleNamespace(active_artifact_id=None), name="x", console=console)
    generated, dt = rh._load_generated_class_timed(source=source, load_generated_class=lambda pine, art: "cls", perf_counter=lambda: 2.0)
    assert generated == "cls" and dt == 0
    window = rh._parse_indicator_plot_window(from_date="1", to_date=None, compare_from="2", compare_to="3", parse_time_ms_func=lambda v: None if v is None else int(v), now_ms=9)
    assert window == (1, 9, 2, 3)
    rh._print_indicator_plot_header(name="p", source=source, symbol="BTC", exchange="binance", market_type="spot", timeframe="1m", from_date="1", to_date=None, console=console)

    result = SimpleNamespace(plots=[SimpleNamespace(bar_time=1, bar_index=0, value=2, title="p")])
    rows_path, rows, elapsed = rh._write_indicator_plot_outputs(backend_result=result, output_path=tmp_path, compare_from_ms=None, compare_to_ms=None, export_plot_records_func=lambda records, path, from_ms=None, to_ms=None: path.write_text("x", encoding="utf-8") or len(records), perf_counter=lambda: 3.0)
    assert rows_path.exists() and rows == 1 and elapsed == 0
    rh._write_indicator_plot_run_meta(name="n", source=source, symbol="BTC", exchange="binance", market_type="spot", timeframe="1m", start_ms=1, end_ms=2, compare_from_ms=None, compare_to_ms=None, bars_total=1, data_fetch_info={"source":"test"}, plots_rows=1, timings={}, plots_csv=rows_path, output_path=tmp_path, write_json_func=lambda path, payload: path.write_text(json.dumps(payload), encoding="utf-8"), console=console)
    assert (tmp_path / "run_meta.json").exists()
