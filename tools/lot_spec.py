"""Reproduce bounded multi-entry contracts from the pinned local version catalogs."""
import argparse
from packages.contracts.lots import lot_policy,lot_apis,NOTE
from tools.specification import ROOT,SpecError,encode,file_map,read_json,sha
RELEASE='pine-lots-20261024.1'

def prepare():
    lock=read_json(ROOT/'spec/lock.json');versions={}
    for v in range(1,7):
        ref=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json')
        entries=[e for e in ref['entries'] if e['name'] in ('strategy','strategy.entry','strategy.close','strategy.exit')]
        if {e['name'] for e in entries}!={'strategy','strategy.entry','strategy.close','strategy.exit'}:raise SpecError('SPEC_EVIDENCE','Missing lot strategy evidence.')
        versions[str(v)]={'profile_id':f'openpine.lots.v{v}.1','policy':lot_policy(v),'apis':lot_apis(v),
            'evidence':[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in entries]}
    data={'schema_version':'openpine.lot-spec.1','base_manifest_sha256':lock['manifest_sha256'],
        'version_review_manifest_sha256':lock['version_review']['manifest_sha256'],'versions':versions,
        'review':{'date':'2026-10-24','external_pine_execution':False,'basis':NOTE,'sources':[],
            'decisions':['Pyramiding is present in v2-v6 local headers; v1 is an explicit reconstruction. close_entries_rule is present in v4-v6; v1-v3 selection is reconstructed.',
                'A positive literal pyramiding or explicit close_entries_rule selects a separate ten-argument declaration. Existing declarations and broker state schemas retain independent readers.',
                'At most 16 same-direction active lots, 16 pending entry IDs, 16 protective brackets and 16 reached commands per bar. Zero pyramiding permits one lot. Capacity is checked when an entry becomes eligible; excess entries are ignored. Aggregate quantity is bounded to 1e24.',
                'Position quantity and average price are derived from active lots. Each lot retains its fill/order identity, original and remaining quantity and cumulative entry fee allocation. Closed slices are persisted in the journal rather than unbounded checkpoint history.',
                'Close quantity is computed from matching entry-ID lots at command time. FIFO freezes oldest open lots globally; ANY freezes oldest matching entry-ID lots. Frozen close targets are clipped to their remaining quantities and never bind future fills.',
                'Protective exits bind current matching lots first, otherwise an exact pending entry order. Pending targets become frozen lot allocations at entry fill, under the selected FIFO/ANY rule. Reservations cannot double-book a lot; reductions do not automatically reallocate to other lots.',
                'An opposite entry closes all old lots then opens its requested new quantity as separate fills. One exit fill charges one rounded fee, allocated cumulatively across closed slices; each lot allocates its original entry fee cumulatively, with the last slice receiving the residue.',
                'Pending entry OHLC order and stop-limit activation deferral are retained. At most one intrabar entry fill per bar; a new intrabar fill activates protection only for the next bar. No path interpolation, observed/live protection, custom OCA or external-equivalence claim.'],
            'test_refs':['tests/test_lots.py','tests/test_lots_pipeline.py','conformance/lots/cases.json']}}
    files={'lots.json':encode(data)};files['manifest.json']=encode({'schema_version':'openpine.lot-manifest.1','release':RELEASE,'files':{n:sha(raw) for n,raw in files.items()}})
    return files

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args(argv);target=ROOT/'spec/bundles'/RELEASE;files=prepare()
    if target.exists():
        if file_map(target)!={n:sha(raw) for n,raw in files.items()}:raise SpecError('SPEC_IMMUTABLE','Lot release differs.')
    elif args.check:raise SpecError('SPEC_IMPORT','Lot release missing.')
    else:
        target.mkdir()
        for name,raw in files.items():(target/name).write_bytes(raw)
    print('Lot immutable release verified.');return 0
if __name__=='__main__':raise SystemExit(main())
