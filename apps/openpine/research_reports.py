"""Self-contained research metadata; large series remain bounded job resources."""
import hashlib
from pathlib import Path
from decimal import Decimal, localcontext

from packages.contracts.wire import digest, canonical
from packages.contracts.inputs import transport_values
from packages.storage.optimization import SearchRepository
from packages.storage.experiments import ExperimentRepository
from packages.storage.trades import event_rows
from packages.optimizer.reporting import strategy_metrics
from packages.optimizer import reporting
from packages.storage.research import ResearchMetricRepository

METRIC_IMPLEMENTATION = hashlib.sha256(Path(reporting.__file__).read_bytes()).hexdigest()


def envelope(body):
    value = {'schema_version': 'openpine.research-report.1', 'sha256': digest(body), 'report': body}
    if len(canonical(value)) > 16*1024*1024:
        raise ValueError('Report exceeds 16 MiB; export individual child searches instead.')
    return value


class ResearchReports:
    def __init__(self, manager):
        self.manager = manager

    def _variant(self, row):
        manager = self.manager
        plan = row['plan'].payload()
        state = manager.get(row['job_id']) if row['job_id'] else None
        result = manager.summary(row['job_id']) if state and state['result_id'] else None
        metrics = None
        if result:
            summary = result['result']
            cache = ResearchMetricRepository(manager.db)
            metrics = cache.get(state['result_id'], METRIC_IMPLEMENTATION)
            if metrics is None:
                try:
                    metrics = strategy_metrics(summary, event_rows(manager.objects, summary['simulation']['journal'])
                                               if summary.get('simulation') else (), cancelled=manager.stopping.is_set)
                except (ValueError, TypeError, ArithmeticError) as error:
                    metrics = {'schema_version': reporting.SCHEMA, 'status': 'invalid_metric',
                               'values': {}, 'undefined': {}, 'error': str(error)}
                cache.put(state['result_id'], METRIC_IMPLEMENTATION, metrics)
        return {'ordinal': row['ordinal'], 'job_id': row['job_id'], 'plan': plan,
                'plan_sha256': digest(plan), 'inputs': transport_values(row['plan'].settings['values']),
                'status': state['status'] if state else 'planned',
                'result_id': state['result_id'] if state else None,
                'error': state.get('error') if state else None, 'summary': result, 'descriptive_metrics': metrics}

    def create(self, kind, identifier):
        if kind == 'walkforward':return self._study(identifier)
        if kind not in ('search', 'experiment'):
            raise ValueError('Report kind must be search or experiment.')
        manager = self.manager
        with manager.limiter.slot('read'):
            experiments = ExperimentRepository(manager.db)
            if kind == 'search':
                saved = SearchRepository(manager.db).get(identifier)
                manifest = saved['manifest']
                groups = [saved['selection_id'], *([saved['holdout_id']] if saved['holdout_id'] else [])]
                evidence = {phase: SearchRepository(manager.db).assessments(identifier, phase)
                            for phase in ('selection', 'holdout')}
                decision = saved['decision']
                desired, error = saved['desired'], saved['error']
            else:
                manifest, _ = experiments.manifest(identifier)
                groups, evidence, decision = [identifier], {}, None
                control = experiments.group(identifier)
                desired, error = control['desired'], control['error']
            variants = []
            for group_index, group in enumerate(groups):
                for row in experiments.variants(group, limit=256):
                    variant = self._variant(row)
                    variant['phase'] = ('selection' if group_index == 0 else 'holdout') if kind == 'search' else 'experiment'
                    variant['candidate_ordinal'] = decision['winner_ordinal'] if variant['phase'] == 'holdout' else row['ordinal']
                    if variant['job_id'] is None and experiments.group(group)['desired'] == 'cancelled':variant['status'] = 'skipped'
                    variants.append({'experiment_id': group, **variant})
            source = manager.objects.get(manifest['input_ref'])
            program = {'input': source}
            for key in ('source_ref', 'artifact_ref'):
                if key in source: program[key] = manager.objects.get(source[key])
            snapshots = sorted({row['plan']['snapshot_id'] for row in variants} |
                               {binding['snapshot_id'] for row in variants for binding in row['plan'].get('contexts', [])})
            if kind == 'search':snapshots = sorted(set(snapshots) | {manifest['parent_snapshot_id']} | {b['snapshot_id'] for b in manifest['parent_contexts']})
            body = {'kind': kind, 'id': identifier, 'manifest': manifest,
                    'desired': desired, 'error': error,
                    'manifest_sha256': digest(manifest), 'program': program,
                    'datasets': {sha: manager.market.manifest(sha) for sha in snapshots},
                    'variants': variants, 'decision': decision, 'assessments': evidence,
                    'complete': all(v['status'] in ('succeeded', 'failed', 'cancelled', 'timed_out', 'skipped') for v in variants)
                                and (desired == 'cancelled' or kind != 'search' or decision is not None and
                                     (decision['winner_ordinal'] is None or bool(evidence['holdout']))),
                    'metric_implementation': METRIC_IMPLEMENTATION,
                    'series_policy': 'Large series and journals are referenced by immutable result manifests; this JSON contains metadata, not their file contents.'}
            return envelope(body)

    def _study(self, identifier):
        from packages.storage.walkforward import WalkForwardRepository
        from packages.contracts.optimization import decimal_value, decimal_text
        manager = self.manager
        with manager.limiter.slot('read'):
            studies = WalkForwardRepository(manager.db)
            saved = studies.get(identifier)
            rows = studies.folds(identifier)
            folds = []
            winners = []
            scores = []
            for row in rows:
                search = SearchRepository(manager.db).get(row['search_id'])
                decision = search['decision']
                ordinal = decision['winner_ordinal'] if decision else None
                evidence = SearchRepository(manager.db).assessments(row['search_id'], 'holdout')
                selected = None
                holdout = None
                if ordinal is not None:
                    variant = ExperimentRepository(manager.db).variants(search['selection_id'], start=ordinal, limit=1)[0]
                    selected = self._variant(variant)
                    selected.update(phase='selection', fold_ordinal=row['ordinal'], candidate_ordinal=ordinal)
                    if search['holdout_id']:
                        variant = ExperimentRepository(manager.db).variants(search['holdout_id'], limit=1)[0]
                        holdout = self._variant(variant)
                        holdout.update(phase='holdout', fold_ordinal=row['ordinal'], candidate_ordinal=ordinal)
                if evidence and evidence[0]['assessment']['status'] == 'eligible':scores.append(decimal_value(evidence[0]['assessment']['score']))
                winners.append(selected['plan']['settings']['sha256'] if selected else None)
                folds.append({**row, 'windows': search['manifest']['windows'], 'selection': selected, 'holdout': holdout})
            scores.sort()
            with localcontext() as context:
                context.prec = 700
                median = (scores[(len(scores)-1)//2]+scores[len(scores)//2])/2 if scores else None
            source = manager.objects.get(saved['manifest']['input_ref'])
            program = {'input': source}
            for key in ('source_ref', 'artifact_ref'):
                if key in source:program[key] = manager.objects.get(source[key])
            variants = [v for fold in folds for v in (fold['selection'], fold['holdout']) if v]
            snapshots = sorted({saved['manifest']['parent_snapshot_id']} | {v['plan']['snapshot_id'] for v in variants} |
                               {b['snapshot_id'] for v in variants for b in v['plan'].get('contexts', [])})
            return envelope({'kind': 'walkforward', 'id': identifier, 'manifest': saved['manifest'],
                'manifest_sha256': saved['manifest_sha256'], 'desired': saved['desired'], 'error': saved['error'],
                'program': program, 'datasets': {sha: manager.market.manifest(sha) for sha in snapshots},
                'folds': folds, 'variants': variants, 'complete': all(f['receipt'] is not None for f in folds),
                'stability': {'eligible_holdouts': len(scores), 'score_min': decimal_text(scores[0]) if scores else None,
                    'score_median': decimal_text(median) if scores else None, 'score_max': decimal_text(scores[-1]) if scores else None,
                    'winner_changes': sum(a is not None and b is not None and a != b for a,b in zip(winners, winners[1:]))},
                'metric_implementation': METRIC_IMPLEMENTATION,
                'series_policy': 'Independent windows, each with fresh capital. No concatenated equity or compounded return is implied.'})
