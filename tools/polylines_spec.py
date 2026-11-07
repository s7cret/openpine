"""Pin straight polyline signatures and the underlying point-array evidence."""
import argparse

from packages.contracts.chart_points import POINT_ARRAY, POINT_ARRAY_OPS
from packages.contracts.colors import constants
from packages.contracts.polylines import apis, policy
from packages.contracts.wire import canonical, digest, read_json
from tools.specification import ROOT


def profile():
    lock = read_json(ROOT / 'spec/lock.json')
    versions, evidence = {}, {}
    arrays = {'array.new<type>' if op == 'new' else 'na' if op == 'is_na' else f'array.{op}'
              for op in POINT_ARRAY_OPS}
    for version in range(1, 7):
        reference = read_json(ROOT / lock['snapshot'] / f'reference/v{version}.json')
        rows = reference['entries']
        selected = [row for row in rows if row['category'] in ('types', 'functions') and row['name'] in
                    ('polyline', 'polyline.new', 'polyline.delete')]
        signatures = apis(version)
        assert {row['name'] for row in selected} == ({'polyline', 'polyline.new', 'polyline.delete'} if signatures else set())
        for api in signatures:
            row = next(row for row in selected if row['name'] == api['name'])
            names = [parameter['name'] for parameter in api['parameters']]
            params = [parameter for parameter in row['parameters'] if parameter['name'] in names]
            for parameter, pinned in zip(api['parameters'], params, strict=True):
                base, = parameter['types']
                spelling = 'array<chart.point>' if base == POINT_ARRAY else base
                assert parameter['name'] == pinned['name'] and spelling in {x.split()[-1] for x in pinned['type_spellings']}
                if parameter['name'] == 'force_overlay':
                    assert parameter['qualifier'] == 'const' and pinned['type_spellings'] == ['const bool'], pinned
            first_optional = len(params) - len(api['defaults'])
            for pinned, value in zip(params[first_optional:], api['defaults'], strict=True):
                spelling = 'na' if value is None else 'false' if value is False else (
                    'color.blue' if value == constants(version)['color.blue'][0] else str(value))
                assert pinned['default_claim']['spellings'] == [spelling]
        if signatures:
            array_rows = [row for row in rows if row['category'] == 'functions' and row['name'] in arrays]
            assert {row['name'] for row in array_rows} == arrays
            selected.extend(array_rows)
        versions[str(version)] = dict(apis=signatures, reference_sha256=digest(reference))
        evidence[str(version)] = [{k: row[k] for k in ('name', 'entry_id', 'basis', 'source_record_sha256', 'syntax_spellings')}
                                  for row in selected]
    return dict(schema_version='openpine.polylines-spec.1', source_lock_sha256=digest(lock), policy=policy(),
                versions=versions, evidence=evidence)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    path = ROOT / 'spec/polylines-profile.json'
    raw = canonical(profile()) + b'\n'
    if parser.parse_args(argv).check:
        assert path.read_bytes() == raw, 'Polyline profile is stale.'
    else:
        path.write_bytes(raw)
    print('Polyline profile verified.')


if __name__ == '__main__':
    main()
