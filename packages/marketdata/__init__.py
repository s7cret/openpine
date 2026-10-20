"""Offline immutable close-only (.1) and OHLCV (.2) historical input."""

import hashlib
import json
import math
from dataclasses import asdict
from pathlib import Path

from packages.contracts import DataIdentity, Diagnostic, HistoricalBar, HistoricalData, OpenPineError

MAX_BARS = 100_000
MAX_BYTES = 8 * 1024 * 1024


def invalid(message):
    raise OpenPineError(Diagnostic("INPUT_INVALID", "BARS_INVALID", message))


def validate_data(data: HistoricalData) -> None:
    if type(data) is not HistoricalData or data.schema_version not in ("openpine.historical-bars.1", "openpine.historical-bars.2"):
        invalid("Unsupported historical data contract.")
    if any(type(value) is not str or not value.strip() or len(value) > 256
           for value in (data.context_id, data.symbol, data.timeframe)):
        invalid("Context ID, symbol and timeframe must be nonempty strings of at most 256 characters.")
    if type(data.bars) is not tuple or not 1 <= len(data.bars) <= MAX_BARS:
        invalid(f"Historical input requires 1..{MAX_BARS} immutable bars.")
    previous = -1
    for bar in data.bars:
        if type(bar) is not HistoricalBar or type(bar.time) is not int or not previous < bar.time < 2**63:
            invalid("Bar opening times must be nonnegative, strictly increasing 64-bit integers (UTC milliseconds).")
        for slot in ("open", "high", "low", "close", "volume"):
            value = getattr(bar, slot)
            if value is not None and (type(value) is not float or not math.isfinite(value)):
                invalid(f"{slot} must be a finite float or null (numeric na).")
        if bar.volume is not None and bar.volume < 0:
            invalid("Volume must be nonnegative or null.")
        if data.schema_version.endswith(".1") and any(getattr(bar, slot) is not None for slot in ("open", "high", "low", "volume")):
            invalid("OHLCV values require historical-bars.2.")
        prices = [v for v in (bar.open, bar.close) if v is not None]
        if (bar.high is not None and any(bar.high < v for v in [*prices, *([bar.low] if bar.low is not None else [])])) or (bar.low is not None and any(bar.low > v for v in prices)):
            invalid("OHLC prices must satisfy low <= open/close <= high when defined.")
        previous = bar.time


def from_payload(payload) -> HistoricalData:
    keys = {"schema_version", "context_id", "symbol", "timeframe", "bars"}
    if type(payload) is not dict or payload.keys() != keys or payload["schema_version"] not in ("openpine.historical-bars.1", "openpine.historical-bars.2"):
        invalid("Historical input must use the exact historical-bars.1/.2 fields.")
    slots = ("close",) if payload["schema_version"].endswith(".1") else ("close", "open", "high", "low", "volume")
    rows = payload["bars"]
    if type(rows) is not list or not 1 <= len(rows) <= MAX_BARS:
        invalid(f"Historical input requires 1..{MAX_BARS} bars.")
    bars = []
    for row in rows:
        if type(row) is not dict or row.keys() != {"time", *slots}:
            invalid("Each bar must contain exactly the fields of its input schema.")
        values = {}
        for slot in slots:
            value = row[slot]
            if value is not None:
                if type(value) not in (int, float):
                    invalid(f"{slot} must be numeric or null; booleans are not numbers.")
                try:
                    value = float(value)
                except OverflowError:
                    invalid(f"{slot} is outside the supported finite range.")
            values[slot] = value
        bars.append(HistoricalBar(time=row["time"], **values))
    data = HistoricalData(payload["context_id"], payload["symbol"], payload["timeframe"], tuple(bars), schema_version=payload["schema_version"])
    validate_data(data)
    return data


def read_bars(path: Path) -> HistoricalData:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                invalid(f"Duplicate input field: {key}.")
            result[key] = value
        return result

    def constant(value):
        invalid(f"Nonfinite JSON number: {value}.")

    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        invalid("Historical input exceeds the 8 MiB implementation limit.")
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError) as error:
        invalid(f"Invalid historical JSON: {error}")
    return from_payload(payload)


def data_identity(data: HistoricalData) -> DataIdentity:
    validate_data(data)
    payload = asdict(data)
    if data.schema_version.endswith(".1"):
        payload["bars"] = [{"time": bar.time, "close": bar.close} for bar in data.bars]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                     allow_nan=False).encode("utf-8")
    return DataIdentity(hashlib.sha256(raw).hexdigest(), data.context_id, data.symbol, data.timeframe,
                        tuple(bar.time for bar in data.bars))
