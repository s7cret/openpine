"""Additive visual bands profile; imported specification files stay immutable."""
import argparse
from tools.specification import ROOT
from packages.contracts.bands import policy,apis
from packages.contracts.wire import read_json,digest,canonical

def profile():
    lock=read_json(ROOT/'spec/lock.json');versions={};evidence={}
    for v in range(1,7):
        r=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json')
        versions[str(v)]={'policy':policy(v),'apis':apis(v),'reference_sha256':digest(r)}
        evidence[str(v)]=[{k:e[k] for k in ('entry_id','basis','source_record_sha256','sources')} for e in r['entries'] if e['name'] in ('input','input.color','nz','plot','hline','fill')]
    return {'schema_version':'openpine.visual-bands-spec.1','source_lock_sha256':digest(lock),'versions':versions,'evidence':evidence,
        'review':{'early_versions':'color inputs unavailable v1-v3; v1/v2 nz and fill gap behavior reconstructed; first three fill arguments only',
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
