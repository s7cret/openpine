"""Verify the visual OHLC ABI against version-specific reference evidence."""

import argparse

from packages.contracts.visual_ohlc import KINDS, apis, policy
from packages.contracts.wire import canonical, digest, read_json
from tools.colors_spec import assert_overlay
from tools.specification import ROOT


def profile():
    lock = read_json(ROOT / 'spec/lock.json')
    versions = {}
    evidence = {}
    for version in range(1, 7):
        reference = read_json(ROOT / lock['snapshot'] / f'reference/v{version}.json')
        entries = {e['name']: e for e in reference['entries'] if e['name'] in KINDS}
        assert entries.keys() == set(KINDS)
        signatures = apis(version)
        for api in signatures:
            entry = entries[api['name']]
            if version >= 3:
                assert entry['basis'] == 'primary'
                names = [p['name'] for p in entry['parameters']]
                admitted = [p['name'] for p in api['parameters']]
                assert [n for n in names if n in admitted] == admitted
                assert_overlay(reference, api['name'], signatures, version)
            else:
                assert entry['basis'] in ('inferred', 'unverified') and not entry['parameters']
        versions[str(version)] = {'policy': policy(version), 'apis': signatures,
                                  'reference_sha256': digest(reference)}
        evidence[str(version)] = [
            {k: entry[k] for k in ('name', 'entry_id', 'basis', 'syntax_spellings', 'parameters', 'source_record_sha256')}
            for entry in entries.values()
        ]
    return {'schema_version': 'openpine.visual-ohlc-spec.1', 'source_lock_sha256': digest(lock),
            'versions': versions, 'evidence': evidence}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    path = ROOT / 'spec/visual-ohlc-profile.json'
    raw = canonical(profile()) + b'\n'
    if args.check:
        assert path.read_bytes() == raw, 'Visual OHLC profile is stale.'
    else:
        path.write_bytes(raw)
    print('Visual OHLC profile verified.')


if __name__ == '__main__':
    main()
