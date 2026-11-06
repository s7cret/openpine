"""Pin the box text ABI to the imported Pine v1–v6 reference."""
import argparse

from packages.contracts import box_text, label_text
from packages.contracts.colors import constants as colors
from packages.contracts.markers import SIZES
from packages.contracts.wire import canonical, digest, read_json
from tools.specification import ROOT

DEFAULT_CLAIMS = {
    'text': [], 'text_size': ['size.auto'], 'text_color': ['color.black'],
    'text_halign': ['text.align_center'], 'text_valign': ['text.align_center'],
    'text_wrap': ['text.wrap_none'], 'text_font_family': ['font.family_default'],
    'force_overlay': ['false'], 'text_formatting': ['text.format_none'],
}


def profile():
    lock = read_json(ROOT / 'spec/lock.json')
    versions, evidence = {}, {}
    for version in range(1, 7):
        reference = read_json(ROOT / lock['snapshot'] / f'reference/v{version}.json')
        signatures = box_text.apis(version)
        tokens = (set(box_text.tokens(version)) | set(label_text.constants(version))
                  | {f'size.{size}' for size in SIZES}) if version >= 5 else set()
        rows = [row for row in reference['entries']
                if row['category'] == 'functions' and row['name'] in ('box.new', *box_text.SETTERS)
                or row['category'] == 'constants' and row['name'] in tokens]
        expected = {api['name'] for api in signatures} | tokens | ({'box.new'} if version == 4 else set())
        assert {row['name'] for row in rows} == expected, version
        assert sum(row['name'] == 'box.new' for row in rows) == (2 if version >= 5 else int(version == 4))
        if version == 4:
            row, = rows
            assert not ({p['name'] for p in row['parameters']} & box_text.VERSIONS.keys())
        if version >= 5:
            assert colors(version)['color.black'][0] == box_text.defaults(5)['text_color']
        for api in signatures:
            names = [param['name'] for param in api['parameters']]
            row, = (row for row in rows if row['name'] == api['name']
                    and [p['name'] for p in row['parameters']] == names)
            constructor = api['operation'] in box_text.CONSTRUCTORS
            assert row['result_type_spellings'] == ['series box' if constructor else 'void']
            for param, pinned in zip(api['parameters'], row['parameters'], strict=True):
                name = param['name']
                if name not in box_text.VERSIONS:
                    continue
                assert {f"{param['qualifier']} {base}" for base in param['types']} <= set(pinned['type_spellings']), pinned
                if constructor:
                    assert pinned['default_claim']['spellings'] == DEFAULT_CLAIMS[name], pinned
                else:
                    assert pinned['requiredness_claim'] == 'required', pinned
        versions[str(version)] = box_text.evidence_version(reference, version)
        evidence[str(version)] = [
            {key: row[key] for key in ('name', 'entry_id', 'basis', 'source_record_sha256', 'syntax_spellings')}
            for row in rows
        ]
    return dict(schema_version='openpine.box-text-spec.1', source_lock_sha256=digest(lock),
                versions=versions, evidence=evidence)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    path = ROOT / 'spec/box-text-profile.json'
    raw = canonical(profile()) + b'\n'
    if parser.parse_args(argv).check:
        assert path.read_bytes() == raw, 'Box text profile is stale.'
    else:
        path.write_bytes(raw)
    print('Box text profile verified.')


if __name__ == '__main__':
    main()
