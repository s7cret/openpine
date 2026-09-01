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
