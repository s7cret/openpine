"""Architecture acceptance: compatibility, restart plans, bounded work and retention."""

import concurrent.futures
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

import apsw

from apps.openpine.bootstrap import local_manager, local_services
from apps.ui.server import Server
from packages.compiler.semantic.specification import ROOT
from packages.storage.database import SCHEMA_VERSION
from packages.contracts import HISTORICAL_PROFILE_ID, HistoricalBar, HistoricalData, OpenPineError
from packages.contracts.marketdata import DataQuery
from packages.contracts.storage import ResourcePolicy
from packages.contracts.wire import canonical, digest
from packages.execution.artifacts import export_program, import_program
from packages.execution.identity import implementation_hash, build_identity
from packages.execution.limits import WorkLimiter
from packages.execution.queries import legacy_response
from packages.execution.session import Session
from packages.marketdata.providers import make_snapshot
from packages.storage.backup import backup, restore
from packages.storage.candles import CandleSource
from packages.storage.objects import ObjectStore
from tools.verify_architecture import violations
from test_jobs import request, wait


class HardeningTests(unittest.TestCase):
    def test_maintenance_rejects_escaped_roots_before_deleting_anything(self):
        with tempfile.TemporaryDirectory() as directory, local_services(directory) as services:
            orphan=services.objects.put({"unpublished":True})
            original=Path.resolve
            for target in (services.objects.root,services.lifecycle.root/"attempts"):
                def escaped(path,*args,**kwargs):
                    return services.lifecycle.root.parent/"outside-state" if path == target else original(path,*args,**kwargs)
                with patch.object(Path,"resolve",escaped),self.assertRaisesRegex(ValueError,"escapes"):
                    services.lifecycle.collect(apply=True,grace_seconds=0)
                services.objects.verify(orphan)

    def test_source_plan_rejects_changed_execution_without_retry(self):
        with tempfile.TemporaryDirectory() as directory, local_manager(directory,
                policy=ResourcePolicy(workers=1,total_memory_mb=512,max_attempts=3)) as manager:
            with patch("packages.execution.preparation.plan_identity",return_value=("0"*64,"0"*64)):
                job=manager.submit(request())["job_id"]
            state=wait(manager,job)
            self.assertEqual(state["status"],"failed",state)
            self.assertEqual(state["error"]["code"],"PLAN_IMPLEMENTATION")
            self.assertEqual(state["attempt_count"],1)

    def test_runtime_rejection_is_not_retried_as_an_infrastructure_crash(self):
        with tempfile.TemporaryDirectory() as directory, local_manager(directory,
                policy=ResourcePolicy(workers=1,total_memory_mb=512,max_attempts=3)) as manager:
            payload=request()
            payload["input"]["source"]='//@version=6\nindicator("runtime error")\nplot(ta.ema(close,0))'
            job=manager.submit(payload)["job_id"]
            state=wait(manager,job)
            self.assertEqual(state["error"]["code"],"EMA_LENGTH")
            self.assertEqual(state["attempt_count"],1)

    def test_many_output_values_keep_export_bounded_even_with_few_bars(self):
        response={"status":"executed","result":{"series":[],"observation_count":1000,
            "outputs":[{"count":1000} for _ in range(101)]}}
        def forbidden(*args):raise AssertionError("large export materialized")
        self.assertIs(legacy_response(None,response,None,forbidden),response)

    def test_empty_catalog_backup_restores_without_any_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with local_services(root/"live") as services:
                backup(services.db,root/"live",root/"backup")
            restore(root/"backup",root/"restore")
            with local_services(root/"restore") as services:
                self.assertEqual(services.lifecycle.inspect()["object_count"],0)

    def test_transport_change_preserves_saved_ir_but_changes_build(self):
        session = Session(profile_id=HISTORICAL_PROFILE_ID)
        program = session.compile('//@version=6\nindicator("compatibility")\nplot(close)')
        saved = export_program(program)
        target = ROOT / "apps/ui/server.py"
        original = Path.read_bytes
        def changed(path):
            raw = original(path)
            return raw+b"\n# transport change\n" if path == target else raw
        before = implementation_hash(), build_identity()
        with patch.object(Path,"read_bytes",changed):
            self.assertEqual(implementation_hash(),before[0])
            self.assertNotEqual(build_identity(),before[1])
            fresh = Session(profile_id=HISTORICAL_PROFILE_ID)
            result = fresh.execute(import_program(saved),data=HistoricalData("test","TEST:A","1",(HistoricalBar(0,7.),)))
        self.assertEqual(result.outputs[0].values,(7.,))
        with patch.object(Path,"read_bytes",lambda p: original(p)+b"\n# changed\n" if p == ROOT/"packages/runtime/engine.py" else original(p)):
            self.assertNotEqual(implementation_hash(),before[0])

    def test_boundary_gate_detects_nested_and_relative_imports(self):
        self.assertEqual(violations(),[])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); package=root/"packages/runtime";package.mkdir(parents=True)
            (package/"bad.py").write_text("def bad():\n    from packages.storage.objects import ObjectStore\n    from ..storage import objects\n")
            self.assertEqual(len(violations(root)),2)

    def test_restart_uses_frozen_retry_and_budget_not_current_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            manager=local_manager(directory,policy=ResourcePolicy(workers=1,total_memory_mb=512,max_attempts=2,heartbeat_timeout=3))
            try:
                manager.stopping.set();manager.thread.join()
                # Freeze a durable running attempt, then close only its storage,
                # reproducing the metadata left by a coordinator crash.
                manager.stopping.clear()
                job=manager.submit(request())["job_id"]
                plan=manager.plan(job)
                state=manager.get(job)
                state.update(status="running",attempt_id=uuid.uuid4().hex,attempt_count=1,started_at=time.time())
                manager.repository.claim(state)
                with self.assertRaises(apsw.ConstraintError):
                    manager.db.execute("UPDATE run_plans SET plan=? WHERE job_id=?",(canonical(replace(plan,max_attempts=1).payload()),job))
            finally:
                manager.services.close();manager.closed=True
            with local_manager(directory,policy=ResourcePolicy(workers=1,total_memory_mb=512,max_attempts=1,heartbeat_timeout=120)) as restarted:
                self.assertEqual(restarted.plan(job),plan)
                state=wait(restarted,job)
                self.assertEqual(state["status"],"succeeded",state)
                self.assertEqual(state["attempt_count"],2)
                self.assertEqual(restarted.summary(job)["run_plan_sha256"],digest(plan.payload()))
                with self.assertRaises(ValueError):
                    restarted.repository.attach_plan(job,replace(plan,max_attempts=1))

    def test_new_host_cannot_silently_shrink_saved_worker_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            manager=local_manager(directory)
            manager.stopping.set();manager.thread.join();manager.stopping.clear()
            job=manager.submit(request())["job_id"]
            manager.services.close();manager.closed=True
            with local_manager(directory,policy=ResourcePolicy(workers=1,memory_mb_per_worker=128,total_memory_mb=128)) as restarted:
                state=wait(restarted,job)
                self.assertEqual(state["error"]["code"],"RESOURCE_POLICY_INCOMPATIBLE")
                self.assertEqual(restarted.plan(job).memory_mb,512)

    def test_v1_catalog_migration_preserves_input_and_backfills_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            with local_manager(directory) as manager:
                job=manager.submit(request())["job_id"]
                wait(manager,job)
                stored=manager.request(job)
                original=manager.objects.path(stored["input_ref"]).read_bytes()
            db=apsw.Connection(str(Path(directory)/"catalog.sqlite3"))
            db.execute('DROP TABLE search_assessments; DROP TABLE search_holdouts; DROP TABLE searches')
            db.execute("DROP TABLE experiment_variants; DROP TABLE experiments; DROP TABLE input_presets; DROP INDEX IF EXISTS datasets_created; DROP INDEX IF EXISTS coverage_snapshot; DROP TABLE live_bindings; DROP TABLE live_arrivals; DROP TABLE live_series; DROP TABLE live_sessions; DROP TABLE live_closed; DROP TABLE live_parts; DROP TABLE live_observations; DROP TABLE live_feeds; DROP TABLE data_pages; DROP TABLE data_tasks; DROP TABLE market_instruments; DROP TABLE replay_parts; DROP TABLE replay_events; DROP TABLE replays; DROP TABLE checkpoints; DROP TABLE run_plans; DROP TABLE selection_cache; DROP TABLE pins; DROP TABLE settings; ALTER TABLE coverage DROP COLUMN created_at; DELETE FROM imports WHERE path='__run_plans_v1__'; PRAGMA user_version=1")
            db.close()
            with local_manager(directory) as manager:
                self.assertEqual(manager.db.connection.pragma("user_version"),SCHEMA_VERSION)
                self.assertEqual(manager.request(job),stored)
                self.assertEqual(manager.objects.path(stored["input_ref"]).read_bytes(),original)
                self.assertEqual(manager.plan(job).input_ref,stored["input_ref"])

    def test_cached_range_reuses_manifest_without_reading_rows(self):
        with tempfile.TemporaryDirectory() as directory, local_services(directory) as services:
            calls=[]
            class Provider:
                revision=0
                def fetch(provider,query):
                    calls.append(1)
                    return make_snapshot(HistoricalData("cache","TEST:A","1",tuple(
                        HistoricalBar(t,float(provider.revision)) for t in range(0,120000,60000))),{"provider":"test.1"})
            provider=Provider();query=DataQuery("TEST:A","1",0,120000)
            first=services.market.ensure(provider,query,namespace="test.1")
            with patch.object(CandleSource,"rows",side_effect=AssertionError("cached range scanned")):
                self.assertEqual(services.market.ensure(provider,query,namespace="test.1"),first)
            self.assertEqual(len(calls),1)
            provider.revision=1
            second=services.market.ensure(provider,query,namespace="test.1",refresh=True)
            self.assertNotEqual(first,second)
            self.assertEqual([b.close for b in services.market.source(first)],[0.,0.])
            self.assertEqual([b.close for b in services.market.source(second)],[1.,1.])

    def test_weighted_admission_fails_fast_and_releases_after_error(self):
        limiter=WorkLimiter()
        entered,release=threading.Event(),threading.Event()
        def prepare():
            with limiter.slot("prepare"):
                entered.set();release.wait(5)
        thread=threading.Thread(target=prepare);thread.start();self.assertTrue(entered.wait(5))
        try:
            with self.assertRaises(OpenPineError):
                with limiter.slot("prepare"): pass
            with limiter.slot("read"):
                self.assertEqual(limiter.reserved_mb,384)
                def extra():
                    with limiter.slot("read"): pass
                with concurrent.futures.ThreadPoolExecutor(1) as pool:
                    with self.assertRaises(OpenPineError):pool.submit(extra).result()
        finally:
            release.set();thread.join(5)
        with self.assertRaises(RuntimeError),limiter.slot("prepare"):
            raise RuntimeError("interrupted preparation")
        self.assertEqual(limiter.reserved_mb,0)

    def test_gc_backup_and_retention_protect_roots_and_ignore_orphans(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with local_manager(root/"live") as manager:
                job=manager.submit(request())["job_id"];wait(manager,job)
                expected=manager.result(job)
                pinned=manager.freeze_input({"source":"//@version=6\nindicator(\"saved\")\nplot(close)","source_name":"saved.pine"})
                orphan=manager.objects.put({"unused":"unpublished attempt"})
                backup(manager.db,manager.directory,root/"backup")
                self.assertFalse((root/"backup/objects"/orphan[:2]/orphan).exists())
            restore(root/"backup",root/"restore")
            with local_manager(root/"restore") as restored:
                self.assertEqual(restored.result(job),expected)
                restored.objects.verify(pinned)
            services=local_services(root/"live")
            try:
                report=services.lifecycle.collect(grace_seconds=0)
                self.assertIn(orphan,report["candidates"]);self.assertNotIn(pinned,report["candidates"])
                services.objects.verify(orphan)
                services.lifecycle.collect(apply=True,grace_seconds=0)
                self.assertFalse(services.objects.path(orphan).exists())
                cutoff=time.time()
                preview=services.lifecycle.prune(cutoff)
                self.assertIn(job,preview["jobs"])
                self.assertTrue(services.db.get(job))
                services.lifecycle.prune(cutoff,apply=True)
                services.lifecycle.collect(apply=True,grace_seconds=0)
                services.objects.verify(pinned)
                self.assertEqual(services.db.execute("SELECT count(*) FROM jobs"),[(0,)])
                services.db.check()
            finally:services.close()

    def test_collection_refuses_active_jobs_and_corrupt_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            manager=local_manager(directory)
            manager.stopping.set();manager.thread.join();manager.stopping.clear()
            try:
                job=manager.submit(request())["job_id"]
                with self.assertRaises(ValueError):manager.services.lifecycle.collect(apply=True,grace_seconds=0)
                manager.cancel(job)
                manager.objects.path(manager.request(job)["input_ref"]).write_bytes(b"broken")
                with self.assertRaises(ValueError):manager.services.lifecycle.collect(apply=True,grace_seconds=0)
            finally:manager.close()

    def test_object_quota_is_shared_across_processes_and_allows_dedup(self):
        with tempfile.TemporaryDirectory() as directory:
            script="from packages.storage.objects import ObjectStore;import sys; ObjectStore(sys.argv[1],quota_bytes=128).put(sys.argv[2]*80)"
            children=[subprocess.Popen([sys.executable,"-c",script,directory,value],cwd=ROOT,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE) for value in ("A","B")]
            results=[p.communicate(timeout=15) for p in children]
            self.assertEqual(sorted(p.returncode for p in children),[0,1],results)
            objects=ObjectStore(directory,quota_bytes=128)
            names=list(Path(directory).glob("[0-9a-f][0-9a-f]/*"))
            self.assertLessEqual(sum(p.stat().st_size for p in names),128)
            objects.put(objects.get(names[0].name))


class AdmissionHTTPTests(unittest.TestCase):
    def test_busy_preparation_keeps_status_available_and_http_threads_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            server=Server(("127.0.0.1",0),directory)
            thread=threading.Thread(target=server.serve_forever);thread.start()
            base=f"http://127.0.0.1:{server.server_port}"
            def call(path,payload=None):
                req=urllib.request.Request(base+path,data=canonical(payload) if payload is not None else None,
                    headers={"Content-Type":"application/json","X-OpenPine-Token":server.token})
                try:response=urllib.request.urlopen(req,timeout=10)
                except urllib.error.HTTPError as error:response=error
                with response:return response.status,json.loads(response.read())
            entered,release=threading.Event(),threading.Event()
            original=server.app.handle
            def blocked(request):
                entered.set();release.wait(5);return original(request)
            payload={"source":'//@version=6\nindicator("bounded")\nplot(close)',"source_name":"bounded.pine"}
            try:
                with patch.object(server.app,"handle",side_effect=blocked),concurrent.futures.ThreadPoolExecutor(1) as pool:
                    first=pool.submit(call,"/api/compile",payload);self.assertTrue(entered.wait(5))
                    busy=call("/api/compile",payload)
                    self.assertEqual(busy[0],503)
                    self.assertEqual(busy[1]["diagnostics"][0]["code"],"COORDINATOR_BUSY")
                    self.assertEqual(call("/api/config")[0],200)
                    release.set();self.assertEqual(first.result()[0],200)
                sockets=[]
                try:
                    for _ in range(server.jobs.limiter.policy.connections):
                        conn=socket.create_connection(("127.0.0.1",server.server_port),timeout=5)
                        conn.sendall(b"GET /api/config HTTP/1.1\r\n")
                        sockets.append(conn)
                    extra=socket.create_connection(("127.0.0.1",server.server_port),timeout=5)
                    try:self.assertIn(b"503",extra.recv(1024))
                    finally:extra.close()
                finally:
                    for conn in sockets:conn.close()
            finally:
                release.set();server.shutdown();thread.join(5);server.server_close()
