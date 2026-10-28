"""Explicit research deletion with a content-addressed preview and later GC."""
from packages.contracts.application import TERMINAL_STATUSES
from packages.contracts.wire import digest
from packages.storage.lifecycle import reachable
from packages.storage.optimization import SearchRepository
from packages.storage.experiments import ExperimentRepository
from packages.storage.operations import OperationRepository


class _Preview(Exception):
    def __init__(self, plan):self.plan = plan


class ResearchMaintenance:
    def __init__(self, manager):self.manager = manager

    def _delete(self, kind, identifier):
        db = self.manager.db
        experiments = ExperimentRepository(db)
        if kind == 'walkforward':
            from packages.storage.walkforward import WalkForwardRepository
            studies = WalkForwardRepository(db)
            saved = studies.get(identifier)
            folds = studies.folds(identifier)
            if saved['desired'] != 'cancelled' and any(f['receipt'] is None for f in folds):
                raise ValueError('Only completed or cancelled studies can be deleted.')
            OperationRepository(db).mark_deleted(kind,identifier)
            db.execute('DELETE FROM walkforward_folds WHERE walkforward_id=?', (identifier,))
            db.execute('DELETE FROM walkforwards WHERE walkforward_id=?', (identifier,))
            groups, jobs = [], []
            for fold in folds:
                child_groups, child_jobs = self._delete('search', fold['search_id'])
                groups.extend(child_groups);jobs.extend(child_jobs)
            return groups, jobs
        if kind == 'search':
            saved = SearchRepository(db).get(identifier)
            if db.execute('SELECT 1 FROM walkforward_folds WHERE search_id=?', (identifier,)):
                raise ValueError('Delete the owning walk-forward study instead.')
            groups = [saved['selection_id'], *([saved['holdout_id']] if saved['holdout_id'] else [])]
            if saved['desired'] != 'cancelled' and (saved['decision'] is None or
                    saved['decision']['winner_ordinal'] is not None and not SearchRepository(db).assessments(identifier, 'holdout')):
                raise ValueError('Only completed or cancelled searches can be deleted.')
        elif kind == 'experiment':
            experiments.group(identifier)
            if db.execute('SELECT 1 FROM searches WHERE selection_id=? OR holdout_id=?', (identifier, identifier)):
                raise ValueError('Delete the owning search instead.')
            groups = [identifier]
        else:raise ValueError('Deletion kind must be search or experiment.')
        jobs = []
        for group in groups:
            rows = experiments.variants(group, limit=256)
            cancelled = experiments.group(group)['desired'] == 'cancelled'
            for row in rows:
                if not row['job_id']:
                    if not cancelled:raise ValueError('The experiment has unsubmitted variants.')
                    continue
                state = self.manager.get(row['job_id'])
                if state['status'] not in TERMINAL_STATUSES:raise ValueError('The experiment has unfinished jobs.')
                jobs.append({'job_id': state['job_id'], 'result_id': state['result_id'], 'status': state['status']})
        if kind == 'search':
            OperationRepository(db).mark_deleted(kind,identifier)
            for table in ('search_assessments', 'search_holdouts', 'searches'):
                db.execute(f'DELETE FROM {table} WHERE search_id=?', (identifier,))
        for group in groups:
            OperationRepository(db).mark_deleted('experiment',group)
            db.execute('DELETE FROM experiment_variants WHERE experiment_id=?', (group,))
            db.execute('DELETE FROM experiments WHERE experiment_id=?', (group,))
        for row in jobs:
            db.execute('DELETE FROM attempts WHERE job_id=?', (row['job_id'],))
            db.execute('UPDATE job_lineage SET deleted=1 WHERE job_id=?',(row['job_id'],))
            db.execute('DELETE FROM jobs WHERE job_id=?', (row['job_id'],))
        db.execute('DELETE FROM results WHERE result_id NOT IN (SELECT result_id FROM jobs WHERE result_id IS NOT NULL)')
        return groups, jobs

    def _plan(self, kind, identifier):
        manager = self.manager
        before = reachable(manager.db, manager.objects)
        groups, jobs = self._delete(kind, identifier)
        after = reachable(manager.db, manager.objects)
        objects = [{'object_id': sha, 'bytes': manager.objects.verify(sha).stat().st_size}
                   for sha in sorted(before - after)]
        return {'schema_version': 'openpine.research-retention.1', 'kind': kind, 'id': identifier,
                'experiments': groups, 'jobs': jobs, 'objects': objects,
                'reclaimable_object_bytes': sum(row['bytes'] for row in objects),
                'remaining_roots_sha256': digest(sorted(after)),
                'preserved': 'Datasets, coverage, presets, pins and other research remain. Files become GC candidates after grace.'}

    def preview(self, kind, identifier):
        manager = self.manager
        with manager.lock, manager.limiter.slot('read'):
            try:
                with manager.db.transaction():raise _Preview(self._plan(kind, identifier))
            except _Preview as result:plan = result.plan
        return {**plan, 'preview_id': digest(plan)}

    def apply(self, kind, identifier, preview_id):
        manager = self.manager
        with manager.lock, manager.limiter.slot('prepare'), manager.db.transaction():
            if manager.db.active() or manager.children or manager.external_workers:
                raise ValueError('Stop active execution before research deletion.')
            if manager.db.execute("SELECT 1 FROM data_tasks WHERE status IN ('queued','running') LIMIT 1") or manager.db.execute("SELECT 1 FROM live_sessions WHERE desired='running' LIMIT 1"):
                raise ValueError('Stop active data acquisition and live sessions before deletion.')
            plan = self._plan(kind, identifier)
            if digest(plan) != preview_id:raise ValueError('Deletion preview is stale; obtain a new preview.')
        return {'status': 'deleted', 'jobs_removed': len(plan['jobs']), 'experiments_removed': len(plan['experiments']),
                'gc_candidate_bytes': plan['reclaimable_object_bytes'], 'files_removed': 0}
