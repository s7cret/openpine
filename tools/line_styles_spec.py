"""Pin line appearance signatures and constants to the imported Pine reference."""
import argparse

from packages.contracts.line_styles import CONSTRUCTORS, apis, constants, evidence_version
from packages.contracts.wire import canonical, digest, read_json
from tools.specification import ROOT


DEFAULTS_SOURCE = 'https://www.tradingview.com/pine-script-docs/visuals/lines-and-boxes/#creating-lines'


def profile():
    lock = read_json(ROOT / 'spec/lock.json')
    versions, evidence = {}, {}
    for version in range(1, 7):
        reference = read_json(ROOT / lock['snapshot'] / f'reference/v{version}.json')
        signatures = apis(version)
        tokens = constants(version)
        selected = [row for row in reference['entries']
                    if row['category'] == 'functions' and row['name'] in ('line.new', 'line.set_color', 'line.set_style', 'line.set_width')
                    or row['category'] == 'constants' and row['name'] in constants(4)]
        assert {row['name'] for row in selected} == {a['name'] for a in signatures} | tokens.keys(), version
        assert sum(row['name'] == 'line.new' for row in selected) == (2 if version >= 5 else int(version == 4))
        for row in selected:
            if row['name'] in tokens:
                assert row['result_type_spellings'] == ['const string'], row
        for api in signatures:
            names = [p['name'] for p in api['parameters']]
            row, = (row for row in selected if row['name'] == api['name']
                    and [p['name'] for p in row['parameters'][:len(names)]] == names)
            result = 'series[line]' if version == 4 else 'series line'
            assert row['result_type_spellings'] == [result if api['operation'] in CONSTRUCTORS else 'void']
            for parameter, pinned in zip(api['parameters'], row['parameters'][:len(names)], strict=True):
                base, = parameter['types']
                spellings = {s.split()[-1].replace('integer', 'int') for s in pinned['type_spellings']}
                assert base in spellings or (version == 4 and base in ('int', 'float') and spellings == {'series'}), pinned
                if parameter['name'] in ('xloc', 'extend'):
                    assert pinned['default_claim']['spellings'] == [dict(xloc='xloc.bar_index', extend='extend.none')[parameter['name']]]
                if parameter['name'] == 'force_overlay':
                    assert parameter['qualifier'] == 'const' and pinned['type_spellings'] == ['const bool'], pinned
                    assert api['defaults'][-1] is False and pinned['default_claim']['spellings'] == ['false'], pinned
        versions[str(version)] = evidence_version(reference, version)
        evidence[str(version)] = [
            {key: row[key] for key in ('name', 'entry_id', 'basis', 'source_record_sha256', 'syntax_spellings')}
            for row in selected
        ]
    return dict(schema_version='openpine.line-styles-spec.1', source_lock_sha256=digest(lock),
                defaults_source=DEFAULTS_SOURCE, versions=versions, evidence=evidence)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    path = ROOT / 'spec/line-styles-profile.json'
    raw = canonical(profile()) + b'\n'
    if parser.parse_args(argv).check:
        assert path.read_bytes() == raw, 'Line styles profile is stale.'
    else:
        path.write_bytes(raw)
    print('Line styles profile verified.')


if __name__ == '__main__':
    main()
