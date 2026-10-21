"""Local durable jobs. A single lease owner supervises bounded child processes."""

import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from packages.contracts import Diagnostic, OpenPineError
from packages.contracts.application import JobStatus, TERMINAL_STATUSES
from packages.contracts.wire import atomic_json, canonical, digest, read_json
from packages.marketdata.providers import snapshot_from_payload

ROOT = Path(__file__).resolve().parents[2]
MAX_RESULT_BYTES = 32 * 1024 * 1024


def invalid(message, code="JOB_INVALID"):
    raise OpenPineError(Diagnostic("INPUT_INVALID", code, message))


def validate_request(payload):
    if type(payload) is not dict or payload.keys() != {"schema_version", "input", "snapshot", "timeout_seconds"} or payload["schema_version"] != "openpine.job-request.1":
        invalid("Unknown job request schema/fields.")
    timeout = payload["timeout_seconds"]
    if type(timeout) not in (float, int) or not 0 < timeout <= 300:
        invalid("Job timeout must be greater than zero and at most 300 seconds.")
    content = payload["input"]
    if type(content) is not dict or content.keys() not in ({"source", "source_name"}, {"artifact"}):
        invalid("Job requires either source/source_name or an artifact.")
    if "source" in content and (type(content["source"]) is not str or len(content["source"].encode("utf-8")) > 65536 or type(content["source_name"]) is not str or len(content["source_name"]) > 4096):
        invalid("Invalid source text/name or source exceeds 64 KiB.")
    snapshot_from_payload(payload["snapshot"])
    if len(canonical(payload)) > 16 * 1024 * 1024:
        invalid("Job request exceeds 16 MiB.")


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
    def __init__(self, directory, *, max_workers=2, capacity=32):
        if type(max_workers) is not int or not 1 <= max_workers <= 4 or type(capacity) is not int or not 1 <= capacity <= 128:
            invalid("Invalid worker/queue capacity.")
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lease = Lease(self.directory / ".manager.lock")
        self.max_workers, self.capacity = max_workers, capacity
        self.lock = threading.RLock()
        self.children = {}
        self.states = {}
        self.stopping = threading.Event()
        self.closed = False
        for path in sorted(self.directory.glob("*/status.json")):
            if not re.fullmatch(r"[0-9a-f]{32}", path.parent.name):
                continue
            try:
                state = read_json(path)
                if state["status"] not in TERMINAL_STATUSES:
                    state.update(status=JobStatus.FAILED, finished_at=time.time(),
                                 result_sha256=None,
                                 error={"code": "HOST_RESTARTED", "message": "Host stopped before committing completion."})
                    atomic_json(path, state)
                self.states[path.parent.name] = state
            except (OSError, ValueError, KeyError):
                continue
        self.thread = threading.Thread(target=self._loop, name="openpine-jobs", daemon=True)
        self.thread.start()

    def _write(self, job_id):
        atomic_json(self.directory / job_id / "status.json", self.states[job_id])

    def submit(self, request):
        validate_request(request)
        # Freeze the transport value before the background scheduler can observe it.
        with self.lock:
            if self.stopping.is_set():
                invalid("Job manager is closed.")
            if sum(s["status"] not in TERMINAL_STATUSES for s in self.states.values()) >= self.capacity:
                invalid("Job queue is full.", "JOB_QUEUE_FULL")
            job_id = uuid.uuid4().hex
            path = self.directory / job_id
            path.mkdir()
            atomic_json(path / "request.json", request)
            self.states[job_id] = {"schema_version": "openpine.job-status.1", "job_id": job_id,
                "status": JobStatus.QUEUED, "phase": "queued", "completed": 0,
                "total": len(request["snapshot"]["data"]["bars"]), "created_at": time.time(),
                "started_at": None, "finished_at": None, "worker_pid": None, "error": None,
                "result_sha256": None}
            self._write(job_id)
            return dict(self.states[job_id])

    def get(self, job_id):
        with self.lock:
            if type(job_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", job_id) or job_id not in self.states:
                invalid("Unknown job identifier.", "JOB_NOT_FOUND")
            return dict(self.states[job_id])

    def list(self):
        with self.lock:
            return [dict(s) for s in sorted(self.states.values(), key=lambda s: s["created_at"], reverse=True)[:100]]

    def result(self, job_id):
        with self.lock:
            state = self.get(job_id)
            if state["status"] not in (JobStatus.SUCCEEDED, JobStatus.FAILED) or not state["result_sha256"]:
                invalid("This job has no committed result.", "RESULT_UNAVAILABLE")
            result = read_json(self.directory / job_id / "result.json", max_bytes=MAX_RESULT_BYTES)
            if digest(result) != state["result_sha256"]:
                invalid("Stored result hash mismatch.", "RESULT_INVALID")
            return result

    def request(self, job_id):
        self.get(job_id)
        return read_json(self.directory / job_id / "request.json")

    def _stop_child(self, job_id):
        child = self.children.pop(job_id, None)
        if child:
            process, log, _ = child
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            log.close()

    def _finish(self, job_id, status, *, error=None, result_sha256=None):
        self._stop_child(job_id)
        state = self.states[job_id]
        state.update(status=status, phase="completed" if status == JobStatus.SUCCEEDED else str(status),
                     finished_at=time.time(), error=error, result_sha256=result_sha256)
        if status == JobStatus.SUCCEEDED:
            state["completed"] = state["total"]
        if not result_sha256:
            (self.directory / job_id / "result.json").unlink(missing_ok=True)
        self._write(job_id)

    def cancel(self, job_id):
        with self.lock:
            state = self.get(job_id)
            if state["status"] not in TERMINAL_STATUSES:
                self._finish(job_id, JobStatus.CANCELLED, error={"code": "CANCELLED", "message": "Cancelled by caller."})
            return self.get(job_id)

    def _start(self, job_id):
        path = self.directory / job_id
        request = read_json(path / "request.json")
        log = (path / "worker.log").open("wb")
        bootstrap = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('apps.worker',run_name='__main__')"
        try:
            process = subprocess.Popen([sys.executable, "-X", "utf8", "-c", bootstrap, str(ROOT),
                "--directory", str(path), "--parent-pid", str(os.getpid())], cwd=path,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError:
            log.close()
            raise
        self.children[job_id] = (process, log, time.monotonic() + request["timeout_seconds"])
        self.states[job_id].update(status=JobStatus.RUNNING, phase="starting", worker_pid=process.pid, started_at=time.time())
        self._write(job_id)

    def _tick(self):
        with self.lock:
            for job_id, (process, _, deadline) in list(self.children.items()):
                path = self.directory / job_id
                state = self.states[job_id]
                if process.poll() is not None:
                    try:
                        if process.returncode != 0:
                            raise ValueError(f"Worker exited with code {process.returncode}.")
                        result = read_json(path / "result.json", max_bytes=MAX_RESULT_BYTES)
                        if result.get("status") not in ("executed", "rejected"):
                            raise ValueError("Worker returned an unknown response.")
                        error = result["diagnostics"][0] if result["status"] == "rejected" else None
                        self._finish(job_id, JobStatus.FAILED if error else JobStatus.SUCCEEDED,
                                     error=error, result_sha256=digest(result))
                    except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
                        self._finish(job_id, JobStatus.FAILED, error={"code": "WORKER_CRASH", "message": str(error)})
                elif time.monotonic() >= deadline:
                    self._finish(job_id, JobStatus.TIMED_OUT, error={"code": "JOB_TIMEOUT", "message": "Worker exceeded its execution timeout."})
                elif (path / "progress.json").is_file():
                    try:
                        progress = read_json(path / "progress.json", max_bytes=4096)
                        if type(progress["completed"]) is int and 0 <= progress["completed"] <= state["total"] and progress["phase"] in ("compiling", "executing"):
                            state.update(completed=progress["completed"], phase=progress["phase"])
                            self._write(job_id)
                    except (OSError, ValueError, KeyError, TypeError):
                        pass
            for job_id, state in list(self.states.items()):
                if len(self.children) >= self.max_workers:
                    break
                if state["status"] == JobStatus.QUEUED:
                    try:
                        self._start(job_id)
                    except (OSError, ValueError, TypeError, KeyError) as error:
                        self._finish(job_id, JobStatus.FAILED, error={"code": "WORKER_START", "message": str(error)})

    def _loop(self):
        while not self.stopping.wait(0.04):
            self._tick()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stopping.set()
        self.thread.join(timeout=10)
        with self.lock:
            for job_id, state in list(self.states.items()):
                if state["status"] not in TERMINAL_STATUSES:
                    self._finish(job_id, JobStatus.CANCELLED, error={"code": "HOST_STOPPED", "message": "Application shut down."})
            self.lease.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
