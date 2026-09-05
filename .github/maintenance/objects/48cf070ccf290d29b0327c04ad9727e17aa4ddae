"""OP-04: the two worker modes must not reinterpret canonical bar envelopes."""
from copy import deepcopy

import pytest

from marketdata_provider.canonical.bar import make_canonical_bar
from openpine_contracts import Finality, seal_content_hash
from openpine.runtime.rc6_worker_runtime import _bar_values, _engine_bar

OPENED = 1_725_145_620_000
STACK = "sha256:" + "d" * 64
COMMIT = "5" * 40


def bar(**overrides):
    fields = dict(
        instrument_id="binance:spot:SOLUSDT", timeframe="1m",
        open_time_utc_ms=OPENED, open=100, high=102, low=99, close=101,
        volume=1000, snapshot_id="snapshot-review", provider="binance",
        provider_revision={"known": True, "revision": "1"},
        producer_commit=COMMIT, stack_id=STACK, finality="FINAL", created_at_utc_ms=0,
    )
    fields.update(overrides)
    return make_canonical_bar(**fields)


@pytest.mark.parametrize("finality", ["FINAL", "OPEN"])
def test_engine_bar_preserves_canonical_finality(finality):
    result = _engine_bar(bar(finality=finality))
    assert result.finality is Finality(finality)
    assert result.time == OPENED
    assert result.time_close == OPENED + 59_999
    assert result.close == 101


@pytest.mark.parametrize("decoder", [_engine_bar, _bar_values])
@pytest.mark.parametrize("tamper", ["content", "nested_hash", "legacy_closed"])
def test_both_decoders_reject_tampered_envelopes(decoder, tamper):
    envelope = deepcopy(bar())
    if tamper == "content":
        envelope["close"] = "100"
    elif tamper == "nested_hash":
        envelope["bar_content_hash"] = "sha256:" + "f" * 64
        envelope = seal_content_hash(envelope, schema_id="openpine.marketdata.bar.v2")
    else:
        envelope["closed"] = True
    with pytest.raises(ValueError):
        decoder(envelope)


@pytest.mark.parametrize("decoder", [_engine_bar, _bar_values])
@pytest.mark.parametrize("state", ["CORRECTED", "REVOKED"])
def test_revision_requires_upstream_snapshot_admission(decoder, state):
    original = bar()
    revised = bar(revision_state=state, revision=1,
                  superseded_bar_hash=original["bar_content_hash"])
    with pytest.raises(ValueError, match="revision"):
        decoder(revised)


def context(**overrides):
    value = dict(stack_manifest_hash=STACK, producer_commits={"marketdata-provider": COMMIT},
                 series_id="binance:spot:SOLUSDT:1m", instrument_id="binance:spot:SOLUSDT",
                 timeframe="1m", finality_policy="CLOSED_BAR_ONLY")
    value.update(overrides)
    return value


def test_stream_filters_open_without_fabricating_a_final_bar():
    from openpine.runtime.rc6_marketdata import RC6BarAdmission
    stream = RC6BarAdmission(context())
    assert stream.accept(bar()).finality is Finality.FINAL
    assert stream.accept(bar(open_time_utc_ms=OPENED + 60_000, finality="OPEN")) is None
    assert stream.received == 2
    assert stream.excluded_open == 1


@pytest.mark.parametrize("field,value", [
    ("instrument_id", "other"), ("series_id", "other"), ("timeframe", "5m"),
    ("stack_manifest_hash", "sha256:" + "e" * 64),
    ("producer_commits", {"marketdata-provider": "7" * 40}),
])
def test_stream_rejects_context_drift(field, value):
    from openpine.runtime.rc6_marketdata import RC6BarAdmission
    with pytest.raises(ValueError, match="identity"):
        RC6BarAdmission(context(**{field: value})).accept(bar())


def test_stream_rejects_duplicate_bar_even_across_batches():
    from openpine.runtime.rc6_marketdata import RC6BarAdmission
    stream = RC6BarAdmission(context())
    stream.accept(bar())
    with pytest.raises(ValueError, match="sequence"):
        stream.accept(bar())


def test_stream_rejects_snapshot_change():
    from openpine.runtime.rc6_marketdata import RC6BarAdmission
    stream = RC6BarAdmission(context())
    stream.accept(bar())
    with pytest.raises(ValueError, match="snapshot"):
        stream.accept(bar(open_time_utc_ms=OPENED + 60_000, snapshot_id="other"))


def test_allow_open_preserves_open_finality():
    from openpine.runtime.rc6_marketdata import RC6BarAdmission
    stream = RC6BarAdmission(context(finality_policy="ALLOW_OPEN"))
    assert stream.accept(bar(finality="OPEN")).finality is Finality.OPEN
