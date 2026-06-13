from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from marketdata_provider.contracts import Bar, BarQuery, InstrumentKey, parse_timeframe
from openpine.cli import runtime_helpers as rh


class Console:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *parts, **kwargs) -> None:
        self.lines.append(" ".join(str(p) for p in parts))


class Registry:
    def __init__(self) -> None:
        self.statuses: list[tuple[str, str]] = []

    def update_status(self, strategy_id: str, status: str) -> None:
        self.statuses.append((strategy_id, status))


class Config:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


_CONFIG_KEYS = [
    "symbol",
    "timeframe",
    "start_time",
    "end_time",
    "exchange",
    "market_type",
    "initial_capital",
    "default_qty_type",
    "default_qty_value",
    "commission_type",
    "commission_value",
    "slippage",
    "slippage_type",
    "exit_matching",
    "pyramiding",
    "margin_long",
    "margin_short",
    "process_orders_on_close",
    "calc_on_order_fills",
    "calc_on_every_tick",
    "use_bar_magnifier",
    "qty_step",
    "qty_rounding_mode",
    "max_bars_back",
    "score_start_time",
    "score_end_time",
    "max_pre_bars",
    "warmup_metadata",
    "export_resume_state",
    "content_hash_enabled",
    "collect_events",
    "collect_order_lifecycle",
    "capture_plots",
    "plot_from_ms",
    "plot_to_ms",
]
Config.__signature__ = inspect.Signature(
    [
        inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, default=None)
        for name in _CONFIG_KEYS
    ]
)


