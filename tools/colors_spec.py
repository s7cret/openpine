"""Reproduce the additive color profile from the pinned versioned reference."""
import argparse
from tools.specification import ROOT
from packages.contracts.colors import policy,apis
from packages.contracts.wire import read_json,digest,canonical

def profile():
    lock=read_json(ROOT/'spec/lock.json');versions={};evidence={}
    for v in range(1,7):
        ref=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json')
        versions[str(v)]={'policy':policy(v),'apis':apis(v),'reference_sha256':digest(ref)}
        evidence[str(v)]=[{k:e[k] for k in ('entry_id','basis','source_record_sha256','sources')} for e in ref['entries']
            if e['name']=='color' or e['name'].startswith('color.') or e['name'] in ('plot','red','green','blue','line','histogram')]
    return {'schema_version':'openpine.colors-spec.1','release':'pine-colors-20261103.1','source_lock_sha256':digest(lock),
        'versions':versions,'evidence':evidence,'review':{'external_execution_verified':False,
        'sources':['https://www.tradingview.com/pine-script-docs/visuals/colors/',
            'https://www.tradingview.com/pine-script-docs/migration-guides/to-pine-version-6/#color-changes'],
        'legacy':'v1-v3 palette and HEX availability reconstructed; v2 constructor inferred from version example',
        'scope':'typed colors and plot presentation; unchanged request and graphics-object profiles'}}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    target=ROOT/'spec/colors-profile.json';raw=canonical(profile())+b'\n'
    if a.check:assert target.read_bytes()==raw,'Color profile differs.'
    else:target.write_bytes(raw)
    print('Color profile verified.')
if __name__=='__main__':main()
