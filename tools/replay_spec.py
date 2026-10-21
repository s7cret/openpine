"""Pin the observed-event execution choices separately from Pine language claims."""

import argparse
import hashlib
from packages.contracts.realtime import policy
from packages.contracts.wire import canonical, read_json, digest
from tools.specification import ROOT


def expected():
    lock=read_json(ROOT/'spec/lock.json')
    return {'schema_version':'openpine.replay-profile.1', 'source_lock_sha256':digest(lock),
        'qualification':'Local execution choices; no TradingView equivalence claim. Existing language, series, request and broker contracts retain their pinned per-version qualification.',
        'versions':{str(v):{'policy':policy(v),'reference_sha256':hashlib.sha256(
            (ROOT/lock['snapshot']/f'reference/v{v}.json').read_bytes()).hexdigest()} for v in range(1,7)}}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args(argv)
    path=ROOT/'spec/replay-profile.json';value=expected()
    if args.check:
        if read_json(path)!=value:raise ValueError('Replay profiles differ from the pinned per-version execution choices.')
    else:path.write_bytes(canonical(value)+b'\n')
    print('Observed replay profiles v1-v6 verified.');return 0


if __name__=='__main__':raise SystemExit(main())
