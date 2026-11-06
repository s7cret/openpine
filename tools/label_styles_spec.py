"""Pin label appearance signatures, tokens and documented defaults."""
import argparse

from packages.contracts.label_styles import CONSTRUCTORS, LOCATIONS, SETTERS, SIZES, STYLES, apis, evidence_version
from packages.contracts.wire import canonical, digest, read_json
from tools.specification import ROOT

DEFAULTS = dict(xloc='xloc.bar_index', yloc='yloc.price', style='label.style_label_down', size='size.normal')
TOKENS = (*STYLES, *LOCATIONS, *(f'size.{size}' for size in SIZES))


def profile():
    lock = read_json(ROOT / 'spec/lock.json')
    versions, evidence = {}, {}
    for version in range(1, 7):
        reference = read_json(ROOT / lock['snapshot'] / f'reference/v{version}.json')
        signatures = apis(version)
        tokens = TOKENS if version >= 4 else ()
        selected = [row for row in reference['entries']
                    if row['category'] == 'functions' and row['name'] in ('label.new', *SETTERS)
                    or row['category'] == 'constants' and row['name'] in tokens]
        assert {row['name'] for row in selected} == {api['name'] for api in signatures} | set(tokens), version
        assert sum(row['name'] == 'label.new' for row in selected) == (2 if version >= 5 else int(version == 4))
        for api in signatures:
            constructor = api['operation'] in CONSTRUCTORS
            names = [p['name'] for p in api['parameters']]
            row, = (row for row in selected if row['name'] == api['name']
                    and [p['name'] for p in row['parameters'][:len(names)]] == names)
            result = 'series[label]' if version == 4 else 'series label'
            assert row['result_type_spellings'] == [result if constructor else 'void']
            for parameter, pinned in zip(api['parameters'], row['parameters'][:len(names)], strict=True):
                name = parameter['name']
                types = {s.removeprefix('series[').removesuffix(']').split()[-1] for s in pinned['type_spellings']}
                if version == 4 and constructor and name in ('x', 'y'):
                    assert types == {'series'}
                else:
                    assert set(parameter['types']) <= types, (version, pinned)
                if constructor and name in DEFAULTS:
                    assert pinned['default_claim']['spellings'] == [DEFAULTS[name]], (version, pinned)
                if constructor and name in ('color', 'textcolor'):
                    assert pinned['default_claim']['state'] == 'not_documented', (version, pinned)
                if api['operation'] == 'label.set_size' and name == 'size':
                    assert pinned['default_claim']['spellings'] == ['size.normal' if version == 6 else 'size.auto']
        versions[str(version)] = evidence_version(reference, version)
        evidence[str(version)] = [
            {key: row[key] for key in ('name', 'entry_id', 'basis', 'source_record_sha256', 'syntax_spellings')}
            for row in selected
        ]
    return dict(schema_version='openpine.label-styles-spec.1', source_lock_sha256=digest(lock),
                versions=versions, evidence=evidence)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    path = ROOT / 'spec/label-styles-profile.json'
    raw = canonical(profile()) + b'\n'
    if parser.parse_args(argv).check:
        assert path.read_bytes() == raw, 'Label appearance profile is stale.'
    else:
        path.write_bytes(raw)
    print('Label appearance profile verified.')


if __name__ == '__main__':
    main()
