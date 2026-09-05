"""One canonical market-data admission boundary for both RC6 transports.

Revision chains must be resolved by the snapshot owner before execution. Until
RC6 can carry the corresponding revision proof, rejecting a revised envelope is
safer than converting a correction or revocation into an ordinary historical bar.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from backtest_engine.models import Bar
from marketdata_provider.timeframes import close_time_ms
from openpine_contracts import Finality, content_hash, validate_payload, verify_content_hash

SCHEMA = "openpine.marketdata.bar.v2"
# The envelope hash includes provenance; bar_content_hash binds the canonical
# series value. Both are mandatory. Producer/consumer conformance tests use the
# real marketdata-provider producer, rather than a hand-written test envelope.
_VALUE_FIELDS = (
    "series_id", "instrument_id", "timeframe", "open_time_utc_ms",
    "close_time_utc_ms", "open", "high", "low", "close", "volume", "finality",
    "revision_state", "revision", "provider", "provider_revision", "superseded_bar_hash",
)


def decode_canonical_bar(
    envelope: Mapping[str, Any], *, context: Mapping[str, Any] | None = None,
) -> Bar:
    """Validate the complete envelope before converting any value to float."""
    if not isinstance(envelope, Mapping):
        raise ValueError("canonical bar must be an object")
    validate_payload(SCHEMA, envelope)
    if not verify_content_hash(envelope, schema_id=SCHEMA):
        raise ValueError("bar content hash is invalid")
    value_hash = content_hash({key: envelope[key] for key in _VALUE_FIELDS}, schema_id=SCHEMA)
    if envelope["bar_content_hash"] != value_hash:
        raise ValueError("bar_content_hash is invalid")
    if context is not None:
        expected = {
            "stack_id": context["stack_manifest_hash"],
            "producer": "marketdata-provider",
            "producer_commit": context["producer_commits"]["marketdata-provider"],
            "series_id": context["series_id"],
            "instrument_id": context["instrument_id"],
            "timeframe": context["timeframe"],
        }
        if any(envelope[key] != value for key, value in expected.items()):
            raise ValueError("bar execution context identity mismatch")
    if envelope["revision_state"] != "ORIGINAL" or envelope["revision"] != 0:
        raise ValueError("RC6 bar revision requires snapshot revision admission")
    if envelope["superseded_bar_hash"] is not None:
        raise ValueError("original bar cannot supersede a revision")
    opened, closed = envelope["open_time_utc_ms"], envelope["close_time_utc_ms"]
    if closed != close_time_ms(opened, envelope["timeframe"]):
        raise ValueError("bar close time does not match its timeframe")
    numbers = {key: Decimal(str(envelope[key])) for key in ("open", "high", "low", "close", "volume")}
    if not all(value.is_finite() and math.isfinite(float(value)) for value in numbers.values()):
        raise ValueError("bar values must be finite in the runtime numeric range")
    if not (numbers["low"] <= min(numbers["open"], numbers["close"])
            <= max(numbers["open"], numbers["close"]) <= numbers["high"]):
        raise ValueError("bar OHLC invariants are invalid")
    if numbers["volume"] < 0:
        raise ValueError("bar volume must be nonnegative")
    return Bar(
        time=opened, time_close=closed, finality=Finality(envelope["finality"]),
        **{key: float(value) for key, value in numbers.items()},
    )


class RC6BarAdmission:
    """Validate identity/order across chunks, then apply the declared finality policy."""
    def __init__(self, context: Mapping[str, Any]) -> None:
        self.context = context
        self.policy = context["finality_policy"]
        if self.policy not in {"CLOSED_BAR_ONLY", "ALLOW_OPEN"}:
            raise ValueError("unsupported bar finality policy")
        self.last_time: int | None = None
        self.snapshot_id: str | None = None
        self.received = 0
        self.excluded_open = 0

    def accept(self, envelope: Mapping[str, Any]) -> Bar | None:
        bar = decode_canonical_bar(envelope, context=self.context)
        if self.last_time is not None and bar.time <= self.last_time:
            raise ValueError("bar sequence must be strictly increasing; duplicate revision or time")
        snapshot = envelope["snapshot_id"]
        if self.snapshot_id is not None and snapshot != self.snapshot_id:
            raise ValueError("bar snapshot identity changed within the run")
        self.last_time, self.snapshot_id = bar.time, snapshot
        self.received += 1
        if self.policy == "CLOSED_BAR_ONLY" and bar.finality is Finality.OPEN:
            self.excluded_open += 1
            return None
        return bar
