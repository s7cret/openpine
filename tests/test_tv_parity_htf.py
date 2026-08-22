from __future__ import annotations

from types import SimpleNamespace

import pytest
from openpine_contracts import AdmitError

from openpine.gateway.routes.tv_parity import _run_isolated_tv_replay
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


def _snapshot_hash(bars, supplemental_bars=None) -> str:
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


def test_isolated_tv_replay_forwards_confirmed_htf_bars() -> None:
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

    bars = [_bar()]
    result = _run_isolated_tv_replay(
        Adapter(),
        b"STAMPED",
        bars,
        _config(),
        {},
        None,
        htf_bars=htf_bars,
        expected_data_snapshot_hash=_snapshot_hash(bars, htf_bars),
    )
    assert result.ok is True
    assert seen["htf_bars"] == htf_bars


def test_isolated_tv_replay_stamps_confirmed_provider_htf_bars() -> None:
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

    result = _run_isolated_tv_replay(
        Adapter(),
        b"STAMPED",
        bars,
        _config(),
        {},
        None,
        expected_data_snapshot_hash=_snapshot_hash(bars),
    )
    assert result.ok is True
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


def test_isolated_tv_replay_fails_closed_without_time_close() -> None:
    seen: dict[str, object] = {}

    class Adapter:
        def run_isolated(self, source, bars, config, **kwargs):
            seen["htf_bars"] = kwargs.get("htf_bars")
            return SimpleNamespace(ok=True)

    with pytest.raises(AdmitError, match="time_close"):
        _run_isolated_tv_replay(
            Adapter(),
            b"STAMPED",
            [SimpleNamespace(time=1, open=1, high=1, low=1, close=1, volume=1)],
            _config(),
            {},
            None,
            expected_data_snapshot_hash="sha256:" + "a" * 64,
        )
    assert "htf_bars" not in seen


def test_isolated_tv_replay_keeps_none_when_other_htf_unconfirmed() -> None:
    seen: dict[str, object] = {}

    class Adapter:
        def run_isolated(self, source, bars, config, **kwargs):
            seen["htf_bars"] = kwargs.get("htf_bars")
            return SimpleNamespace(ok=True)

    bars = [_bar()]
    result = _run_isolated_tv_replay(
        Adapter(),
        b"STAMPED",
        bars,
        _config(),
        {},
        None,
        htf_timeframe="1D",
        expected_data_snapshot_hash=_snapshot_hash(bars),
    )
    assert result.ok is True
    assert seen["htf_bars"] is None


def test_tv_parity_run_wires_explicit_htf_timeframe() -> None:
    import inspect

    from openpine.gateway.routes import tv_parity

    source = inspect.getsource(tv_parity.run_tv_parity)
    assert "htf_timeframe" in source
    assert "mtf_series_json" in source
    bg = inspect.getsource(tv_parity._run_tv_parity_background)
    assert "_confirmed_htf_bars_for_backtest" in bg
    assert "htf_bars=stamped_htf" in bg
    assert "mtf_series=mtf_series" in bg


def test_tv_parity_mtf_form_parser_is_strict_and_canonical() -> None:
    import pytest
    from fastapi import HTTPException

    from openpine.gateway.routes.tv_parity import _parse_mtf_series_form

    assert _parse_mtf_series_form(
        '[{"symbol":"btcusdt","timeframe":"1d"},'
        '{"symbol":"ethusdt","timeframe":"4H"}]',
        chart_symbol="BTCUSDT",
        htf_timeframe=None,
    ) == [
        {"symbol": "BTCUSDT", "timeframe": "1D"},
        {"symbol": "ETHUSDT", "timeframe": "4h"},
    ]
    with pytest.raises(HTTPException, match="must decode to an array"):
        _parse_mtf_series_form(
            '{"symbol":"BTCUSDT"}',
            chart_symbol="BTCUSDT",
            htf_timeframe=None,
        )
    with pytest.raises(HTTPException, match="cannot be combined"):
        _parse_mtf_series_form(
            '[{"symbol":"BTCUSDT","timeframe":"4h"}]',
            chart_symbol="BTCUSDT",
            htf_timeframe="1D",
        )
