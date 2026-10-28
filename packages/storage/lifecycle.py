"""Explicit retention and reachability. Maintenance requires exclusive ownership."""

import re
import shutil
import time
from pathlib import Path

from packages.contracts.application import TERMINAL_STATUSES
from packages.contracts.storage import RunPlan
from packages.contracts.wire import canonical, digest, loads
from .objects import ObjectStore, identifier, sync_directory
from .checkpoints import validate_manifest


def quota(database):
    rows = database.execute("SELECT value FROM settings WHERE name='object_quota_bytes'")
    return loads(rows[0][0]) if rows else 0


def reachable(database, objects, *, omit_replays=(),omit_feeds=()):
    """Fail closed on corrupt references; never infer references from arbitrary IR."""
    from .operations import OperationRepository
    OperationRepository(database).audit()
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
    for (sha,) in database.execute('SELECT input_ref FROM input_presets'):input_object(sha)
    from .launch import LaunchRepository
    launches=LaunchRepository(database)
    for (key,) in database.execute('SELECT launch_id FROM launches'):
        row=launches.get(key);input_object(row['config']['input_ref'])
        prepared_ref=row['state']['prepared_ref']
        if prepared_ref:
            mark(prepared_ref);prepared=objects.get(prepared_ref)
            if prepared.get('schema_version')!='openpine.prepared-launch.1':raise ValueError('Invalid prepared launch object.')
            plan=RunPlan.from_payload(prepared['plan']);request=prepared['request']
            if plan.input_ref!=request['input_ref'] or plan.snapshot_id!=request['snapshot_id']:
                raise ValueError('Prepared launch differs from its frozen plan.')
            input_object(request['input_ref'])
            for sha in [request['snapshot_id'],*(b['snapshot_id'] for b in request.get('contexts',[])),*([request['magnifier']['snapshot_id']] if request.get('magnifier') else [])]:
                if not database.execute('SELECT 1 FROM datasets WHERE snapshot_id=?',(sha,)):raise ValueError('Prepared launch snapshot is missing.')
    from .walkforward import WalkForwardRepository
    studies = WalkForwardRepository(database)
    for (study_id,) in database.execute('SELECT walkforward_id FROM walkforwards'):
        studies.folds(study_id)
    for experiment_id,raw,sha in database.execute('SELECT experiment_id,manifest,manifest_sha256 FROM experiments'):
        manifest=loads(raw)
        if digest(manifest)!=sha:raise ValueError('Experiment manifest checksum differs.')
        rows=database.execute('SELECT ordinal,request,plan_sha256 FROM experiment_variants WHERE experiment_id=? ORDER BY ordinal',(experiment_id,))
        if manifest['variants']!=[{'request_sha256':digest(loads(request)),'plan_sha256':plan_sha} for _,request,plan_sha in rows] or [row[0] for row in rows]!=list(range(len(rows))):
            raise ValueError('Experiment composition differs from its immutable manifest.')
    for raw,plan_raw,plan_sha in database.execute('SELECT request,plan,plan_sha256 FROM experiment_variants'):
        request=loads(raw);plan=loads(plan_raw);RunPlan.from_payload(plan)
        if digest(plan)!=plan_sha or any(plan[k]!=request[k] for k in ('input_ref','snapshot_id','timeout_seconds')) or plan.get('settings')!=request.get('settings'):
            raise ValueError('Experiment variant differs from its frozen plan.')
        if plan.get('contexts',[])!=request.get('contexts',[]) or plan.get('primary_context')!=request.get('primary_context'):
            raise ValueError('Experiment context graph differs from its frozen plan.')
        if plan.get('magnifier')!=request.get('magnifier'):raise ValueError('Magnifier differs from its frozen plan.')
        if plan.get('evaluation_window')!=request.get('evaluation_window'):raise ValueError('Experiment window differs from its plan.')
        input_object(request['input_ref'])
        for sha in [request['snapshot_id'],*(b['snapshot_id'] for b in request.get('contexts',[])),*([request['magnifier']['snapshot_id']] if request.get('magnifier') else [])]:
            if not database.execute('SELECT 1 FROM datasets WHERE snapshot_id=?',(sha,)):raise ValueError('Experiment references a missing dataset.')
    from .optimization import SearchRepository
    from .experiments import ExperimentRepository
    searches=SearchRepository(database);experiments=ExperimentRepository(database)
    for (search_id,) in database.execute('SELECT search_id FROM searches'):
        saved=searches.get(search_id);manifest=saved['manifest'];input_object(manifest['input_ref'])
        if experiments.manifest(saved['selection_id'])[0]!=manifest['selection_composition']:
            raise ValueError('Search selection experiment differs from its manifest.')
        roots=[manifest['parent_snapshot_id'],*(b['snapshot_id'] for b in manifest['parent_contexts'])]
        for ordinal in range(len(manifest['search']['candidates'])):
            request,plan=searches.holdout(search_id,ordinal)
            if (any(getattr(plan,k)!=request[k] for k in ('input_ref','snapshot_id','timeout_seconds'))
                    or plan.settings!=request.get('settings') or plan.evaluation_window!=request.get('evaluation_window') or plan.magnifier!=request.get('magnifier')):
                raise ValueError('Saved search holdout differs from its plan.')
            input_object(request['input_ref']);roots.extend([request['snapshot_id'],*(b['snapshot_id'] for b in request.get('contexts',[])),*([request['magnifier']['snapshot_id']] if request.get('magnifier') else [])])
        if any(not database.execute('SELECT 1 FROM datasets WHERE snapshot_id=?',(sha,)) for sha in roots):
            raise ValueError('Search references a missing snapshot.')
        evidence=searches.assessments(search_id)
        if saved['decision'] and digest(evidence)!=saved['decision']['selection_evidence_sha256']:
            raise ValueError('Search decision differs from its selection evidence.')
        for row in [*evidence,*searches.assessments(search_id,'holdout')]:
            if database.get(row['job_id'])['result_id']!=row['result_id']:raise ValueError('Search assessment differs from its job result.')
    for job_id, raw, result_id in database.execute("SELECT job_id,request,result_id FROM jobs"):
        request = loads(raw)
        input_object(request["input_ref"])
        if not database.execute("SELECT 1 FROM datasets WHERE snapshot_id=?", (request["snapshot_id"],)):
            raise ValueError("Job references a missing dataset.")
        for binding in [*request.get("contexts",[]),*([{'snapshot_id':request['magnifier']['snapshot_id']}] if request.get('magnifier') else [])]:
            if not database.execute("SELECT 1 FROM datasets WHERE snapshot_id=?",(binding["snapshot_id"],)):
                raise ValueError("Job references a missing context dataset.")
        plans = database.execute("SELECT plan_id,plan FROM run_plans WHERE job_id=?", (job_id,))
        if plans:
            plan = loads(plans[0][1]); RunPlan.from_payload(plan)
            if digest(plan) != plans[0][0] or any(plan[k] != request[k] for k in ("input_ref","snapshot_id","timeout_seconds")):
                raise ValueError("Persisted run plan does not match its job.")
            if plan.get("primary_context")!=request.get("primary_context") or plan.get("contexts",[])!=request.get("contexts",[]):
                raise ValueError("Persisted context plan differs from its job.")
            if plan.get('settings')!=request.get('settings'):raise ValueError('Persisted settings differ from their job.')
            if plan.get('magnifier')!=request.get('magnifier'):raise ValueError('Persisted magnifier differs from its job.')
            if plan.get('evaluation_window')!=request.get('evaluation_window'):raise ValueError('Persisted evaluation window differs from its job.')
        if result_id:
            mark(result_id)
            result = objects.get(result_id, max_bytes=32*1024*1024)
            if "artifact_ref" in result:
                mark(result["artifact_ref"])
                for part in result["result"]["series"]:
                    mark(part["object_id"])
                for part in result['result'].get('simulation',{}).get('journal',{}).get('parts',[]):mark(part['object_id'])
    for (checkpoint_id,) in database.execute("SELECT checkpoint_id FROM checkpoints"):
        mark(checkpoint_id)
        checkpoint=validate_manifest(objects.get(checkpoint_id))
        for key in ("artifact_ref","runtime_ref","output_ref"):mark(checkpoint[key])
        from . import checkpoint_tree
        for key in ('runtime_ref','output_ref'):checkpoint_tree.references(objects,checkpoint[key],mark)
        prefix=checkpoint_tree.get(objects,checkpoint['output_ref'])
        for part in prefix["files"]:mark(part["object_id"])
        if prefix["pending"] is not None:mark(prefix["pending"]["object_id"])
        if 'simulation' in prefix:
            trade=prefix['simulation']
            for part in trade['files']:mark(part['object_id'])
            if trade['pending'] is not None:mark(trade['pending']['object_id'])
    from .replay import references
    references(database, objects, mark,omit_replays=omit_replays)
    for (raw,) in database.execute('SELECT request FROM data_tasks'):
        request=loads(raw)
        if request.get('schema_version')=='openpine.file-acquisition.1':mark(request['input_ref'])
    for feed,sha in database.execute('SELECT feed_id,object_id FROM live_parts'):
        if feed not in omit_feeds:mark(sha)
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
            # A killed CLI cannot execute TemporaryDirectory.__exit__. Replay
            # state references immutable objects only; these private work files
            # are disposable once their child process releases the attempt lease.
            from .lease import Lease
            from packages.contracts import OpenPineError
            from packages.contracts.wire import read_json
            for path in self.root.glob('replay-attempt-*'):
                if not re.fullmatch(r'replay-attempt-[a-z0-9_]{8}',path.name): continue
                if path.is_symlink() or not path.resolve().is_relative_to(self.root): raise ValueError('Unsafe replay cleanup target.')
                marker=path/'owner.json'
                if not marker.exists():
                    if not any(path.iterdir()):path.rmdir()
                    continue
                owner=read_json(marker,max_bytes=4096)
                if (type(owner) is not dict or owner.keys()!={'schema_version','replay_id'}
                        or owner['schema_version']!='openpine.replay-attempt.1'
                        or not re.fullmatch(r'[0-9a-f]{32}',owner['replay_id'])): raise ValueError('Invalid replay attempt owner.')
                try: lease=Lease(path/'.active.lock')
                except OpenPineError: continue
                lease.close()
                shutil.rmtree(path)
        return {**report,"applied":apply}

    def prune(self, before, *, expire_inputs=False, expire_coverage=False, apply=False):
        if type(before) not in (int,float) or not 0 <= before <= time.time():
            raise ValueError("Retention cutoff must be a past UTC Unix timestamp.")
        jobs = [job for job,raw in self.db.execute("SELECT job_id,state FROM jobs")
                if (state:=loads(raw))["status"] in TERMINAL_STATUSES and (state.get("finished_at") or state["created_at"]) < before]
        experiment_jobs={job for (job,) in self.db.execute('SELECT job_id FROM experiment_variants WHERE job_id IS NOT NULL')}
        jobs=[job for job in jobs if job not in experiment_jobs]
        launch_states=[loads(raw) for (raw,) in self.db.execute('SELECT state FROM launches')]
        launch_jobs={state['job_id'] for state in launch_states if state.get('job_id')}
        jobs=[job for job in jobs if job not in launch_jobs]
        pins = [name for (name,) in self.db.execute("SELECT name FROM pins WHERE created_at<?",(before,))] if expire_inputs else []
        coverage = [row for (row,) in self.db.execute("SELECT id FROM coverage WHERE created_at<?",(before,))] if expire_coverage else []
        retained = {loads(raw)["snapshot_id"] for job,raw in self.db.execute("SELECT job_id,request FROM jobs") if job not in set(jobs)}
        retained |= {binding["snapshot_id"] for job,raw in self.db.execute("SELECT job_id,request FROM jobs")
            if job not in set(jobs) for binding in loads(raw).get("contexts",[])}
        retained |= {request['magnifier']['snapshot_id'] for job,raw in self.db.execute('SELECT job_id,request FROM jobs')
            if job not in set(jobs) and (request:=loads(raw)).get('magnifier')}
        for table in ('experiment_variants','search_holdouts'):
            for (raw,) in self.db.execute('SELECT request FROM '+table):
                request=loads(raw)
                if request.get('magnifier'):retained.add(request['magnifier']['snapshot_id'])
        for (raw,) in self.db.execute('SELECT config FROM launches'):
            config=loads(raw)
            if config.get('magnifier'):retained.add(config['magnifier']['snapshot_id'])
        retained |= {sha for row,sha in self.db.execute("SELECT id,snapshot_id FROM coverage") if row not in set(coverage) and sha}
        retained |= {sha for (sha,) in self.db.execute('SELECT snapshot_id FROM data_pages WHERE snapshot_id IS NOT NULL')}
        retained |= {sha for (sha,) in self.db.execute('SELECT snapshot_id FROM live_closed WHERE snapshot_id IS NOT NULL')}
        retained |= {state['snapshot_id'] for (raw,) in self.db.execute('SELECT state FROM data_tasks')
            if (state:=loads(raw)).get('snapshot_id')}
        retained |= {state['warmup_snapshot'] for (raw,) in self.db.execute('SELECT state FROM live_sessions')
            if (state:=loads(raw)).get('warmup_snapshot')}
        retained |= {source['warmup_snapshot'] for (raw,) in self.db.execute('SELECT state FROM live_sessions')
            for source in loads(raw).get('sources',{}).values() if source.get('warmup_snapshot')}
        retained |= {request['snapshot_id'] for (raw,) in self.db.execute('SELECT request FROM experiment_variants') if (request:=loads(raw))}
        retained |= {binding['snapshot_id'] for (raw,) in self.db.execute('SELECT request FROM experiment_variants') for binding in loads(raw).get('contexts',[])}
        retained |= {request['snapshot_id'] for (raw,) in self.db.execute('SELECT request FROM search_holdouts') if (request:=loads(raw))}
        retained |= {binding['snapshot_id'] for (raw,) in self.db.execute('SELECT request FROM search_holdouts') for binding in loads(raw).get('contexts',[])}
        retained |= {manifest['parent_snapshot_id'] for (raw,) in self.db.execute('SELECT manifest FROM searches') if (manifest:=loads(raw))}
        retained |= {binding['snapshot_id'] for (raw,) in self.db.execute('SELECT manifest FROM searches') for binding in loads(raw)['parent_contexts']}
        for state in launch_states:
            retained.update(s['snapshot_id'] for s in state['sources'] if s.get('snapshot_id'))
            if state['prepared_ref']:
                prepared=self.objects.get(state['prepared_ref'])
                retained.update(r['snapshot_id'] for r in prepared['datasets'])
        for (raw,) in self.db.execute('SELECT config FROM launches'):
            config=loads(raw);retained.update(s['snapshot_id'] for s in [config['primary'],*config['contexts']] if s['kind']=='snapshot')
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
