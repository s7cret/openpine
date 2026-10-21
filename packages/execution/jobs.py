"""A single coordinator owns the SQLite queue and bounded isolated attempts."""

import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path

from packages.contracts.application import TERMINAL_STATUSES
from packages.contracts.storage import ResourcePolicy
from packages.contracts.wire import atomic_json, read_json, digest
from packages.execution.resources import resident_bytes
from packages.execution.preparation import invalid, make_plan

ROOT = Path(__file__).resolve().parents[2]

class JobManager:
    def __init__(self, directory, *, max_workers=2, capacity=32, policy=None, services=None):
        if type(capacity) is not int or not 1 <= capacity <= 10000:
            invalid("Invalid queue capacity.")
        self.policy = policy or ResourcePolicy(workers=max_workers, total_memory_mb=max_workers * 512)
        self.directory = Path(directory).resolve()
        if services is None:
            raise TypeError("Construct local coordinators with apps.openpine.bootstrap.local_manager.")
        self.services = services
        self.db, self.lease = services.db, services.lease
        self.repository = services.repository
        self.objects, self.market = services.objects, services.market
        self.preparation, self.queries = services.preparation, services.queries
        self.limiter = services.limiter
        self.lock = threading.RLock()
        self.children, self.fatal = {}, None
        self.stopping = threading.Event()
        self.closed = False
        self.max_workers, self.capacity = self.policy.workers, capacity
        try:
            self.preparation.migrate(self.directory, self.policy)
            for state in self.repository.active():
                if state["status"] == "running":
                    if not self._capture_checkpoint(state):continue
                    cursor=0 if not state.get("checkpoint_id") else self.services.checkpoints.manifest(state["checkpoint_id"])["completed"]
                    if state.get("checkpoint_id") and (state.get("pause_requested") or state.get("pause_after_bars") is not None and cursor>=state["pause_after_bars"]):
                        self._pause_committed(state)
                    else:
                        self._failed_attempt(state, "HOST_RESTARTED", "Coordinator stopped before publication.")
        except BaseException:
            self.services.close()
            raise
        self.thread = threading.Thread(target=self._loop, name="openpine-jobs", daemon=True)
        self.thread.start()

    def freeze_input(self, content):
        return self.preparation.freeze_input(content)

    def plan(self, job_id):
        self.get(job_id)
        return self.repository.plan(job_id)

    def submit(self, request, *, checkpoint_interval_bars=100000, pause_after=None):
        with self.lock:
            if self.stopping.is_set() or self.fatal:
                invalid("Coordinator is unavailable.", "STORAGE_UNAVAILABLE")
            if len(self.repository.active()) >= self.capacity:
                invalid("Job queue is full.", "JOB_QUEUE_FULL")
            request = self.preparation.freeze(request)
            total = self.market.manifest(request["snapshot_id"])["identity"]["count"]
            if pause_after is not None and (type(pause_after) is not int or not 1<=pause_after<=total):
                invalid("Pause cursor must identify a bar inside the input.")
            plan=replace(make_plan(request,self.policy),checkpoint_interval_bars=checkpoint_interval_bars)
            job_id = uuid.uuid4().hex
            state = {"schema_version": "openpine.job-status.2", "job_id": job_id,
                "status": "queued", "phase": "queued", "completed": 0, "total": total,
                "created_at": time.time(), "started_at": None, "finished_at": None,
                "worker_pid": None, "error": None, "result_sha256": None,
                "result_id": None, "attempt_id": None, "attempt_count": 0,"failure_count":0,
                "checkpoint_id":None,"pause_requested":False,"pause_after_bars":pause_after}
            self.repository.insert(state, request, plan=plan)
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
        self.get(job_id)
        return self.queries.summary(job_id)

    def result(self, job_id):
        self.get(job_id)
        return self.queries.result(job_id)

    def chart(self, job_id, start=0, limit=2000):
        self.get(job_id)
        return self.queries.chart(job_id, start, limit)

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
        if not self._capture_checkpoint(state):return
        error = {"code": code, "message": message}
        state["failure_count"]=state.get("failure_count",max(0,state["attempt_count"]-1))+1
        if state["failure_count"] < self.repository.plan(state["job_id"]).max_attempts:
            previous = state["attempt_id"]
            state.update(status="queued", phase="queued", error=error, worker_pid=None, attempt_id=None)
            self.repository.retry(state, previous, error)
        else:
            self._finish(state, "failed", error=error)

    def _capture_checkpoint(self,state):
        if not state.get("attempt_id"):return True
        path=self.directory/"attempts"/state["job_id"]/state["attempt_id"]/"checkpoint.json"
        if not path.is_file():return True
        try:
            envelope=read_json(path,max_bytes=4096)
        except FileNotFoundError:return True
        try:
            if (envelope.keys()!={"attempt_id","checkpoint_id","completed"}
                    or envelope["attempt_id"]!=state["attempt_id"]):raise ValueError("Stale checkpoint publication.")
            if envelope["checkpoint_id"]==state.get("checkpoint_id"):return True
            manifest=self.services.checkpoints.manifest(envelope["checkpoint_id"],
                plan=self.repository.plan(state["job_id"]),total=state["total"])
            if envelope["completed"]!=manifest["completed"]:raise ValueError("Checkpoint cursor mismatch.")
            self.repository.checkpoint(state,envelope["checkpoint_id"],envelope["completed"])
            return True
        except (ValueError,KeyError,TypeError,OSError) as error:
            self._finish(state,"failed",error={"code":"CHECKPOINT_INVALID","message":str(error)})
            return False

    def _pause_committed(self,state):
        self._stop_child(state["job_id"])
        completed=0
        if state.get("checkpoint_id"):
            completed=self.services.checkpoints.manifest(state["checkpoint_id"],
                plan=self.repository.plan(state["job_id"]),total=state["total"])["completed"]
        state.update(status="paused",phase="paused",completed=completed,pause_requested=False,
            worker_pid=None,error=None,finished_at=None)
        self.repository.pause(state)

    def pause(self,job_id):
        with self.lock:
            state=self.get(job_id)
            if state["status"]=="queued":self._pause_committed(state)
            elif state["status"]=="running":
                state["pause_requested"]=True
                self.repository.save(state)
                path=self.directory/"attempts"/job_id/state["attempt_id"]/"control.json"
                atomic_json(path,{"attempt_id":state["attempt_id"],"pause":True})
            return self.get(job_id)

    def resume(self,job_id):
        with self.lock:
            state=self.get(job_id)
            if state["status"]!="paused":invalid("Only a paused job can resume.")
            if self.stopping.is_set() or self.fatal:invalid("Coordinator is unavailable.","STORAGE_UNAVAILABLE")
            if len(self.repository.active())>=self.capacity:invalid("Job queue is full.","JOB_QUEUE_FULL")
            state.update(status="queued",phase="queued",attempt_id=None,worker_pid=None,
                pause_requested=False,pause_after_bars=None,error=None,finished_at=None)
            self.repository.resume(state)
            return state

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
        plan = self.repository.plan(job_id)
        state["resumed_from_bar"]=0
        if state.get("checkpoint_id"):
            state["resumed_from_bar"]=self.services.checkpoints.manifest(state["checkpoint_id"],plan=plan,total=state["total"])["completed"]
        state["completed"]=state["resumed_from_bar"]
        manifest = self.market.manifest(plan.snapshot_id)
        path = self.directory / "attempts" / job_id / attempt_id
        path.mkdir(parents=True)
        atomic_json(path / "request.json", {**request, "attempt_id": attempt_id,
            "dataset_manifest_ref": self.objects.put(manifest), "run_plan": plan.payload(),
            "checkpoint_id":state.get("checkpoint_id"),"pause_after_bars":state.get("pause_after_bars"),
            "storage_quota_bytes": self.objects.quota_bytes})
        log = (path / "worker.log").open("wb")
        bootstrap = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('apps.worker',run_name='__main__')"
        try:
            env = {**os.environ, "OMP_NUM_THREADS": str(plan.threads),
                "OPENBLAS_NUM_THREADS": str(plan.threads)}
            process = subprocess.Popen([sys.executable, "-X", "utf8", "-c", bootstrap, str(ROOT),
                "--directory", str(path), "--store", str(self.directory), "--parent-pid", str(os.getpid())],
                cwd=path, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError:
            log.close(); raise
        self.children[job_id] = (process, log, time.monotonic() + plan.timeout_seconds, path, time.monotonic())
        state["worker_pid"] = process.pid
        self.repository.save(state)

    def _tick(self):
        with self.lock:
            total_rss = 0
            for job_id, (process, _, deadline, path, born) in list(self.children.items()):
                state = self.get(job_id)
                plan = self.repository.plan(job_id)
                if not self._capture_checkpoint(state):continue
                if time.monotonic() >= deadline:
                    self._finish(state, "timed_out", error={"code": "JOB_TIMEOUT", "message": "Worker exceeded its timeout."})
                elif process.poll() is not None:
                    try:
                        if process.returncode:
                            raise ValueError(f"Worker exited with code {process.returncode}.")
                        envelope = read_json(path / "completion.json")
                        if envelope.keys()=={"attempt_id","checkpoint_id","status"} and envelope["status"]=="paused":
                            if envelope["attempt_id"]!=state["attempt_id"] or envelope["checkpoint_id"]!=state.get("checkpoint_id"):
                                raise ValueError("Pause completion differs from the committed checkpoint.")
                            self._pause_committed(state)
                            continue
                        if envelope.keys() != {"attempt_id", "result_id"} or envelope["attempt_id"] != state["attempt_id"]:
                            raise ValueError("Stale attempt completion.")
                        response = self.objects.get(envelope["result_id"])
                        if response.get("status") not in ("executed", "rejected"):
                            raise ValueError("Invalid worker response.")
                        if response.get("run_plan_sha256") != digest(plan.payload()):
                            raise ValueError("Worker response differs from saved run plan.")
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
                    if rss > plan.memory_mb * 1024**2 or total_rss + resident_bytes(os.getpid()) > (self.policy.total_memory_mb + self.limiter.policy.memory_mb) * 1024**2:
                        self._finish(state, "failed", error={"code": "WORKER_MEMORY", "message": "Worker exceeded its resident memory budget."})
                        continue
                    try:
                        progress = read_json(path / "heartbeat.json", max_bytes=4096)
                        if progress["attempt_id"] != state["attempt_id"]:
                            raise ValueError("Stale heartbeat.")
                        heartbeat = progress["heartbeat"]
                        if time.monotonic() - progress["tick"] > plan.heartbeat_timeout:
                            self._failed_attempt(state, "WORKER_HEARTBEAT", "Worker heartbeat expired.")
                            continue
                        if progress["tick"] == state.get("heartbeat_tick"):
                            continue
                        if (type(progress["completed"]) is int and 0 <= progress["completed"] <= state["total"]
                                and progress["phase"] in ("starting", "compiling", "executing", "publishing","checkpointing","restoring")):
                            state.update(completed=progress["completed"], phase=progress["phase"], heartbeat=heartbeat,
                                heartbeat_tick=progress["tick"])
                            self.repository.progress(state)
                    except FileNotFoundError:
                        if time.monotonic() - born > plan.heartbeat_timeout:
                            self._failed_attempt(state, "WORKER_HEARTBEAT", "Worker did not start its heartbeat.")
                    except (OSError, ValueError, KeyError, TypeError):
                        if time.monotonic() - born > plan.heartbeat_timeout:
                            self._failed_attempt(state, "WORKER_HEARTBEAT", "Worker heartbeat is invalid.")
            for state in self.repository.active():
                if len(self.children) >= self.max_workers:
                    break
                if state["status"] == "queued":
                    plan = self.repository.plan(state["job_id"])
                    if plan.memory_mb > self.policy.memory_mb_per_worker or plan.threads > self.policy.threads_per_worker:
                        self._finish(state, "failed", error={"code":"RESOURCE_POLICY_INCOMPATIBLE",
                            "message":"Saved run plan exceeds the current host worker limits."})
                        continue
                    allocated = sum(self.repository.plan(j).memory_mb for j in self.children)
                    if allocated + plan.memory_mb > self.policy.total_memory_mb or not self.limiter.healthy():
                        continue
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
            self.services.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
