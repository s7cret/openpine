"""Reproduce bounded net-order/OCA contracts from the pinned local catalogs."""
import argparse
from packages.contracts.order_groups import group_policy,group_apis,NOTE,EVIDENCE_NAMES
from tools.specification import ROOT,SpecError,encode,file_map,read_json,sha
RELEASE='pine-order-groups-20261025.1'


def prepare():
    lock=read_json(ROOT/'spec/lock.json');versions={}
    for v in range(1,7):
        ref=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json');entries=[e for e in ref['entries'] if e['name'] in EVIDENCE_NAMES]
        if {e['name'] for e in entries}!=set(EVIDENCE_NAMES):raise SpecError('SPEC_EVIDENCE','Missing grouped order evidence.')
        versions[str(v)]={'profile_id':f'openpine.order-groups.v{v}.1','policy':group_policy(v),'apis':group_apis(v),
            'evidence':[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in entries]}
    data={'schema_version':'openpine.order-group-spec.1','base_manifest_sha256':lock['manifest_sha256'],
        'version_review_manifest_sha256':lock['version_review']['manifest_sha256'],'versions':versions,
        'review':{'date':'2026-10-25','external_pine_execution':False,'basis':NOTE,'sources':[],
            'decisions':['Full local signatures exist in v3-v6; v1 names and incomplete v2 examples require explicit reconstruction.',
                'Net orders add, reduce oldest lots globally, close or cross zero, ignoring entry pyramiding but retaining the 16-lot hard bound. Opposite entry still closes old lots then opens its requested amount.',
                'close_all freezes current unreserved lot IDs in source order, executes next open and never binds future fills. Optional when is named-only in v1-v5; v6 has no when. Immediate fills and alerts are outside this profile.',
                'Explicit OCA entry/order arguments and named protective exits select the new profile. Unnamed brackets keep their internal stop/take peer cancellation. Explicit exit names use reduce.',
                'OCA effects follow actual fills atomically before the next candidate. Cancel removes active peers; reduce subtracts actual filled quantity, floors to quantity step and retains the oldest exact targets. An entry reversal uses both actual fill legs.',
                'A canceled parent cannot resurrect its pending protection. Candidate lists re-resolve current order identity and quantity after each group effect. Stop-limit activation state survives a pure quantity reduction.',
                'One active nonempty name has one group type. At most 16 groups and 32 active members; membership is bounded to current entries and brackets, with no closed-group checkpoint history.',
                'Known open actions retain source sequence, then protections, then at most one actual intrabar entry/net order. Intrabar stop-limit activation and new intrabar protection wait for the next bar. No external-equivalence claim.'],
            'test_refs':['tests/test_order_groups.py','tests/test_order_groups_pipeline.py','conformance/order-groups/cases.json']}}
    files={'order-groups.json':encode(data)};files['manifest.json']=encode({'schema_version':'openpine.order-group-manifest.1','release':RELEASE,'files':{n:sha(raw) for n,raw in files.items()}})
    return files


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args(argv);target=ROOT/'spec/bundles'/RELEASE;files=prepare()
    if target.exists():
        if file_map(target)!={n:sha(raw) for n,raw in files.items()}:raise SpecError('SPEC_IMMUTABLE','Order group release differs.')
    elif args.check:raise SpecError('SPEC_IMPORT','Order group release missing.')
    else:
        target.mkdir()
        for name,raw in files.items():(target/name).write_bytes(raw)
    print('Order group immutable release verified.');return 0
if __name__=='__main__':raise SystemExit(main())
