"""Transfer and merge raw campaign evidence, never green summary counters.

Fragments preserve the frozen whole plan and shared run ID. Each is verified
against its exact assignment. Merge copies raw artifacts and provenance and is
checked again without access to original runner paths. Failed or repeated work
is retained, not silently replaced by a later successful attempt.
"""
from __future__ import annotations
import math
import shutil
from pathlib import Path
from openpine.verification.execution_campaign import RUN_SCHEMA, aggregate_campaign, descriptor, utc_now
from openpine.verification.execution_identity import ensure_external_output, evidence_path, hash_file, read_artifact, write_once_json
from openpine.verification.execution_plan import validate_plan
from openpine.verification.execution_disk import INLINE_LIMIT, ObservationWriter, iter_observations
from itertools import zip_longest
from openpine.verification.identity import read_json, seal, verify

def fragment_selection(plan: dict, run: dict) -> list[tuple[str, str]]:
    all_keys = {(t['id'], s['id']) for t in plan['tasks'] for s in t['shards']}
    rows = run.get('selection')
    if not isinstance(rows, list) or not rows or any((not isinstance(v, list) or len(v) != 2 or any((not isinstance(x, str) for x in v)) for v in rows)):
        raise ValueError('fragment needs an explicit nonempty assignment')
    keys = [tuple(v) for v in rows]
    if len(keys) != len(set(keys)) or not set(keys).issubset(all_keys):
        raise ValueError('duplicate or unexpected fragment assignment')
    return keys

