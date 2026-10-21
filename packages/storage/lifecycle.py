"""Explicit retention and reachability. Maintenance requires exclusive ownership."""

import re
import shutil
import time
from pathlib import Path

from packages.contracts.application import TERMINAL_STATUSES
from packages.contracts.storage import RunPlan
from packages.contracts.wire import canonical, digest, loads
from .objects import ObjectStore, identifier, sync_directory


def quota(database):
    rows = database.execute("SELECT value FROM settings WHERE name='object_quota_bytes'")
    return loads(rows[0][0]) if rows else 0


def reachable(database, objects):
    """Fail closed on corrupt references; never infer references from arbitrary IR."""
    marked = set()

    def mark(sha):
        identifier(sha)
        if sha not in marked:
            objects.verify(sha)
            marked.add(sha)

    def input_object(sha):
        mark(sha)
        value = objects.get(sha)
        for key in ("artifact_ref", "source_ref"):
            if key in value:
                mark(value[key])

    for (raw,) in database.execute("SELECT manifest FROM datasets"):
        sha = loads(raw)["object_id"]
        mark(sha)
        for part in objects.get(sha)["parts"]:
            mark(part["object_id"])
    for (sha,) in database.execute("SELECT object_id FROM pins"):
        input_object(sha)
    for job_id, raw, result_id in database.execute("SELECT job_id,request,result_id FROM jobs"):
        request = loads(raw)
        input_object(request["input_ref"])
        if not database.execute("SELECT 1 FROM datasets WHERE snapshot_id=?", (request["snapshot_id"],)):
            raise ValueError("Job references a missing dataset.")
        plans = database.execute("SELECT plan_id,plan FROM run_plans WHERE job_id=?", (job_id,))
        if plans:
            plan = loads(plans[0][1]); RunPlan(**plan)
            if digest(plan) != plans[0][0] or any(plan[k] != request[k] for k in ("input_ref","snapshot_id","timeout_seconds")):
                raise ValueError("Persisted run plan does not match its job.")
        if result_id:
            mark(result_id)
            result = objects.get(result_id, max_bytes=32*1024*1024)
            if "artifact_ref" in result:
                mark(result["artifact_ref"])
                for part in result["result"]["series"]:
                    mark(part["object_id"])
    return marked


