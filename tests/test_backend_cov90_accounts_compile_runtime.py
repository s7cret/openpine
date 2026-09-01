from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace


from openpine.gateway.routes import accounts_data as ad
from openpine.jobs import JobStatus


def test_accounts_data_coverage_and_backfill_paths(monkeypatch, tmp_path):
    state = SimpleNamespace(config=SimpleNamespace(data_cache_root=None, data_dir=tmp_path), scheduler=None)

    class Store:
        def coverage(self, **kwargs):
            return [
                {"timeframe": "1m", "earliest_ms": 1, "latest_ms": 2, "bar_count": 3, "gaps": [{"a": 1}]},
                {"timeframe": "5m", "bar_count": 0},
            ]

    import marketdata_provider

    monkeypatch.setattr(marketdata_provider, "create_candle_store", lambda config: Store())
    rows = asyncio.run(ad.data_coverage("BTCUSDT", state=state))
    assert [row.timeframe for row in rows] == ["1m", "5m"]

    monkeypatch.setattr(marketdata_provider, "create_candle_store", lambda config: (_ for _ in ()).throw(RuntimeError("bad")))
    assert asyncio.run(ad.data_coverage("BTCUSDT", state=state)) == []

    events: list[tuple] = []
    monkeypatch.setattr(ad.ws_manager, "update_progress", lambda *a, **k: events.append((a, k)))
    async def _broadcast(*a, **k):
        return None
    monkeypatch.setattr(ad.ws_manager, "broadcast_progress", _broadcast)

    class Scheduler:
        def __init__(self, job):
            self.job = job
            self.done = None
            self.failed = None
            self.running = False

        def get_job(self, job_id):
            return self.job

        def mark_running(self, job_id):
            self.running = True
            self.job.status = JobStatus.RUNNING

        def mark_done(self, job_id, result):
            self.done = result

        def mark_failed(self, job_id, error):
            self.failed = error

    job = SimpleNamespace(status=JobStatus.PENDING)
    scheduler = Scheduler(job)
    state = SimpleNamespace(scheduler=scheduler)
    monkeypatch.setattr(ad, "_run_data_backfill_sync", lambda payload, state, cb: (cb(5, 1, 10, 2, None, "fetch") or cb(10, 2, 10, 2, None, "write") or {"bars_loaded": 10, "skipped_existing": 2}))
    asyncio.run(ad._run_data_backfill_job("job1", {"symbol": "BTCUSDT"}, state))
    assert scheduler.done["bars_loaded"] == 10
    assert events

    job_iso = SimpleNamespace(status=JobStatus.PENDING)
    scheduler_iso = Scheduler(job_iso)
    state_iso = SimpleNamespace(scheduler=scheduler_iso)
    subprocess_payloads = []
    monkeypatch.setattr(
        ad,
        "_run_data_backfill_subprocess",
        lambda payload: subprocess_payloads.append(payload)
        or {"bars_loaded": 7, "skipped_existing": 0, "execution_mode": "isolated_process"},
    )
    asyncio.run(
        ad._run_data_backfill_job(
            "job-iso",
            {"symbol": "SOLUSDT", "estimated_source_bars": 250_001},
            state_iso,
        )
    )
    assert scheduler_iso.done["bars_loaded"] == 7
    assert scheduler_iso.done["execution_mode"] == "isolated_process"
    assert subprocess_payloads

    job2 = SimpleNamespace(status=JobStatus.PENDING)
    scheduler2 = Scheduler(job2)
    state2 = SimpleNamespace(scheduler=scheduler2)
    monkeypatch.setattr(ad, "_run_data_backfill_sync", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    asyncio.run(ad._run_data_backfill_job("job2", {"symbol": "ETHUSDT"}, state2))
    assert scheduler2.failed == "boom"

    assert asyncio.run(ad._run_data_backfill_job("missing", {}, SimpleNamespace(scheduler=SimpleNamespace(get_job=lambda job_id: None)))) is None
