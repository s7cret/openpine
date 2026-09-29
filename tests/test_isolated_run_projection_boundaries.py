"""Contract-level boundaries for the parent-side, non-generated execution adapters."""

from types import SimpleNamespace

import pytest
from openpine_contracts import SemanticProfile

from openpine.runtime.isolated_run import (
    IsolatedRunError,
    _chart_bar_payload,
    _confirmed_htf_bars_for_timeframe,
    _generated_semantic_profile,
    _semantic_profile,
    _stamp_confirmed_htf_bars,
    _strategy_projection,
)


@pytest.mark.parametrize("value", [None, "", "not-a-profile"])
def test_semantic_profile_rejects_missing_and_unknown_values(value):
    with pytest.raises(IsolatedRunError, match="semantic_profile"):
        _semantic_profile(SimpleNamespace(semantic_profile=value))
    with pytest.raises(IsolatedRunError, match="semantic_profile"):
        _generated_semantic_profile(value)


def test_semantic_profile_accepts_sealed_values_but_not_guessed_aliases():
    assert _semantic_profile(SimpleNamespace(semantic_profile=SemanticProfile.STRICT_5X)) == SemanticProfile.STRICT_5X.value
    assert _semantic_profile(SimpleNamespace(semantic_profile=SemanticProfile.STRICT_5X.value)) == SemanticProfile.STRICT_5X.value
    assert _generated_semantic_profile(SemanticProfile.LEGACY_4X) == SemanticProfile.LEGACY_4X.value
    assert _generated_semantic_profile(SemanticProfile.LEGACY_4X.value) == SemanticProfile.LEGACY_4X.value


@pytest.mark.parametrize("field", ["symbol", "timeframe", "time", "time_close", "open", "high", "low", "close"])
def test_htf_bars_fail_closed_if_confirmation_or_required_field_missing(field):
    bar = {"symbol": "X", "timeframe": "60", "time": 10, "time_close": 20,
           "open": 1, "high": 2, "low": 0, "close": 1}
    bar.pop(field)
    with pytest.raises(IsolatedRunError, match=field):
        _stamp_confirmed_htf_bars([bar])


@pytest.mark.parametrize("field", ["time", "time_close", "open", "high", "low", "close"])
def test_htf_numeric_fields_reject_non_numeric_values(field):
    bar = {"symbol": "X", "timeframe": "60", "time": 10, "time_close": 20,
           "open": 1, "high": 2, "low": 0, "close": 1}
    bar[field] = "not-a-number"
    with pytest.raises(IsolatedRunError, match="numeric fields"):
        _stamp_confirmed_htf_bars([bar])


def test_htf_rejects_non_object_and_prefers_actual_requested_timeframe_bars():
    with pytest.raises(IsolatedRunError, match="must be an object"):
        _stamp_confirmed_htf_bars([42])
    chart = SimpleNamespace(time=10, time_close=20, open=1, high=2, low=0, close=1, volume=3)
    htf = {"time": 0, "time_close": 600, "open": 9, "high": 10, "low": 8, "close": 9}
    assert _confirmed_htf_bars_for_timeframe(chart_bars=[chart], symbol="X", chart_timeframe="1", requested_timeframe="60", fetched_htf_bars=[htf]) == [
        {"symbol": "X", "timeframe": "60", "time": 0, "time_close": 600,
         "open": 9.0, "high": 10.0, "low": 8.0, "close": 9.0, "volume": 0.0}
    ]
    assert _confirmed_htf_bars_for_timeframe(chart_bars=[chart], symbol="X", chart_timeframe="1", requested_timeframe="1", fetched_htf_bars=[htf])[0]["time_close"] == 20
    assert _confirmed_htf_bars_for_timeframe(chart_bars=[chart], symbol="X", chart_timeframe="1", requested_timeframe="60", fetched_htf_bars=None) is None
    assert _confirmed_htf_bars_for_timeframe(chart_bars=[{"time": 10, "open": 1}], symbol="X", chart_timeframe="1", requested_timeframe=None) is None


@pytest.mark.parametrize("field", ["time", "open", "high", "low", "close"])
def test_chart_bar_requires_all_fields_for_dicts_and_objects(field):
    valid = {"time": 10, "open": 1, "high": 2, "low": 0, "close": 1}
    bad = dict(valid)
    bad.pop(field)
    with pytest.raises(IsolatedRunError, match=field):
        _chart_bar_payload(bad)
    with pytest.raises(IsolatedRunError, match=field):
        _chart_bar_payload(SimpleNamespace(**bad))


@pytest.mark.parametrize("field,value", [("open", "bad"), ("time", "bad"), ("time_close", "bad")])
def test_chart_bar_rejects_bad_numeric_payload(field, value):
    bar = {"time": 10, "open": 1, "high": 2, "low": 0, "close": 1, "time_close": 20}
    bar[field] = value
    with pytest.raises(IsolatedRunError, match="numeric"):
        _chart_bar_payload(bar)


def test_chart_bar_canonical_numeric_conversion_and_optional_volume():
    bar = SimpleNamespace(time="10", open="1", high=2, low=0, close="1.5", volume=None, time_close="20")
    assert _chart_bar_payload(bar) == {"time": 10, "open": 1.0, "high": 2.0, "low": 0.0,
                                       "close": 1.5, "volume": 0.0, "time_close": 20}
    assert "time_close" not in _chart_bar_payload({"time": 10, "open": 1, "high": 2, "low": 0, "close": 1})


@pytest.mark.parametrize("broken,match", [
    ({}, "broker/ledger"),
    ({"broker": {}, "ledger": {}}, "position"),
    ({"broker": {"position": {}}, "ledger": {}}, "trade projection"),
    ({"broker": {"position": {}}, "ledger": {"open_trades": [], "closed_trades": [], "fills": None}}, "fill projection"),
])
def test_strategy_projection_rejects_incomplete_worker_payload(broken, match):
    with pytest.raises(IsolatedRunError, match=match):
        _strategy_projection(broken)


def test_strategy_projection_counts_actual_closed_trades_and_retains_tape():
    fills = [{"id": "fill-1"}]
    active = [{"entry_id": "entry-1"}]
    closed = [{"profit": 4}, {"profit": -2}, {"profit": 0}, {"profit": None}]
    result = _strategy_projection({"broker": {"cash": 100, "equity": 104, "position": {"size": 2,
        "avg_price": 9, "realized_profit": 2, "open_profit": 1}, "max_drawdown": 3},
        "ledger": {"open_trades": active, "closed_trades": closed, "fills": fills}})
    assert {key: result[key] for key in ("grossprofit", "grossloss", "netprofit", "openprofit", "position_size", "opentrades", "closedtrades", "wintrades", "losstrades", "eventrades")} == {
        "grossprofit": 4.0, "grossloss": 2.0, "netprofit": 2.0, "openprofit": 1.0, "position_size": 2.0,
        "opentrades": 1, "closedtrades": 4, "wintrades": 1, "losstrades": 1, "eventrades": 2,
    }
    assert result["position_entry_name"] == "entry-1"
    assert result["fills"] is fills
    assert result["closed_trade_log"] is closed