class Lifecycle:
    def __init__(self, database, root, objects):
        self.db, self.root, self.objects = database, Path(root).resolve(), objects

    def pin(self, name, object_id):
        self.objects.verify(object_id)
        self.db.execute("INSERT INTO pins VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET created_at=excluded.created_at",
            (name,object_id,time.time()))
        if self.db.execute("SELECT object_id FROM pins WHERE name=?", (name,))[0][0] != object_id:
            raise ValueError("Pin name already identifies another object.")

    def set_quota(self, value):
        if type(value) is not int or value < 0:
            raise ValueError("Object quota must be a nonnegative byte count; zero is unlimited.")
        self.db.execute("INSERT INTO settings VALUES('object_quota_bytes',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
            (canonical(value),))
        self.objects.quota_bytes = value

    def inspect(self, *, grace_seconds=3600):
        if type(grace_seconds) not in (int,float) or not 0 <= grace_seconds <= 315360000:
            raise ValueError("Invalid orphan grace period.")
        if self.objects.root.is_symlink() or not self.objects.root.resolve().is_relative_to(self.root):
            raise ValueError("Object directory escapes the owned state directory.")
        attempts = self.root / "attempts"
        if attempts.is_symlink() or not attempts.resolve().is_relative_to(self.root):
            raise ValueError("Attempt directory escapes the owned state directory.")
        marked = reachable(self.db,self.objects)
        files, candidates = [], []
        cutoff = time.time()-grace_seconds
        for path in sorted(self.objects.root.glob("[0-9a-f][0-9a-f]/*")):
            if path.is_symlink() or not path.resolve().is_relative_to(self.objects.root.resolve()):
                raise ValueError("Unsafe object path in maintenance.")
            identifier(path.name)
            stat = path.stat()
            files.append((path.name,stat.st_size))
            if path.name not in marked and stat.st_mtime <= cutoff:
                candidates.append((path.name,stat.st_size))
        return {"schema_version":"openpine.storage-usage.1", "object_count":len(files),
            "object_bytes":sum(n for _,n in files), "reachable_count":len(marked),
            "orphan_count":sum(sha not in marked for sha,_ in files),
            "reclaimable_bytes":sum(n for _,n in candidates), "candidates":[sha for sha,_ in candidates],
            "quota_bytes":quota(self.db), "grace_seconds":grace_seconds}

    def collect(self, *, apply=False, grace_seconds=3600):
        report = self.inspect(grace_seconds=grace_seconds)
        if apply:
            if self.db.active():
                raise ValueError("Recover/cancel pending jobs before applying maintenance.")
            # CLI owns .manager.lock; no coordinator/worker publication may race GC.
            for sha in report["candidates"]:
                path = self.objects.path(sha)
                if path.is_symlink() or not path.resolve().is_relative_to(self.objects.root.resolve()):
                    raise ValueError("Unsafe collection target.")
                path.unlink()
                sync_directory(path.parent)
            # Attempts are disposable only after their job has been explicitly pruned.
            attempts = self.root / "attempts"
            if attempts.is_symlink() or not attempts.resolve().is_relative_to(self.root):
                raise ValueError("Attempt directory escapes the owned state directory.")
            for path in attempts.iterdir() if attempts.exists() else ():
                if re.fullmatch(r"[0-9a-f]{32}",path.name) and not self.db.execute("SELECT 1 FROM jobs WHERE job_id=?",(path.name,)):
                    if path.is_symlink() or not path.resolve().is_relative_to(attempts.resolve()):
                        raise ValueError("Unsafe attempt cleanup target.")
                    shutil.rmtree(path)
        return {**report,"applied":apply}

    def prune(self, before, *, expire_inputs=False, expire_coverage=False, apply=False):
        if type(before) not in (int,float) or not 0 <= before <= time.time():
            raise ValueError("Retention cutoff must be a past UTC Unix timestamp.")
        jobs = [job for job,raw in self.db.execute("SELECT job_id,state FROM jobs")
                if (state:=loads(raw))["status"] in TERMINAL_STATUSES and (state.get("finished_at") or state["created_at"]) < before]
        pins = [name for (name,) in self.db.execute("SELECT name FROM pins WHERE created_at<?",(before,))] if expire_inputs else []
        coverage = [row for (row,) in self.db.execute("SELECT id FROM coverage WHERE created_at<?",(before,))] if expire_coverage else []
        retained = {loads(raw)["snapshot_id"] for job,raw in self.db.execute("SELECT job_id,request FROM jobs") if job not in set(jobs)}
        retained |= {sha for row,sha in self.db.execute("SELECT id,snapshot_id FROM coverage") if row not in set(coverage) and sha}
        datasets = [sha for sha,created in self.db.execute("SELECT snapshot_id,created_at FROM datasets") if sha not in retained and created < before]
        if apply:
            if self.db.active():
                raise ValueError("Recover/cancel pending jobs before applying maintenance.")
            reachable(self.db,self.objects)  # Corruption must not turn into accidental deletion.
            with self.db.transaction():
                for job in jobs:
                    self.db.execute("DELETE FROM attempts WHERE job_id=?",(job,))
                    self.db.execute("DELETE FROM jobs WHERE job_id=?",(job,))
                for name in pins:
                    self.db.execute("DELETE FROM pins WHERE name=?",(name,))
                for row in coverage:
                    self.db.execute("DELETE FROM coverage WHERE id=?",(row,))
                for sha in datasets:
                    self.db.execute("DELETE FROM datasets WHERE snapshot_id=?",(sha,))
                self.db.execute("DELETE FROM results WHERE result_id NOT IN (SELECT result_id FROM jobs WHERE result_id IS NOT NULL)")
        return {"applied":apply,"jobs":jobs,"pins":pins,"coverage":coverage,"datasets":datasets}
