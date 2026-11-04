"""Bind the coordinate constructor and setters to pinned Pine evidence."""
import argparse

from packages.contracts.boxes import COORDINATES, OPERATIONS, apis
from packages.contracts.wire import canonical, digest, read_json
from tools.specification import ROOT


def profile():
    lock = read_json(ROOT / 'spec/lock.json')
    versions, evidence = {}, {}
    for version in range(1, 7):
        key = str(version)
        reference = read_json(ROOT / lock['snapshot'] / f'reference/v{version}.json')
        entries = [e for e in reference['entries'] if e['name'] in OPERATIONS]
        assert {e['name'] for e in entries} == (set(OPERATIONS) if version >= 4 else set())
        coordinate_entries = []
        for entry in entries:
            names = tuple(p['name'] for p in entry['parameters'])
            if entry['name'] == 'box.new':
                if names[:4] != COORDINATES:
                    continue
                parameters = {p['name']: p for p in entry['parameters']}
                for name, expected in {'border_color': 'color.blue', 'bgcolor': 'color.blue',
                                       'border_width': '1', 'xloc': 'xloc.bar_index', 'extend': 'extend.none'}.items():
                    assert parameters[name]['default_claim']['spellings'] == [expected], (version, name)
            else:
                assert names == tuple(n for n, _ in OPERATIONS[entry['name']]), (version, entry['name'])
            coordinate_entries.append(entry)
        if version >= 4:
            assert any(e['name'] == 'box.new' for e in coordinate_entries)
        versions[key] = dict(apis=apis(version), reference_sha256=digest(reference))
        evidence[key] = [{k: e[k] for k in ('name', 'entry_id', 'basis', 'source_record_sha256', 'syntax_spellings')}
                         for e in coordinate_entries]
    return dict(schema_version='openpine.boxes-spec.1', source_lock_sha256=digest(lock),
                versions=versions, evidence=evidence)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    path = ROOT / 'spec/boxes-profile.json'
    raw = canonical(profile()) + b'\n'
    if parser.parse_args(argv).check:
        assert path.read_bytes() == raw, 'Box profile is stale.'
    else:
        path.write_bytes(raw)
    print('Box profile verified.')


if __name__ == '__main__':
    main()
