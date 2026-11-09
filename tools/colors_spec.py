"""Reproduce the additive color profile from the pinned versioned reference."""
import argparse
from tools.specification import ROOT
from packages.contracts.colors import (APPEARANCE_SUFFIX, ARCHIVE_STYLE_FIRST, LINE_STYLES,
                                       PLOT_LINE_STYLE, apis, constants, policy)
from packages.contracts import visual_display
from packages.contracts.wire import read_json,digest,canonical

def profile():
    lock=read_json(ROOT/'spec/lock.json');versions={};evidence={}
    for v in range(1,7):
        ref=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json')
        signatures=apis(v)
        assert_overlay(ref,'plot',signatures,v)
        assert_window(ref,'plot',signatures,v)
        assert_plot_appearance(ref,signatures,v)
        assert_display(ref,signatures,v)
        if v>=3:
            assert any(p['name']=='offset' and p['default_claim']['spellings']==['0']
                       for entry in ref['entries'] if entry['name']=='plot' for p in entry['parameters'])
        versions[str(v)]={'policy':policy(v),'apis':signatures,'reference_sha256':digest(ref)}
        evidence[str(v)]=[{k:e[k] for k in ('entry_id','basis','source_record_sha256','sources')} for e in ref['entries']
            if e['name']=='color' or e['name'].startswith(('color.','plot.style_','plot.linestyle_')) or e['name'] in visual_display.constants(v)
            or e['name'] in ('plot','red','green','blue','line','histogram','areabr')]
    return {'schema_version':'openpine.colors-spec.1','release':'pine-colors-20261103.1','source_lock_sha256':digest(lock),
        'versions':versions,'evidence':evidence,'review':{'external_execution_verified':False,
        'sources':['https://www.tradingview.com/pine-script-docs/visuals/colors/',
            'https://www.tradingview.com/pine-script-docs/migration-guides/to-pine-version-6/#color-changes'],
        'legacy':'v1-v3 palette and HEX availability reconstructed; v2 constructor inferred from version example',
        'scope':'typed colors and plot presentation; unchanged request and graphics-object profiles'}}

def assert_overlay(reference,name,signatures,version):
    from packages.contracts.visual_panes import ABI_SUFFIX, FLAG
    claims=[p for e in reference['entries'] if e['name']==name for p in e.get('parameters',[]) if p['name']==FLAG]
    variants=[api for api in signatures if api['name']==name and api['id'].endswith(ABI_SUFFIX)]
    assert bool(claims)==bool(variants)==(version>=5), (name,version)
    for p in claims:
        assert p['type_spellings']==['const bool'] and p['default_claim']['spellings']==['false'],(name,version)
    for api in variants:
        assert api['parameters'][-1]==dict(name=FLAG,types=['bool'],qualifier='const') and api['defaults'][-1] is False


def assert_plot_appearance(reference, signatures, version):
    record, = (e for e in reference['entries'] if e['name']=='plot' and e['category']=='functions')
    pinned={p['name']:p for p in record['parameters']}
    defaults={'trackprice':'false','histbase':'0.0','join':'false'}
    for name,value in defaults.items():
        assert pinned[name]['default_claim']['spellings']==[value],(name,version)
    variants=[a for a in signatures if APPEARANCE_SUFFIX in a['id'] and not a['id'].endswith(visual_display.ABI_SUFFIX)]
    assert len(variants)==(2 if version<3 else 1)
    for api in variants:
        names=[p['name'] for p in api['parameters']]
        assert set(defaults)<=set(names)
        if version<3:
            prefix='plot(series, title, color, style, linewidth' if api['id'].endswith(ARCHIVE_STYLE_FIRST) else 'plot(series: series, title: string [optional], color: color_series [optional], linewidth:'
            assert any(s.startswith(prefix) for s in record['syntax_spellings'])
        assert ('linestyle' in names)==(version==6)
    entries={e['name'] for e in reference['entries'] if e['category']=='constants'}
    area='areabr' if version<4 else 'plot.style_areabr'
    assert area in entries and area in constants(version)
    for name in ('plot.style_steplinebr','plot.style_stepline_diamond'):
        assert (name in entries)==(name in constants(version))==(version>=5)
    assert ('linestyle' in pinned)==(version==6)
    if version==6:
        assert pinned['linestyle']['type_spellings']==[f'input {PLOT_LINE_STYLE}',f'const {PLOT_LINE_STYLE}']
        assert pinned['linestyle']['default_claim']['spellings']==['plot.linestyle_solid']
        assert all(f'plot.linestyle_{s}' in entries for s in LINE_STYLES)


