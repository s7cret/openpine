"""A single coordinator owns the SQLite queue and bounded isolated attempts."""

import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from packages.contracts import Diagnostic, OpenPineError
from packages.contracts.application import TERMINAL_STATUSES
from packages.contracts.storage import ResourcePolicy, JobRepository as JobRepositoryPort
from packages.contracts.wire import atomic_json, canonical, read_json
from packages.marketdata.providers import snapshot_from_payload
from packages.storage.database import Database
from packages.storage.jobs import JobRepository
from packages.storage.candles import CandleStore
from packages.storage.objects import ObjectStore, identifier
from packages.storage.results import legacy_response, series_rows
from packages.execution.resources import resident_bytes

ROOT = Path(__file__).resolve().parents[2]
MAX_RESULT_BYTES = 32 * 1024 * 1024


def invalid(message, code="JOB_INVALID"):
    raise OpenPineError(Diagnostic("INPUT_INVALID", code, message))


def validate_input(content):
    if type(content) is not dict or content.keys() not in ({"source", "source_name"}, {"artifact"}, {"artifact_ref"}, {"artifact_ref", "source_ref"}):
        invalid("Job requires source/source_name or an artifact.")
    for key in ("artifact_ref", "source_ref"):
        if key in content:
            identifier(content[key])
    if "source" in content and (type(content["source"]) is not str or len(content["source"].encode("utf-8")) > 65536
            or type(content["source_name"]) is not str or len(content["source_name"]) > 4096):
        invalid("Invalid source text/name or source exceeds 64 KiB.")


def validate_request(payload):
    if type(payload) is not dict or payload.get("schema_version") not in ("openpine.job-request.1", "openpine.job-request.2"):
        invalid("Unknown job request schema.")
    timeout = payload.get("timeout_seconds")
    if type(timeout) not in (float, int) or not 0 < timeout <= 3600:
        invalid("Job timeout must be greater than zero and at most 3600 seconds.")
    if payload["schema_version"].endswith(".2"):
        if payload.keys() != {"schema_version", "input_ref", "snapshot_id", "timeout_seconds"}:
            invalid("Unknown reference request fields.")
        identifier(payload["input_ref"]); identifier(payload["snapshot_id"])
    else:
        if payload.keys() != {"schema_version", "input", "snapshot", "timeout_seconds"}:
            invalid("Unknown legacy request fields.")
        validate_input(payload["input"])
        snapshot_from_payload(payload["snapshot"])
        if len(canonical(payload)) > 16 * 1024 * 1024:
            invalid("Legacy job request exceeds 16 MiB.")


class Lease:
    def __init__(self, path):
        self.stream = Path(path).open("a+b")
        self.stream.seek(0, 2)
        if not self.stream.tell():
            self.stream.write(b"0"); self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            invalid("Another application owns this job directory.", "JOB_STORE_BUSY")

    def close(self):
        self.stream.close()


