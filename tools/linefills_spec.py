"""Bind the linefill ABI to the pinned reference for each Pine version."""
import argparse

from packages.contracts.linefills import OPERATIONS, apis
from packages.contracts.wire import canonical, digest, read_json
from tools.specification import ROOT


def profile():
    lock = read_json(ROOT / 'spec/lock.json')
    versions, evidence = {}, {}
    for version in range(1, 7):
        key = str(version)
        reference = read_json(ROOT / lock['snapshot'] / f'reference/v{version}.json')
        entries = [e for e in reference['entries'] if e['name'] in OPERATIONS]
        signatures = apis(version)
        assert {e['name'] for e in entries} == {a['name'] for a in signatures}, version
        by_name = {a['name']: a for a in signatures}
        for entry in entries:
            name = entry['name']
            assert tuple(p['name'] for p in entry['parameters']) == tuple(p for p, _ in OPERATIONS[name])
            result = by_name[name]['result']
            assert entry['result_type_spellings'] == [result if result == 'void' else 'series ' + result]
        versions[key] = dict(apis=signatures, reference_sha256=digest(reference))
        evidence[key] = [{k: e[k] for k in ('name', 'entry_id', 'basis', 'source_record_sha256', 'syntax_spellings')}
                         for e in entries]
    return dict(schema_version='openpine.linefills-spec.1', source_lock_sha256=digest(lock),
                versions=versions, evidence=evidence)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    path = ROOT / 'spec/linefills-profile.json'
    raw = canonical(profile()) + b'\n'
    if parser.parse_args(argv).check:
        assert path.read_bytes() == raw, 'Linefill profile is stale.'
    else:
        path.write_bytes(raw)
    print('Linefill profile verified.')


if __name__ == '__main__':
    main()
