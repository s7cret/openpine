"""Storage admission, failure recovery and immutable replay scenarios."""

import errno
import math
import tempfile
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import apsw
import duckdb

from packages.contracts import Diagnostic, HistoricalBar, HistoricalData, OpenPineError
from packages.contracts.marketdata import DataQuery
from packages.contracts.storage import ResourcePolicy
from packages.contracts.wire import atomic_json, canonical, digest, read_json
from apps.openpine.bootstrap import local_manager as JobManager
from packages.marketdata.providers import make_snapshot, snapshot_payload
from packages.storage.backup import backup, restore
from packages.storage.candles import CandleStore
from packages.storage.database import Database
from test_jobs import request, wait


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = Database(self.root / "catalog.sqlite3")
        self.addCleanup(self.db.close)
        self.store = CandleStore(self.root, self.db)

    def test_exact_engines_and_transaction_rollback(self):
        self.assertEqual(apsw.sqlitelibversion(), "3.54.1")
        self.assertEqual(duckdb.__version__, "2.0.0.dev2610192346")
        with self.assertRaises(RuntimeError), self.db.transaction():
            self.db.execute("INSERT INTO imports VALUES('rolled-back',NULL,NULL)")
            raise RuntimeError("interrupted operation")
        self.assertFalse(self.db.execute("SELECT 1 FROM imports WHERE path='rolled-back'"))
        self.db.check()

    def test_cas_file_barrier_precedes_publication_and_failure_keeps_old_object(self):
        import os
        from packages.storage.objects import ObjectStore
        objects=ObjectStore(self.root/'durability');previous=objects.put({'saved':'old'})
        real_sync,real_link=os.fsync,os.link
        for index,kind in enumerate(('json','stream','copy')):
            with self.subTest(kind=kind):
                value={'next':index};raw=canonical(value);expected=digest(value);source=self.root/'source.json';source.write_bytes(raw)
                def publish():
                    return objects.put(value) if kind=='json' else objects.put_stream(value) if kind=='stream' else objects.copy_file(source)
                with patch('packages.storage.objects.os.fsync',side_effect=OSError(errno.EIO,'sync failed')):
                    with self.assertRaises(OSError):publish()
                self.assertFalse(objects.path(expected).exists());self.assertEqual(objects.get(previous),{'saved':'old'})
                synced=set()
                def sync(fd):
                    stat=os.fstat(fd);synced.add((stat.st_dev,stat.st_ino));return real_sync(fd)
                def link(src,dst):
                    stat=Path(src).stat();self.assertIn((stat.st_dev,stat.st_ino),synced)
                    return real_link(src,dst)
                with patch('packages.storage.objects.os.fsync',side_effect=sync),patch('packages.storage.objects.os.link',side_effect=link):
                    self.assertEqual(publish(),expected)
                self.assertEqual(objects.get(expected),value)

    def test_sqlite_full_rolls_back_and_can_continue(self):
        pages = self.db.connection.pragma("page_count")
        self.db.connection.pragma("max_page_count", pages + 1)
        with self.assertRaises(apsw.FullError):
            with self.db.transaction():
                self.db.execute("INSERT INTO imports VALUES('disk-full',NULL,?)", ("x" * 1_000_000,))
        self.assertFalse(self.db.execute("SELECT 1 FROM imports WHERE path='disk-full'"))
        self.db.connection.pragma("max_page_count", 100000)
        self.db.execute("INSERT INTO imports VALUES('after-full',NULL,NULL)")
        self.db.check()

    def test_legacy_identity_nulls_and_signed_zero_survive_parquet(self):
        data = HistoricalData("edge", "TEST:EDGE", "1", (
            HistoricalBar(0, -0.0), HistoricalBar(60000, None), HistoricalBar(180000, 1.25)))
        snapshot = make_snapshot(data, {"provider": "test.1"})
        self.assertEqual(self.store.save(snapshot), snapshot.sha256)
        restored = self.store.load(snapshot.sha256)
        self.assertEqual(snapshot_payload(restored), snapshot_payload(snapshot))
        self.assertEqual(math.copysign(1, restored.data.bars[0].close), -1)
        self.assertEqual(len(self.store.manifest(snapshot.sha256)["parts"]), 1)

    def test_multiblock_bounded_range_and_corrupt_object(self):
        metadata = {"context_id": "multi", "symbol": "TEST:M", "timeframe": "1", "bar_schema": "openpine.historical-bars.1"}
        with patch("packages.storage.candles.BLOCK_ROWS", 3):
            sha = self.store.ingest(metadata, (HistoricalBar(i*60000, float(i)) for i in range(8)), {"provider": "test.1"})
        self.assertEqual([b.close for b in self.store.source(sha)], list(map(float, range(8))))
        self.assertEqual([b.close for b in self.store.source(sha).rows(60000, 420000, offset=2, limit=2)], [3., 4.])
        manifest = self.store.manifest(sha)
        self.assertEqual(len(manifest["parts"]), 3)
        with patch("packages.storage.candles.BLOCK_ROWS", 4):
            repacked = self.store.ingest(metadata, (HistoricalBar(i*60000, float(i)) for i in range(8)), {"provider": "test.1"})
        self.assertNotEqual(sha, repacked)
        self.assertEqual(self.store.source(sha).identity.sha256, self.store.source(repacked).identity.sha256)
        self.store.objects.path(manifest["parts"][1]["object_id"]).write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            list(self.store.source(sha))

    def test_failed_publication_has_no_dataset_or_coverage(self):
        data = HistoricalData("x", "TEST:X", "1", (HistoricalBar(0, 1.),))
        with patch.object(self.store.objects, "publish", side_effect=OSError(errno.ENOSPC, "disk full")):
            with self.assertRaises(OSError):
                self.store.save(make_snapshot(data, {"provider": "test.1"}))
        self.assertEqual(self.db.execute("SELECT count(*) FROM datasets"), [(0,)])
        self.assertEqual(self.db.execute("SELECT count(*) FROM coverage"), [(0,)])

    def test_coverage_fetches_only_missing_intervals_and_refresh_keeps_old_snapshot(self):
        calls = []

        class Provider:
            offset = 0

            def fetch(provider, query):
                calls.append((query.start_ms, query.end_ms))
                bars = tuple(HistoricalBar(i, float(i//60000 + provider.offset))
                    for i in range(0, 360000, 60000) if query.start_ms <= i < query.end_ms)
                return make_snapshot(HistoricalData("cache", query.symbol, query.timeframe, bars), {"provider": "test.1"})

        provider = Provider()
        first = self.store.ensure(provider, DataQuery("TEST:C", "1", 0, 120000), namespace="test.1")
        second = self.store.ensure(provider, DataQuery("TEST:C", "1", 60000, 240000), namespace="test.1")
        self.assertEqual(calls, [(0, 120000), (120000, 240000)])
        again = self.store.ensure(provider, DataQuery("TEST:C", "1", 60000, 240000), namespace="test.1")
        self.assertEqual(again, second)
        self.assertEqual(len(calls), 2)
        self.assertEqual([b.close for b in self.store.source(second)], [1., 2., 3.])
        provider.offset = 10
        changed = self.store.ensure(provider, DataQuery("TEST:C", "1", 0, 120000), namespace="test.1", refresh=True)
        self.assertEqual([b.close for b in self.store.source(changed)], [10., 11.])
        self.assertEqual([b.close for b in self.store.source(first)], [0., 1.])

    def test_empty_refresh_masks_removed_candles_without_changing_old_snapshot(self):
        class Provider:
            empty = False

            def fetch(provider, query):
                if provider.empty:
                    raise OpenPineError(Diagnostic("INPUT_UNAVAILABLE", "DATA_EMPTY", "No trades."))
                return make_snapshot(HistoricalData("empty", query.symbol, query.timeframe,
                    tuple(HistoricalBar(i, 1.) for i in range(query.start_ms, query.end_ms, 60000))), {"provider": "test.1"})

        provider = Provider()
        original = self.store.ensure(provider, DataQuery("TEST:E", "1", 0, 180000), namespace="test.1")
        provider.empty = True
        with self.assertRaises(ValueError):
            self.store.ensure(provider, DataQuery("TEST:E", "1", 60000, 120000), namespace="test.1", refresh=True)
        selected = self.store.ensure(provider, DataQuery("TEST:E", "1", 0, 180000), namespace="test.1")
        self.assertEqual([b.time for b in self.store.source(selected)], [0, 120000])
        self.assertEqual([b.time for b in self.store.source(original)], [0, 60000, 120000])

    def test_live_candle_is_not_frozen_as_complete_history(self):
        from packages.marketdata.binance import BinanceProvider
        from test_binance import kline
        server_time = [150000]

        def transport(path, params):
            if path.endswith("time"):
                return {"serverTime": server_time[0]}
            return [row for row in [kline(0, 100000), kline(120000, 999999)] if row[0] >= params["startTime"]]

        provider = BinanceProvider(transport=transport)
        query = DataQuery("BINANCE:BTCUSDT", "M", 0, 140000)
        first = self.store.ensure(provider, query)
        self.assertEqual(self.store.missing("binance.spot.rest.1", query), [(120000, 140000)])
        self.assertEqual(self.store.source(first).identity.count, 1)
        self.assertEqual(self.store.ensure(provider, query), first)
        server_time[0] = 1500000
        second = self.store.ensure(provider, query)
        self.assertEqual(self.store.source(second).identity.count, 2)
        self.assertEqual(self.store.source(first).identity.count, 1)
        self.assertEqual(self.store.missing("binance.spot.rest.1", query), [])


class StorageJobTests(unittest.TestCase):
    def test_new_parquet_snapshot_json_export_replays_same_full_result(self):
        from packages.marketdata import data_identity
        from packages.marketdata.providers import snapshot_from_payload
        with tempfile.TemporaryDirectory() as directory, JobManager(directory) as manager:
            original = request()
            data = snapshot_from_payload(original["snapshot"]).data
            snapshot_id = manager.market.ingest({"context_id":data.context_id,"symbol":data.symbol,
                "timeframe":data.timeframe,"bar_schema":data.schema_version},data.bars,{"provider":"new.parquet.1"})
            job = manager.submit({"schema_version":"openpine.job-request.2", "input_ref":manager.freeze_input(original["input"]),
                "snapshot_id":snapshot_id,"timeout_seconds":30})["job_id"]
            self.assertEqual(wait(manager,job)["status"],"succeeded")
            exported = manager.market.export(snapshot_id)
            self.assertEqual(exported["provenance"]["derived_from_snapshot_id"],snapshot_id)
            self.assertEqual(snapshot_from_payload(exported).data,data)
            original["snapshot"] = exported
            replay = manager.submit(original)["job_id"]
            self.assertEqual(wait(manager,replay)["status"],"succeeded")
            first, second = manager.result(job), manager.result(replay)
            self.assertEqual(first["result"],second["result"])
            self.assertEqual(first["result"]["data"]["sha256"],data_identity(data).sha256)
            self.assertNotEqual(first["snapshot_sha256"],second["snapshot_sha256"])

    def test_requests_are_small_shared_references_and_chart_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory, JobManager(directory) as manager:
            a, b = [manager.submit(request())["job_id"] for _ in range(2)]
            self.assertEqual(manager.request(a), manager.request(b))
            self.assertLess(len(canonical(manager.request(a))), 512)
            self.assertNotIn("snapshot", manager.request(a))
            self.assertEqual(wait(manager, a)["status"], "succeeded")
            self.assertEqual(wait(manager, b)["status"], "succeeded")
            window = manager.chart(a, 1, 2)
            self.assertEqual([bar["close"] for bar in window["bars"]], [20., 30.])
            self.assertEqual(window["outputs"][0]["values"], [40., 85.])
            self.assertEqual(window["total"], 4)
            self.assertNotIn("values", manager.summary(a)["result"]["outputs"][0])
            with self.assertRaises(OpenPineError):
                manager.chart(a, 0, 5001)

    def test_retry_uses_new_attempt_and_late_completion_cannot_publish(self):
        policy = ResourcePolicy(workers=1, total_memory_mb=512, max_attempts=2)
        with tempfile.TemporaryDirectory() as directory, JobManager(directory, policy=policy) as manager:
            job = manager.submit(request())["job_id"]
            deadline = time.monotonic() + 10
            while manager.get(job)["status"] == "queued" and time.monotonic() < deadline:
                time.sleep(.01)
            with manager.lock:
                previous = manager.get(job)["attempt_id"]
                manager.children[job][0].kill()
            state = wait(manager, job)
            self.assertEqual(state["status"], "succeeded", state)
            self.assertEqual(state["attempt_count"], 2)
            self.assertNotEqual(state["attempt_id"], previous)
            fake = manager.objects.put({"status": "executed", "result": "late wrong result"})
            atomic_json(Path(directory)/"attempts"/job/previous/"completion.json", {"attempt_id": previous, "result_id": fake})
            manager._tick()
            self.assertEqual(manager.result(job)["result"]["outputs"][0]["values"], [10.,40.,85.,135.])
            self.assertEqual(manager.db.execute("SELECT status FROM attempts WHERE job_id=? ORDER BY number", (job,)),
                [("failed",), ("succeeded",)])

    def test_cancel_is_committed_before_process_signal(self):
        with tempfile.TemporaryDirectory() as directory, JobManager(directory) as manager:
            job = manager.submit(request())["job_id"]
            stop = manager._stop_child
            observed = []

            def check(job_id):
                observed.append(manager.db.get(job_id)["status"])
                stop(job_id)

            with patch.object(manager, "_stop_child", side_effect=check):
                manager.cancel(job)
            self.assertEqual(observed, ["cancelled"])

    def test_worker_memory_budget_failure_is_terminal(self):
        with tempfile.TemporaryDirectory() as directory, JobManager(directory) as manager:
            with manager.lock:
                job = manager.submit(request())["job_id"]
                manager._start(manager.get(job))
                with patch("packages.execution.jobs.resident_bytes", return_value=2**40):
                    manager._tick()
            self.assertEqual(manager.get(job)["error"]["code"], "WORKER_MEMORY")
            self.assertIsNone(manager.get(job)["result_id"])

    def test_backup_restore_replays_and_corrupt_backup_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with JobManager(root/"live") as manager:
                job = manager.submit(request())["job_id"]
                self.assertEqual(wait(manager, job)["status"], "succeeded")
                expected = manager.result(job)
                backup(manager.db, manager.directory, root/"backup")
            restore(root/"backup", root/"restored")
            with JobManager(root/"restored") as manager:
                self.assertEqual(manager.result(job), expected)
                manager.db.check()
            member = next((root/"backup"/"objects").glob("*/*"))
            member.write_bytes(b"corrupted backup")
            with self.assertRaises(ValueError):
                restore(root/"backup", root/"bad-restore")
            self.assertFalse((root/"bad-restore").exists())

    def test_import_legacy_json_is_idempotent_and_preserves_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); job = "a"*32
            legacy = root/job; legacy.mkdir()
            payload = request()
            state = {"schema_version":"openpine.job-status.1", "job_id":job, "status":"running",
                "created_at":time.time(), "phase":"executing", "completed":0, "total":4,
                "started_at":None, "finished_at":None, "worker_pid":None, "error":None, "result_sha256":None}
            atomic_json(legacy/"request.json", payload); atomic_json(legacy/"status.json", state)
            original = {p.name:p.read_bytes() for p in legacy.iterdir()}
            for _ in range(2):
                with JobManager(root) as manager:
                    self.assertEqual(manager.get(job)["error"]["code"], "HOST_RESTARTED")
                    self.assertEqual(len(manager.list()), 1)
                    self.assertEqual(manager.request(job)["schema_version"], "openpine.job-request.2")
            self.assertEqual({p.name:p.read_bytes() for p in legacy.iterdir()}, original)

    def test_aggregate_policy_rejects_oversubscription(self):
        with self.assertRaises(ValueError):
            ResourcePolicy(workers=4, memory_mb_per_worker=512, total_memory_mb=1024)

    def test_start_failure_is_bounded_by_attempt_policy(self):
        policy = ResourcePolicy(workers=1, total_memory_mb=512, max_attempts=2)
        with tempfile.TemporaryDirectory() as directory, JobManager(directory, policy=policy) as manager:
            with manager.lock, patch("packages.execution.jobs.subprocess.Popen", side_effect=OSError("cannot start")):
                job = manager.submit(request())["job_id"]
                manager._tick(); manager._tick(); manager._tick()
            self.assertEqual(manager.get(job)["status"], "failed")
            self.assertEqual(manager.get(job)["attempt_count"], 2)
            self.assertEqual(manager.get(job)["error"]["code"], "WORKER_START")

    def test_expired_heartbeat_rejects_a_live_worker(self):
        with tempfile.TemporaryDirectory() as directory, JobManager(directory) as manager:
            with manager.lock:
                job = manager.submit(request())["job_id"]
                manager._start(manager.get(job))
                heartbeat = {"attempt_id": manager.get(job)["attempt_id"], "heartbeat": time.time()-60,
                    "tick": time.monotonic()-60, "phase": "executing", "completed": 0}
                with patch("packages.execution.supervisor.read_json", return_value=heartbeat):
                    manager._tick()
            self.assertEqual(manager.get(job)["error"]["code"], "WORKER_HEARTBEAT")
            self.assertFalse(manager.children)

    def test_metadata_failure_stops_scheduler_without_publishing_success(self):
        with tempfile.TemporaryDirectory() as directory, JobManager(directory) as manager:
            with patch.object(manager.repository, "progress", side_effect=apsw.FullError("disk full")):
                job = manager.submit(request())["job_id"]
                deadline = time.monotonic()+10
                while not manager.fatal and time.monotonic()<deadline:
                    time.sleep(.01)
            self.assertTrue(manager.fatal)
            self.assertIsNone(manager.db.get(job)["result_id"])
            self.assertFalse(manager.children)
            with self.assertRaises(OpenPineError):
                manager.submit(request())

    def test_repository_fences_terminal_attempt(self):
        with tempfile.TemporaryDirectory() as directory, JobManager(directory) as manager:
            job = manager.submit(request())["job_id"]
            terminal = manager.cancel(job)
            with self.assertRaises(ValueError):
                manager.repository.finish({**terminal, "status": "succeeded", "result_id": "0"*64})
            self.assertEqual(manager.get(job)["status"], "cancelled")
