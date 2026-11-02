"""Reproduce the additive bounded result profile from pinned Wiki records."""
import argparse
from tools.specification import ROOT
from packages.contracts.product_results import policy
from packages.contracts.wire import canonical,digest,read_json

def profile():
    lock=read_json(ROOT/'spec/lock.json');versions={};evidence={}
    for v in range(1,7):
        ref=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json')
        versions[str(v)]={'policy':policy(v),'reference_sha256':digest(ref)}
        evidence[str(v)]=[{k:e[k] for k in ('entry_id','source_record_sha256','sources')} for e in ref['entries'] if any(s in e['entry_id'] for s in ('tuple','loop-return','udt-declaration')) or e['entry_id'].endswith((':for',':while',':for-in-array',':for-in-map',':for-in-matrix'))]
    return {'schema_version':'openpine.product-results-spec.1','release':'pine-product-results-20261102.1',
        'source_lock_sha256':digest(lock),'versions':versions,'evidence':evidence,
        'review':{'external_execution_verified':False,'primary_sources':[
            'https://www.tradingview.com/pine-script-docs/language/type-system/#tuples',
            'https://www.tradingview.com/pine-script-docs/language/loops/',
            'https://www.tradingview.com/pine-script-docs/v5/language/type-system/'],
            'legacy_boundary':'Versioned reconstruction from pinned records; modern docs do not establish v1-v4 equivalence.',
            'supersedes_bounded_exclusions':['object_tuples','object_loop_results','numeric_bool_only_loop_results']}}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    target=ROOT/'spec/product-results-profile.json';raw=canonical(profile())+b'\n'
    if a.check:assert target.read_bytes()==raw,'Product result profile differs.'
    else:target.write_bytes(raw)
    print('Additive product result profile verified.')
if __name__=='__main__':main()