def _copy_checked(source: Path, target: Path, relative: str, expected_hash: str | None=None) -> None:
    origin = evidence_path(source, relative)
    actual = hash_file(origin)
    if expected_hash is not None and actual != expected_hash:
        raise ValueError('fragment artifact checksum mismatch: ' + relative)
    destination = evidence_path(target, relative, must_exist=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with origin.open('rb') as src, destination.open('xb') as dst:
        shutil.copyfileobj(src, dst)
    if hash_file(destination) != actual:
        raise ValueError('artifact changed during transfer')

def _copy_attempt(source: Path, target: Path, attempt: dict) -> None:
    for artifact in attempt['artifacts'].values():
        _copy_checked(source, target, artifact['path'], artifact['sha256'])
    relative = '/'.join((attempt['task'], attempt['shard'], attempt['attempt_id'], 'execution.json'))
    _copy_checked(source, target, relative)

def _raw_disk_rows(verified_runs):
    for root, run in verified_runs:
        yield from iter_observations(root, run['disk_free_guard'])


def _merged_disk_guard(plan: dict, verified_runs: list, output: Path | None = None,
                       existing: dict | None = None, replay_root: Path | None = None) -> dict:
    """Bounded derivation; replay compares every record to authenticated raw runs."""
    if output is None and (not isinstance(existing, dict) or replay_root is None):
        raise ValueError('invalid merged disk receipt')
    disks = [run['disk_free_guard'] for _, run in verified_runs]
    streamed = any('observation_stream' in disk for disk in disks)
    limit = 0 if streamed else INLINE_LIMIT
    writer = ObservationWriter(output, limit) if output is not None else None
    inline, samples, observation_errors, minimum = [], 0, 0, None
    try:
        for row in _raw_disk_rows(verified_runs):
            free = row['free_bytes']
            if free is None:
                observation_errors += 1
            else:
                samples += 1
                minimum = free if minimum is None else min(minimum, free)
            if writer is not None:
                writer.append(row)
            elif not streamed:
                if len(inline) < limit:
                    inline.append(row)
                else:
                    inline.clear()
                    streamed = True
        result = {**plan['disk_free_guard'], 'observations': writer.observations if writer else inline,
                  'minimum_observed_free_bytes': minimum, 'samples': samples,
                  'observation_errors': observation_errors, 'tripped': any(disk['tripped'] for disk in disks),
                  'enforcement': 'sampled cancellation; not a hard disk-size or stop-latency bound'}
        desc = writer.finish() if writer else existing.get('observation_stream') if existing else None
        if desc is not None:
            result['observation_stream'] = desc
        if output is None:
            if streamed and desc is None:
                raise ValueError('merged disk stream missing')
            sentinel = object()
            for raw_row, merged_row in zip_longest(_raw_disk_rows(verified_runs), iter_observations(replay_root, existing), fillvalue=sentinel):
                if raw_row != merged_row:
                    raise ValueError('merged disk observations differ from raw transferred fragments')
        return result
    finally:
        if writer is not None:
            writer.close_partial()

def verify_fragment_provenance(plan: dict, root: Path, run: dict) -> None:
    """Re-validate all transferred fragments and their link to the merged rows."""
    entries = run.get('merged_fragments')
    if not isinstance(entries, list) or not entries:
        raise ValueError('missing raw fragment provenance')
    observed: dict[tuple[str, str], dict] = {}
    seen_roots = set()
    verified_runs = []
    for entry in entries:
        raw = read_artifact(root, entry['run'])
        verify(raw, RUN_SCHEMA)
        if raw.get('merged_fragments') is not None:
            raise ValueError('recursive fragment manifests are not admitted')
        run_path = evidence_path(root, entry['run']['path'])
        if run_path.name != 'run.json' or run_path.parent in seen_roots:
            raise ValueError('duplicate or malformed fragment location')
        seen_roots.add(run_path.parent)
        selection = fragment_selection(plan, raw)
        report = aggregate_campaign(plan, run_path.parent, expected_plan_hash=plan['content_hash'], expected_run_id=run['run_id'], expected_shards=selection)
        if not report['pytest_scope_passed']:
            raise ValueError('raw fragment failed verification: ' + '; '.join(report['errors']))
        verified_runs.append((run_path.parent, raw))
        for attempt in raw['attempts']:
            key = (attempt['task'], attempt['shard'])
            if key in observed:
                raise ValueError('duplicate shard across raw fragments')
            observed[key] = attempt
    merged = {(a['task'], a['shard']): a for a in run['attempts']}
    if len(merged) != len(run['attempts']) or merged != observed:
        raise ValueError('merged attempts differ from raw transferred fragments')
    if 'disk_free_guard' in plan and run.get('disk_free_guard') != _merged_disk_guard(plan, verified_runs, existing=run.get('disk_free_guard'), replay_root=root):
        raise ValueError('merged disk diagnostics differ from raw transferred fragments')

def merge_fragments(plan: dict, fragments: list[Path], output: Path, *, expected_plan_hash: str, expected_run_id: str) -> dict:
    validate_plan(plan, expected_hash=expected_plan_hash)
    if not fragments or len({p.resolve() for p in fragments}) != len(fragments):
        raise ValueError('need distinct fragment directories')
    ensure_external_output(output, {k: Path(v) for k, v in plan['roots'].items()})
    if output.exists() or any((output.resolve().is_relative_to(p.resolve()) for p in fragments)):
        raise ValueError('merge output must be new and outside fragment inputs')
    output.mkdir(parents=True)
    entries, attempts, errors, selections = ([], [], [], [])
    verified_runs = []
    seen = set()
    walls, memories, starts, ends, job_budgets = ([], [], [], [], [])
    for index, source in enumerate(fragments):
        source = source.resolve()
        destination = output / 'fragments' / f'f{index:04d}'
        destination.mkdir(parents=True)
        try:
            raw = read_json(evidence_path(source, 'run.json'))
            verify(raw, RUN_SCHEMA)
            if raw.get('merged_fragments') is not None:
                raise ValueError('merge only raw runner fragments')
            keys = fragment_selection(plan, raw)
            _copy_checked(source, destination, 'run.json')
            if raw.get('binding') is not None:
                artifact = raw['binding']
                _copy_checked(source, destination, artifact['path'], artifact['sha256'])
            for attempt in raw['attempts']:
                _copy_attempt(source, destination, attempt)
            if 'disk_free_guard' in plan:
                stream = raw.get('disk_free_guard', {}).get('observation_stream')
                if stream is not None:
                    _copy_checked(source, destination, stream['path'], stream['sha256'])
            entries.append({'run': descriptor(output, destination / 'run.json')})
            checked = aggregate_campaign(plan, destination, expected_plan_hash=expected_plan_hash, expected_run_id=expected_run_id, expected_shards=keys)
            errors += [f'fragment {index}: {e}' for e in checked['errors']]
            if checked['pytest_scope_passed']:
                verified_runs.append((destination, raw))
            selections += keys
            for attempt in raw['attempts']:
                key = (attempt['task'], attempt['shard'])
                attempts.append(attempt)
                if key in seen:
                    errors.append('duplicate shard from multiple runners: ' + str(key))
                    continue
                seen.add(key)
                _copy_attempt(source, output, attempt)
            walls.append(raw['wall_seconds'])
            memories.append(raw.get('sampled_process_tree_peak_rss_bytes') or 0)
            starts.append(raw['started_at'])
            ends.append(raw['finished_at'])
            job_budgets.append(raw['jobs'])
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append(f'fragment {index}: {error}')
    run = seal({'schema_id': RUN_SCHEMA, 'run_id': expected_run_id, 'plan_hash': plan['content_hash'], 'candidate_hash': plan['source']['content_hash'], 'source_before': plan['source']['content_hash'], 'source_after': plan['source']['content_hash'], 'selection': [list(k) for k in sorted(set(selections))], 'binding': None, 'merged_fragments': entries, 'attempts': attempts, 'errors': errors, 'started_at': min(starts) if starts else utc_now(), 'finished_at': max(ends) if ends else utc_now(), 'wall_seconds': math.fsum(walls), 'wall_measure': 'sum_of_fragment_runner_wall_seconds', 'fragment_wall_seconds': walls, 'fragment_jobs': job_budgets, 'jobs': sum(job_budgets), 'sampled_process_tree_peak_rss_bytes': None, 'fragment_sampled_peak_rss_bytes': memories})
    if 'disk_free_guard' in plan:
        try:
            disk = _merged_disk_guard(plan, verified_runs, output=output)
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append('merged disk diagnostics unavailable: ' + str(error))
            disk = {**plan['disk_free_guard'], 'observations': [], 'samples': 0,
                    'observation_errors': 0, 'minimum_observed_free_bytes': None, 'tripped': True}
        run = seal({**{key: value for key, value in run.items() if key != 'content_hash'},
                    'errors': errors, 'disk_free_guard': disk})
    write_once_json(output / 'run.json', run)
    report = aggregate_campaign(plan, output, expected_plan_hash=expected_plan_hash, expected_run_id=expected_run_id)
    write_once_json(output / 'aggregate.json', report)
    return report
