"""A single coordinator owns the SQLite queue and bounded isolated attempts."""

import os
import re
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import replace,asdict
from pathlib import Path

from packages.contracts.application import TERMINAL_STATUSES
from packages.contracts.storage import ResourcePolicy
from packages.contracts.wire import atomic_json, read_json, digest
from packages.execution.resources import resident_bytes
from packages.execution.supervisor import Supervisor,WorkerFault
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
        self.supervisors = {}
        self.processes = {}
        self.external_workers = {}
        self.external_waiting = {}
        self.waiting = {}
        self.disk_leases = {}
        from .scheduling.fair import FairScheduler
        self.scheduler=FairScheduler(capacity=max(1024,capacity+8))
        self.stopping = threading.Event()
        self.closed = False
        self.max_workers, self.capacity = self.policy.workers, capacity
        try:
            self.preparation.migrate(self.directory, self.policy)
            for state in self.repository.active():
                desired=self.repository.experiment_desired(state['job_id'])
                if desired=='cancelled':
                    self._finish(state,'cancelled');continue
                if desired=='paused':
                    if state['status']=='running' and not self._capture_checkpoint(state):continue
                    self._pause_committed(state);continue
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
            plan=replace(self.preparation.build_plan(request,self.policy),checkpoint_interval_bars=checkpoint_interval_bars)
            return self.enqueue_prepared(request,plan,pause_after=pause_after)

    def enqueue_prepared(self,request,plan,*,job_id=None,experiment_variant=None,pause_after=None):
        """Publish an already frozen composition; no new policy/compilation on dispatch."""
        with self.lock:
            if self.stopping.is_set() or self.fatal:invalid('Coordinator is unavailable.','STORAGE_UNAVAILABLE')
            if len(self.repository.active())>=self.capacity:invalid('Job queue is full.','JOB_QUEUE_FULL')
            if any(getattr(plan,key)!=request[key] for key in ('input_ref','snapshot_id','timeout_seconds')) or request.get('settings')!=plan.settings:
                invalid('Prepared job differs from its immutable run plan.')
            if request.get('evaluation_window')!=plan.evaluation_window:invalid('Prepared evaluation window differs from its plan.')
            if request.get('primary_context')!=(asdict(plan.primary_context) if plan.primary_context else None) or request.get('contexts',[])!=[asdict(c) for c in plan.contexts]:
                invalid('Prepared job context graph differs from its plan.')
            total=self.market.manifest(request['snapshot_id'])['identity']['count']
            job_id = job_id or uuid.uuid4().hex
            if type(job_id) is not str or not re.fullmatch('[0-9a-f]{32}',job_id):invalid('Invalid prepared job identity.')
            try:self.repository.get(job_id)
            except KeyError:pass
            else:invalid('Prepared job identity already exists.')
            state = {"schema_version": "openpine.job-status.2", "job_id": job_id,
                "status": "queued", "phase": "queued", "completed": 0, "total": total,
                "created_at": time.time(), "started_at": None, "finished_at": None,
                "worker_pid": None, "error": None, "result_sha256": None,
                "result_id": None, "attempt_id": None, "attempt_count": 0,"failure_count":0,
                "checkpoint_id":None,"pause_requested":False,"pause_after_bars":pause_after}
            self.repository.insert(state, request, plan=plan,experiment_variant=experiment_variant)
            return state

    def _state(self, job_id):
        with self.lock:
            if self.fatal:
                invalid("Storage coordinator stopped: " + self.fatal, "STORAGE_UNAVAILABLE")
            if type(job_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", job_id):
                invalid("Unknown job identifier.", "JOB_NOT_FOUND")
            try:
                return self.repository.get(job_id)
            except KeyError:
                invalid("Unknown job identifier.", "JOB_NOT_FOUND")

    def get(self,job_id):
        with self.lock:return self._resources(self._state(job_id))

    def list(self):
        with self.lock:return [self._resources(state) for state in self.repository.list()]

    def _resources(self,state):
        state=dict(state)
        state['host_memory']=self.limiter.host.snapshot()
        state['disk']=self.limiter.disk.snapshot()
        if state['status']=='queued' and state['job_id'] in self.waiting:
            state.update(phase='waiting_resources',waiting=self.waiting[state['job_id']])
        return state

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

    def trades(self,job_id,start=0,limit=100):
        self.get(job_id)
        return self.queries.trades(job_id,start,limit)

    def ledger(self,job_id,start=0,limit=2000):
        self.get(job_id)
        return self.queries.ledger(job_id,start,limit)

    @contextmanager
    def reserve_worker(self, memory_mb, threads,*,request_id=None,work_class="live"):
        """Finite replay attempts share the historical worker admission budget."""
        token = uuid.uuid4().hex
        demand="external:"+(request_id or token)
        with self.lock:
            self._schedule_demands()
            if not self.scheduler.offer(demand,work_class):
                from .limits import CoordinatorBusy
                self._worker_busy(demand,"queue_capacity",work_class)
            if self._next_worker()!=demand:
                from .limits import CoordinatorBusy
                self._worker_busy(demand,"workers",work_class)
            allocated = sum(self.repository.plan(j).memory_mb for j in self.children) + sum(self.external_workers.values())
            if (self.closed or self.stopping.is_set() or self.fatal or not self.limiter.healthy()
                    or memory_mb > self.policy.memory_mb_per_worker or threads > self.policy.threads_per_worker
                    or len(self.children)+len(self.external_workers) >= self.max_workers
                    or allocated+memory_mb > self.policy.total_memory_mb):
                from .limits import CoordinatorBusy
                self._worker_busy(demand,'workers',work_class)
            decision=self.limiter.host.reserve('replay:'+token,memory_mb*1024**2)
            if decision:
                from .limits import CoordinatorBusy
                self._worker_busy(demand,'host_memory',work_class,host=decision)
            self.external_workers[token] = memory_mb
            self.scheduler.started(demand)
            self.external_waiting.pop(demand,None)
        disk_token=None
        try:
            from packages.contracts.disk import DiskBusy
            try:disk_token=self.limiter.disk.reserve(896*1024**2,kind='replay')
            except DiskBusy as error:
                from .limits import CoordinatorBusy
                busy=CoordinatorBusy('disk',work_class,self.limiter.active,self.limiter.reserved_mb)
                busy.admission.update(error.admission)
                with self.lock:
                    self.scheduler.offer(demand,work_class)
                    self.external_waiting[demand]=busy.admission
                raise busy from error
            self.limiter.disk.materialized(disk_token,128*1024**2)
            yield
        finally:
            if disk_token:self.limiter.disk.release(disk_token)
            with self.lock:
                self.external_workers.pop(token, None)
                self.limiter.host.release('replay:'+token)

    def defer_worker(self,request_id,admission):
        with self.lock:
            demand="external:"+request_id
            self.scheduler.offer(demand,"live")
            from .limits import CoordinatorBusy
            error=CoordinatorBusy(admission["reason"],"live",self.limiter.active,self.limiter.reserved_mb)
            error.admission.update(admission);self.external_waiting[demand]=error.admission
            raise error

    def note_worker_waiting(self,request_id,admission):
        with self.lock:
            demand="external:"+request_id
            self.scheduler.offer(demand,"live");self.external_waiting[demand]=admission

    def start_worker(self,*args,**kwargs):
        with self.lock:
            if self.closed or self.stopping.is_set():raise RuntimeError("Coordinator is closing.")
            supervisor=Supervisor.start(*args,**kwargs)
            self.processes[id(supervisor)]=supervisor
            return supervisor

    def stop_worker(self,supervisor):
        with self.lock:
            supervisor.close();self.processes.pop(id(supervisor),None)

    def _next_worker(self):
        protected=self.max_workers>1 and len(self.children)+len(self.external_workers)>=self.max_workers-1
        return self.scheduler.choose(live_reserved=protected)

    def _worker_busy(self,demand,reason,kind,host=None):
        from .limits import CoordinatorBusy
        error=CoordinatorBusy(reason,kind,self.limiter.active,self.limiter.reserved_mb,host=host)
        self.external_waiting[demand]=error.admission
        raise error

    def worker_waiting(self,identifier):
        with self.lock:
            self.scheduler.expire()
            for key in list(self.external_waiting):
                if key not in self.scheduler.pending:self.external_waiting.pop(key,None)
            for key,waiting in self.external_waiting.items():
                if identifier in key.split(":"):return dict(waiting)
        return None

    def _schedule_demands(self):
        queued=[s["job_id"] for s in self.repository.active() if s["status"]=="queued"]
        for key in list(self.scheduler.pending):
            if key.startswith("job:") and key[4:] not in queued:self.scheduler.discard(key)
        for job in queued:
            lane="research" if self.repository.experiment_owner(job) else "history"
            self.scheduler.offer("job:"+job,lane,persistent=True)

    def _stop_child(self, job_id):
        self.scheduler.discard("job:"+job_id)
        token=self.disk_leases.pop(job_id,None)
        self.waiting.pop(job_id,None)
        child = self.children.pop(job_id, None)
        if child:
            process, log, *_ = child
            supervisor=self.supervisors.pop(job_id,None)
            if supervisor:self.stop_worker(supervisor)
            else:
                if process.poll() is None:process.kill()
                process.wait(timeout=10)
            log.close()
            # These are private attempt files; checkpoint prefixes are immutable.
            path=child[3]
            for item in path.rglob('*'):
                if item.is_file() and item.suffix in ('.csv','.parquet','.tmp'):
                    if item.is_symlink() or not item.resolve().is_relative_to(path.resolve()):raise ValueError('Unsafe attempt scratch path.')
                    item.unlink(missing_ok=True)
        if token:self.limiter.disk.release(token)
        self.limiter.host.release('job:'+job_id)

    def _finish(self, state, status, *, error=None, result_id=None):
        child=self.children.get(state['job_id'])
        if child:
            try:
                saved=read_json(child[3]/'resources.json',max_bytes=256*1024)
                ledger=saved['runtime']['ledger']
                if all(type(ledger[k]) is int and ledger[k]>=0 for k in ('charged_bytes','peak_charged_bytes','limit_bytes')):
                    state['resources']={k:ledger[k] for k in ('charged_bytes','peak_charged_bytes','limit_bytes')}
                    state['resources'].update(checkpoint_bytes=saved.get('checkpoint_bytes',0),sampled_at=time.time())
                    state['resident_bytes']=saved['sampled_resident_bytes']
                    state['peak_resident_bytes']=max(state.get('peak_resident_bytes',0),state['resident_bytes'])
            except (OSError,ValueError,KeyError,TypeError):pass
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

    def _defer_disk(self,state):
        job_id=state["job_id"];self._stop_child(job_id)
        if not self._capture_checkpoint(state):return
        previous=state["attempt_id"];error={"code":"DISK_WAIT","message":"Waiting for free disk space."}
        state.update(status="queued",phase="queued",attempt_id=None,worker_pid=None,error=error)
        self.repository.retry(state,previous,error)
        self.waiting[job_id]={"reason":"disk","retryable":True,"retry_after_ms":1000,"retry_at":time.monotonic()+1}

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
            state=self._state(job_id)
            if state["status"]=="queued":self._pause_committed(state)
            elif state["status"]=="running":
                state["pause_requested"]=True
                self.repository.save(state)
                path=self.directory/"attempts"/job_id/state["attempt_id"]/"control.json"
                atomic_json(path,{"attempt_id":state["attempt_id"],"pause":True})
            return self.get(job_id)

    def resume(self,job_id):
        with self.lock:
            state=self._state(job_id)
            if state["status"]!="paused":invalid("Only a paused job can resume.")
            if self.stopping.is_set() or self.fatal:invalid("Coordinator is unavailable.","STORAGE_UNAVAILABLE")
            if len(self.repository.active())>=self.capacity:invalid("Job queue is full.","JOB_QUEUE_FULL")
            state.update(status="queued",phase="queued",attempt_id=None,worker_pid=None,
                pause_requested=False,pause_after_bars=None,error=None,finished_at=None)
            self.repository.resume(state)
            return state

    def cancel(self, job_id):
        with self.lock:
            state = self._state(job_id)
            if state["status"] not in TERMINAL_STATUSES:
                state.update(status="cancelled", phase="cancelled", finished_at=time.time(),
                    error={"code": "CANCELLED", "message": "Cancelled by caller."})
                self.repository.finish(state)
                self._stop_child(job_id)
            return self.get(job_id)

    def _start(self, state):
        job_id, attempt_id = state["job_id"], uuid.uuid4().hex
        plan = self.repository.plan(job_id)
        from packages.contracts.disk import DiskBusy
        try:self.disk_leases[job_id]=self.limiter.disk.reserve(896*1024**2,kind='worker')
        except DiskBusy as error:
            self.waiting[job_id]=error.admission;return False
        decision=self.limiter.host.reserve('job:'+job_id,plan.memory_mb*1024**2)
        if decision:
            self.limiter.disk.release(self.disk_leases.pop(job_id))
            self.waiting[job_id]=decision
            return False
        self.waiting.pop(job_id,None)
        if "job:"+job_id in self.scheduler.pending:self.scheduler.started("job:"+job_id)
        state.update(status="running", phase="starting", attempt_id=attempt_id,
            attempt_count=state["attempt_count"] + 1, started_at=time.time(), finished_at=None, error=None, completed=0)
        self.repository.claim(state)
        request = self.request(job_id)
        state["resumed_from_bar"]=0
        if state.get("checkpoint_id"):
            state["resumed_from_bar"]=self.services.checkpoints.manifest(state["checkpoint_id"],plan=plan,total=state["total"])["completed"]
        state["completed"]=state["resumed_from_bar"]
        manifest = self.market.manifest(plan.snapshot_id)
        path = self.directory / "attempts" / job_id / attempt_id
        path.mkdir(parents=True)
        atomic_json(path / "request.json", {**request, "attempt_id": attempt_id,
            "dataset_manifest_ref": self.objects.put(manifest), "run_plan": plan.payload(),
            'magnifier_manifest_ref':self.objects.put(self.market.manifest(plan.magnifier['snapshot_id'])) if plan.magnifier else None,
            "context_manifest_refs":[self.objects.put(self.market.manifest(c.snapshot_id)) for c in plan.contexts],
            "checkpoint_id":state.get("checkpoint_id"),"pause_after_bars":state.get("pause_after_bars"),
            "storage_quota_bytes": self.objects.quota_bytes})
        log = (path / "worker.log").open("wb")
        bootstrap = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('apps.worker',run_name='__main__')"
        try:
            supervisor=self.start_worker([sys.executable, "-X", "utf8", "-c", bootstrap, str(ROOT),
                "--directory", str(path), "--store", str(self.directory), "--parent-pid", str(os.getpid())],
                path,log,threads=plan.threads,timeout=plan.timeout_seconds,heartbeat_timeout=plan.heartbeat_timeout,
                memory_mb=plan.memory_mb,attempt_id=attempt_id)
        except OSError:
            log.close(); raise
        self.limiter.disk.materialized(self.disk_leases[job_id],128*1024**2)
        process=supervisor.process
        self.supervisors[job_id]=supervisor
        self.children[job_id] = (process, log, supervisor.deadline, path, supervisor.born)
        state["worker_pid"] = process.pid
        self.repository.save(state)
        return True

    def _tick(self):
        with self.lock:
            total_rss = 0
            for job_id, (process, _, deadline, path, born) in list(self.children.items()):
                state = self._state(job_id)
                plan = self.repository.plan(job_id)
                if not self._capture_checkpoint(state):continue
                supervisor=self.supervisors[job_id]
                try:progress=supervisor.poll()
                except WorkerFault as error:
                    if error.code=='WORKER_HEARTBEAT':self._failed_attempt(state,error.code,str(error))
                    else:self._finish(state,'timed_out' if error.code=='JOB_TIMEOUT' else 'failed',error={'code':error.code,'message':str(error)})
                    continue
                if process.poll() is not None:
                    try:
                        if process.returncode:
                            raise ValueError(f"Worker exited with code {process.returncode}.")
                        envelope = read_json(path / "completion.json")
                        if envelope=={'attempt_id':state['attempt_id'],'status':'waiting_disk'}:
                            self._defer_disk(state)
                            continue
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
                                or response["result"]["observation_count"] != state["total"]
                                or response["result"]["schema_version"]!=plan.output_schema):
                            raise ValueError("Result does not belong to the requested dataset.")
                        self._finish(state, "failed" if error else "succeeded", error=error, result_id=envelope["result_id"])
                    except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
                        self._failed_attempt(state, "WORKER_CRASH", str(error))
                else:
                    rss = supervisor.rss; total_rss += rss
                    self.limiter.host.materialized('job:'+job_id,rss,plan.memory_mb*1024**2)
                    state['resident_bytes']=rss
                    state["peak_resident_bytes"] = max(state.get("peak_resident_bytes", 0), rss)
                    if rss > plan.memory_mb * 1024**2 or total_rss + resident_bytes(os.getpid()) > (self.policy.total_memory_mb + self.limiter.policy.memory_mb) * 1024**2:
                        self._finish(state, "failed", error={"code": "WORKER_MEMORY", "message": "Worker exceeded its resident memory budget."})
                        continue
                    try:
                        if progress is None:continue
                        heartbeat = progress["heartbeat"]
                        if progress["tick"] == state.get("heartbeat_tick"):
                            continue
                        if (type(progress["completed"]) is int and 0 <= progress["completed"] <= state["total"]
                                and progress["phase"] in ("starting", "compiling", "executing", "publishing","checkpointing","restoring")):
                            state.update(completed=progress["completed"], phase=progress["phase"], heartbeat=heartbeat,
                                heartbeat_tick=progress["tick"])
                            resources=progress.get('resources')
                            if (type(resources) is dict and resources.keys()=={'charged_bytes','peak_charged_bytes','limit_bytes','checkpoint_bytes','sampled_at'}
                                    and all(type(resources[k]) is int and 0<=resources[k]<=2**63-1 for k in resources if k!='sampled_at')
                                    and type(resources['sampled_at']) in (int,float) and 0<resources['sampled_at']<=time.time()+60):
                                state['resources']=resources
                            self.repository.progress(state)
                    except FileNotFoundError:
                        if time.monotonic() - born > plan.heartbeat_timeout:
                            self._failed_attempt(state, "WORKER_HEARTBEAT", "Worker did not start its heartbeat.")
                    except (OSError, ValueError, KeyError, TypeError):
                        if time.monotonic() - born > plan.heartbeat_timeout:
                            self._failed_attempt(state, "WORKER_HEARTBEAT", "Worker heartbeat is invalid.")
            self._schedule_demands()
            active=self.repository.active()
            chosen=self._next_worker()
            active.sort(key=lambda state:("job:"+state["job_id"]!=chosen,state["created_at"],state["job_id"]))
            for state in active:
                if state["status"] == "queued":
                    if self.waiting.get(state["job_id"],{}).get("retry_at",0)>time.monotonic():continue
                    if (len(self.children)+len(self.external_workers)>=self.max_workers
                            or self._next_worker()!="job:"+state["job_id"]):
                        self.waiting[state["job_id"]]={"reason":"workers","retryable":True,"retry_after_ms":100}
                        continue
                    desired=self.repository.experiment_desired(state['job_id'])
                    if desired=='cancelled':self._finish(state,'cancelled');continue
                    if desired=='paused':self._pause_committed(state);continue
                    plan = self.repository.plan(state["job_id"])
                    if plan.memory_mb > self.policy.memory_mb_per_worker or plan.threads > self.policy.threads_per_worker:
                        self._finish(state, "failed", error={"code":"RESOURCE_POLICY_INCOMPATIBLE",
                            "message":"Saved run plan exceeds the current host worker limits."})
                        continue
                    allocated = sum(self.repository.plan(j).memory_mb for j in self.children)+sum(self.external_workers.values())
                    if allocated + plan.memory_mb > self.policy.total_memory_mb or not self.limiter.healthy():
                        self.waiting[state["job_id"]]={"reason":"host_memory","retryable":True,"retry_after_ms":100}
                        continue
                    try:
                        self._start(state)
                    except (OSError, ValueError, TypeError, KeyError) as error:
                        import errno
                        if isinstance(error,OSError) and error.errno==errno.ENOSPC and getattr(error,"retryable",True):self._defer_disk(state)
                        else:self._failed_attempt(state, "WORKER_START", str(error))

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
                    if self.repository.experiment_owner(state['job_id']):
                        self._stop_child(state['job_id'])
                        if self._capture_checkpoint(state):self._pause_committed(state)
                    else:self._finish(state, "cancelled", error={"code": "HOST_STOPPED", "message": "Application shut down."})
        finally:
            for job_id in list(self.children):
                self._stop_child(job_id)
            with self.lock:
                for supervisor in list(self.processes.values()):self.stop_worker(supervisor)
            self.services.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