def _strategy(**overrides):
    values = dict(
        strategy_id="s1",
        name="Strategy",
        pine_id="pine1",
        artifact_id="art1",
        params_json='{"fast": 2}',
        params_hash="hash1",
        symbol="BTCUSDT",
        exchange="binance",
        market_type="spot",
        timeframe="1m",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _bar(t: int) -> Bar:
    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    return Bar(
        instrument=inst,
        timeframe=tf,
        time=t,
        time_close=t + 60_000,
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=10.0,
        closed=True,
    )


class ArtifactError(Exception):
    pass


class ArtifactStore:
    def get_artifact(self, artifact_id, pine_id):
        return {
            "compile_meta": {
                "translation_metadata": {
                    "declaration": {
                        "arguments": {
                            "initial_capital": 1234.0,
                            "commission_type": "cash_per_contract",
                            "close_entries_rule": "lifo",
                        }
                    }
                }
            }
        }


class Orchestrator:
    def __init__(self) -> None:
        self.provider = None

    def set_provider(self, provider) -> None:
        self.provider = provider

    def get_bars(self, query):
        return [_bar(query.start_ms), _bar(query.start_ms + 60_000)]


class EmptyOrchestrator(Orchestrator):
    def get_bars(self, query):
        return []


def _deps(**overrides):
    values = dict(
        ArtifactStore=ArtifactStore,
        BacktestArtifactError=ArtifactError,
        BarQuery=BarQuery,
        InstrumentKey=InstrumentKey,
        parse_timeframe=parse_timeframe,
        DataOrchestrator=Orchestrator,
        create_local_marketdata_provider_adapter=lambda: SimpleNamespace(
            _provider=SimpleNamespace(last_fetch_info={"source": "test"})
        ),
        load_strategy_class_from_artifact=lambda *args, **kwargs: type("GeneratedStrategy", (), {}),
        BacktestRunConfig=Config,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_prepare_strategy_backtest_inputs_history_and_error_branches():
    console = Console()
    registry = Registry()
    strategy = _strategy()

    prepared = rh._prepare_strategy_backtest_inputs(
        strategy=strategy,
        strategy_id="s1",
        from_date="2",
        to_date="180",
        capture_plots=True,
        capture_from="3",
        capture_to="4",
        history_from="1",
        warmup_bars=0,
        gap_policy="allow_with_metadata",
        now_ms=999_000,
        registry=registry,
        deps=_deps(),
        perf_counter=lambda: 1.0,
        console=console,
    )
    assert prepared.start_ms == 1000
    assert prepared.requested_start_ms == 2000
    assert prepared.params == {"fast": 2}
    assert prepared.config.kwargs["commission_type"] == "fixed_per_contract"
    assert prepared.provider._provider.last_fetch_info == {"source": "test"}

    with pytest.raises(SystemExit):
        rh._prepare_strategy_backtest_inputs(
            strategy=strategy,
            strategy_id="s1",
            from_date="2",
            to_date="3",
            capture_plots=False,
            capture_from=None,
            capture_to=None,
            history_from="3",
            warmup_bars=0,
            gap_policy="fail",
            now_ms=999_000,
            registry=registry,
            deps=_deps(),
            perf_counter=lambda: 1.0,
            console=console,
        )
    assert registry.statuses[-1] == ("s1", "paused")

    with pytest.raises(SystemExit):
        rh._prepare_strategy_backtest_inputs(
            strategy=strategy,
            strategy_id="s1",
            from_date="2",
            to_date="3",
            capture_plots=False,
            capture_from=None,
            capture_to=None,
            history_from=None,
            warmup_bars=5,
            gap_policy="fail",
            now_ms=999_000,
            registry=registry,
            deps=_deps(parse_timeframe=lambda value: SimpleNamespace(duration_ms=None)),
            perf_counter=lambda: 1.0,
            console=console,
        )
    assert registry.statuses[-1] == ("s1", "paused")


def test_prepare_strategy_replay_and_indicator_inputs_success_and_failures(monkeypatch):
    console = Console()
    registry = Registry()
    strategy = _strategy()

    replay = rh._prepare_strategy_replay_inputs(
        strategy=strategy,
        strategy_id="s1",
        from_date="1",
        to_date="3",
        now_ms=999_000,
        registry=registry,
        load_strategy_class=lambda *args, **kwargs: type("ReplayStrategy", (), {}),
        artifact_error_cls=ArtifactError,
        artifact_store_cls=ArtifactStore,
        bar_query_cls=BarQuery,
        instrument_key_cls=InstrumentKey,
        parse_timeframe_func=parse_timeframe,
        orchestrator_cls=Orchestrator,
        config_cls=Config,
        perf_counter=lambda: 1.0,
        console=console,
    )
    assert replay.bars
    assert replay.params == {"fast": 2}
    assert replay.config.kwargs["exit_matching"] == "LIFO"

    source = SimpleNamespace(id="pine1", active_artifact_id="art1")

    class SourceRegistry:
        def get_source(self, name):
            return source

        def close(self):
            pass

    prepared = rh._prepare_indicator_plot_inputs(
        name="pine",
        symbol="BTCUSDT",
        timeframe="1m",
        exchange="binance",
        market_type="spot",
        from_date="1",
        to_date="3",
        compare_from="1",
        compare_to="3",
        now_ms=999_000,
        registry_cls=SourceRegistry,
        parse_time_ms_func=lambda value: None if value is None else int(value) * 1000,
        load_generated_class=lambda *args: type("Indicator", (), {}),
        artifact_error_cls=ArtifactError,
        bar_query_cls=BarQuery,
        instrument_key_cls=InstrumentKey,
        parse_timeframe_func=parse_timeframe,
        orchestrator_cls=Orchestrator,
        provider_factory=lambda: SimpleNamespace(_provider=SimpleNamespace(last_fetch_info="info")),
        perf_counter=lambda: 1.0,
        console=console,
    )
    assert prepared.source is source
    assert prepared.generated_class.__name__ == "Indicator"
    assert prepared.bars
    assert prepared.data_fetch_info == "info"

    with pytest.raises(SystemExit):
        rh._prepare_indicator_plot_inputs(
            name="pine",
            symbol="BTCUSDT",
            timeframe="1m",
            exchange="binance",
            market_type="spot",
            from_date="3",
            to_date="1",
            compare_from=None,
            compare_to=None,
            now_ms=999_000,
            registry_cls=SourceRegistry,
            parse_time_ms_func=lambda value: None if value is None else int(value) * 1000,
            load_generated_class=lambda *args: type("Indicator", (), {}),
            artifact_error_cls=ArtifactError,
            bar_query_cls=BarQuery,
            instrument_key_cls=InstrumentKey,
            parse_timeframe_func=parse_timeframe,
            orchestrator_cls=Orchestrator,
            provider_factory=lambda: None,
            perf_counter=lambda: 1.0,
            console=console,
        )

    with pytest.raises(SystemExit):
        rh._prepare_indicator_plot_inputs(
            name="pine",
            symbol="BTCUSDT",
            timeframe="1m",
            exchange="binance",
            market_type="spot",
            from_date="1",
            to_date="3",
            compare_from=None,
            compare_to=None,
            now_ms=999_000,
            registry_cls=SourceRegistry,
            parse_time_ms_func=lambda value: None if value is None else int(value) * 1000,
            load_generated_class=lambda *args: (_ for _ in ()).throw(ArtifactError("bad artifact")),
            artifact_error_cls=ArtifactError,
            bar_query_cls=BarQuery,
            instrument_key_cls=InstrumentKey,
            parse_timeframe_func=parse_timeframe,
            orchestrator_cls=Orchestrator,
            provider_factory=lambda: None,
            perf_counter=lambda: 1.0,
            console=console,
        )

    with pytest.raises(SystemExit):
        rh._prepare_indicator_plot_inputs(
            name="pine",
            symbol="BTCUSDT",
            timeframe="1m",
            exchange="binance",
            market_type="spot",
            from_date="1",
            to_date="3",
            compare_from=None,
            compare_to=None,
            now_ms=999_000,
            registry_cls=SourceRegistry,
            parse_time_ms_func=lambda value: None if value is None else int(value) * 1000,
            load_generated_class=lambda *args: type("Indicator", (), {}),
            artifact_error_cls=ArtifactError,
            bar_query_cls=BarQuery,
            instrument_key_cls=InstrumentKey,
            parse_timeframe_func=parse_timeframe,
            orchestrator_cls=EmptyOrchestrator,
            provider_factory=lambda: None,
            perf_counter=lambda: 1.0,
            console=console,
        )


def test_indicator_runtime_and_strategy_persistence_error_edges(monkeypatch, tmp_path):
    console = Console()
    calls = []

    def fake_execute(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(plots=[])

    monkeypatch.setattr(rh, "_execute_indicator_plot_runtime", fake_execute)
    result, elapsed = rh._run_indicator_plot_runtime(
        generated_class=object,
        bars=[_bar(0), _bar(60_000)],
        symbol="BTCUSDT",
        timeframe="1m",
        exchange="binance",
        market_type="spot",
        provider=SimpleNamespace(_provider="provider"),
        compare_from_ms=0,
        compare_to_ms=120_000,
        progress_every=1,
        console=console,
        perf_counter=lambda: 2.0,
    )
    assert result.plots == []
    assert elapsed == 0
    assert calls[0]["config"].symbol == "BTCUSDT"

    class BadStore:
        def create_run(self, request):
            raise RuntimeError("save boom")

    rh._save_strategy_backtest_result_safely(
        store=BadStore(),
        request_cls=lambda **kwargs: SimpleNamespace(**kwargs),
        strategy=_strategy(),
        start_ms=1,
        end_ms=2,
        visible_start_ms=1,
        effective_pre_bars=0,
        bars_total=1,
        data_fetch_info=None,
        result=SimpleNamespace(raw_result=SimpleNamespace(trades=[], open_trades=[], plots=[])),
        capture_plots=True,
        timings={},
        total_started=0.0,
        perf_counter=lambda: 1.0,
        console=console,
    )
    assert any("failed to save" in line for line in console.lines)

    class FailingAdapter:
        def run(self, *args, **kwargs):
            raise RuntimeError("engine boom")

    deps = SimpleNamespace(BacktestEngineAdapter=FailingAdapter)
    registry = Registry()
    with pytest.raises(SystemExit):
        rh._run_strategy_backtest_or_exit(
            deps=deps,
            prepared=SimpleNamespace(
                strategy_class=object,
                bars=[_bar(0)],
                config=Config(),
                params={},
                provider=SimpleNamespace(_provider=None),
                effective_pre_bars=0,
            ),
            registry=registry,
            strategy_id="s1",
            console=console,
            perf_counter=lambda: 1.0,
        )
    assert registry.statuses[-1] == ("s1", "error")

    import openpine.config as config_mod
    import openpine.state.store as state_store_mod

    monkeypatch.setattr(
        config_mod.OpenPineConfig,
        "load",
        classmethod(lambda cls: SimpleNamespace(data_dir=tmp_path)),
    )

    class Store:
        def __init__(self, root):
            self.root = root

        def save_runtime_snapshot(self, **kwargs):
            return SimpleNamespace(snapshot_id="snap1")

    monkeypatch.setattr(state_store_mod, "StateStore", Store)
    rh._save_strategy_resume_snapshot(
        strategy=_strategy(),
        prepared=SimpleNamespace(bars=[_bar(60_000)], end_ms=120_000),
        result=SimpleNamespace(resume_state={"runtime_state": {}}),
        console=console,
    )
    assert any("State snapshot saved" in line for line in console.lines)
