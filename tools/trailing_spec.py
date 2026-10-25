"""Reproduce the immutable relative/trailing exit contract without network access."""
import argparse
from packages.contracts.trailing import trailing_policy,trailing_apis,NOTE,source_review
from tools.specification import ROOT,SpecError,encode,file_map,read_json,sha
RELEASE='pine-trailing-20261025.1'

def prepare():
    lock=read_json(ROOT/'spec/lock.json');versions={}
    for v in range(1,7):
        ref=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json');entry=next(e for e in ref['entries'] if e['name']=='strategy.exit')
        versions[str(v)]={'profile_id':f'openpine.trailing.v{v}.1','policy':trailing_policy(v),'apis':trailing_apis(v),
            'evidence':[{'entry_id':entry['entry_id'],'source_record_sha256':entry['source_record_sha256']}]}
    data={'schema_version':'openpine.trailing-spec.1','base_manifest_sha256':lock['manifest_sha256'],
        'version_review_manifest_sha256':lock['version_review']['manifest_sha256'],'versions':versions,
        'review':{'date':'2026-10-25','external_pine_execution':False,'basis':NOTE,
            'sources':[source_review()],
            'decisions':['The pinned wiki supplies full v3-v6 parameter names; v1 has names only and v2 partial examples. Early signatures and precedence are explicitly reconstructed.',
                'Each exact reserved target has its own entry price, effective levels, activation, extreme and stop. Pending targets bind only on their actual parent fill.',
                'v6 selects the first directional level: nearer favorable profit/activation, nearer adverse loss, absolute on ties. v1-v5 absolute priority is explicit; only v5/v6 is established by the additional migration source.',
                'Known open activation and ratchet precede crossings. Intrabar checks use the previous effective stop; a high/low update or intrabar activation becomes effective next bar. No synthetic tick path or retroactive trigger.',
                'Nonnegative whole tick distances and offsets, positive aligned resolved levels. Missing tick metadata and incomplete trailing pairs fail closed.',
                'Unchanged price configuration preserves surviving exact-target state on reissue, including quantity/OCA changes. Changed price configuration resets it. New targets start fresh.',
                'Fixed and active trailing stops choose the more protective level, fixed on ties. Stop wins ambiguous stop/take. Each equal order/role/level/basis batch uses one fill and one fee.',
                'Existing lot reservations, OCA and journal are shared. Effects use actual filled quantities before the next candidate; reductions retain surviving target state.',
                'At most 16 brackets with 16 targets each. Checkpoint contains only current state. Prior broker profiles and state readers remain unchanged.'],
            'test_refs':['tests/test_trailing.py','tests/test_trailing_pipeline.py','conformance/trailing/cases.json']}}
    files={'trailing.json':encode(data)};files['manifest.json']=encode({'schema_version':'openpine.trailing-manifest.1','release':RELEASE,'files':{n:sha(b) for n,b in files.items()}});return files

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args(argv);target=ROOT/'spec/bundles'/RELEASE;files=prepare()
    if target.exists():
        if file_map(target)!={n:sha(b) for n,b in files.items()}:raise SpecError('SPEC_IMMUTABLE','Trailing release differs.')
    elif args.check:raise SpecError('SPEC_IMPORT','Trailing release missing.')
    else:
        target.mkdir()
        for name,b in files.items():(target/name).write_bytes(b)
    print('Trailing immutable release verified.');return 0
if __name__=='__main__':raise SystemExit(main())
