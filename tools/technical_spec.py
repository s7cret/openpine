"""Reproduce the reviewed TA profile from the immutable local version catalogs."""
import argparse
from packages.contracts.technical import technical_apis, technical_policy
from tools.specification import ROOT, SpecError, encode, file_map, read_json, sha

RELEASE = "pine-technical-20261023.1"


def prepare():
    lock = read_json(ROOT/'spec/lock.json')
    versions = {}
    for version in range(1,7):
        entries = read_json(ROOT/lock['snapshot']/f'reference/v{version}.json')['entries']
        apis = technical_apis(version)
        for api in apis:
            api['evidence'] = [{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']}
                for e in entries if e['name']==api['name']]
            api['basis'] = 'artifact_defined_with_explicit_reconstruction' if api['evidence'] else 'explicit_reconstruction'
        versions[str(version)] = {'profile_id':f'openpine.technical.v{version}.1',
            'policy':technical_policy(version),'apis':apis}
    data = {'schema_version':'openpine.technical-spec.1','base_manifest_sha256':lock['manifest_sha256'],
        'version_review_manifest_sha256':lock['version_review']['manifest_sha256'],'versions':versions,
        'review':{'date':'2026-10-23','external_pine_execution':False,
            'sources':[],
            'basis':'Pinned per-version wiki records plus explicit bounded-model choices, not external execution equivalence.',
            'decisions':[
                'Admit one numeric RSI length overload. Legacy ratio overloads, series lengths, collections and tuples are outside this profile.',
                'All lengths are simple integers 1..100000, fixed per state. First defined samples seed RMA with SMA, then alpha=1/length. NA does not advance the RMA seed.',
                'TR reads physical prior-bar close through explicit HistoryIR. The variable uses handle_na=false; the function takes explicit bool. ATR uses true and RMA.',
                'RSI uses changes between consecutive defined sources, length changes to seed, 100 for gain-only, 0 for loss-only, NA for both zero. Missing sources retain result.',
                'Highest/lowest include NA holes in reached-call windows and emit available extrema before a full window. A single length selects high/low.',
                'v1/v2 names and incomplete signatures are explicitly reconstructed; named calls are not admitted. Later signatures are bounded and do not imply full overload coverage.',
                'Each state has a strict checkpoint codec, isolated rollback branch and shared memory budget. Public calls lower to explicit numeric intrinsics, including physical history.'],
            'test_refs':['tests/test_technical.py','tests/test_technical_pipeline.py','tests/test_request_expressions.py','conformance/technical/cases.json']}}
    files={'technical.json':encode(data)}
    files['manifest.json']=encode({'schema_version':'openpine.technical-manifest.1','release':RELEASE,
        'files':{n:sha(raw) for n,raw in files.items()}})
    return files


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args()
    files=prepare();target=ROOT/'spec/bundles'/RELEASE
    if target.exists():
        if file_map(target)!={n:sha(raw) for n,raw in files.items()}:raise SpecError('SPEC_IMMUTABLE','Technical release differs; choose a new release.')
    elif args.check:raise SpecError('SPEC_IMPORT','Technical release missing.')
    else:
        target.mkdir(parents=True)
        for n,raw in files.items():(target/n).write_bytes(raw)
    print('Technical-analysis release verified.')


if __name__=='__main__':main()
