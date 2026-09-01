from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from marketdata_provider.contracts import (
    Bar,
    BarQuery,
    BarSeries,
    CoverageReport,
    InstrumentKey,
    parse_timeframe,
)

from openpine.gateway.routes import backtest as bt
from tests.admission_helpers import make_deployment_identity, make_sealed_artifact
from tests.rc4_fixtures import admitted_manifest, canonical_series


class _Cursor:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)


class _Storage:
    def execute(self, sql, params=()):
        if str(sql).startswith("PRAGMA"):
            return _Cursor([(0, "run_id"), (1, "data_fingerprint")])
        return _Cursor()

    def commit(self):
        pass


class _Store:
    def __init__(self):
        self.saved = []
        self.failed = []

    def save_result(self, **kwargs):
        self.saved.append(kwargs)

    def mark_failed(self, run_id, message):
        self.failed.append((run_id, message))

    def mark_cancelled(self, run_id, message):
        pass


class _WS:
    def update_progress(self, *args, **kwargs):
        pass

    async def broadcast_progress(self, *args, **kwargs):
        pass


def _series():
    inst = InstrumentKey(exchange="binance", market="spot", symbol="SOLUSDT")
    tf = parse_timeframe("1d")
    bars = (
        Bar(inst, tf, 0, 86_400_000, 10.0, 11.0, 9.0, 10.5, 1.0, True),
        Bar(inst, tf, 86_400_000, 172_800_000, 10.5, 12.0, 10.0, 11.0, 1.0, True),
    )
    query = BarQuery(inst, tf, 0, 172_800_000, gap_policy="allow_with_metadata")
    coverage = CoverageReport(0, 172_800_000, 0, 172_800_000, source_mix=("unit",))
    return canonical_series(BarSeries(query, bars, coverage))
