"""Reproduce the additive defaults/switch profile from versioned Wiki evidence."""
import argparse
from tools.specification import ROOT
from packages.contracts.semantic_core import policy
from packages.contracts.wire import canonical, digest, read_json


def profile():
    lock = read_json(ROOT / 'spec/lock.json')
    versions, evidence = {}, {}
    for v in range(1, 7):
        reference = read_json(ROOT / lock['snapshot'] / f'reference/v{v}.json')
        versions[str(v)] = {'policy': policy(v), 'reference_sha256': digest(reference)}
        evidence[str(v)] = [{k: entry[k] for k in ('entry_id', 'source_record_sha256', 'sources')}
            for entry in reference['entries'] if entry['name'] == 'switch' or entry['entry_id'].endswith(('udf-default-parameters', 'udf-parameters', 'udf-single'))]
    return {'schema_version': 'openpine.semantic-core-spec.1', 'release': 'pine-semantic-core-20261030.1',
        'source_lock_sha256': digest(lock), 'versions': versions, 'evidence': evidence,
        'review': {'external_execution_verified': False, 'decisions': [
            'The v5/v6 Wiki keyword and UDF records admit switch and optional scalar parameters. v1-v4 explicitly reject these additions.',
            'Existing reconstructed v1-v4 functions and named arguments are retained; this release does not certify their historical TradingView equivalence.',
            'Defaults are a bounded literal-only subset, checked even in unused functions and when an explicit argument overrides the default.',
            'No new evaluator or wire node: fully bound UserCallIR arguments retain explicit source order and mark inserted closed defaults.',
            'Switch lowers to private lexical blocks and existing lazy conditions. Selector executes once; only the first matching branch executes.',
            'Missing numeric/bool matches inherit the admitted if policy. Strings and tuples require an explicit default; reference-returning switch is outside this slice.',
            'Overloads, methods, general default expressions and objects remain separate future slices.'],
            'corroborating_primary_sources': [
                'https://www.tradingview.com/pine-script-docs/v5/release-notes/',
                'https://www.tradingview.com/pine-script-docs/language/user-defined-functions/'],
            'test_refs': ['tests/test_semantic_core.py', 'conformance/semantic-core/cases.json']}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    path = ROOT / 'spec/semantic-core-profile.json'
    raw = canonical(profile()) + b'\n'
    if args.check:
        assert path.read_bytes() == raw, 'Semantic core profile differs.'
    else:
        path.write_bytes(raw)
    print('Versioned defaults/switch profile verified.')


if __name__ == '__main__':main()