def assert_window(reference,name,signatures,version):
    from packages.contracts.visual_panes import WINDOW, WINDOW_SUFFIX
    entries=[e for e in reference['entries'] if e['name']==name]
    claims=[p for e in entries for p in e['parameters'] if p['name']==WINDOW]
    variants=[a for a in signatures if a['name']==name and WINDOW_SUFFIX in a['id']]
    assert bool(variants)==(version>=3), (name,version)
    if version<3:
        assert not claims, (name,version)
    if variants:
        expected=['const integer'] if version==3 else ['integer'] if version==4 else ['input int','const int']
        assert claims and all(p['type_spellings']==expected for p in claims), (name,version)
        for api in variants:
            assert dict(name=WINDOW,types=['int'],qualifier='const' if version==3 else 'input') in api['parameters']
            if name=='fill':
                assert api['operation']=='bands.fill_plot'
                assert any(s.startswith('fill(plot1, plot2, color,') and WINDOW in s
                           for entry in entries for s in entry['syntax_spellings'])
                assert all(WINDOW not in s for entry in entries for s in entry['syntax_spellings']
                           if s.startswith('fill(hline1, hline2,'))


def assert_display(reference, signatures, version):
    field, suffix = visual_display.FIELD, visual_display.ABI_SUFFIX
    extended_suffix = visual_display.DESTINATION_SUFFIX
    entries = [entry for entry in reference['entries'] if entry['name'] in visual_display.MINIMUM_VERSION]
    constants = {entry['name'] for entry in reference['entries'] if entry['category']=='constants'}
    assert constants.intersection((*visual_display.VALUES, *visual_display.DESTINATION_TOKENS))==set(visual_display.constants(version))
    extended = {api['id'].removesuffix(extended_suffix):api for api in signatures if api['id'].endswith(extended_suffix)}
    variants = {api['id'].removesuffix(suffix):api for api in signatures
                if api['id'].endswith(suffix) and not api['id'].endswith(extended_suffix)}
    for base in (api for api in signatures if not api['id'].endswith(suffix)):
        names = {p['name'] for p in base['parameters']}
        claims = []
        for entry in entries:
            if entry['name'] != base['name']:
                continue
            pinned = {p['name']:p for p in entry['parameters']}
            if base['name']=='fill' and (('top_value' in pinned)!=('top_value' in names)
                    or not names.intersection({'plot1','hline1'}) <= pinned.keys()):
                continue
            if field in pinned:
                claims.append(pinned[field])
        extension = variants.pop(base['id'], None)
        assert bool(claims)==(extension is not None), (base['id'],version)
        if extension is not None:
            assert len(claims)==1 and claims[0]['default_claim']['spellings']==[visual_display.DEFAULT]
            types = claims[0]['type_spellings']
            if version==4:
                assert types==['integer']
            else:
                assert {'const plot_simple_display','input plot_simple_display'}<=set(types)
            param = dict(name=field, types=[visual_display.DISPLAY_TYPE], qualifier='input')
            assert extension == dict(base, id=base['id']+suffix, parameters=[*base['parameters'],param],
                                     defaults=[*base['defaults'],visual_display.DEFAULT])
        destinations = extended.pop(base['id'], None)
        assert (destinations is not None)==(version>=5 and base['operation'] in visual_display.DESTINATION_OPERATIONS)
        if destinations is not None:
            assert len(claims)==1 and {'const plot_display','input plot_display'}<=set(claims[0]['type_spellings'])
            param = dict(name=field, types=[visual_display.DESTINATION_TYPE], qualifier='input')
            assert destinations == dict(base, id=base['id']+extended_suffix, parameters=[*base['parameters'],param],
                                        defaults=[*base['defaults'],visual_display.DEFAULT])
    assert not variants and not extended


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    target=ROOT/'spec/colors-profile.json';raw=canonical(profile())+b'\n'
    if a.check:assert target.read_bytes()==raw,'Color profile differs.'
    else:target.write_bytes(raw)
    print('Color profile verified.')
if __name__=='__main__':main()
