"""Canonical decimal to binary float admission never invents a zero."""
from dataclasses import asdict

import pytest
from pinelib.runtime.metadata import InstrumentContext

from openpine.runtime.rc6_marketdata import RC6BarAdmission
from openpine.runtime.rc6_worker_runtime import _bar_values, _engine_bar
from openpine.runtime.request_data import build_request_manifest
from rc6_tests.test_rc6_marketdata_boundary import bar, context


@pytest.mark.parametrize("decoder", [_bar_values, _engine_bar])
@pytest.mark.parametrize("field,tiny", [
    (field, tiny) for field in ("open", "high", "low", "close", "volume")
    for tiny in ("1e-999", "-1e-999") if field != "volume" or tiny == "1e-999"
])
def test_nonzero_decimal_underflow_is_rejected_in_both_paths(decoder, field, tiny):
    # Wide finite bounds keep each chosen price valid before float conversion.
    values = dict(open=0, high=1, low=-1, close=0, volume=1)
    values[field] = tiny
    if field == "high":
        values.update(open=-1, low=-2, close=-1)
    if field == "low":
        values.update(open=1, high=2, close=1)
    with pytest.raises(ValueError, match=f"{field} underflows"):
        decoder(bar(**values))


@pytest.mark.parametrize("decoder", [_bar_values, _engine_bar])
@pytest.mark.parametrize("value", ["0", "5e-324", "-5e-324", "1e-300", "0.1"])
def test_genuine_zero_and_representable_subnormals_remain_valid(decoder, value):
    result = decoder(bar(open=value, high=value, low=value, close=value, volume="0"))
    assert result.close == float(value)
    assert result.volume == 0
    if value != "0":
        assert result.close != 0


def test_rejected_numeric_bar_does_not_advance_stream_cursor():
    stream = RC6BarAdmission(context())
    with pytest.raises(ValueError, match="underflows"):
        stream.accept(bar(volume="1e-999"))
    assert stream.received == stream.excluded_open == 0
    assert stream.last_time is stream.snapshot_id is None
    assert stream.accept(bar()).volume == 1000
    assert stream.received == 1


def test_request_preload_uses_same_numeric_boundary():
    instrument = InstrumentContext(
        ticker="SOLUSDT", tickerid="binance:spot:SOLUSDT", prefix="BINANCE",
        currency="USDT", basecurrency="SOL", timezone="UTC",
        instrument_type="crypto", mintick=0.01,
    )
    execution_context = {**context(), "content_hash": "sha256:" + "e" * 64}
    dataset = {
        "instrument_id": "binance:spot:SOLUSDT", "timeframe": "1m", "market": "spot",
        "instrument": asdict(instrument), "bars": [bar(volume="1e-999")],
    }
    with pytest.raises(ValueError, match="volume underflows"):
        build_request_manifest(execution_context, [dataset])


def test_negative_volume_is_rejected_by_canonical_producer():
    from marketdata_provider.errors import MDValidationError
    with pytest.raises(MDValidationError, match="volume must be nonnegative"):
        bar(volume="-1e-999")
