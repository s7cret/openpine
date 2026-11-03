"""Retain pinned label signatures and verify their version boundary."""
import argparse

from packages.contracts.labels import OPERATIONS, apis
from packages.contracts.wire import canonical, digest, read_json
from tools.specification import ROOT


def profile(kind='label', operations=OPERATIONS, api_for=apis):
    lock = read_json(ROOT / 'spec/lock.json')
    versions, evidence = {}, {}
    for version in range(1, 7):
        key = str(version)
        reference = read_json(ROOT / lock['snapshot'] / f'reference/v{version}.json')
        entries = [entry for entry in reference['entries'] if entry['name'] in operations]
        assert {entry['name'] for entry in entries} == (set(operations) if version >= 4 else set())
        for name, parameters in operations.items():
            if version >= 4:
                names = tuple(n for n, _ in parameters)
                assert any(tuple(p['name'] for p in e['parameters'][:len(names)]) == names
                           for e in entries if e['name'] == name)
        versions[key] = dict(apis=api_for(version), reference_sha256=digest(reference))
        evidence[key] = [{k: entry[k] for k in ('name', 'entry_id', 'basis', 'source_record_sha256', 'syntax_spellings')}
                         for entry in entries]
    return dict(schema_version=f'openpine.{kind}s-spec.1', source_lock_sha256=digest(lock),
                versions=versions, evidence=evidence)


def main(argv=None, *, kind='label', operations=OPERATIONS, api_for=apis):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    path = ROOT / f'spec/{kind}s-profile.json'
    raw = canonical(profile(kind, operations, api_for)) + b'\n'
    if parser.parse_args(argv).check:
        assert path.read_bytes() == raw, f'{kind.capitalize()} profile is stale.'
    else:
        path.write_bytes(raw)
    print(f'{kind.capitalize()} profile verified.')


if __name__ == '__main__':
    main()