class JobManager:
    def __init__(self, directory, *, max_workers=2, capacity=32, policy=None):
        if type(capacity) is not int or not 1 <= capacity <= 10000:
            invalid("Invalid queue capacity.")
        self.policy = policy or ResourcePolicy(workers=max_workers, total_memory_mb=max_workers * 512)
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lease = Lease(self.directory / ".manager.lock")
        self.lock = threading.RLock()
        self.children, self.fatal = {}, None
        self.stopping = threading.Event()
        self.closed = False
        self.max_workers, self.capacity = self.policy.workers, capacity
        try:
            self.db = Database(self.directory / "catalog.sqlite3")
            self.repository: JobRepositoryPort = JobRepository(self.db)
            self.objects = ObjectStore(self.directory / "objects")
            self.market = CandleStore(self.directory, self.db)
            self.migrate_legacy()
            for state in self.repository.active():
                if state["status"] == "running":
                    self._failed_attempt(state, "HOST_RESTARTED", "Coordinator stopped before publication.")
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            self.lease.close()
            raise
        self.thread = threading.Thread(target=self._loop, name="openpine-jobs", daemon=True)
        self.thread.start()

    def migrate_legacy(self):
        if self.repository.scanned("__legacy_scan_v1__"):
            return
        for path in sorted(self.directory.glob("*/status.json")):
            job_id = path.parent.name
            if not re.fullmatch(r"[0-9a-f]{32}", job_id):
                continue
            try:
                state = read_json(path)
                if state["job_id"] != job_id or state["status"] not in {*TERMINAL_STATUSES, "queued", "running"}:
                    raise ValueError("Invalid legacy state.")
                request = self._freeze(read_json(path.parent / "request.json"))
                state.update(attempt_id=None, attempt_count=0, result_id=None)
                if state["status"] not in TERMINAL_STATUSES:
                    state.update(status="failed", finished_at=time.time(), result_sha256=None,
                        error={"code": "HOST_RESTARTED", "message": "Imported interrupted legacy job."})
                elif state.get("result_sha256"):
                    from packages.contracts.wire import digest
                    result = read_json(path.parent / "result.json", max_bytes=MAX_RESULT_BYTES)
                    if digest(result) != state["result_sha256"]:
                        raise ValueError("Legacy result hash mismatch.")
                    state["result_id"] = self.objects.put(result)
                self.repository.insert(state, request, imported_from=str(path))
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.repository.record_import(str(path), error=str(error))
        self.repository.record_import("__legacy_scan_v1__")

    def _freeze(self, request):
        validate_request(request)
        if request["schema_version"].endswith(".1"):
            snapshot_id = self.market.save(snapshot_from_payload(request["snapshot"]))
            content = request["input"]
        else:
            snapshot_id = request["snapshot_id"]
            self.market.manifest(snapshot_id)
            content = self.objects.get(request["input_ref"])
        input_ref = self.freeze_input(content)
        return {"schema_version": "openpine.job-request.2", "input_ref": input_ref,
            "snapshot_id": snapshot_id, "timeout_seconds": request["timeout_seconds"]}

    def freeze_input(self, content):
        validate_input(content)
        if "artifact" in content:
            content = {"artifact_ref": self.objects.put(content["artifact"])}
        if "artifact_ref" in content:
            self.objects.verify(content["artifact_ref"])
        if "source_ref" in content:
            original = self.objects.get(content["source_ref"])
            if type(original) is not dict or original.keys() != {"source", "source_name"}:
                invalid("Invalid source reference.")
            validate_input(original)
        return self.objects.put(content)

    def submit(self, request):
        with self.lock:
            if self.stopping.is_set() or self.fatal:
                invalid("Coordinator is unavailable.", "STORAGE_UNAVAILABLE")
            if len(self.repository.active()) >= self.capacity:
                invalid("Job queue is full.", "JOB_QUEUE_FULL")
            request = self._freeze(request)
            total = self.market.manifest(request["snapshot_id"])["identity"]["count"]
            job_id = uuid.uuid4().hex
            state = {"schema_version": "openpine.job-status.2", "job_id": job_id,
                "status": "queued", "phase": "queued", "completed": 0, "total": total,
                "created_at": time.time(), "started_at": None, "finished_at": None,
                "worker_pid": None, "error": None, "result_sha256": None,
                "result_id": None, "attempt_id": None, "attempt_count": 0}
            self.repository.insert(state, request)
            return state

    def get(self, job_id):
        with self.lock:
            if self.fatal:
                invalid("Storage coordinator stopped: " + self.fatal, "STORAGE_UNAVAILABLE")
            if type(job_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", job_id):
                invalid("Unknown job identifier.", "JOB_NOT_FOUND")
            try:
                return self.repository.get(job_id)
            except KeyError:
                invalid("Unknown job identifier.", "JOB_NOT_FOUND")

    def list(self):
        return self.repository.list()

    def request(self, job_id):
        self.get(job_id)
        return self.repository.request(job_id)

    def summary(self, job_id):
        state = self.get(job_id)
        if not state["result_id"]:
            invalid("This job has no committed result.", "RESULT_UNAVAILABLE")
        try:
            return self.objects.get(state["result_id"], max_bytes=MAX_RESULT_BYTES)
        except ValueError as error:
            invalid(str(error), "RESULT_INVALID")

    def result(self, job_id):
        response = self.summary(job_id)
        try:
            return legacy_response(self.objects, response, self.market.source(self.request(job_id)["snapshot_id"]))
        except OpenPineError:
            raise
        except ValueError as error:
            invalid(str(error), "RESULT_INVALID")

    def chart(self, job_id, start=0, limit=2000):
        response = self.summary(job_id)
        if response["status"] != "executed":
            return response
        if type(start) is not int or type(limit) is not int or start < 0 or not 1 <= limit <= 5000:
            invalid("Chart requires start>=0 and 1..5000 rows.")
        summary = response["result"]
        if "series" not in summary:
            invalid("Legacy result: use full export.")
        source = self.market.source(response["snapshot_sha256"])
        bars = [asdict(bar) for bar in source.rows(offset=start, limit=limit)]
        values = {info["output_id"]: [] for info in summary["outputs"]}
        for _, _, output_id, value in series_rows(self.objects, summary, start, start + limit):
            values[output_id].append(value)
        return {"schema_version": "openpine.chart-window.1", "start": start, "total": source.identity.count,
            "bars": bars, "outputs": [{**info, "values": values[info["output_id"]]} for info in summary["outputs"]]}

    def _stop_child(self, job_id):
        child = self.children.pop(job_id, None)
        if child:
            process, log, *_ = child
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5); log.close()

    def _finish(self, state, status, *, error=None, result_id=None):
        self._stop_child(state["job_id"])
        state.update(status=status, phase="completed" if status == "succeeded" else status,
            finished_at=time.time(), error=error, result_id=result_id, result_sha256=result_id)
        if status == "succeeded":
            state["completed"] = state["total"]
        self.repository.finish(state)

    def _failed_attempt(self, state, code, message):
        self._stop_child(state["job_id"])
        error = {"code": code, "message": message}
        if state["attempt_count"] < self.policy.max_attempts:
            previous = state["attempt_id"]
            state.update(status="queued", phase="queued", error=error, worker_pid=None, attempt_id=None)
            self.repository.retry(state, previous, error)
        else:
            self._finish(state, "failed", error=error)

    def cancel(self, job_id):
        with self.lock:
            state = self.get(job_id)
            if state["status"] not in TERMINAL_STATUSES:
                state.update(status="cancelled", phase="cancelled", finished_at=time.time(),
                    error={"code": "CANCELLED", "message": "Cancelled by caller."})
                self.repository.finish(state)
                self._stop_child(job_id)
            return self.get(job_id)

    def _start(self, state):
        job_id, attempt_id = state["job_id"], uuid.uuid4().hex
        state.update(status="running", phase="starting", attempt_id=attempt_id,
            attempt_count=state["attempt_count"] + 1, started_at=time.time(), finished_at=None, error=None, completed=0)
        self.repository.claim(state)
        request = self.request(job_id)
        manifest = self.market.manifest(request["snapshot_id"])
        path = self.directory / "attempts" / job_id / attempt_id
        path.mkdir(parents=True)
        atomic_json(path / "request.json", {**request, "attempt_id": attempt_id,
            "dataset_manifest_ref": self.objects.put(manifest), "resources": asdict(self.policy)})
        log = (path / "worker.log").open("wb")
        bootstrap = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('apps.worker',run_name='__main__')"
        try:
            env = {**os.environ, "OMP_NUM_THREADS": str(self.policy.threads_per_worker),
                "OPENBLAS_NUM_THREADS": str(self.policy.threads_per_worker)}
            process = subprocess.Popen([sys.executable, "-X", "utf8", "-c", bootstrap, str(ROOT),
                "--directory", str(path), "--store", str(self.directory), "--parent-pid", str(os.getpid())],
                cwd=path, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError:
            log.close(); raise
        self.children[job_id] = (process, log, time.monotonic() + request["timeout_seconds"], path, time.monotonic())
        state["worker_pid"] = process.pid
        self.repository.save(state)

    def _tick(self):
        with self.lock:
            total_rss = 0
            for job_id, (process, _, deadline, path, born) in list(self.children.items()):
                state = self.get(job_id)
                if time.monotonic() >= deadline:
                    self._finish(state, "timed_out", error={"code": "JOB_TIMEOUT", "message": "Worker exceeded its timeout."})
                elif process.poll() is not None:
                    try:
                        if process.returncode:
                            raise ValueError(f"Worker exited with code {process.returncode}.")
                        envelope = read_json(path / "completion.json")
                        if envelope.keys() != {"attempt_id", "result_id"} or envelope["attempt_id"] != state["attempt_id"]:
                            raise ValueError("Stale attempt completion.")
                        response = self.objects.get(envelope["result_id"])
                        if response.get("status") not in ("executed", "rejected"):
                            raise ValueError("Invalid worker response.")
                        error = response["diagnostics"][0] if response["status"] == "rejected" else None
                        if response["status"] == "executed" and (response["snapshot_sha256"] != self.request(job_id)["snapshot_id"]
                                or response["result"]["observation_count"] != state["total"]):
                            raise ValueError("Result does not belong to the requested dataset.")
                        self._finish(state, "failed" if error else "succeeded", error=error, result_id=envelope["result_id"])
                    except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
                        self._failed_attempt(state, "WORKER_CRASH", str(error))
                else:
                    rss = resident_bytes(process.pid); total_rss += rss
                    state["peak_resident_bytes"] = max(state.get("peak_resident_bytes", 0), rss)
                    if rss > self.policy.memory_mb_per_worker * 1024**2 or total_rss > self.policy.total_memory_mb * 1024**2:
                        self._finish(state, "failed", error={"code": "WORKER_MEMORY", "message": "Worker exceeded its resident memory budget."})
                        continue
                    try:
                        progress = read_json(path / "heartbeat.json", max_bytes=4096)
                        if progress["attempt_id"] != state["attempt_id"]:
                            raise ValueError("Stale heartbeat.")
                        heartbeat = progress["heartbeat"]
                        if time.monotonic() - progress["tick"] > self.policy.heartbeat_timeout:
                            self._failed_attempt(state, "WORKER_HEARTBEAT", "Worker heartbeat expired.")
                            continue
                        if progress["tick"] == state.get("heartbeat_tick"):
                            continue
                        if (type(progress["completed"]) is int and 0 <= progress["completed"] <= state["total"]
                                and progress["phase"] in ("starting", "compiling", "executing", "publishing")):
                            state.update(completed=progress["completed"], phase=progress["phase"], heartbeat=heartbeat,
                                heartbeat_tick=progress["tick"])
                            self.repository.progress(state)
                    except FileNotFoundError:
                        if time.monotonic() - born > self.policy.heartbeat_timeout:
                            self._failed_attempt(state, "WORKER_HEARTBEAT", "Worker did not start its heartbeat.")
                    except (OSError, ValueError, KeyError, TypeError):
                        if time.monotonic() - born > self.policy.heartbeat_timeout:
                            self._failed_attempt(state, "WORKER_HEARTBEAT", "Worker heartbeat is invalid.")
            for state in self.repository.active():
                if len(self.children) >= self.max_workers:
                    break
                if state["status"] == "queued":
                    try:
                        self._start(state)
                    except (OSError, ValueError, TypeError, KeyError) as error:
                        self._failed_attempt(state, "WORKER_START", str(error))

    def _loop(self):
        while not self.stopping.wait(.1):
            try:
                self._tick()
            except Exception as error:
                self.fatal = str(error)
                with self.lock:
                    for job_id in list(self.children):
                        self._stop_child(job_id)
                return

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stopping.set(); self.thread.join(timeout=10)
        try:
            with self.lock:
                for state in self.repository.active():
                    self._finish(state, "cancelled", error={"code": "HOST_STOPPED", "message": "Application shut down."})
        finally:
            for job_id in list(self.children):
                self._stop_child(job_id)
            self.db.close(); self.lease.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
