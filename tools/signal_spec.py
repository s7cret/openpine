"""Reproduce the signal and weighted-window release from pinned local evidence."""
import argparse
from packages.contracts.signals import signal_apis,signal_policy
from tools.specification import ROOT,SpecError,read_json,encode,sha,file_map
RELEASE='pine-signals-20261024.1'


def prepare():
    lock=read_json(ROOT/'spec/lock.json');versions={}
    for v in range(1,7):
        entries=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json')['entries'];apis=signal_apis(v)
        for api in apis:
            api['evidence']=[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in entries if e['name']==api['name']]
            api['basis']='artifact_defined_with_explicit_reconstruction'
        versions[str(v)]={'profile_id':f'openpine.signals.v{v}.1','policy':signal_policy(v),'apis':apis}
    data={'schema_version':'openpine.signal-spec.1','base_manifest_sha256':lock['manifest_sha256'],'version_review_manifest_sha256':lock['version_review']['manifest_sha256'],'versions':versions,
        'review':{'date':'2026-10-24','external_pine_execution':False,'basis':'Pinned versioned wiki inventory with explicit bounded execution decisions.',
            'sources':['https://www.tradingview.com/pine-script-docs/faq/functions/','https://www.tradingview.com/pine-script-docs/faq/techniques/'],
            'decisions':['All six versions have independent signatures/evidence. Legacy missing overload details and warmup/NA choices are explicitly reconstructed; named calls start v3.',
                'Lengths and occurrence are simple and fixed per written call and context. Dynamic lengths, string/color valuewhen and nested collections are outside this release.',
                'Events update only when reached, including loop iterations. valuewhen stores true events even when the selected source is NA, preserving int/float/bool type.',
                'Sums, WMA and VWMA use exact bounded-size integer accumulators and finite outputs. Zero volume denominator yields NA; missing source/volume pairs do not advance VWMA.',
                'Momentum uses reached-call history including NA. Rising/falling compare the current value to all previous length defined values after full warmup, not to adjacent differences.',
                'Extremum offsets include NA holes, are nonpositive, use the latest equal candidate, and publish available warmup extrema.',
                'Every kernel has a closed state codec, bounded storage and private changed-cell rollback branches. No wall clock or mutable provider metadata participates.'],
            'test_refs':['tests/test_signals.py','tests/test_signals_state.py','tests/test_signals_pipeline.py','conformance/signals/cases.json']}}
    files={'signals.json':encode(data)};files['manifest.json']=encode({'schema_version':'openpine.signal-manifest.1','release':RELEASE,'files':{n:sha(r) for n,r in files.items()}})
    return files


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args()
    target=ROOT/'spec/bundles'/RELEASE;files=prepare()
    if target.exists():
        if file_map(target)!={n:sha(r) for n,r in files.items()}:raise SpecError('SPEC_IMMUTABLE','Signal release differs; choose a new release.')
    elif args.check:raise SpecError('SPEC_IMPORT','Signal release missing.')
    else:
        target.mkdir(parents=True)
        for n,r in files.items():(target/n).write_bytes(r)
    print('Versioned signal release verified.')


if __name__=='__main__':main()
