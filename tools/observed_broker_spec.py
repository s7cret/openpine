"""Pin an additive observed-price broker contract for all six language profiles."""
import argparse
from packages.contracts.observed_broker import policy
from packages.contracts.wire import canonical,read_json,digest
from tools.specification import ROOT

def expected():
    lock=read_json(ROOT/'spec/lock.json')
    return {'schema_version':'openpine.observed-broker-profile.1','source_lock_sha256':digest(lock),
        'legacy_replay_sha256':digest(read_json(ROOT/'spec/replay-profile.json')),
        'versions':{str(v):policy(v) for v in range(1,7)}}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args(argv)
    path=ROOT/'spec/observed-broker-profile.json'
    from tools.observed_broker_fixtures import files
    examples=ROOT/'examples/observed-broker'
    if args.check:
        if read_json(path)!=expected():raise ValueError('Observed broker profile differs from pinned execution choices.')
        for name,raw in files().items():
            if (examples/name).read_bytes()!=raw:raise ValueError('Observed broker example differs: '+name)
    else:
        path.write_bytes(canonical(expected())+b'\n');examples.mkdir(parents=True,exist_ok=True)
        for name,raw in files().items():(examples/name).write_bytes(raw)
    print('Observed broker profiles v1-v6 verified.');return 0

if __name__=='__main__':raise SystemExit(main())
