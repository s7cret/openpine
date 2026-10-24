"""Reproduce the reviewed UTC execution-environment release offline."""

import argparse
from tools.specification import ROOT, SpecError, encode, file_map, read_json, sha
from packages.contracts.environment import environment_policy,environment_references,environment_apis

RELEASE='pine-environment-20261024.1'
REVIEW={'date':'2026-10-24','external_pine_execution':False,
    'sources':['https://www.tradingview.com/pine-script-docs/concepts/time/',
        'https://www.tradingview.com/pine-script-docs/concepts/bar-states/',
        'https://www.tradingview.com/pine-script-docs/concepts/chart-information/'],
    'decisions':[
        'UTC/24x7 time-based bars only. time is the recorded opening; time_close is the exclusive calendar end, never the next observed opening or a host clock.',
        'Calendar references and single-timestamp functions use UTC. timestamp accepts five/six numeric fields from v2; strict valid dates in the shared calendar range. Missing v1 timestamp and barstate evidence is not silently backfilled.',
        'v2 exposes only documented islast/isrealtime; v3 adds first/history/new/confirmed; lastconfirmedhistory starts v4. Older names ticker/period/is* are separate version-reviewed names.',
        'Historical islast/lastconfirmedhistory use the frozen full input boundary, including research warmup. Observed history requires an explicit pinned historical-end boundary; events after it (or all events without it) are realtime. First observed close without an opening is a confirmed new observation. Recorded mode survives offline replay.',
        'Observed islastconfirmedhistory requires a pinned historical-end boundary; absent boundary yields false. This is a bounded artifact-defined reconstruction rather than exact exchange-open-state emulation.',
        'Instrument numeric/currency/type fields require explicit immutable metadata. Source snapshots bind it transitively through their content ID. Replay configuration binds it directly. No mutable catalogue lookup occurs during execution.',
        'symbol prefix/ticker/root follow the qualified identifier for the supported spot/custom model. No derivative root inference, arbitrary zones/DST, exchange sessions, timenow, synthetic chart types or wall-clock functions.',
        'Requested numeric time/calendar/mintick expressions use the requested environment. barstate and string-returning request expressions remain explicitly outside the request profile.',
        'New programs use closed typed IR .10 and runtime-state .6 with environment identity. Old bundles and old IR structural forms are retained.'],
    'test_refs':['tests/test_environment.py','tests/test_environment_state.py','tests/test_environment_pipeline.py','conformance/environment/cases.json']}


def prepare():
    lock=read_json(ROOT/'spec/lock.json');versions={}
    for v in range(1,7):
        refs=environment_references(v);apis=environment_apis(v);names={a['name'] for a in (*refs,*apis)}
        entries=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json')['entries']
        versions[str(v)]={'profile_id':f'openpine.environment.v{v}.1','policy':environment_policy(v),
            'references':refs,'apis':apis,'evidence':[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in entries if e['name'] in names]}
    data={'schema_version':'openpine.environment-spec.1','base_manifest_sha256':lock['manifest_sha256'],
        'version_review_manifest_sha256':lock['version_review']['manifest_sha256'],'versions':versions,'review':REVIEW}
    files={'environment.json':encode(data)}
    files['manifest.json']=encode({'schema_version':'openpine.environment-manifest.1','release':RELEASE,'files':{n:sha(r) for n,r in files.items()}})
    return files


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args()
    target=ROOT/'spec/bundles'/RELEASE;files=prepare()
    if target.exists():
        if file_map(target)!={n:sha(r) for n,r in files.items()}:raise SpecError('SPEC_IMMUTABLE','Environment release differs; choose a new release.')
    elif args.check:raise SpecError('SPEC_IMPORT','Environment release is missing.')
    else:
        target.mkdir(parents=True)
        for n,r in files.items():(target/n).write_bytes(r)
    print('Versioned UTC environment release verified.')


if __name__=='__main__':main()
