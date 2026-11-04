"""Bind coordinate constructors and constants to the pinned Pine references."""
import argparse

from packages.contracts.drawing_xloc import apis, constants
from packages.contracts.wire import canonical, digest, read_json
from tools.specification import ROOT


def profile():
    lock = read_json(ROOT / 'spec/lock.json')
    versions, evidence = {}, {}
    for version in range(1, 7):
        reference = read_json(ROOT / lock['snapshot'] / f'reference/v{version}.json')
        tokens = constants(version)
        signatures = apis(version)
        by_name = {api['name']: {p['name'] for p in api['parameters']} for api in signatures}
        selected = []
        for entry in reference['entries']:
            name = entry['name']
            parameters = {p['name']: p for p in entry['parameters']}
            if name in tokens:
                assert entry['result_type_spellings'] == ['const string']
            elif name in by_name and by_name[name] <= parameters.keys():
                assert parameters['xloc']['default_claim']['spellings'] == ['xloc.bar_index'], (version, name)
            else:
                continue
            selected.append(entry)
        assert {entry['name'] for entry in selected} == set(tokens) | by_name.keys(), version
        if version < 4:
            assert not {'xloc.bar_index', 'xloc.bar_time'} & {entry['name'] for entry in reference['entries']}
        versions[str(version)] = dict(apis=signatures, constants=list(tokens), reference_sha256=digest(reference))
        evidence[str(version)] = [{k: e[k] for k in ('name', 'entry_id', 'basis', 'source_record_sha256', 'syntax_spellings')}
                                  for e in selected]
    return dict(schema_version='openpine.drawing-xloc-spec.1', source_lock_sha256=digest(lock),
                versions=versions, evidence=evidence)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    path = ROOT / 'spec/drawing-xloc-profile.json'
    raw = canonical(profile()) + b'\n'
    if parser.parse_args(argv).check:
        assert path.read_bytes() == raw, 'Drawing coordinate profile is stale.'
    else:
        path.write_bytes(raw)
    print('Drawing coordinate profile verified.')


if __name__ == '__main__':
    main()
