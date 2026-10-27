"""Pin the lower request policy to saved v1-v6 catalogs, without rewriting bundles."""
import argparse
from packages.contracts.lower_tf import policy,PARAMETERS
from packages.contracts.wire import canonical,digest,read_json
from tools.specification import ROOT

def expected():
    versions={}
    for v in range(1,7):
        catalog=read_json(ROOT/f'spec/bundles/pine-spec-20261020.1/reference/v{v}.json')
        entries=[e for e in catalog['entries'] if e['name']=='request.security_lower_tf']
        assert bool(entries)==(v>=5)
        assert all([p['name'] for p in e['parameters']]==list(PARAMETERS) for e in entries)
        versions[str(v)]={'policy':policy(v),'reference_sha256':digest(catalog),'evidence':[
            {k:e[k] for k in ('entry_id','source_record_sha256','parameters','result_type_spellings')} for e in entries]}
    return {'schema_version':'openpine.lower-tf-profile.1','source_lock_sha256':digest(read_json(ROOT/'spec/lock.json')),
        'versions':versions,'decisions':['Pinned historical native-currency contexts only.',
        'Default 100000 is an explicit OpenPine history window, not a resolution of conflicting or missing source defaults.',
        'calc_bars_count selects the last N actual closed rows at the execution end, not future rows beyond a research window; older primary windows produce empty arrays.',
        'Strictly smaller divisible UTC minute pairs in this first profile; no inferred resampling or exchange calendar.',
        'Only closed child values become available, including fill-driven historical recalculations.',
        'ignore_invalid_symbol=true and explicit currency conversion are rejected before starting a job.',
        'No exact TradingView equivalence or live security_lower_tf claim.']}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    path=ROOT/'spec/lower-tf-profile.json';value=expected()
    if a.check:
        if read_json(path)!=value:raise ValueError('Lower request profile differs from its evidence.')
    else:path.write_bytes(canonical(value)+b'\n')
    print('Lower-timeframe request v1-v6 evidence and local profile verified.')
    return 0
if __name__=='__main__':raise SystemExit(main())
