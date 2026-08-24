from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from marketdata_provider.canonical.provider import ProviderRawBar, build_public_snapshot
from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe
from openpine_contracts import Finality, RevisionState

from openpine.data.orchestrator import (
    CanonicalBarSeries,
    DataOrchestrator,
    StorageUnavailableError,
)

STACK_HASH = "sha256:" + "d" * 64
COMMIT = "e098947dfd30444273090e521e5c749673909c37"


def _query() -> BarQuery:
    return BarQuery(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        timeframe=parse_timeframe("1m"),
        start_ms=1_000,
        end_ms=61_000,
        source="provider",
        gap_policy="allow_with_metadata",
    )


def _snapshot() -> dict:
    return build_public_snapshot(
        _query(),
        [
            ProviderRawBar(
                instrument_id=_query().instrument.serialize(),
                timeframe="1m",
                open_time_utc_ms=1_000,
                close_time_utc_ms=60_999,
                open="10.00",
                high="11.00",
                low="9.00",
                close="10.50",
                volume="2.00",
                finality=Finality.FINAL,
                provider="binance",
                provider_revision="revision-1",
                revision_state=RevisionState.ORIGINAL,
                revision=0,
            )
        ],
        provider_revision={"known": True, "revision": "revision-1"},
        producer_commit=COMMIT,
        stack_id=STACK_HASH,
    )


class _Provider:
    persists_fetches = True

    def __init__(self, snapshot: dict) -> None:
        self.snapshot = snapshot

    def fetch_bars(self, query: BarQuery) -> dict:
        assert query == _query()
        return deepcopy(self.snapshot)


def _orchestrator(snapshot: dict) -> DataOrchestrator:
    return DataOrchestrator(
        provider=_Provider(snapshot),
        store=SimpleNamespace(),
        cache_enabled=False,
    )


def test_orchestrator_preserves_exact_sealed_bar_envelopes_with_compatibility_bars() -> None:
    snapshot = _snapshot()
    loaded = _orchestrator(snapshot).load_bars(_query())

    assert isinstance(loaded, CanonicalBarSeries)
    assert loaded.canonical_bars == tuple(snapshot["bars"])
    assert loaded.snapshot == snapshot
    assert loaded.bars[0].time == 1_000
    assert loaded.bars[0].close == 10.5
    assert loaded.bars[0].closed is True


def test_orchestrator_rejects_tampered_canonical_bar_before_return() -> None:
    snapshot = _snapshot()
    snapshot["bars"][0]["close"] = "99.00"

    with pytest.raises(StorageUnavailableError, match="content hash"):
        _orchestrator(snapshot).load_bars(_query())
