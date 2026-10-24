"""Reproduce the immutable primitive-array release, without remote access."""

import argparse
from tools.specification import ROOT, SpecError, encode, file_map, read_json, sha
from packages.contracts.arrays import array_policy, array_apis

RELEASE = 'pine-arrays-20261024.1'
REVIEW = {'date':'2026-10-24', 'external_pine_execution':False,
    'sources':['https://www.tradingview.com/pine-script-docs/language/arrays/',
        'https://www.tradingview.com/pine-script-docs/language/loops/',
        'https://www.tradingview.com/pine-script-docs/migration-guides/to-pine-version-6/'],
    'basis':'Pinned per-version wiki records plus documented reference behavior; historical details are explicit bounded reconstructions.',
    'decisions':[
        'No arrays in v1-v3; primitive arrays in v4-v6; generic syntax and for-in in v5/v6. Constructors use the reviewed new_* names, with new<type> additionally admitted in v5/v6.',
        'References are always series. Assignment aliases the object; copy creates an independent primitive buffer. UDF may mutate global/parameter array contents but may not rebind globals/parameters.',
        'History stores prior object identities, not deep snapshots. Missing references admit na(x); other operations on them fail. Arrays retain primitive NA elements, except bool NA is unavailable in v6.',
        'Only get/set admit v6 negative indices. For-in evaluates the collection once, reads its current length and current indexed element on every iteration, and binds immutable local targets. V5 resizing behavior is explicitly reconstructed from the documented modern rule.',
        'Statistics ignore NA; empty/all-NA results are NA. Sum/min/max preserve numeric element type; average returns float. Nonzero nth ranks, methods, nested collections, reference comparisons, varip and arrays in request expressions are excluded.',
        'Each run/context owns its heap. Only completed-bar persistent/history roots survive. A private preview copies primitive buffers on first mutation. Checkpoints contain a closed heap and typed IDs; roots and budgets are validated before publication.',
        'Per-array length, total objects/cells/string bytes and per-bar work are bounded. Bulk operations and reclamation consume work and check cancellation. The profile does not reproduce platform wall-clock limits.'],
    'test_refs':['tests/test_arrays.py','tests/test_arrays_state.py','tests/test_arrays_pipeline.py','conformance/arrays/cases.json']}


def prepare():
    lock = read_json(ROOT/'spec/lock.json');versions = {}
    for version in range(1,7):
        apis = array_apis(version)
        names = {api['name'] for api in apis} | ({'for...in'} if version >= 5 else set())
        entries = read_json(ROOT/lock['snapshot']/f'reference/v{version}.json')['entries']
        versions[str(version)] = {'profile_id':f'openpine.arrays.v{version}.1', 'policy':array_policy(version), 'apis':apis,
            'evidence':[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in entries if e['name'] in names]}
    data = {'schema_version':'openpine.array-spec.1','base_manifest_sha256':lock['manifest_sha256'],
        'version_review_manifest_sha256':lock['version_review']['manifest_sha256'],'versions':versions,'review':REVIEW}
    files = {'arrays.json':encode(data)}
    files['manifest.json'] = encode({'schema_version':'openpine.array-manifest.1','release':RELEASE,
        'files':{name:sha(raw) for name,raw in files.items()}})
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true')
    args = parser.parse_args();files = prepare();target = ROOT/'spec/bundles'/RELEASE
    if target.exists():
        if file_map(target) != {name:sha(raw) for name,raw in files.items()}:raise SpecError('SPEC_IMMUTABLE','Array release differs; choose a new release.')
    elif args.check:raise SpecError('SPEC_IMPORT','Array release is missing.')
    else:
        target.mkdir(parents=True)
        for name,raw in files.items():(target/name).write_bytes(raw)
    print('Versioned array release verified.')


if __name__ == '__main__':main()
