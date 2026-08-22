from __future__ import annotations

from types import SimpleNamespace

import pytest

from openpine.gateway.routes import backtest as routes
from openpine.run_identity import execution_data_snapshot_hash


def _bar() -> SimpleNamespace:
    return SimpleNamespace(
        time=0,
        time_close=59_999,
        open=1,
        high=2,
        low=0.5,
        close=1.5,
        volume=3,
    )


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1m",
        start_time=0,
        end_time=60_000,
    )


def _snapshot_hash(bars: list[object], supplemental_bars=None) -> str:
    return execution_data_snapshot_hash(
        bars=bars,
        supplemental_bars=supplemental_bars,
        exchange="binance",
        market="spot",
        symbol="BTCUSDT",
        timeframe="1m",
        start_ms=0,
        end_ms=60_000,
        finality_policy="CLOSED_BAR_ONLY",
    )


def test_artifact_worker_uses_stamped_source_not_recapture(monkeypatch) -> None:
    recaptures: list[tuple] = []

    def recapture(*args, **kwargs):
        recaptures.append((args, kwargs))
        return b"LATER"

    monkeypatch.setattr(
        "openpine.runtime.isolated_run.capture_generated_source", recapture
    )

    seen: dict[str, object] = {}

    class Adapter:
        def run_isolated(self, source, bars, config, **kwargs):
            seen["source"] = source
            seen["params"] = kwargs.get("params")
            return SimpleNamespace(ok=True)

    monkeypatch.setattr(
        "openpine.runtime.engine.BacktestEngineAdapter", lambda: Adapter()
    )
    monkeypatch.setattr(routes, "_put_backtest_process_result", lambda out, result: seen.setdefault("result", result))
    monkeypatch.setattr(routes, "_put_backtest_process_error", lambda out, exc: seen.setdefault("error", exc))

    spec = routes._ArtifactBacktestSpec(
        pine_id="pine-1",
        artifact_id="art-1",
        symbol="BTCUSDT",
        timeframe="1m",
        cache_dir="/tmp",
        exchange="binance",
        market="spot",
        prefetch_end_ms=60_000,
        source=b"STAMPED",
        data_snapshot_hash=_snapshot_hash([_bar()]),
    )
    routes._artifact_backtest_process_entry(
        object(), spec, [_bar()], _config(), {"qty": 3}
    )

    assert recaptures == []
    assert seen["source"] == b"STAMPED"
    assert seen["params"] == {"qty": 3}
    assert getattr(seen["result"], "ok", None) is True
    assert "error" not in seen


def test_artifact_spec_requires_captured_source() -> None:
    with pytest.raises(TypeError):
        routes._ArtifactBacktestSpec(
            pine_id="pine-1",
            artifact_id="art-1",
            symbol="BTCUSDT",
            timeframe="1m",
            cache_dir="/tmp",
            exchange="binance",
            market="spot",
            prefetch_end_ms=60_000,
        )


def test_artifact_worker_forwards_confirmed_htf_bars(monkeypatch) -> None:
    seen: dict[str, object] = {}
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

    class Adapter:
        def run_isolated(self, source, bars, config, **kwargs):
            seen["htf_bars"] = kwargs.get("htf_bars")
            return SimpleNamespace(ok=True)

    monkeypatch.setattr(
        "openpine.runtime.engine.BacktestEngineAdapter", lambda: Adapter()
    )
    monkeypatch.setattr(routes, "_put_backtest_process_result", lambda out, result: None)
    monkeypatch.setattr(routes, "_put_backtest_process_error", lambda out, exc: seen.setdefault("error", exc))

    spec = routes._ArtifactBacktestSpec(
        pine_id="pine-1",
        artifact_id="art-1",
        symbol="BTCUSDT",
        timeframe="1m",
        cache_dir="/tmp",
        exchange="binance",
        market="spot",
        prefetch_end_ms=60_000,
        source=b"STAMPED",
        data_snapshot_hash=_snapshot_hash([_bar()], htf_bars),
        htf_bars=htf_bars,
    )
    routes._artifact_backtest_process_entry(object(), spec, [_bar()], _config(), {})
    assert "error" not in seen
    assert seen["htf_bars"] == htf_bars


