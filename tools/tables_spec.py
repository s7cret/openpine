"""Bind the basic pane table profile to the pinned Pine references."""
import argparse

from packages.contracts.tables import OPERATIONS, apis, constants
from packages.contracts.wire import canonical, digest, read_json
from tools.specification import ROOT


def profile():
    lock = read_json(ROOT / 'spec/lock.json')
    versions, evidence = {}, {}
    for version in range(1, 7):
        key = str(version)
        reference = read_json(ROOT / lock['snapshot'] / f'reference/v{version}.json')
        positions = constants(version)
        entries = [e for e in reference['entries'] if e['name'] in OPERATIONS or e['name'] in positions]
        expected = set(OPERATIONS) | positions.keys() if version >= 4 else set()
        assert {e['name'] for e in entries} == expected, version
        for entry in entries:
            name = entry['name']
            if name not in OPERATIONS:
                assert entry['result_type_spellings'] == ['const string'], (version, name)
                continue
            parameters = {p['name']: p for p in entry['parameters']}
            assert set(n for n, _ in OPERATIONS[name]) <= parameters.keys(), (version, name)
            if name == 'table.cell':
                assert parameters['text_color']['default_claim']['spellings'] == ['color.black'], version
        versions[key] = dict(apis=apis(version), positions=positions, reference_sha256=digest(reference))
        evidence[key] = [{k: e[k] for k in ('name', 'entry_id', 'basis', 'source_record_sha256', 'syntax_spellings')}
                         for e in entries]
    return dict(schema_version='openpine.tables-spec.1', source_lock_sha256=digest(lock),
                versions=versions, evidence=evidence)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    path = ROOT / 'spec/tables-profile.json'
    raw = canonical(profile()) + b'\n'
    if parser.parse_args(argv).check:
        assert path.read_bytes() == raw, 'Table profile is stale.'
    else:
        path.write_bytes(raw)
    print('Table profile verified.')


if __name__ == '__main__':
    main()
