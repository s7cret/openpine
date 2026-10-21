"""Historical provider boundary and immutable, replayable data snapshots."""

import hashlib
from dataclasses import asdict, replace
from pathlib import Path
from typing import Protocol

from packages.contracts import Diagnostic, OpenPineError
from packages.contracts.marketdata import DataQuery, MarketSnapshot
from packages.contracts.wire import canonical, digest, loads, read_json
from packages.marketdata import MAX_BYTES, data_identity, from_payload, invalid


class HistoricalProvider(Protocol):
    def fetch(self, query: DataQuery) -> MarketSnapshot: ...


def validate_query(query):
    if type(query) is not DataQuery or any(type(v) is not str or not v.strip() or len(v) > 256 for v in (query.symbol, query.timeframe)):
        invalid("Invalid data query identity.")
    if type(query.start_ms) is not int or type(query.end_ms) is not int or not 0 <= query.start_ms < query.end_ms < 2**63:
        invalid("Data range requires 0 <= start < end < 2^63, in UTC milliseconds.")


def data_payload(data):
    data_identity(data)
    payload = asdict(data)
    if data.schema_version.endswith(".1"):
        payload["bars"] = [{"time": b.time, "close": b.close} for b in data.bars]
    else:
        payload["bars"] = [asdict(b) for b in data.bars]
    return payload


def make_snapshot(data, provenance):
    if type(provenance) is not dict or not provenance.get("provider"):
        invalid("Snapshot provenance requires a provider.")
    raw = canonical(provenance)
    if len(raw) > 65536:
        invalid("Snapshot provenance exceeds 64 KiB.")
    body = {"schema_version": "openpine.market-snapshot.1", "data": data_payload(data),
            "data_sha256": data_identity(data).sha256, "provenance": loads(raw)}
    return MarketSnapshot(data, raw, digest(body))


def snapshot_payload(snapshot):
    checked = make_snapshot(snapshot.data, loads(snapshot.provenance))
    if checked.sha256 != snapshot.sha256:
        invalid("Snapshot identity mismatch.")
    return {"schema_version": "openpine.market-snapshot.1", "sha256": checked.sha256,
            "data": data_payload(checked.data), "data_sha256": data_identity(checked.data).sha256,
            "provenance": loads(checked.provenance)}


def snapshot_from_payload(payload):
    if type(payload) is not dict or payload.keys() != {"schema_version", "sha256", "data", "data_sha256", "provenance"} or payload["schema_version"] != "openpine.market-snapshot.1":
        invalid("Unknown snapshot schema/fields.")
    data = from_payload(payload["data"])
    result = make_snapshot(data, payload["provenance"])
    if result.sha256 != payload["sha256"] or data_identity(data).sha256 != payload["data_sha256"]:
        invalid("Snapshot content hash mismatch.")
    return result


class SnapshotStore:
    def __init__(self, directory):
        self.directory = Path(directory)

    def save(self, snapshot):
        payload = snapshot_payload(snapshot)
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / (snapshot.sha256 + ".json")
        raw = canonical(payload) + b"\n"
        try:
            with path.open("xb") as stream:
                stream.write(raw)
        except FileExistsError:
            if path.read_bytes() != raw:
                invalid("Existing immutable snapshot differs from its content identity.")
        return path

    def load(self, sha256):
        if type(sha256) is not str or len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            invalid("Invalid snapshot identifier.")
        payload = read_json(self.directory / (sha256 + ".json"), max_bytes=16 * 1024 * 1024)
        result = snapshot_from_payload(payload)
        if result.sha256 != sha256:
            invalid("Snapshot does not belong to this identifier.")
        return result


class FileProvider:
    def __init__(self, path):
        self.path = Path(path)

    def fetch(self, query):
        validate_query(query)
        with self.path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        try:
            payload = loads(raw, max_bytes=MAX_BYTES)
        except ValueError as error:
            invalid(str(error))
        data = from_payload(payload)
        if (data.symbol, data.timeframe) != (query.symbol, query.timeframe):
            invalid("Requested symbol/timeframe differs from the file.")
        bars = tuple(b for b in data.bars if query.start_ms <= b.time < query.end_ms)
        if not bars:
            raise OpenPineError(Diagnostic("INPUT_UNAVAILABLE", "DATA_EMPTY", "No bars in the requested range."))
        selected = replace(data, bars=bars)
        return make_snapshot(selected, {"provider": "file.1", "source_sha256": hashlib.sha256(raw).hexdigest(),
                                        "query": asdict(query), "gap_policy": "preserve", "range": "[start,end)"})
