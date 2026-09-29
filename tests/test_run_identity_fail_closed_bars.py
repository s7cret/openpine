"""Fail-closed execution-data identity for malformed bars and query windows."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from openpine_contracts import AdmitError

from openpine.run_identity import execution_data_snapshot_hash


_QUERY = {
    "exchange": "binance", "market": "spot", "symbol": "SOLUSDT", "timeframe": "1m",
    "start_ms": 0, "end_ms": 60_000, "finality_policy": "CLOSED_BAR_ONLY",
}
_BAR = {
    "time": 0, "time_close": 60_000, "open": Decimal("10.25"),
    "high": Decimal("11"), "low": Decimal("10"), "close": Decimal("10.75"),
    "volume": None,
}


@pytest.mark.parametrize("overrides,message", [
    ({"exchange": ""}, "query identity is incomplete"),
    ({"timeframe": None}, "query identity is incomplete"),
    ({"start_ms": True}, "start_ms must be an integer"),
    ({"end_ms": 1.0}, "end_ms must be an integer"),
    ({"end_ms": -1}, "range is invalid"),
])
def test_snapshot_rejects_incomplete_query(overrides: dict, message: str) -> None:
    with pytest.raises(AdmitError, match=message) as exc:
        execution_data_snapshot_hash(bars=[_BAR], **{**_QUERY, **overrides})
    assert exc.value.code == "DATA_SNAPSHOT_INVALID"


@pytest.mark.parametrize("field,value,message", [
    ("time", True, "bars\\[0\\].time must be an integer"),
    ("time_close", -1, "bars\\[0\\] closes before it opens"),
    ("open", False, "bars\\[0\\].open must be numeric"),
    ("high", float("nan"), "bars\\[0\\].high must be finite"),
    ("low", Decimal("Infinity"), "bars\\[0\\].low must be finite"),
    ("close", "", "bars\\[0\\].close must be numeric"),
    ("volume", object(), "bars\\[0\\].volume must be numeric"),
])
def test_snapshot_rejects_invalid_market_bar(field: str, value: object, message: str) -> None:
    with pytest.raises(AdmitError, match=message) as exc:
        execution_data_snapshot_hash(bars=[{**_BAR, field: value}], **_QUERY)
    assert exc.value.code == "DATA_SNAPSHOT_INVALID"


def test_snapshot_requires_bars_and_all_required_fields() -> None:
    with pytest.raises(AdmitError, match="bars are required"):
        execution_data_snapshot_hash(bars=[], **_QUERY)
    for bar in ({key: value for key, value in _BAR.items() if key != "volume"},
                SimpleNamespace(time=0, time_close=60_000)):
        with pytest.raises(AdmitError, match="bar is missing") as exc:
            execution_data_snapshot_hash(bars=[bar], **_QUERY)
        assert exc.value.code == "DATA_SNAPSHOT_INVALID"


@pytest.mark.parametrize("symbol,timeframe", [("", "1D"), ("SOLUSDT", None)])
def test_supplemental_bar_requires_explicit_series_identity(symbol: object, timeframe: object) -> None:
    with pytest.raises(AdmitError, match="series identity is incomplete") as exc:
        execution_data_snapshot_hash(bars=[_BAR], supplemental_bars=[{
            **_BAR, "symbol": symbol, "timeframe": timeframe,
        }], **_QUERY)
    assert exc.value.code == "DATA_SNAPSHOT_INVALID"


def test_snapshot_rejects_misaligned_and_unsealed_canonical_envelopes() -> None:
    with pytest.raises(AdmitError, match="must align"):
        execution_data_snapshot_hash(bars=[_BAR], canonical_bar_envelopes=[], **_QUERY)
    with pytest.raises(AdmitError, match="canonical bar envelope is invalid: 0"):
        execution_data_snapshot_hash(bars=[_BAR], canonical_bar_envelopes=[{}], **_QUERY)


def test_optional_volume_and_decimal_identity_change_digest() -> None:
    a = execution_data_snapshot_hash(bars=[_BAR], **_QUERY)
    b = execution_data_snapshot_hash(bars=[{**_BAR, "volume": Decimal("0.01")}], **_QUERY)
    c = execution_data_snapshot_hash(bars=[{**_BAR, "volume": "0.01"}], **_QUERY)
    assert a != b and b != c and a != c
