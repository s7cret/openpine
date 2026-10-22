"""Immutable Parquet blocks, exact snapshots and provider coverage intervals."""

import csv
import hashlib
import itertools
import struct
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from packages.contracts import Diagnostic, HistoricalBar, HistoricalData, OpenPineError
from packages.contracts.storage import DatasetIdentity
from packages.contracts.wire import canonical, digest, loads
from packages.marketdata import MAX_BARS, validate_data, invalid
from packages.marketdata.providers import make_snapshot, snapshot_payload, validate_query
from .columnar import connection, quoted
from .objects import ObjectStore, identifier

SLOTS = ("open", "high", "low", "close", "volume")
BLOCK_ROWS = 100_000


def row_bytes(bar):
    return struct.pack(">q", bar.time) + b"".join(b"\0" if (v := getattr(bar, s)) is None
        else b"\1" + struct.pack(">d", v) for s in SLOTS)


def logical_hasher(metadata):
    h = hashlib.sha256()
    h.update(canonical({k: metadata[k] for k in ("context_id", "symbol", "timeframe", "bar_schema")}))
    return h


class CandleSource:
    def __init__(self, objects, manifest, *, threads=1, memory_mb=128):
        self.objects, self.manifest = objects, manifest
        self.identity = DatasetIdentity(**manifest["identity"])
        self.context_id = self.identity.context_id
        self.threads, self.memory_mb = threads, memory_mb

    def rows(self, start_ms=0, end_ms=2**63, *, offset=0, limit=None):
        if (type(start_ms) is not int or type(end_ms) is not int or not 0 <= start_ms < end_ms <= 2**63
                or type(offset) is not int or offset < 0 or limit is not None and (type(limit) is not int or limit < 1)):
            raise ValueError("Invalid bounded candle range.")
        parts, ranges = [], []
        for index, part in enumerate(self.manifest["parts"]):
            lo = max(start_ms, part["select_start"])
            hi = min(end_ms, part["select_end"])
            if lo >= hi:
                continue
            ranges.append((lo, hi, part))
            parts.append((index, lo, hi, part))
        if not parts:
            return
        ordered = sorted(ranges, key=lambda r: (r[0], r[1]))
        if all(a[1] <= b[0] for a, b in zip(ordered, ordered[1:])):
            # Common case: chronological non-overlapping blocks. No global sort,
            # window or 10-million-row intermediate allocation is necessary.
            skipped, emitted = offset, 0
            with connection(threads=self.threads, memory_mb=self.memory_mb) as db:
                for lo, hi, part in ordered:
                    full = lo == part["first_ms"] and hi == part["last_ms"] + 1
                    if full and skipped >= part["count"]:
                        skipped -= part["count"]
                        continue
                    path = self.objects.verify(part["object_id"])
                    if skipped and not full:
                        count = db.execute("SELECT count(*) FROM read_parquet(?) WHERE time>=? AND time<?",
                            [str(path), lo, hi]).fetchone()[0]
                        if skipped >= count:
                            skipped -= count
                            continue
                    take = 10_000_000 if limit is None else limit - emitted
                    if take <= 0:
                        return
                    cursor = db.execute("SELECT time,open,high,low,close,volume FROM read_parquet(?) "
                        "WHERE time>=? AND time<? ORDER BY time LIMIT ? OFFSET ?", [str(path), lo, hi, take, skipped])
                    skipped = 0
                    while batch := cursor.fetchmany(8192):
                        for timestamp, op, high, low, close, volume in batch:
                            if limit is not None and emitted >= limit:
                                return
                            yield HistoricalBar(timestamp, close, op, high, low, volume)
                            emitted += 1
            return
        selects = [f"SELECT *,{index} AS revision FROM read_parquet({quoted(self.objects.verify(part['object_id']))}) "
            f"WHERE time>={lo} AND time<{hi}" for index, lo, hi, part in parts]
        sql = ("SELECT time,open,high,low,close,volume FROM (" + " UNION ALL ".join(selects) +
            ") QUALIFY row_number() OVER (PARTITION BY time ORDER BY revision DESC)=1 ORDER BY time")
        if limit is not None:
            sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
        with connection(threads=self.threads, memory_mb=self.memory_mb) as db:
            cursor = db.execute(sql)
            while batch := cursor.fetchmany(8192):
                for timestamp, op, high, low, close, volume in batch:
                    yield HistoricalBar(timestamp, close, op, high, low, volume)

    def __iter__(self):
        previous, count = -1, 0
        h = logical_hasher(self.manifest["metadata"])
        for bar in self.rows():
            if bar.time <= previous:
                raise ValueError("Snapshot rows are not strictly chronological.")
            previous, count = bar.time, count + 1
            h.update(row_bytes(bar))
            yield bar
        expected = self.manifest["logical_sha256"]
        if count != self.identity.count or h.hexdigest() != expected:
            raise ValueError("Snapshot logical identity/count mismatch.")

    def verify_cursor(self,index,last_time):
        if type(index) is not int or not 0<index<=self.identity.count:
            raise ValueError("Invalid checkpoint input cursor.")
        # Verify even skipped prefix blocks: resume must not hide corruption.
        self.verify_blocks()
        previous=list(self.rows(offset=index-1,limit=1))
        if len(previous)!=1 or previous[0].time!=last_time:
            raise ValueError("Checkpoint timestamp differs from its input cursor.")

    def verify_blocks(self):
        for part in self.manifest["parts"]:self.objects.verify(part["object_id"])

    def read_from(self,index):
        if index==0:
            yield from self
            return
        count=index
        for bar in self.rows(offset=index):
            count+=1
            yield bar
        if count!=self.identity.count:raise ValueError("Resumed input count mismatch.")


