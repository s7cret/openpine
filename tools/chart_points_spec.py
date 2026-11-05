"""Pin the chart.point type, signatures and drawing overloads."""
import argparse

from packages.contracts.chart_points import BASE, FIELDS, apis
from packages.contracts.colors import constants
from packages.contracts.wire import canonical, digest, read_json
from tools.specification import ROOT


def profile():
    lock = read_json(ROOT / 'spec/lock.json')
    versions, evidence = {}, {}
    supported_names = {api['name'] for api in apis(5) if api['name'].startswith('chart.point.')} | {BASE}
    for version in range(1, 7):
        reference = read_json(ROOT / lock['snapshot'] / f'reference/v{version}.json')
        rows = reference['entries']
        signatures = apis(version)
        selected = []
        if version < 5:
            assert supported_names.isdisjoint(row['name'] for row in rows), version
        else:
            selected.append(next(row for row in rows if row['name'] == BASE and row['category'] == 'types'))
        for api in signatures:
            parameters = {p['name']: p['types'][0] for p in api['parameters']}
            matches = []
            for row in rows:
                if row['category'] != 'functions' or row['name'] != api['name']:
                    continue
                fields = {p['name']: p for p in row['parameters']}
                if all(name in fields and base in {t.split()[-1] for t in fields[name]['type_spellings']}
                       for name, base in parameters.items()):
                    matches.append(row)
            assert len(matches) == 1, (version, api['operation'])
            row, = matches
            assert api['result'] in {t.split()[-1] for t in row['result_type_spellings']}
            defaults = dict(zip(tuple(parameters)[len(parameters)-len(api['defaults']):], api['defaults'], strict=True))
            for p in row['parameters']:
                if p['name'] not in defaults:
                    continue
                value = defaults[p['name']]
                if type(value) is dict:
                    value = value['reference']
                elif value == constants(version)['color.blue'][0]:
                    value = 'color.blue'
                # Label text uses the empty string in the established OpenPine label profile.
                if value != '':
                    assert p['default_claim']['spellings'] == [value], (version, row['name'], p['name'])
            selected.append(row)
        versions[str(version)] = dict(apis=signatures, fields=[list(field) for field in FIELDS] if signatures else [],
                                      reference_sha256=digest(reference))
        evidence[str(version)] = [
            {k: row[k] for k in ('name', 'entry_id', 'basis', 'source_record_sha256', 'syntax_spellings')}
            for row in selected]
    return dict(schema_version='openpine.chart-points-spec.1', source_lock_sha256=digest(lock),
                versions=versions, evidence=evidence)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    path = ROOT / 'spec/chart-points-profile.json'
    raw = canonical(profile()) + b'\n'
    if parser.parse_args(argv).check:
        assert path.read_bytes() == raw, 'Chart point profile is stale.'
    else:
        path.write_bytes(raw)
    print('Chart point profile verified.')


if __name__ == '__main__':
    main()
