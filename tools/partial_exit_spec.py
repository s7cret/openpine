"""Reproduce the explicit historical partial-exit review from frozen wiki artifacts."""
import argparse
from packages.contracts.partial_exits import partial_policy,partial_apis,NOTE
from tools.specification import ROOT,SpecError,encode,file_map,read_json,sha
RELEASE='pine-partial-exits-20261024.1'

def prepare():
    lock=read_json(ROOT/'spec/lock.json');versions={}
    for version in range(1,7):
        reference=read_json(ROOT/lock['snapshot']/f'reference/v{version}.json')
        entries=[e for e in reference['entries'] if e['name'] in ('strategy.exit','strategy.close')]
        if {e['name'] for e in entries}!={'strategy.exit','strategy.close'}:raise SpecError('SPEC_EVIDENCE','Missing partial exit names.')
        versions[str(version)]={'profile_id':f'openpine.partial-exits.v{version}.1','policy':partial_policy(version),'apis':partial_apis(version),
            'evidence':[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in entries]}
    data={'schema_version':'openpine.partial-exit-spec.1','base_manifest_sha256':lock['manifest_sha256'],
        'version_review_manifest_sha256':lock['version_review']['manifest_sha256'],'versions':versions,
        'review':{'date':'2026-10-24','external_pine_execution':False,'basis':NOTE,'sources':[],
            'decisions':['v1/v2 names and v3 close quantity signatures are incomplete; the common named quantity slice is reconstructed explicitly. v3 exit and v4-v6 quantities follow their catalog names. when exists only through v5.',
                'A named qty or qty_percent opts that program into a separate broker. Existing calls without these names retain their legacy signatures. Mixed legacy commands are supported inside an opted-in program.',
                'qty wins over qty_percent. Nonnegative qty, or 0..100 percent of the current exact target quantity, is frozen at command time, floored to quantity_step, clipped to remaining/unreserved quantity; zero is ignored with a reason.',
                'Multiple brackets reserve quantity in command order against an exact pending entry or fill. Modification releases its old reservation. Freed capacity is not redistributed automatically. Manual closes clip remaining reservations in creation order.',
                'Next-open market commands precede protective OHLC checks. All known open crossings execute first; otherwise stop touches precede limit touches globally, with sequence ties. Unknown intrabar order is disclosed as a model, not reconstructed tick data.',
                'Every exit fill creates one closed trade slice. Entry commission is allocated by rounded cumulative closed proportion; the final slice receives the exact residue. Slice gross/net PnL and audit totals are exact Decimal; standard display totals keep money rounding.',
                'Full close/reversal cancels protection of the exact old fill. Commands never attach to a later unrelated entry with the same ID. Pyramiding, limit/stop entries, trailing, custom OCA groups and observed/live protective execution remain outside this release.'],
            'test_refs':['tests/test_partial_broker.py','tests/test_partial_pipeline.py','conformance/partial-exits/cases.json']}}
    files={'partial-exits.json':encode(data)}
    files['manifest.json']=encode({'schema_version':'openpine.partial-exit-manifest.1','release':RELEASE,'files':{n:sha(raw) for n,raw in files.items()}})
    return files

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args(argv)
    target=ROOT/'spec/bundles'/RELEASE;files=prepare()
    if target.exists():
        if file_map(target)!={n:sha(raw) for n,raw in files.items()}:raise SpecError('SPEC_IMMUTABLE','Partial exit release differs.')
    elif args.check:raise SpecError('SPEC_IMPORT','Missing partial exit release.')
    else:
        target.mkdir()
        for n,raw in files.items():(target/n).write_bytes(raw)
    print('Partial exit release verified.');return 0

if __name__=='__main__':raise SystemExit(main())