def test_artifact_worker_stamps_confirmed_provider_htf_bars(monkeypatch) -> None:
    seen: dict[str, object] = {}
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

    class Adapter:
        def run_isolated(self, source, bars, config, **kwargs):
            seen["htf_bars"] = kwargs.get("htf_bars")
            return SimpleNamespace(ok=True)

    monkeypatch.setattr(
        "openpine.runtime.engine.BacktestEngineAdapter", lambda: Adapter()
    )
    monkeypatch.setattr(routes, "_put_backtest_process_result", lambda out, result: None)
    monkeypatch.setattr(
        routes, "_put_backtest_process_error", lambda out, exc: seen.setdefault("error", exc)
    )

    spec = routes._ArtifactBacktestSpec(
        pine_id="pine-1",
        artifact_id="art-1",
        symbol="BTCUSDT",
        timeframe="1m",
        cache_dir="/tmp",
        exchange="binance",
        market="spot",
        prefetch_end_ms=60_000,
        source=b"STAMPED",
        data_snapshot_hash=_snapshot_hash([_bar()]),
    )
    routes._artifact_backtest_process_entry(object(), spec, bars, _config(), {})
    assert "error" not in seen
    assert seen["htf_bars"] == [
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


def test_artifact_worker_fails_closed_when_chart_time_close_is_missing(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class Adapter:
        def run_isolated(self, source, bars, config, **kwargs):
            seen["htf_bars"] = kwargs.get("htf_bars")
            return SimpleNamespace(ok=True)

    monkeypatch.setattr(
        "openpine.runtime.engine.BacktestEngineAdapter", lambda: Adapter()
    )
    monkeypatch.setattr(routes, "_put_backtest_process_result", lambda out, result: None)
    monkeypatch.setattr(
        routes, "_put_backtest_process_error", lambda out, exc: seen.setdefault("error", exc)
    )

    spec = routes._ArtifactBacktestSpec(
        pine_id="pine-1",
        artifact_id="art-1",
        symbol="BTCUSDT",
        timeframe="1m",
        cache_dir="/tmp",
        exchange="binance",
        market="spot",
        prefetch_end_ms=60_000,
        source=b"STAMPED",
        data_snapshot_hash=_snapshot_hash([_bar()]),
    )
    routes._artifact_backtest_process_entry(
        object(),
        spec,
        [SimpleNamespace(time=1, open=1, high=1, low=1, close=1, volume=1)],
        _config(),
        {},
    )
    assert "htf_bars" not in seen
    assert "time_close" in str(seen["error"])


def test_artifact_worker_keeps_none_when_other_htf_unconfirmed(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class Adapter:
        def run_isolated(self, source, bars, config, **kwargs):
            seen["htf_bars"] = kwargs.get("htf_bars")
            return SimpleNamespace(ok=True)

    monkeypatch.setattr(
        "openpine.runtime.engine.BacktestEngineAdapter", lambda: Adapter()
    )
    monkeypatch.setattr(routes, "_put_backtest_process_result", lambda out, result: None)
    monkeypatch.setattr(
        routes, "_put_backtest_process_error", lambda out, exc: seen.setdefault("error", exc)
    )
    spec = routes._ArtifactBacktestSpec(
        pine_id="pine-1",
        artifact_id="art-1",
        symbol="BTCUSDT",
        timeframe="1m",
        cache_dir="/tmp",
        exchange="binance",
        market="spot",
        prefetch_end_ms=60_000,
        source=b"STAMPED",
        data_snapshot_hash=_snapshot_hash([_bar()]),
        htf_timeframe="1D",
    )
    routes._artifact_backtest_process_entry(
        object(),
        spec,
        [
            SimpleNamespace(
                time=0,
                time_close=59_999,
                open=1,
                high=2,
                low=0.5,
                close=1.5,
                volume=3,
            )
        ],
        _config(),
        {},
    )
    assert "error" not in seen
    assert seen["htf_bars"] is None


def test_backtest_helper_fetches_explicit_htf_timeframe() -> None:
    loaded: list[str] = []
    chart = [
        SimpleNamespace(time=0, time_close=59_999, open=1, high=2, low=0.5, close=1.5, volume=3)
    ]
    fetched = [
        SimpleNamespace(time=0, time_close=86_399_999, open=40, high=43, low=39, close=42, volume=1)
    ]

    def load_bars(query):
        loaded.append(str(getattr(query.timeframe, "canonical", query.timeframe)))
        return SimpleNamespace(bars=fetched)

    strategy = SimpleNamespace(symbol="btcusdt", timeframe="1m", exchange="binance", market_type="spot")
    stamped = routes._confirmed_htf_bars_for_backtest(
        chart,
        strategy=strategy,
        requested_timeframe="1D",
        load_bars=load_bars,
        from_ms=0,
        to_ms=60_000,
    )
    assert "1D" in loaded
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


def test_backtest_helper_same_timeframe_does_not_refetch() -> None:
    loaded: list[object] = []
    chart = [
        SimpleNamespace(time=0, time_close=59_999, open=1, high=2, low=0.5, close=1.5, volume=3)
    ]
    strategy = SimpleNamespace(symbol="BTCUSDT", timeframe="1m", exchange="binance", market_type="spot")
    stamped = routes._confirmed_htf_bars_for_backtest(
        chart,
        strategy=strategy,
        requested_timeframe="1m",
        load_bars=lambda query: loaded.append(query) or SimpleNamespace(bars=chart),
        from_ms=0,
        to_ms=60_000,
    )
    assert loaded == []
    assert stamped[0]["timeframe"] == "1m"
