"""Deterministic selection/sharding of frozen inventories; no acceptance shortcut.

The planner selects existing obligations. It does not derive expected Pine
values, rebaseline a lock, or turn a component run into full-stage acceptance.
"""
from __future__ import annotations
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Mapping, Sequence
from openpine.verification.identity import digest, seal, verify
from openpine.verification.execution_identity import ENV_SCHEMA, SOURCE_SCHEMA
PLAN_SCHEMA = 'openpine.test_execution_plan.v1'
PROFILES = ('smoke', 'affected', 'component', 'integration', 'stage-full', 'release-full')
HASH = re.compile('sha256:[0-9a-f]{64}\\Z')

def _nodes(values: Sequence[str]) -> list[str]:
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError('empty inventory')
    result = list(values)
    if any((not isinstance(n, str) or '::' not in n or n.startswith(('-', '/', '@')) or ('\n' in n) or ('\r' in n) or ('\x00' in n) or ('\\' in n.split('::', 1)[0]) or ('..' in n.split('::', 1)[0].split('/')) for n in result)) or len(set(result)) != len(result):
        raise ValueError('invalid or duplicate node ID')
    return sorted(result)

def select_components(policy: dict, profile: str, requested: Sequence[str], changes: Sequence[str]=()) -> tuple[list[str], list[str]]:
    if profile not in PROFILES:
        raise ValueError('unknown test profile')
    components = policy['components']
    if not components or any((not isinstance(n, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]*', n) for n in components)) or any((n not in components for n in requested)):
        raise ValueError('unknown component')
    dependencies = {n: set(v.get('dependencies', [])) for n, v in components.items()}
    if any((not deps.issubset(components) or name in deps for name, deps in dependencies.items())):
        raise ValueError('invalid dependency graph')
    pending = {n: set(d) for n, d in dependencies.items()}
    while pending:
        ready = {n for n, d in pending.items() if not d}
        if not ready:
            raise ValueError('cyclic dependency graph')
        pending = {n: d - ready for n, d in pending.items() if n not in ready}
    if profile in {'stage-full', 'release-full', 'integration'}:
        return (sorted(components), ['full component boundary coverage'])
    if profile != 'affected':
        if not requested:
            raise ValueError('profile needs an explicit component')
        return (sorted(set(requested)), ['explicit scoped selection'])
    from fnmatch import fnmatchcase
    affected = set(requested)
    reasons = []
    for change in changes:
        owner, separator, relative = change.partition('/')
        if not separator or owner not in components or '..' in relative.split('/'):
            return (sorted(components), ['unknown change escalates to full inventory'])
        if any((fnmatchcase(relative, pattern) for pattern in policy.get('critical_paths', []))):
            return (sorted(components), ['shared contract/fixture change escalates: ' + change])
        affected.add(owner)
    if not affected:
        raise ValueError('affected selection needs changes or owners')
    # Dependencies remain preparation requirements, but their full suites are
    # not implied by a consumer's inclusion. Test obligations flow upstream to
    # consumers/boundary owners only; make_plan records the dependency closure
    # separately as preparation_components.
    while True:
        expanded = affected | {n for n, d in dependencies.items() if d & affected}
        if expanded == affected:
            break
        affected = expanded
    reasons.append('transitive owner/consumer boundary closure; prerequisites prepared separately')
    return (sorted(affected), reasons)

def assign_shards(nodes: Sequence[str], count: int, durations: Mapping[str, float] | None=None) -> list[dict]:
    nodes = _nodes(nodes)
    if type(count) is not int or not 1 <= count <= 64:
        raise ValueError('shard count must be 1..64')
    timings = durations or {}
    if any((not isinstance(v, (int, float)) or isinstance(v, bool) or (not math.isfinite(v)) or (v < 0) for v in timings.values())):
        raise ValueError('invalid duration')
    groups: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        groups[node.split('::', 1)[0]].append(node)
    weighted = [(sum((max(float(timings.get(n, 1.0)), 0.001) for n in values)), name, values) for name, values in groups.items()]
    bins = [{'id': f's{i:03}', 'nodeids': [], 'estimated_seconds': 0.0} for i in range(min(count, len(groups)))]
    for weight, _, members in sorted(weighted, key=lambda r: (-r[0], r[1])):
        target = min(bins, key=lambda b: (b['estimated_seconds'], b['id']))
        target['nodeids'].extend(members)
        target['estimated_seconds'] += weight
    for shard in bins:
        shard['nodeids'].sort()
    return bins

def make_plan(*, profile: str, policy: dict, roots: Mapping[str, Path], source: dict, inventories: dict, environments: dict, requested: Sequence[str]=(), changes: Sequence[str]=(), shard_count: int=1, durations: dict | None=None, coverage: bool=False) -> dict:
    from openpine.verification.pytest_gate import validate_inventory
    verify(source, SOURCE_SCHEMA)
    selected, reasons = select_components(policy, profile, requested, changes)
    dependencies = {
        name: set(settings.get('dependencies', []))
        for name, settings in policy['components'].items()
    }
    selected_set = set(selected)
    preparation_components = set()
    pending = list(selected)
    seen = set()
    while pending:
        component = pending.pop()
        if component in seen:
            continue
        seen.add(component)
        for prerequisite in dependencies[component]:
            pending.append(prerequisite)
            if prerequisite not in selected_set:
                preparation_components.add(prerequisite)
    if set(roots) != set(source['components']) or not set(selected).issubset(roots):
        raise ValueError('source roots differ from candidate')
    if not environments:
        raise ValueError('no execution environments')
    for key, env in environments.items():
        if not re.fullmatch('[a-zA-Z0-9_.-]+', key):
            raise ValueError('invalid environment ID')
        verify(env['identity'], ENV_SCHEMA)
        if not Path(env['executable']).is_absolute():
            raise ValueError('interpreter must be absolute')
    if type(coverage) is not bool:
        raise ValueError('coverage selection must be boolean')
    tasks = []
    for name in selected:
        settings = policy['components'][name]
        required = settings.get('pythons', ['3.11', '3.13'])
        seen_versions = set()
        for env_id, env in sorted(environments.items()):
            minor = '.'.join(env['identity']['python'].split('.')[:2])
            if minor not in required:
                if profile in {'stage-full', 'release-full'}:
                    continue
            seen_versions.add(minor)
            key = name + '@' + env_id
            if key not in inventories:
                raise ValueError('missing collected inventory: ' + key)
            inv = inventories[key]
            nodes = _nodes(inv['nodeids'])
            validate_inventory(nodes, inv['reviewed_lock'], inv.get('deselected', 0))
            if inv['source_hash'] != source['content_hash'] or inv['environment_hash'] != env['identity']['content_hash']:
                raise ValueError('stale collection source/environment: ' + key)
            full_hash = digest(nodes)
            if profile == 'smoke':
                patterns = settings.get('smoke', [])
                if (
                    not isinstance(patterns, list)
                    or not patterns
                    or len(set(patterns)) != len(patterns)
                    or any(
                        not isinstance(node, str)
                        or any(token in node for token in ('*', '?', '['))
                        for node in patterns
                    )
                ):
                    raise ValueError('smoke selectors must be explicit node IDs: ' + name)
                smoke_nodes = _nodes(patterns)
                if not set(smoke_nodes).issubset(nodes):
                    raise ValueError('smoke selector is not a required node ID: ' + name)
                nodes = smoke_nodes
            markers = inv.get('node_markers', {node: [] for node in inv['nodeids']})
            if set(markers) != set(inv['nodeids']) or any((not isinstance(m, list) or any((not isinstance(v, str) for v in m)) for m in markers.values())):
                raise ValueError('missing or malformed collection marker evidence')
            excluded_markers = list(policy.get('untraced_markers', ['performance'])) if coverage else []
            traced = [n for n in nodes if not set(markers[n]).intersection(excluded_markers)]
            traced_set = set(traced)
            untraced = [n for n in nodes if n not in traced_set]
            shards = []
            for members, instrumented in ((traced, coverage), (untraced, False)):
                if members:
                    for shard in assign_shards(members, shard_count, (durations or {}).get(name)):
                        shard['id'] = f's{len(shards):03}'
                        shard['coverage'] = instrumented
                        shards.append(shard)
            tasks.append({'id': key, 'component': name, 'environment': env_id, 'nodeids': nodes, 'nodeids_hash': digest(nodes), 'full_inventory_hash': full_hash, 'reviewed_lock_hash': digest(inv['reviewed_lock']), 'deselected': inv.get('deselected', 0), 'plugins': list(settings.get('plugins', [])), 'timeout_seconds': settings.get('timeout_seconds', 1800), 'cpu_slots': settings.get('cpu_slots', 1), 'memory_mib': settings.get('memory_mib', 256), 'coverage': coverage, 'coverage_package': name.replace('-', '_'), 'node_markers': {n: markers[n] for n in nodes}, 'untraced_markers': excluded_markers, 'exclusive_group': settings.get('exclusive_group'), 'variant': 'functional', 'execution_path': 'pytest', 'mode': 'source', 'shards': shards})
        if profile in {'stage-full', 'release-full'} and (not set(required).issubset(seen_versions)):
            raise ValueError('mandatory interpreter missing for ' + name + ': ' + ','.join(sorted(set(required) - seen_versions)))
    gates = list(policy.get('required_gates', {}).get(profile, []))
    if profile in {'stage-full', 'release-full'} and (not gates):
        raise ValueError('full profile must declare non-pytest acceptance gates')
    plan = seal({'schema_id': PLAN_SCHEMA, 'profile': profile, 'policy_hash': digest(policy), 'source': source, 'roots': {k: str(Path(v).resolve()) for k, v in sorted(roots.items())}, 'environments': environments, 'selection_reasons': reasons, 'preparation_components': sorted(preparation_components), 'tasks': tasks, 'required_gates': gates, 'full_acceptance_requires_owner_gates': True})
    validate_plan(plan)
    return plan

def validate_plan(plan: dict, *, expected_hash: str | None=None) -> dict:
    verify(plan, PLAN_SCHEMA)
    if expected_hash is not None and plan['content_hash'] != expected_hash:
        raise ValueError('execution plan identity mismatch')
    if plan.get('profile') not in PROFILES or not HASH.fullmatch(plan.get('policy_hash', '')):
        raise ValueError('invalid plan policy/profile')
    verify(plan['source'], SOURCE_SCHEMA)
    if 'source_commits' in plan:
        commits = plan['source_commits']
        if not isinstance(commits, dict) or set(commits) != set(plan['source']['components']) or any(
            not isinstance(value, str) or re.fullmatch('[0-9a-f]{40}', value) is None
            for value in commits.values()
        ):
            raise ValueError('plan producer commits do not match source components')
    if set(plan['roots']) != set(plan['source']['components']) or any((not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]*', n) for n in plan['roots'])) or any((not Path(p).is_absolute() for p in plan['roots'].values())):
        raise ValueError('plan root mismatch')
    preparation_components = plan.get('preparation_components')
    if (
        not isinstance(preparation_components, list)
        or preparation_components != sorted(set(preparation_components))
        or not set(preparation_components).issubset(plan['roots'])
    ):
        raise ValueError('invalid preparation component set')
    for name, env in plan['environments'].items():
        if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]*', name) or not Path(env['executable']).is_absolute():
            raise ValueError('invalid environment identity')
        verify(env['identity'], ENV_SCHEMA)
    ids = []
    obligations = set()
    if not plan['tasks']:
        raise ValueError('empty execution plan')
    for task in plan['tasks']:
        ids.append(task['id'])
        if task['component'] not in plan['roots'] or task['environment'] not in plan['environments'] or task['id'] != task['component'] + '@' + task['environment']:
            raise ValueError('task identity mismatch')
        if task.get('variant') != 'functional' or task.get('execution_path') != 'pytest' or task.get('mode') != 'source':
            raise ValueError('unsupported task execution boundary')
        nodes = _nodes(task['nodeids'])
        if nodes != task['nodeids'] or digest(nodes) != task['nodeids_hash']:
            raise ValueError('task inventory hash/order mismatch')
        assigned, shard_ids = ([], [])
        for shard in task['shards']:
            if not re.fullmatch('s[0-9]{3}', shard['id']):
                raise ValueError('invalid shard ID')
            if type(shard.get('coverage', task.get('coverage', False))) is not bool:
                raise ValueError('invalid shard instrumentation')
            if 'node_markers' in task:
                markers = task['node_markers']
                if set(markers) != set(nodes):
                    raise ValueError('task markers omit required nodes')
                if any((not isinstance(v, list) or len(set(v)) != len(v) or any((not isinstance(m, str) for m in v)) for v in markers.values())):
                    raise ValueError('malformed marker evidence')
                for node in shard['nodeids']:
                    desired = bool(task.get('coverage', False)) and (not set(markers[node]).intersection(task.get('untraced_markers', [])))
                    if shard.get('coverage', task.get('coverage', False)) != desired:
                        raise ValueError('shard instrumentation differs from marker obligations')
            shard_ids.append(shard['id'])
            assigned.extend(_nodes(shard['nodeids']))
            for node in shard['nodeids']:
                obligation = (task['id'], node, task['variant'], task['execution_path'], task['mode'])
                if obligation in obligations:
                    raise ValueError('duplicate obligation')
                obligations.add(obligation)
        if len(set(shard_ids)) != len(shard_ids) or sorted(assigned) != nodes:
            raise ValueError('missing/unexpected/duplicate shard assignment')
        if type(task['timeout_seconds']) is not int or not 1 <= task['timeout_seconds'] <= 86400 or type(task['cpu_slots']) is not int or (not 1 <= task['cpu_slots'] <= 64):
            raise ValueError('invalid task resource budget')
        if type(task.get('memory_mib', 256)) is not int or not 64 <= task.get('memory_mib', 256) <= 1048576:
            raise ValueError('invalid task memory reservation')
        if type(task.get('coverage', False)) is not bool or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', task.get('coverage_package', task['component'].replace('-', '_'))):
            raise ValueError('invalid coverage instrumentation')
        if task['exclusive_group'] is not None and (not isinstance(task['exclusive_group'], str)):
            raise ValueError('invalid exclusive group')
        if not isinstance(task['plugins'], list) or any((not re.fullmatch('[A-Za-z_][A-Za-z0-9_.]*', p) for p in task['plugins'])):
            raise ValueError('invalid plugin list')
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate task')
    gates = plan['required_gates']
    if not isinstance(gates, list) or len(set(gates)) != len(gates) or any((not re.fullmatch('[a-z][a-z0-9_-]*', g) for g in gates)) or (plan['profile'] in {'stage-full', 'release-full'} and (not gates)):
        raise ValueError('invalid required owner gates')
    return plan

def task_shard(plan: dict, task_id: str, shard_id: str) -> tuple[dict, dict]:
    validate_plan(plan)
    matches = [t for t in plan['tasks'] if t['id'] == task_id]
    if len(matches) != 1:
        raise ValueError('unknown task')
    shards = [s for s in matches[0]['shards'] if s['id'] == shard_id]
    if len(shards) != 1:
        raise ValueError('unknown shard')
    return (matches[0], shards[0])
