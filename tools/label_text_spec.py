"""Pin the label text ABI and token availability to Pine v1–v6."""
import argparse

from packages.contracts.label_text import CONSTRUCTORS, SETTERS, VERSIONS, apis, constants, evidence_version
from packages.contracts.wire import canonical, digest, read_json
from tools.specification import ROOT

DEFAULT_CLAIMS = {'textalign': ['text.align_center'], 'tooltip': [], 'text_font_family': ['font.family_default'],
                  'force_overlay': ['false'], 'text_formatting': ['text.format_none']}


def profile():
    lock = read_json(ROOT / 'spec/lock.json')
    versions, evidence = {}, {}
    for version in range(1, 7):
        reference = read_json(ROOT / lock['snapshot'] / f'reference/v{version}.json')
        tokens = constants(version)
        signatures = apis(version)
        rows = [row for row in reference['entries']
                if row['category'] == 'functions' and row['name'] in ('label.new', *SETTERS)
                or row['category'] == 'constants' and row['name'] in tokens]
        assert {row['name'] for row in rows} == {api['name'] for api in signatures} | set(tokens), version
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
                if name not in VERSIONS:
                    continue
                types = {s.split()[-1] for s in pinned['type_spellings']}
                assert set(param['types']) <= types
                if version >= 5:
                    assert f"{param['qualifier']} {param['types'][0]}" in pinned['type_spellings']
                if constructor or name == 'text_formatting':
                    assert pinned['default_claim']['spellings'] == DEFAULT_CLAIMS[name]
        versions[str(version)] = evidence_version(reference, version)
        evidence[str(version)] = [
            {key: row[key] for key in ('name', 'entry_id', 'basis', 'source_record_sha256', 'syntax_spellings')}
            for row in rows
        ]
    return dict(schema_version='openpine.label-text-spec.1', source_lock_sha256=digest(lock),
                versions=versions, evidence=evidence)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    path = ROOT / 'spec/label-text-profile.json'
    raw = canonical(profile()) + b'\n'
    if parser.parse_args(argv).check:
        assert path.read_bytes() == raw, 'Label text profile is stale.'
    else:
        path.write_bytes(raw)
    print('Label text profile verified.')


if __name__ == '__main__':
    main()
