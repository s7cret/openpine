"""Pin box border operations and defaults to the imported Pine reference."""
import argparse

from packages.contracts.box_borders import CONSTRUCTORS, EXTEND_TOKENS, SETTERS, STYLE_TOKENS, apis, evidence_version
from packages.contracts.wire import canonical, digest, read_json
from tools.specification import ROOT

DEFAULTS = dict(border_color='color.blue', border_width='1', border_style='line.style_solid',
                extend='extend.none', xloc='xloc.bar_index', bgcolor='color.blue')


def profile():
    lock = read_json(ROOT / 'spec/lock.json')
    versions, evidence = {}, {}
    for version in range(1, 7):
        reference = read_json(ROOT / lock['snapshot'] / f'reference/v{version}.json')
        signatures = apis(version)
        tokens = (*STYLE_TOKENS, *EXTEND_TOKENS) if version >= 4 else ()
        selected = [row for row in reference['entries']
                    if row['category'] == 'functions' and row['name'] in ('box.new', *SETTERS)
                    or row['category'] == 'constants' and row['name'] in (*STYLE_TOKENS, *EXTEND_TOKENS)]
        assert {row['name'] for row in selected} == {a['name'] for a in signatures} | set(tokens), version
        assert sum(row['name'] == 'box.new' for row in selected) == (2 if version >= 5 else int(version == 4))
        for api in signatures:
            names = [p['name'] for p in api['parameters']]
            row, = (row for row in selected if row['name'] == api['name']
                    and [p['name'] for p in row['parameters'][:len(names)]] == names)
            result = 'series[box]' if version == 4 else 'series box'
            assert row['result_type_spellings'] == [result if api['operation'] in CONSTRUCTORS else 'void']
            for parameter, pinned in zip(api['parameters'], row['parameters'][:len(names)], strict=True):
                base, = parameter['types']
                spellings = {s.removeprefix('series[').removesuffix(']').split()[-1].replace('integer', 'int')
                             for s in pinned['type_spellings']}
                assert base in spellings, (version, pinned)
                if api['operation'] in CONSTRUCTORS and parameter['name'] in DEFAULTS:
                    assert pinned['default_claim']['spellings'] == [DEFAULTS[parameter['name']]], (version, pinned)
        versions[str(version)] = evidence_version(reference, version)
        evidence[str(version)] = [
            {key: row[key] for key in ('name', 'entry_id', 'basis', 'source_record_sha256', 'syntax_spellings')}
            for row in selected
        ]
    return dict(schema_version='openpine.box-borders-spec.1', source_lock_sha256=digest(lock),
                versions=versions, evidence=evidence)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    path = ROOT / 'spec/box-borders-profile.json'
    raw = canonical(profile()) + b'\n'
    if parser.parse_args(argv).check:
        assert path.read_bytes() == raw, 'Box border profile is stale.'
    else:
        path.write_bytes(raw)
    print('Box border profile verified.')


if __name__ == '__main__':
    main()
