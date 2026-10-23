"""Reproduce the reviewed loop release without changing older bundles."""

import argparse
from tools.specification import ROOT, SpecError, encode, file_map, read_json, sha
from packages.contracts.loops import loop_policy

RELEASE = 'pine-loops-20261024.1'
REVIEW = {'date': '2026-10-24', 'external_pine_execution': False,
    'basis': 'Pinned per-version wiki records, official documentation and an explicitly bounded execution model.',
    'sources': [
        {'url': 'https://www.tradingview.com/pine-script-docs/v3/language/expressions-declarations-and-statements/',
         'finding': 'For, break and continue were introduced in v2; positive step and automatic direction.'},
        {'url': 'https://www.tradingview.com/pine-script-docs/release-notes/',
         'finding': 'October 2021 introduced while with Pine v5.'},
        {'url': 'https://www.tradingview.com/pine-script-docs/migration-guides/to-pine-version-6/',
         'finding': 'V6 evaluates the for end boundary before every iteration; v5 freezes it at entry.'},
        {'url': 'https://www.tradingview.com/pine-script-docs/language/loops/',
         'finding': 'Loop scope, last completed return, nearest break/continue and first iteration of a direct var initializer.'}],
    'decisions': [
        'No loops in v1. For/break/continue in v2-v6; while in v5-v6. V2-v4 frozen end bounds and the modern direct-var rule are explicit historical reconstructions.',
        'For evaluates initial/end/positive-int step at entry, fixes direction from initial bounds, includes both ends and stops on a missing bound. V6 re-evaluates the end after each completed/continued iteration. Explicit counter writes and collection iteration are excluded.',
        'Statement loops may end with assignments, calls or control. Value loops return their last completed tail; numeric missing is NA, v6 bool missing is false. Flat numeric/bool tuple tails are allowed; string/void values and control-only value tails are excluded.',
        'Each written local var and stateful call chain is shared by iterations. Ordinary declarations initialize each reached iteration. History remains bar-aligned with final variable/expression values; changing a simple TA length through the counter is rejected.',
        'The existing unconditional-global request profile is retained: loops may consume an already scheduled requested value but cannot introduce requests inside their body or requested expression.',
        'All loops in a bar share fixed iteration and operation budgets. Cancellation is checked within execution. Only completed bars can be checkpointed or published; failed/cancelled evaluations are poisoned. No platform wall-clock equivalence is claimed.'],
    'test_refs': ['tests/test_loops.py', 'tests/test_loops_state.py', 'tests/test_loops_pipeline.py', 'conformance/loops/cases.json']}


def prepare():
    lock = read_json(ROOT / 'spec/lock.json')
    versions = {}
    for version in range(1, 7):
        entries = read_json(ROOT / lock['snapshot'] / f'reference/v{version}.json')['entries']
        versions[str(version)] = {'profile_id': f'openpine.loops.v{version}.1', 'policy': loop_policy(version),
            'evidence': [{'entry_id': e['entry_id'], 'source_record_sha256': e['source_record_sha256']}
                for e in entries if e['name'] in ('for', 'while', 'break', 'continue')],
            'basis': 'documented_absence' if version == 1 else 'artifact_defined_with_explicit_reconstruction'}
    data = {'schema_version': 'openpine.loop-spec.1', 'base_manifest_sha256': lock['manifest_sha256'],
        'version_review_manifest_sha256': lock['version_review']['manifest_sha256'], 'versions': versions, 'review': REVIEW}
    files = {'loops.json': encode(data)}
    files['manifest.json'] = encode({'schema_version': 'openpine.loop-manifest.1', 'release': RELEASE,
        'files': {name: sha(raw) for name, raw in files.items()}})
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    files = prepare()
    target = ROOT / 'spec/bundles' / RELEASE
    if target.exists():
        if file_map(target) != {n: sha(raw) for n, raw in files.items()}:
            raise SpecError('SPEC_IMMUTABLE', 'Loop release differs; choose a new release.')
    elif args.check:raise SpecError('SPEC_IMPORT', 'Loop release is missing.')
    else:
        target.mkdir(parents=True)
        for name, raw in files.items():(target / name).write_bytes(raw)
    print('Versioned loop release verified.')


if __name__ == '__main__':main()
