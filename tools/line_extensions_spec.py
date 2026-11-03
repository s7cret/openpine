"""Bind the extension overload and read operation to pinned Pine evidence."""
import argparse

from packages.contracts.line_extensions import NAMES, apis, constants
from packages.contracts.wire import canonical, digest, read_json
from tools.specification import ROOT


def profile():
    lock = read_json(ROOT / 'spec/lock.json')
    versions, evidence = {}, {}
    for version in range(1, 7):
        key = str(version)
        reference = read_json(ROOT / lock['snapshot'] / f'reference/v{version}.json')
        modes = list(constants(version))
        selected = {name: [] for name in (*NAMES, *modes)}
        for entry in reference['entries']:
            if entry['name'] in selected:
                selected[entry['name']].append(entry)
        if version >= 4:
            for api in apis(version):
                names = [p['name'] for p in api['parameters']]
                if api['name'] == 'line.new':
                    # xloc precedes the named extend argument in Pine.
                    names.insert(4, 'xloc')
                matches = [e for e in selected[api['name']]
                           if [p['name'] for p in e['parameters'][:len(names)]] == names]
                assert matches, (version, api['name'])
                if api['name'] == 'line.new':
                    assert all(e['parameters'][5]['default_claim']['spellings'] == ['extend.none'] for e in matches)
                selected[api['name']] = matches
            assert all(selected[name] for name in modes)
        else:
            assert not any(selected.values())
        versions[key] = dict(apis=apis(version), constants=modes, reference_sha256=digest(reference))
        evidence[key] = [{k: entry[k] for k in ('name', 'entry_id', 'basis', 'source_record_sha256', 'syntax_spellings')}
                         for records in selected.values() for entry in records]
    return dict(schema_version='openpine.line-extensions-spec.1', source_lock_sha256=digest(lock),
                versions=versions, evidence=evidence)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    path = ROOT / 'spec/line-extensions-profile.json'
    raw = canonical(profile()) + b'\n'
    if parser.parse_args(argv).check:
        assert path.read_bytes() == raw, 'Line extension profile is stale.'
    else:
        path.write_bytes(raw)
    print('Line extension profile verified.')


if __name__ == '__main__':
    main()
