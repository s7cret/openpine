from __future__ import annotations

from types import SimpleNamespace

import pytest

from openpine.gateway.routes import backtest as routes


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
    )
    routes._artifact_backtest_process_entry(object(), spec, [], object(), {})

    assert recaptures == []
    assert seen["source"] == b"STAMPED"
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
        htf_bars=htf_bars,
    )
    routes._artifact_backtest_process_entry(object(), spec, [], object(), {})
    assert "error" not in seen
    assert seen["htf_bars"] == htf_bars
