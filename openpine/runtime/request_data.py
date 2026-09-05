"""Admission of bounded immutable request datasets for isolated RC6 execution.

The caller supplies MarketData Provider envelopes and explicit metadata. This
boundary does not grant network or filesystem access, or resample chart bars.
"""

from __future__ import annotations

import ast
import dataclasses
from decimal import Decimal, DecimalException
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from openpine_contracts import content_hash, decimal_string, verify_content_hash
from pinelib.request import CanonicalBar, DataFinality
from pinelib.request.snapshots import RequestSource, SnapshotRequestProvider, normalized_period
from pinelib.runtime.metadata import InstrumentContext
from pinelib.errors import PineRuntimeError
from marketdata_provider.errors import MarketDataError
from marketdata_provider.timeframes import to_pine_timeframe
from openpine.runtime.rc6_marketdata import decode_canonical_bar

SCHEMA = "openpine.request_snapshots.v1"
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_SOURCE_BARS = 250_000
MAX_DATASETS = 64
_NUMERIC_METADATA = frozenset({"mintick", "pointvalue", "mincontract"})
_METADATA = frozenset(field.name for field in dataclasses.fields(InstrumentContext))


def build_request_manifest(
    context: Mapping[str, Any], datasets: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Seal detached original envelopes and explicit requested metadata.

    Descriptors contain instrument_id, timeframe (provider units), market,
    InstrumentContext fields, and an ordered list of original FINAL bar envelopes.
    """
    rows = []
    for dataset in datasets:
        row = dict(dataset)
        instrument = dict(row["instrument"])
        for name in _NUMERIC_METADATA:
            value = instrument[name]
            if type(value) not in (str, int, float):
                raise ValueError("numeric instrument metadata is required")
            instrument[name] = decimal_string(Decimal(str(value)))
        row["instrument"] = instrument
        rows.append(row)
    manifest = {
        "schema_id": SCHEMA,
        "execution_context_hash": context["content_hash"],
        "datasets": rows,
    }
    manifest["content_hash"] = content_hash(manifest, schema_id=SCHEMA)
    detached = json.loads(json.dumps(manifest, allow_nan=False))
    request_provider_from_manifest(detached, context)
    return detached


def request_provider_from_manifest(
    manifest: object, context: Mapping[str, Any]
) -> SnapshotRequestProvider:
    """Normalize data errors before staging without concealing implementation bugs."""
    try:
        return _request_provider_from_manifest(manifest, context)
    except (PineRuntimeError, MarketDataError, DecimalException) as exc:
        raise ValueError(f"invalid request snapshots: {exc}") from exc


def _request_provider_from_manifest(
    manifest: object, context: Mapping[str, Any]
) -> SnapshotRequestProvider:
    if not isinstance(manifest, Mapping) or set(manifest) != {
        "schema_id",
        "execution_context_hash",
        "datasets",
        "content_hash",
    }:
        raise ValueError("request snapshot manifest has an invalid schema")
    if manifest["schema_id"] != SCHEMA or not verify_content_hash(manifest, schema_id=SCHEMA):
        raise ValueError("request snapshot manifest hash is invalid")
    if manifest["execution_context_hash"] != context["content_hash"]:
        raise ValueError("request snapshots belong to another execution context")
    if (
        len(
            json.dumps(
                manifest, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode()
        )
        > MAX_MANIFEST_BYTES
    ):
        raise ValueError("request snapshot preload exceeds the 4 MiB transport limit")
    datasets = manifest["datasets"]
    if not isinstance(datasets, list) or not 1 <= len(datasets) <= MAX_DATASETS:
        raise ValueError("request snapshot dataset count is invalid")
    sources = []
    for dataset in datasets:
        if not isinstance(dataset, Mapping) or set(dataset) != {
            "instrument_id",
            "timeframe",
            "market",
            "instrument",
            "bars",
        }:
            raise ValueError("request dataset descriptor is incomplete")
        for field in ("instrument_id", "timeframe", "market"):
            value = dataset[field]
            if type(value) is not str or not value or value.strip() != value:
                raise ValueError("request dataset identity must be canonical text")
        meta = dataset["instrument"]
        if not isinstance(meta, Mapping) or set(meta) != _METADATA:
            raise ValueError("request instrument metadata must be explicit")
        converted = dict(meta)
        for name, value in meta.items():
            if type(value) is not str or not value or value.strip() != value:
                raise ValueError("request instrument metadata must be canonical text")
            if name in _NUMERIC_METADATA:
                number = Decimal(value)
                if (
                    not number.is_finite()
                    or number <= 0
                    or not math.isfinite(float(number))
                    or float(number) == 0
                ):
                    raise ValueError("request instrument numeric metadata is invalid")
                if decimal_string(number) != value:
                    raise ValueError("request metadata decimal is not canonical")
                converted[name] = float(number)
        instrument = InstrumentContext(**converted)
        period = normalized_period(to_pine_timeframe(dataset["timeframe"]))
        envelopes = dataset["bars"]
        if not isinstance(envelopes, list) or len(envelopes) > MAX_SOURCE_BARS:
            raise ValueError("request dataset bar count is invalid")
        bars, snapshot_id = [], None
        for envelope in envelopes:
            checked = decode_canonical_bar(
                envelope,
                context={
                    "stack_manifest_hash": context["stack_manifest_hash"],
                    "producer_commits": context["producer_commits"],
                    "instrument_id": dataset["instrument_id"],
                    "series_id": dataset["instrument_id"] + ":" + dataset["timeframe"],
                    "timeframe": dataset["timeframe"],
                },
            )
            if checked.finality.value != "FINAL":
                raise ValueError("compiled historical requests require original final snapshots")
            if snapshot_id is not None and snapshot_id != envelope["snapshot_id"]:
                raise ValueError("request dataset snapshot changes within one source")
            snapshot_id = envelope["snapshot_id"]
            bars.append(
                CanonicalBar(
                    dataset["instrument_id"],
                    period,
                    checked.time,
                    checked.time_close + 1,
                    *(envelope[name] for name in ("open", "high", "low", "close", "volume")),
                    DataFinality.FINAL,
                    0,
                )
            )
        sources.append(
            RequestSource(
                dataset["instrument_id"],
                instrument,
                dataset["market"],
                period,
                tuple(bars),
                manifest["content_hash"],
            )
        )
    return SnapshotRequestProvider(tuple(sources), max_bars=MAX_SOURCE_BARS)


def admit_request_data(
    source: str | bytes, engine_config: Mapping[str, Any], context: Mapping[str, Any]
):
    """Reject a missing/invalid provider before staging or starting a worker."""
    tree = ast.parse(source)
    required = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "pinelib.abi.compiled_request"
        and any(alias.name in {"security_v1", "security_lower_tf_v1"} for alias in node.names)
        for node in ast.walk(tree)
    )
    manifest = engine_config.get("request_manifest")
    if manifest is None:
        if required:
            raise ValueError(
                "RC6_REQUEST_DATA: compiled requests require preloaded immutable request snapshots"
            )
        return None
    return request_provider_from_manifest(manifest, context)
