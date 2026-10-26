"""Pin calculation and variable persistence independently of existing profiles."""
import argparse
from packages.contracts.intrabar import policy
from packages.contracts.wire import canonical,digest,read_json
from tools.specification import ROOT

def expected():
    versions={}
    for v in range(1,7):
        path=ROOT/f'spec/bundles/pine-spec-20261020.1/reference/v{v}.json'
        data=read_json(path);rows=data['entries'];evidence=[]
        for row in rows:
            if row.get('name')=='varip' or row.get('name')=='strategy' and any(p.get('name')=='calc_on_every_tick' for p in row.get('parameters',[])):
                evidence.append({'entry_id':row['entry_id'],'name':row['name']})
        versions[str(v)]={'policy':policy(v),'reference_sha256':digest(data),'evidence':evidence}
    return {'schema_version':'openpine.intrabar-profile.1','source_lock_sha256':digest(read_json(ROOT/'spec/lock.json')),
        'observed_broker_sha256':digest(read_json(ROOT/'spec/observed-broker-profile.json')),'versions':versions}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv);path=ROOT/'spec/intrabar-profile.json'
    if a.check:
        if read_json(path)!=expected():raise ValueError('Intrabar policy differs from pinned artifacts.')
    else:path.write_bytes(canonical(expected())+b'\n')
    from tools.intrabar_fixtures import files
    root=ROOT/'examples/intrabar'
    if not a.check:root.mkdir(parents=True,exist_ok=True)
    for name,raw in files().items():
        if a.check:
            if (root/name).read_bytes()!=raw:raise ValueError('Intrabar fixture differs: '+name)
        else:(root/name).write_bytes(raw)
    print('Intrabar calculation profile v1-v6 verified.');return 0
if __name__=='__main__':raise SystemExit(main())
