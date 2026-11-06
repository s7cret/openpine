"""Pin vertical label modes and setters to the imported references."""
import argparse

from packages.contracts.label_styles import LOCATIONS, PRICE
from packages.contracts.label_yloc import CONSTRUCTORS, SET_MODE, SET_PRICE, apis, evidence_version
from packages.contracts.wire import canonical, digest, read_json
from tools.specification import ROOT


def profile():
    lock = read_json(ROOT / 'spec/lock.json')
    versions, evidence = {}, {}
    for version in range(1, 7):
        reference = read_json(ROOT / lock['snapshot'] / f'reference/v{version}.json')
        signatures = apis(version)
        locations = LOCATIONS if version >= 4 else ()
        rows = [row for row in reference['entries']
                if row['category'] == 'functions' and row['name'] in ('label.new', SET_MODE, SET_PRICE)
                or row['category'] == 'constants' and row['name'] in locations]
        assert {row['name'] for row in rows} == {api['name'] for api in signatures} | set(locations), version
        assert sum(row['name'] == 'label.new' for row in rows) == (2 if version >= 5 else int(version == 4))
        for api in signatures:
            names = [param['name'] for param in api['parameters']]
            row, = (row for row in rows if row['name'] == api['name']
                    and [p['name'] for p in row['parameters'][:len(names)]] == names)
            constructor = api['operation'] in CONSTRUCTORS
            result = 'series[label]' if version == 4 else 'series label'
            assert row['result_type_spellings'] == [result if constructor else 'void']
            for param, pinned in zip(api['parameters'], row['parameters'][:len(names)], strict=True):
                name = param['name']
                if name not in ('id', 'y', 'yloc'):
                    continue
                types = {s.split()[-1] for s in pinned['type_spellings']}
                assert types == {'series'} if name == 'y' and version == 4 else set(param['types']) <= types
                assert param['qualifier'] == 'series'
                if name == 'yloc' and constructor:
                    assert pinned['default_claim']['spellings'] == [PRICE]
        versions[str(version)] = evidence_version(reference, version)
        evidence[str(version)] = [
            {key: row[key] for key in ('name', 'entry_id', 'basis', 'source_record_sha256', 'syntax_spellings')}
            for row in rows
        ]
    return dict(schema_version='openpine.label-yloc-spec.1', source_lock_sha256=digest(lock),
                versions=versions, evidence=evidence)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    path = ROOT / 'spec/label-yloc-profile.json'
    raw = canonical(profile()) + b'\n'
    if parser.parse_args(argv).check:
        assert path.read_bytes() == raw, 'Label vertical-coordinate profile is stale.'
    else:
        path.write_bytes(raw)
    print('Label vertical-coordinate profile verified.')


if __name__ == '__main__':
    main()
