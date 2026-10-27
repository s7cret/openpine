"""Verify the immutable Wiki evidence and pin the local magnifier model."""
import argparse
from packages.contracts.magnifier import policy
from packages.contracts.wire import canonical,digest,read_json
from tools.specification import ROOT

def expected():
    versions={}
    for v in range(1,7):
        data=read_json(ROOT/f'spec/bundles/pine-spec-20261020.1/reference/v{v}.json')
        evidence=[{'entry_id':r['entry_id'],'parameters':[p['name'] for p in r.get('parameters',[]) if p['name']=='use_bar_magnifier']}
            for r in data['entries'] if r.get('name')=='strategy']
        assert any(r['parameters'] for r in evidence)==policy(v)['source_flag']
        versions[str(v)]={'policy':policy(v),'reference_sha256':digest(data),'evidence':evidence}
    return {'schema_version':'openpine.bar-magnifier-profile.1','source_lock_sha256':digest(read_json(ROOT/'spec/lock.json')),
        'strategy_cycle_sha256':digest(read_json(ROOT/'spec/strategy-cycle-profile.json')),'versions':versions}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args(argv)
    path=ROOT/'spec/bar-magnifier-profile.json';value=expected()
    if args.check:
        if read_json(path)!=value:raise ValueError('Magnifier model differs from pinned evidence.')
    else:path.write_bytes(canonical(value)+b'\n')
    from tools.magnifier_fixtures import files
    root=ROOT/'examples/bar-magnifier'
    if not args.check:root.mkdir(parents=True,exist_ok=True)
    for name,raw in files().items():
        if args.check:
            if (root/name).read_bytes()!=raw:raise ValueError('Magnifier fixture differs: '+name)
        else:(root/name).write_bytes(raw)
    print('Bar magnifier contract v1-v6 verified.');return 0
if __name__=='__main__':raise SystemExit(main())
