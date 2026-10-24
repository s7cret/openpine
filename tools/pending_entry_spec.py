"""Reproduce the bounded v1-v6 pending-entry contract from frozen local catalogs."""
import argparse
from packages.contracts.pending_entries import entry_policy,entry_apis,NOTE
from tools.specification import ROOT,SpecError,encode,file_map,read_json,sha
RELEASE='pine-pending-entries-20261024.1'

def prepare():
    lock=read_json(ROOT/'spec/lock.json');versions={}
    for version in range(1,7):
        reference=read_json(ROOT/lock['snapshot']/f'reference/v{version}.json');entries=[e for e in reference['entries'] if e['name']=='strategy.entry']
        if not entries:raise SpecError('SPEC_EVIDENCE','Missing entry evidence.')
        versions[str(version)]={'profile_id':f'openpine.pending-entries.v{version}.1','policy':entry_policy(version),'apis':entry_apis(version),
            'evidence':[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in entries]}
    data={'schema_version':'openpine.pending-entry-spec.1','base_manifest_sha256':lock['manifest_sha256'],
        'version_review_manifest_sha256':lock['version_review']['manifest_sha256'],'versions':versions,
        'review':{'date':'2026-10-24','external_pine_execution':False,'basis':NOTE,'sources':[],
            'decisions':['v1 has entry names without signatures; v2 has stop examples but incomplete limit/stop-limit shapes. Those gaps are reconstructed explicitly. v3-v6 expose limit and stop; when is admitted only through v5.',
                'A named limit or stop opts the program into persistent entries and the shared partial-exit accounting engine. Both na choose market; other programs preserve their old broker profiles.',
                'Entries are first eligible on the next bar. Limits improve at an eligible open or fill at a touched level without slippage. Stops cross at open or touch at the level with adverse pinned slippage. Quantity is positive, fixed and step-aligned; volume does not split entry fills.',
                'Stop-limit is a two-stage order. Activation at open permits the limit on that bar; activation on an unknown intrabar touch defers the limit to the next bar, even if it could already be marketable. No interpolated tick path or future-price ordering is claimed.',
                'All known open executions use sequence order. Existing and open-activated protection precedes conditional intrabar entries. At most one actual intrabar entry fill is admitted per bar; other eligible entries remain pending with a reason. Protection attached to an intrabar fill first executes next bar.',
                'Same entry ID replaces its exact pending order, resets stop-limit activation and cancels protection attached to the old order. cancel/cancel_all remove pending entries and attached reservations. Missing-target closes are ignored; they do not cancel unfilled entries.',
                'One position, no pyramiding, no expiry, no custom OCA groups, trailing, volume partial fills or observed/live protective execution. Full-close/reversal and partial-exit fees use the separately tested accounting helpers.'],
            'test_refs':['tests/test_pending_entries.py','tests/test_pending_pipeline.py','conformance/pending-entries/cases.json']}}
    files={'pending-entries.json':encode(data)}
    files['manifest.json']=encode({'schema_version':'openpine.pending-entry-manifest.1','release':RELEASE,'files':{n:sha(raw) for n,raw in files.items()}})
    return files

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args(argv);target=ROOT/'spec/bundles'/RELEASE;files=prepare()
    if target.exists():
        if file_map(target)!={n:sha(raw) for n,raw in files.items()}:raise SpecError('SPEC_IMMUTABLE','Pending-entry release differs.')
    elif args.check:raise SpecError('SPEC_IMPORT','Pending-entry release missing.')
    else:
        target.mkdir()
        for name,raw in files.items():(target/name).write_bytes(raw)
    print('Pending entry immutable release verified.');return 0
if __name__=='__main__':raise SystemExit(main())
