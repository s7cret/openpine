"""Additive visual bands profile; imported specification files stay immutable."""
import argparse
from tools.specification import ROOT
from packages.contracts.bands import policy,apis
from packages.contracts.wire import read_json,digest,canonical
from tools.colors_spec import assert_overlay, assert_window

def profile():
    lock=read_json(ROOT/'spec/lock.json');versions={};evidence={}
    for v in range(1,7):
        r=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json')
        # Independently retain explicit pinned claims rather than only
        # checking a generated profile against the implementation that made it.
        if v>=4:
            claims=[p for e in r['entries'] if e['name']=='fill' for p in e.get('parameters',[]) if p['name']=='fillgaps']
            assert claims and all(p['default_claim']['state']=='explicit_value' and p['default_claim']['spellings']==['false'] for p in claims)
            assert policy(v)['fillgaps_default'] is False,'Pinned fillgaps default is false.'
            assert all(p['qualifier']=='const' for a in apis(v) if a['name']=='fill' for p in a['parameters'] if p['name']=='fillgaps')
        if v==3:
            claim=[p for e in r['entries'] if e['name']=='hline' for p in e.get('parameters',[]) if p['name']=='color']
            assert claim and all(p['type_spellings']==['const color'] for p in claim)
            assert next(p for a in apis(v) if a['name']=='hline' for p in a['parameters'] if p['name']=='color')['qualifier']=='const'
        signatures=apis(v)
        assert_overlay(r,'plot',signatures,v)
        assert_window(r,'plot',signatures,v)
        assert_window(r,'fill',signatures,v)
        versions[str(v)]={'policy':policy(v),'apis':signatures,'reference_sha256':digest(r)}
        evidence[str(v)]=[{k:e[k] for k in ('entry_id','basis','source_record_sha256','sources')} for e in r['entries'] if e['name'] in ('input','input.color','nz','plot','hline','fill')]
    return {'schema_version':'openpine.visual-bands-spec.1','source_lock_sha256':digest(lock),'versions':versions,'evidence':evidence,
        'review':{'early_versions':'color inputs unavailable v1-v3; v1/v2 nz reconstructed; pre-v4 gaps=false inferred from the first explicit v4 default; first three fill arguments only',
        'known_gap_rule':'v4-v6 default false; const bool required by v5/v6, conservatively const in v4',
        'undocumented_defaults':{'basis':'Pinned reference does not document these defaults; explicit OpenPine choices, external equivalence unverified',
            'hline_color':'#2196F3FF','hline_style':'solid','title':'','legacy_fill_transp':90},
        'nz_default':'transparent black #00000000; not color.black',
        'sources':['https://www.tradingview.com/pine-script-docs/language/type-system/','https://www.tradingview.com/pine-script-docs/visuals/fills/',
        'https://www.tradingview.com/pine-script-docs/migration-guides/to-pine-version-5/',
        'https://www.tradingview.com/blog/en/create-color-inputs-in-pine-20751/'],'external_execution_verified':False}}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    path=ROOT/'spec/visual-bands-profile.json';raw=canonical(profile())+b'\n'
    if a.check:assert path.read_bytes()==raw,'Visual bands profile differs.'
    else:path.write_bytes(raw)
    print('Visual bands profile verified.')
if __name__=='__main__':main()