class CandleStore:
    def __init__(self, root, database, *, objects=None):
        self.root, self.db = Path(root), database
        self.objects = objects if objects is not None else ObjectStore(self.root / "objects")

    def manifest(self, snapshot_id):
        identifier(snapshot_id)
        rows = self.db.execute("SELECT manifest FROM datasets WHERE snapshot_id=?", (snapshot_id,))
        if not rows:
            raise ValueError("Unknown dataset snapshot.")
        manifest = self.objects.get(loads(rows[0][0])["object_id"])
        if manifest["snapshot_id"] != snapshot_id:
            raise ValueError("Snapshot catalog identity mismatch.")
        return manifest

    def source(self, snapshot_id, **options):
        return CandleSource(self.objects, self.manifest(snapshot_id), **options)

    def slice(self,snapshot_id,start_ms,end_ms):
        """Freeze a range of one exact snapshot, reusing its immutable Parquet parts."""
        if type(start_ms) is not int or type(end_ms) is not int or not 0<=start_ms<end_ms<=2**63-1:
            raise ValueError('Invalid immutable snapshot slice.')
        original=self.manifest(snapshot_id)
        parts=[{**part,'select_start':max(start_ms,part['select_start']),'select_end':min(end_ms,part['select_end'])}
               for part in original['parts'] if max(start_ms,part['select_start'])<min(end_ms,part['select_end'])]
        temporary={**original,'parts':parts};h=logical_hasher(original['metadata']);count=0;first=last=None
        for bar in CandleSource(self.objects,temporary).rows():
            h.update(row_bytes(bar));count+=1;first=bar.time if first is None else first;last=bar.time
        if not count:raise ValueError('Snapshot slice contains no bars.')
        provenance={**original['provenance'],'selection':{'schema_version':'openpine.snapshot-slice.1',
            'parent_snapshot_id':snapshot_id,'start_ms':start_ms,'end_ms':end_ms}}
        return self._register(original['metadata'],parts,provenance,count,first,last,h.hexdigest())

    def _register(self, metadata, parts, provenance, count, first, last, logical, *, legacy=None):
        identity = DatasetIdentity(legacy["data_sha256"] if legacy else logical,
            metadata["context_id"], metadata["symbol"], metadata["timeframe"], count, first, last,
            "openpine.historical-json.1" if legacy else "openpine.candle-rows.1")
        manifest = {"schema_version": "openpine.dataset-snapshot.2", "metadata": metadata,
            "identity": asdict(identity), "logical_sha256": logical, "parts": parts,
            "provenance": provenance, "legacy": legacy}
        snapshot_id = legacy["snapshot_id"] if legacy else digest(manifest)
        manifest["snapshot_id"] = snapshot_id
        object_id = self.objects.put(manifest)
        self.db.execute("INSERT OR IGNORE INTO datasets VALUES (?,?,?)",
            (snapshot_id, canonical({"object_id": object_id}), time.time()))
        return snapshot_id

    def ingest(self, metadata, bars, provenance, *, legacy=None):
        metadata = dict(metadata)
        if metadata.keys() != {"context_id", "symbol", "timeframe", "bar_schema"}:
            raise ValueError("Unknown candle metadata fields.")
        if not isinstance(provenance, dict) or not provenance.get("provider") or len(canonical(provenance)) > 65536:
            raise ValueError("Candle provenance requires a provider.")
        parts, count, first, last = [], 0, None, -1
        h = logical_hasher(metadata)
        iterator = iter(bars)
        with tempfile.TemporaryDirectory(prefix=".ingest-", dir=self.root) as temporary:
            for number in itertools.count():
                batch = tuple(itertools.islice(iterator, BLOCK_ROWS))
                if not batch:
                    break
                validate_data(HistoricalData(metadata["context_id"], metadata["symbol"], metadata["timeframe"],
                    batch, schema_version=metadata["bar_schema"]))
                if batch[0].time <= last or count + len(batch) > 10_000_000:
                    invalid("Candle stream is unordered or exceeds 10 million bars.")
                csv_path, parquet = Path(temporary) / "rows.csv", Path(temporary) / f"{number}.parquet"
                block_hash = hashlib.sha256()
                with csv_path.open("w", newline="", encoding="utf-8") as stream:
                    writer = csv.writer(stream)
                    for bar in batch:
                        raw = row_bytes(bar); h.update(raw); block_hash.update(raw)
                        writer.writerow([bar.time, *(getattr(bar, s) for s in SLOTS)])
                with connection(temporary=temporary) as db:
                    db.execute(f"COPY (SELECT * FROM read_csv({quoted(csv_path)}, header=false, "
                        "columns={'time':'BIGINT','open':'DOUBLE','high':'DOUBLE','low':'DOUBLE','close':'DOUBLE','volume':'DOUBLE'})) "
                        f"TO {quoted(parquet)} (FORMAT PARQUET, COMPRESSION ZSTD)")
                sha = self.objects.publish(parquet)
                parts.append({"object_id": sha, "logical_sha256": block_hash.hexdigest(), "count": len(batch),
                    "first_ms": batch[0].time, "last_ms": batch[-1].time,
                    "select_start": batch[0].time, "select_end": batch[-1].time + 1})
                first = batch[0].time if first is None else first
                last, count = batch[-1].time, count + len(batch)
                parquet.unlink()
        if not count:
            invalid("A snapshot must contain at least one candle.")
        return self._register(metadata, parts, provenance, count, first, last, h.hexdigest(), legacy=legacy)

    def save(self, snapshot):
        value = snapshot_payload(snapshot)
        # Keep exact legacy IDs and numerical values without keeping another bars JSON.
        try:
            self.manifest(snapshot.sha256)
            return snapshot.sha256
        except ValueError as error:
            if str(error) != "Unknown dataset snapshot.":
                raise
        return self.ingest({"context_id": snapshot.data.context_id, "symbol": snapshot.data.symbol,
            "timeframe": snapshot.data.timeframe, "bar_schema": snapshot.data.schema_version}, snapshot.data.bars,
            value["provenance"], legacy={"snapshot_id": snapshot.sha256, "data_sha256": value["data_sha256"]})

    def load(self, snapshot_id):
        manifest = self.manifest(snapshot_id)
        if manifest["identity"]["count"] > MAX_BARS:
            raise ValueError("Use the bounded dataset reader for snapshots above 100000 bars.")
        metadata = manifest["metadata"]
        data = HistoricalData(metadata["context_id"], metadata["symbol"], metadata["timeframe"],
            tuple(self.source(snapshot_id)), schema_version=metadata["bar_schema"])
        provenance = manifest["provenance"] if manifest["legacy"] else {
            **manifest["provenance"], "derived_from_snapshot_id": snapshot_id}
        snapshot = make_snapshot(data, provenance)
        if manifest["legacy"] and snapshot.sha256 != snapshot_id:
            raise ValueError("Legacy snapshot identity mismatch after Parquet roundtrip.")
        return snapshot

    def export(self, snapshot_id):
        manifest = self.manifest(snapshot_id)
        return snapshot_payload(self.load(snapshot_id)) if manifest["identity"]["count"] <= MAX_BARS else manifest

    def missing(self, namespace, query):
        validate_query(query)
        intervals = self.db.execute("SELECT start_ms,end_ms FROM coverage WHERE namespace=? AND symbol=? AND timeframe=? "
            "AND end_ms>? AND start_ms<? ORDER BY start_ms,end_ms", (namespace, query.symbol, query.timeframe, query.start_ms, query.end_ms))
        cursor, missing = query.start_ms, []
        for start, end in intervals:
            if start > cursor:
                missing.append((cursor, min(start, query.end_ms)))
            cursor = max(cursor, end)
        if cursor < query.end_ms:
            missing.append((cursor, query.end_ms))
        return missing

    def ensure(self, provider, query, *, namespace="binance.spot.rest.1", refresh=False):
        from packages.marketdata.cache import HistoryCache
        return HistoryCache(self).ensure(provider, query, namespace=namespace, refresh=refresh)

    def cover(self, namespace, query, start, stop, snapshot_id):
        self.db.execute("INSERT INTO coverage(namespace,symbol,timeframe,start_ms,end_ms,snapshot_id,created_at) VALUES(?,?,?,?,?,?,?)",
            (namespace, query.symbol, query.timeframe, start, stop, snapshot_id, time.time()))

    def select(self, query, *, namespace):
        validate_query(query)
        rows = self.db.execute("SELECT start_ms,end_ms,snapshot_id FROM coverage WHERE namespace=? AND symbol=? AND timeframe=? "
            "AND end_ms>? AND start_ms<? ORDER BY id", (namespace, query.symbol, query.timeframe, query.start_ms, query.end_ms))
        # Cache the exact revision set, not merely min/max coverage. Refreshes,
        # including empty revisions, change the key and preserve old snapshots.
        key = digest({"schema":"openpine.range-selection.1", "namespace":namespace,
            "query":asdict(query), "revisions":rows})
        cached = self.db.execute("SELECT snapshot_id FROM selection_cache WHERE cache_key=?", (key,))
        if cached:
            self.manifest(cached[0][0])
            return cached[0][0]
        parts, metadata, claimed = [], None, []
        # Newer fetches own their entire requested interval, including confirmed
        # empty intervals. A provider correction can remove an old candle too.
        for start, end, snapshot_id in reversed(rows):
            intervals = [(max(start, query.start_ms), min(end, query.end_ms))]
            for a, b in claimed:
                intervals = [(x, y) for lo, hi in intervals
                    for x, y in ((lo, min(hi, a)), (max(lo, b), hi)) if x < y]
            claimed.append((start, end))
            if snapshot_id is None or not intervals:
                continue
            manifest = self.manifest(snapshot_id)
            if metadata is not None and metadata != manifest["metadata"]:
                raise ValueError("Provider changed dataset metadata inside one history series.")
            metadata = manifest["metadata"]
            for part in manifest["parts"]:
                for start, end in intervals:
                    lo, hi = max(start, part["select_start"]), min(end, part["select_end"])
                    if lo < hi:
                        parts.append({**part, "select_start": lo, "select_end": hi})
        parts.sort(key=lambda p: (p["select_start"], p["object_id"]))
        if metadata is None:
            raise OpenPineError(Diagnostic("INPUT_UNAVAILABLE", "DATA_EMPTY", "No confirmed candles in the requested range."))
        h, count, first, last = logical_hasher(metadata), 0, None, -1
        temporary = {"parts": parts, "metadata": metadata, "identity": asdict(DatasetIdentity("0"*64,
            metadata["context_id"], query.symbol, query.timeframe, 0, 0, 0))}
        for bar in CandleSource(self.objects, temporary).rows():
            h.update(row_bytes(bar)); count += 1
            first = bar.time if first is None else first
            last = bar.time
        if not count:
            raise OpenPineError(Diagnostic("INPUT_UNAVAILABLE", "DATA_EMPTY", "No confirmed candles in the requested range."))
        snapshot_id = self._register(metadata, parts, {"provider": namespace, "query": asdict(query),
            "gap_policy": "preserve", "range": "[start,end)"}, count, first, last, h.hexdigest())
        self.db.execute("INSERT OR IGNORE INTO selection_cache VALUES(?,?)", (key,snapshot_id))
        return snapshot_id
