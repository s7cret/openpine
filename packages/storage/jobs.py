"""Transactional job repository. SQL and fencing stay in this adapter."""

import time

from packages.contracts.application import TERMINAL_STATUSES
from dataclasses import asdict
from packages.contracts.storage import RunPlan
from packages.contracts.wire import canonical, loads, digest


class JobRepository:
    def __init__(self, database):
        self.db = database

    def get(self, job_id):
        return self.db.get(job_id)

    def list(self, limit=100):
        return self.db.list(limit)

    def active(self):
        return self.db.active()

    def scanned(self, name):
        return bool(self.db.execute("SELECT 1 FROM imports WHERE path=?", (name,)))

    def record_import(self, path, job_id=None, error=None):
        self.db.execute("INSERT OR REPLACE INTO imports VALUES(?,?,?)", (path, job_id, error))

    def insert(self, state, request, *, imported_from=None, plan=None,experiment_variant=None):
        with self.db.transaction():
            self.db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?) ON CONFLICT(job_id) DO NOTHING",
                (state["job_id"], state["status"], state["created_at"], canonical(state), canonical(request),
                 state["attempt_id"], state["result_id"]))
            if imported_from:
                self.record_import(imported_from, state["job_id"])
            if plan is not None:
                self.attach_plan(state["job_id"], plan)
            if experiment_variant is not None:
                group,ordinal=experiment_variant
                rows=self.db.execute('SELECT v.candidate_id,v.job_id,e.desired FROM experiment_variants v JOIN experiments e USING(experiment_id) WHERE v.experiment_id=? AND v.ordinal=?',(group,ordinal))
                if rows!=[(state['job_id'],None,'running')]:raise ValueError('Experiment variant is stale or stopped.')
                self.db.execute('UPDATE experiment_variants SET job_id=? WHERE experiment_id=? AND ordinal=?',(state['job_id'],group,ordinal))

    def experiment_owner(self,job_id):
        return bool(self.db.execute('SELECT 1 FROM experiment_variants WHERE job_id=?',(job_id,)))

    def experiment_desired(self,job_id):
        rows=self.db.execute('SELECT e.desired FROM experiment_variants v JOIN experiments e USING(experiment_id) WHERE v.job_id=?',(job_id,))
        return rows[0][0] if rows else None

    def attach_plan(self, job_id, plan):
        value = plan.payload()
        self.db.execute("INSERT OR IGNORE INTO run_plans VALUES(?,?,?)", (job_id, digest(value), canonical(value)))
        if self.plan(job_id) != plan:
            raise ValueError("A persisted run plan cannot be changed.")

    def plan(self, job_id):
        rows = self.db.execute("SELECT plan_id,plan FROM run_plans WHERE job_id=?", (job_id,))
        if not rows:
            raise KeyError(job_id)
        value = loads(rows[0][1])
        if digest(value) != rows[0][0]:
            raise ValueError("Run plan identity mismatch.")
        return RunPlan.from_payload(value)

    def request(self, job_id):
        return loads(self.db.execute("SELECT request FROM jobs WHERE job_id=?", (job_id,))[0][0])

    def claim(self, state):
        with self.db.transaction():
            if self.get(state["job_id"])["status"] != "queued":
                raise ValueError("Only a queued job can be claimed.")
            self.db.execute("INSERT INTO attempts(attempt_id,job_id,number,status,started_at) VALUES(?,?,?,'running',?)",
                (state["attempt_id"], state["job_id"], state["attempt_count"], state["started_at"]))
            self.db.save(state)

    def save(self, state):
        self.db.save(state)

    def _fence(self, state):
        current = self.get(state["job_id"])
        if current["status"] in TERMINAL_STATUSES or current["attempt_id"] != state["attempt_id"]:
            raise ValueError("Stale or already completed attempt cannot change a job.")

    def finish(self, state):
        with self.db.transaction():
            self._fence(state)
            if state["result_id"]:
                self.db.execute("INSERT OR IGNORE INTO results VALUES(?,?,?)",
                    (state["result_id"], canonical({"object_id": state["result_id"]}), time.time()))
            self.db.save(state)
            if state["attempt_id"]:
                self.db.execute("UPDATE attempts SET status=?,finished_at=?,error=? WHERE attempt_id=? AND status='running'",
                    (state["status"], state["finished_at"], canonical(state["error"]), state["attempt_id"]))

    def retry(self, state, previous_attempt, error):
        with self.db.transaction():
            current = self.get(state["job_id"])
            if current["status"] != "running" or current["attempt_id"] != previous_attempt:
                raise ValueError("Stale attempt cannot requeue a job.")
            self.db.execute("UPDATE attempts SET status='failed',finished_at=?,error=? WHERE attempt_id=?",
                (time.time(), canonical(error), previous_attempt))
            self.db.save(state)

    def progress(self, state):
        with self.db.transaction():
            self._fence(state)
            self.db.save(state)
            self.db.execute("UPDATE attempts SET heartbeat=? WHERE attempt_id=?",
                (state["heartbeat"], state["attempt_id"]))

    def checkpoint(self,state,checkpoint_id,completed):
        with self.db.transaction():
            self._fence(state)
            if self.get(state["job_id"])["status"]!="running":raise ValueError("Only a running attempt can publish a checkpoint.")
            current=self.db.execute("SELECT completed FROM checkpoints WHERE job_id=?",(state["job_id"],))
            if current and completed<current[0][0]:raise ValueError("Checkpoint cursor cannot move backwards.")
            self.db.execute("INSERT INTO checkpoints VALUES(?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET "
                "checkpoint_id=excluded.checkpoint_id,completed=excluded.completed,attempt_id=excluded.attempt_id,published_at=excluded.published_at",
                (state["job_id"],checkpoint_id,completed,state["attempt_id"],time.time()))
            state["checkpoint_id"]=checkpoint_id
            self.db.save(state)

    def pause(self,state):
        with self.db.transaction():
            self._fence(state)
            self.db.save(state)
            if state["attempt_id"]:
                self.db.execute("UPDATE attempts SET status='paused',finished_at=? WHERE attempt_id=?",(time.time(),state["attempt_id"]))

    def resume(self,state):
        with self.db.transaction():
            if self.get(state["job_id"])["status"]!="paused":raise ValueError("Only a paused job can resume.")
            self.db.save(state)
