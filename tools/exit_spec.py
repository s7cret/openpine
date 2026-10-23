"""Reproduce the immutable v1-v6 protective-exit review from local artifacts."""
import argparse
from packages.contracts.protection import exit_policy
from tools.specification import ROOT,SpecError,encode,file_map,read_json,sha

RELEASE='pine-exits-20261023.1'
NAMES={'strategy.exit','strategy.cancel','strategy.cancel_all'}


def prepare():
    lock=read_json(ROOT/'spec/lock.json');versions={}
    for version in range(1,7):
        reference=read_json(ROOT/lock['snapshot']/f'reference/v{version}.json')
        entries=[e for e in reference['entries'] if e['name'] in NAMES]
        if {e['name'] for e in entries}!=NAMES:raise SpecError('SPEC_EVIDENCE','Incomplete exit evidence.')
        versions[str(version)]={'profile_id':f'openpine.exits.v{version}.1','policy':exit_policy(version),
            'evidence':[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in entries]}
    data={'schema_version':'openpine.exit-spec.1','base_manifest_sha256':lock['manifest_sha256'],
        'version_review_manifest_sha256':lock['version_review']['manifest_sha256'],'versions':versions,
        'review':{'date':'2026-10-23','external_pine_execution':False,
            'basis':'Local frozen catalogs and explicit independent bounded OHLC execution model.',
            'sources':[],
            'decisions':[
                'v1/v2 contain names or incomplete examples. Their signatures and when defaults are explicit reconstructions. v3-v6 admit a named absolute stop/limit subset; from_entry is deliberately required and literal. when is admitted only through v5.',
                'One full-position bracket bound to an exact open fill or currently pending entry, never a later unrelated entry with the same ID. Same exit ID modifies; another active exit ID requires cancellation first. Missing targets are ignored with a journal reason.',
                'Commands are applied at confirmed close, first eligible on the next bar. Pending market commands execute first at open; protections activate against their exact fill and inspect that bar OHLC. Close/reversal/entry removal cancels obsolete protection.',
                'Open crossings fill at open. Otherwise price touches fill at their level; a bar touching both chooses stop and records ambiguity. No path interpolation or future-price-based favorable ordering.',
                'Stop and market fills receive pinned adverse slippage; limit exits do not. Both pay pinned commission. Prices must be positive and align to an explicitly supplied tick. Both na at a reached exit is rejected.',
                'cancel(id) matches exit or pending market IDs; cancel_all removes both. Neither closes an existing position. Cancellation at close cannot undo fills earlier in the same bar.',
                'Old market policies, pending serialization and state .1 remain unchanged. Protective state .2 validates binding IDs, call sites, levels and accounting. Changed implementation/spec identities require explicit recompilation, never silent resume migration.'],
            'test_refs':['tests/test_protection.py','tests/test_protection_pipeline.py']}}
    files={'exits.json':encode(data)}
    files['manifest.json']=encode({'schema_version':'openpine.exit-manifest.1','release':RELEASE,'files':{n:sha(raw) for n,raw in files.items()}})
    return files


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args()
    target=ROOT/'spec/bundles'/RELEASE;files=prepare()
    if target.exists():
        if file_map(target)!={n:sha(raw) for n,raw in files.items()}:raise SpecError('SPEC_IMMUTABLE','Exit release differs; use a new release.')
    elif args.check:raise SpecError('SPEC_IMPORT','Exit release missing.')
    else:
        target.mkdir(parents=True)
        for name,raw in files.items():(target/name).write_bytes(raw)
    print('Protective exit release verified.')


if __name__=='__main__':main()
