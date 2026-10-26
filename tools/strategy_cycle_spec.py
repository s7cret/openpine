"""Pin source flag availability and explicit execution-phase choices."""
import argparse
from packages.contracts.strategy_cycle import FLAGS,policy
from packages.contracts.wire import canonical,digest,read_json
from tools.specification import ROOT

def expected():
    versions={}
    for v in range(1,7):
        data=read_json(ROOT/f'spec/bundles/pine-spec-20261020.1/reference/v{v}.json')
        rows=[row for row in data['entries'] if row.get('name')=='strategy']
        evidence=[{'entry_id':row['entry_id'],'parameters':[p['name'] for p in row.get('parameters',[]) if p['name'] in FLAGS]} for row in rows]
        found={name:any(name in row['parameters'] for row in evidence) for name in FLAGS}
        assert found==policy(v)['source_flags'],(v,found)
        versions[str(v)]={'policy':policy(v),'reference_sha256':digest(data),'evidence':evidence}
    return {'schema_version':'openpine.strategy-cycle-profile.1','source_lock_sha256':digest(read_json(ROOT/'spec/lock.json')),
        'intrabar_profile_sha256':digest(read_json(ROOT/'spec/intrabar-profile.json')),'versions':versions}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args(argv)
    path=ROOT/'spec/strategy-cycle-profile.json';value=expected()
    if args.check:
        if read_json(path)!=value:raise ValueError('Strategy phase policy differs from pinned artifacts.')
    else:path.write_bytes(canonical(value)+b'\n')
    from tools.strategy_cycle_fixtures import files
    root=ROOT/'examples/strategy-cycle'
    if not args.check:root.mkdir(parents=True,exist_ok=True)
    for name,raw in files().items():
        if args.check:
            if (root/name).read_bytes()!=raw:raise ValueError('Strategy cycle fixture differs: '+name)
        else:(root/name).write_bytes(raw)
    print('Strategy phase contract v1-v6 verified.');return 0
if __name__=='__main__':raise SystemExit(main())
