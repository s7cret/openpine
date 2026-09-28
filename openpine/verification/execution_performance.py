"""Compare repeated equivalent *executed* test workloads, with explicit costs.

This compares test outcomes and resource use, not independent Pine output
conformance. It cannot promote a scoped benchmark to whole-stage acceptance.
"""
from __future__ import annotations
import math
import statistics
from pathlib import Path
from openpine.verification.execution_campaign import aggregate_campaign
from openpine.verification.execution_plan import validate_plan
from openpine.verification.identity import digest, seal

def workload_identity(plan: dict) -> str:
    validate_plan(plan)
    rows = []
    for task in plan['tasks']:
        row = {key: task[key] for key in ('id', 'component', 'environment', 'nodeids', 'full_inventory_hash', 'deselected', 'plugins', 'variant', 'execution_path', 'mode')}
        row['coverage'] = task.get('coverage', False)
        rows.append(row)
    return digest({'source': plan['source']['content_hash'], 'policy_hash': plan['policy_hash'], 'environment_identities': {key: value['identity']['content_hash'] for key, value in plan['environments'].items()}, 'profile': plan['profile'], 'required_gates': plan['required_gates'], 'tasks': rows})

def compare_campaigns(before: list[tuple[dict, Path, str]], after: list[tuple[dict, Path, str]], *, reference_profile: dict, target_speedup: float=2.0) -> dict:
    if len(before) < 5 or len(after) < 5:
        raise ValueError('need at least five executed measurements per organization')
    if not reference_profile or not math.isfinite(target_speedup) or target_speedup <= 0:
        raise ValueError('reference resources and a finite positive target are required')
    fingerprints, evidence_seen, samples = (set(), set(), {})
    resource_identities = set()
    for name, measurements in (('before', before), ('after', after)):
        values = []
        for plan, root, run_id in measurements:
            fingerprints.add(workload_identity(plan))
            report = aggregate_campaign(plan, root, expected_plan_hash=plan['content_hash'], expected_run_id=run_id)
            if not report['pytest_scope_passed']:
                raise ValueError('a failed/incomplete/cancelled run cannot serve as a timing sample')
            if report['run_hash'] in evidence_seen:
                raise ValueError('the same execution is not five independent measurements')
            evidence_seen.add(report['run_hash'])
            wall, cpu = (report['wall_seconds'], report['cpu_seconds'])
            if any((type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in (wall, cpu))) or wall == 0:
                raise ValueError('invalid measured resource cost')
            from openpine.verification.identity import read_json
            raw = read_json(root / 'run.json')
            if not isinstance(raw.get('resource_profile'), dict):
                raise ValueError('actual quota/affinity resource profile is missing')
            resource_identities.add(digest(raw['resource_profile']))
            if raw.get('merged_fragments') is not None:
                raise ValueError('sum of cross-runner wall cost is not elapsed wall time')
            if raw.get('affinity_cpus') != reference_profile.get('affinity_cpus'):
                raise ValueError('measurement resource profile differs from the reference')
            values.append({'run_id': run_id, 'run_hash': report['run_hash'], 'aggregate_hash': report['content_hash'], 'wall_seconds': wall, 'cpu_seconds': cpu, 'jobs': raw['jobs'], 'sampled_rss_bytes': raw.get('sampled_process_tree_peak_rss_bytes'), 'required_obligations': report['required_obligations']})
        samples[name] = values
    if len(resource_identities) != 1:
        raise ValueError('before/after used different quota or affinity resource profiles')
    if len(fingerprints) != 1:
        raise ValueError('before/after changed source, interpreter, policy or required test scope')
    metrics = {}
    for name, rows in samples.items():
        metrics[name] = {'n': len(rows), 'wall_median': statistics.median((r['wall_seconds'] for r in rows)), 'wall_max': max((r['wall_seconds'] for r in rows)), 'cpu_median': statistics.median((r['cpu_seconds'] for r in rows)), 'sampled_peak_rss_max': max((r['sampled_rss_bytes'] for r in rows if r['sampled_rss_bytes'] is not None), default=None)}
    speedup = metrics['before']['wall_median'] / metrics['after']['wall_median']
    return seal({'schema_id': 'openpine.test_performance_comparison.v1', 'ok': True, 'scope': 'exact compared pytest workload; not full stage or Pine numerical conformance', 'workload_hash': next(iter(fingerprints)), 'reference_profile': reference_profile, 'samples': samples, 'metrics': metrics, 'wall_speedup': speedup, 'cpu_cost_ratio': metrics['after']['cpu_median'] / metrics['before']['cpu_median'] if metrics['before']['cpu_median'] else None, 'target_speedup': target_speedup, 'target_met': speedup >= target_speedup, 'full_stage_accepted': False, 'p95': None, 'p95_reason': 'small sample: median and maximum reported'})
