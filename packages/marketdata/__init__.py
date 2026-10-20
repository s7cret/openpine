"""Offline, immutable close-only input for the confirmed historical profile."""

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
    if type(data) is not HistoricalData or data.schema_version != "openpine.historical-bars.1":
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
        if bar.close is not None and (type(bar.close) is not float or not math.isfinite(bar.close)):
            invalid("Close must be a finite float or null (numeric na).")
        previous = bar.time


def from_payload(payload) -> HistoricalData:
    keys = {"schema_version", "context_id", "symbol", "timeframe", "bars"}
    if type(payload) is not dict or payload.keys() != keys or payload["schema_version"] != "openpine.historical-bars.1":
        invalid("Historical input must use the exact openpine.historical-bars.1 fields.")
    rows = payload["bars"]
    if type(rows) is not list or not 1 <= len(rows) <= MAX_BARS:
        invalid(f"Historical input requires 1..{MAX_BARS} bars.")
    bars = []
    for row in rows:
        if type(row) is not dict or row.keys() != {"time", "close"}:
            invalid("Each bar must contain exactly time and close.")
        value = row["close"]
        if value is not None:
            if type(value) not in (int, float):
                invalid("Close must be numeric or null; booleans are not numbers.")
            try:
                value = float(value)
            except OverflowError:
                invalid("Close is outside the supported finite range.")
        bars.append(HistoricalBar(row["time"], value))
    data = HistoricalData(payload["context_id"], payload["symbol"], payload["timeframe"], tuple(bars))
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
    raw = json.dumps(asdict(data), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                     allow_nan=False).encode("utf-8")
    return DataIdentity(hashlib.sha256(raw).hexdigest(), data.context_id, data.symbol, data.timeframe,
                        tuple(bar.time for bar in data.bars))
