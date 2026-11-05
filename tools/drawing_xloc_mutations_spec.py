"""Pin drawing coordinate setter and getter signatures."""
import argparse

from packages.contracts.drawing_xloc_mutations import OPERATIONS, apis
from packages.contracts.wire import canonical, digest, read_json
from tools.specification import ROOT


def profile():
    lock = read_json(ROOT / 'spec/lock.json')
    versions, evidence = {}, {}
    for version in range(1, 7):
        reference = read_json(ROOT / lock['snapshot'] / f'reference/v{version}.json')
        rows = {row['name']: row for row in reference['entries']
                if row['category'] == 'functions' and row['name'] in OPERATIONS}
        signatures = apis(version)
        assert set(rows) == {api['name'] for api in signatures}, version
        for api in signatures:
            row = rows[api['name']]
            for parameter, pinned in zip(api['parameters'], row['parameters'], strict=True):
                base, = parameter['types']
                spellings = {s.removeprefix('series[').removesuffix(']').split()[-1]
                             for s in pinned['type_spellings']}
                assert parameter['name'] == pinned['name'], (version, api, pinned)
                assert base in spellings or (version == 4 and base == 'int' and spellings == {'series'}), pinned
            result = 'series[integer]' if version == 4 else 'series int'
            assert row['result_type_spellings'] == ['void' if api['result'] == 'void' else result], row
        versions[str(version)] = dict(apis=signatures, reference_sha256=digest(reference))
        evidence[str(version)] = [{k: row[k] for k in ('name', 'entry_id', 'basis', 'source_record_sha256', 'syntax_spellings')}
                                  for row in rows.values()]
    return dict(schema_version='openpine.drawing-xloc-mutations-spec.1', source_lock_sha256=digest(lock),
                versions=versions, evidence=evidence)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    path = ROOT / 'spec/drawing-xloc-mutations-profile.json'
    raw = canonical(profile()) + b'\n'
    if parser.parse_args(argv).check:
        assert path.read_bytes() == raw, 'Drawing coordinate mutation and read profile is stale.'
    else:
        path.write_bytes(raw)
    print('Drawing coordinate mutation and read profile verified.')


if __name__ == '__main__':
    main()
