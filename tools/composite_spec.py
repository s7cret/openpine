"""Reproduce the offline composite profile from pinned version catalogs."""
import argparse
from packages.contracts.composite import composite_apis,composite_policy
from tools.specification import ROOT,SpecError,encode,file_map,read_json,sha

RELEASE='pine-composite-20261023.1'


def prepare():
    lock=read_json(ROOT/'spec/lock.json');versions={}
    for version in range(1,7):
        entries=read_json(ROOT/lock['snapshot']/f'reference/v{version}.json')['entries']
        apis=composite_apis(version)
        for api in apis:
            api['evidence']=[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']}
                for e in entries if e['name']==api['name']]
            api['basis']='artifact_defined_with_explicit_reconstruction' if api['evidence'] else 'explicit_reconstruction'
        versions[str(version)]={'profile_id':f'openpine.composite.v{version}.1','policy':composite_policy(version),'apis':apis}
    data={'schema_version':'openpine.composite-spec.1','base_manifest_sha256':lock['manifest_sha256'],
        'version_review_manifest_sha256':lock['version_review']['manifest_sha256'],'versions':versions,
        'review':{'date':'2026-10-23','external_pine_execution':False,
            'basis':'Pinned per-version reference records and explicit bounded reconstruction; not TradingView execution proof.',
            'decisions':[
                'Flat products contain 2..16 resolved scalar results. They are ephemeral return values, not first-class arrays or mutable tuple variables. Each result inherits the strongest dependency qualifier.',
                'Destructuring declares new scalar names atomically after exactly one evaluation; repeated underscore targets discard values. UDF returns and explicit value-if branches share the same representation. Nested products, tuple parameters, tuple reassignment and tuple ternary are excluded.',
                'MACD is recorded in v1..v6; the legacy array spelling is interpreted as a fixed three-result product. BB has no v1..v3 reference and is explicitly reconstructed there from SMA and population standard deviation.',
                'Variance/stdev use a fixed simple-int length 1..100000, last length defined sources, full warmup and exact binary sums/squares. Unbiased singleton yields NA. Optional biased=true is admitted only in v5/v6, matching their reference signatures.',
                'MACD reuses the existing version-specific EMA accumulators: SMA seed before v5, first defined source in v5/v6. On missing source fast/slow hold; the signal still consumes a defined difference. Each written composite call owns one complete checkpoint/rollback state.',
                'BB returns mean, mean+mult*population-stdev and mean-mult*population-stdev. Nonpositive or missing multipliers are not replaced silently.',
                'One unconditional request may return a flat numeric product from the admitted expression subset. All components share one requested evaluation, event, gaps/lookahead mapping and checkpoint. UDF captures, nested/dynamic requests and lower timeframes remain excluded.'],
            'test_refs':['tests/test_composite.py','tests/test_composite_state.py','tests/test_composite_pipeline.py','conformance/composite/cases.json']}}
    files={'composite.json':encode(data)}
    files['manifest.json']=encode({'schema_version':'openpine.composite-manifest.1','release':RELEASE,
        'files':{name:sha(raw) for name,raw in files.items()}})
    return files


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args()
    files=prepare();target=ROOT/'spec/bundles'/RELEASE
    if target.exists():
        if file_map(target)!={n:sha(raw) for n,raw in files.items()}:raise SpecError('SPEC_IMMUTABLE','Composite release differs; choose a new release.')
    elif args.check:raise SpecError('SPEC_IMPORT','Composite release missing.')
    else:
        target.mkdir(parents=True)
        for name,raw in files.items():(target/name).write_bytes(raw)
    print('Composite-analysis release verified.')


if __name__=='__main__':main()
