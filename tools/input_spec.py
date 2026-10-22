"""Reproduce the reviewed artifact-defined input slice, including legacy profiles."""
import argparse
import copy
from packages.contracts.inputs import input_policy
from tools.specification import ROOT,read_json,encode,sha,file_map,SpecError

RELEASE='pine-inputs-20261022.1'


def prepare():
    lock=read_json(ROOT/'spec/lock.json');versions={}
    for version in range(1,7):
        reference=read_json(ROOT/lock['snapshot']/f'reference/v{version}.json')
        selected=[e for e in reference['entries'] if e['name']=='input' or e['name'].startswith('input.')]
        versions[str(version)]={'language_version':version,'profile_id':input_policy(version)['profile_id'],
            'policy':input_policy(version),'basis':'artifact_defined_with_explicit_historical_reconstruction',
            'evidence':[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in selected]}
    data={'schema_version':'openpine.input-spec.1','base_manifest_sha256':lock['manifest_sha256'],
        'version_review_manifest_sha256':lock['version_review']['manifest_sha256'],'versions':versions,
        'review':{'date':'2026-10-22','external_pine_execution':False,'decisions':[
            'Only ordinary direct global scalar/source input declarations, maximum 64. No input calls inside functions, conditionals or arbitrary expressions.',
            'v1-v3 use generic input and bare/literal type selectors; v4 input.* are type constants; v5-v6 have typed input functions and inferred generic input.',
            'defval/title and legacy type may be positional; other supported fields are named. This is a deliberately narrower binding contract than the complete upstream signatures.',
            'Numeric/bool/string values use input qualifier; source selects open/high/low/close/volume and returns series float. No external plot-source handles.',
            'Constraints/defaults/presentation are literals; options are literal lists from v3. Range constraints and options are mutually exclusive. Step controls UI increment and does not impose an execution lattice.',
            'confirm=false only; display, active dependencies, interactive chart picking and dynamically parameterized requests remain explicitly rejected. v6 literal active affects presentation only.',
            'Names identify inputs within an immutable compiled program; settings have a separate schema hash and normalized values hash. Settings never mutate the original IR.',
            'Incomplete v1/v2 signatures and ambiguous legacy parameter order are reconstructed as this explicit implementation profile, not asserted as TradingView parity.'
        ],'test_refs':['tests/test_inputs.py','tests/test_input_execution.py']}}
    files={'inputs.json':encode(data)}
    files['manifest.json']=encode({'schema_version':'openpine.input-manifest.1','release':RELEASE,'files':{k:sha(v) for k,v in files.items()}})
    return files


def pin(files):
    path=ROOT/'spec/schema/import.schema.json';schema=read_json(path);defs=schema['$defs']
    defs['lock']['properties']['input_release']=copy.deepcopy(defs['lock']['properties']['strategy_release'])
    defs['profile']['properties']['input_profile_ids']=copy.deepcopy(defs['profile']['properties']['strategy_profile_ids'])
    manifest=copy.deepcopy(defs['strategy_manifest']);manifest['properties']['schema_version']={'const':'openpine.input-manifest.1'}
    defs['input_manifest']=manifest
    policy=copy.deepcopy(defs['strategy_policy'])
    policy['properties']['schema_version']={'const':'openpine.input-spec.1'}
    policy['properties']['review']['required']=['date','external_pine_execution','decisions','test_refs']
    # Each version has its own closed policy; runtime implements the same constants.
    for version in range(1,7):
        row=policy['properties']['versions']['properties'][str(version)]
        row['properties']['policy']={'const':input_policy(version)}
        row['properties']['profile_id']={'const':input_policy(version)['profile_id']}
        row['properties']['language_version']={'const':version}
        row['properties']['basis']={'const':'artifact_defined_with_explicit_historical_reconstruction'}
        row['required']+=['language_version','basis']
    defs['input_policy']=policy
    path.write_bytes(encode(schema))
    lock=read_json(ROOT/'spec/lock.json');lock['schema_sha256']=sha(path.read_bytes())
    lock['input_release']={'path':'spec/bundles/'+RELEASE,'manifest_sha256':sha(files['manifest.json'])}
    for version in range(1,7):
        relative=f'spec/profiles/pine-v{version}.json';profile=read_json(ROOT/relative)
        profile['input_profile_ids']=[input_policy(version)['profile_id']];(ROOT/relative).write_bytes(encode(profile))
        lock['profiles'][relative]=sha((ROOT/relative).read_bytes())
    (ROOT/'spec/lock.json').write_bytes(encode(lock))
    from tools.specification import load_snapshot,coverage
    (ROOT/'spec/coverage.json').write_bytes(encode(coverage(load_snapshot())))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');parser.add_argument('--pin',action='store_true')
    args=parser.parse_args();files=prepare();target=ROOT/'spec/bundles'/RELEASE
    if target.exists():
        if file_map(target)!={k:sha(v) for k,v in files.items()}:raise SpecError('SPEC_IMMUTABLE','Input release differs; choose a new release.')
    elif args.check:raise SpecError('SPEC_IMPORT','Input release is missing.')
    else:
        target.mkdir(parents=True)
        for name,raw in files.items():(target/name).write_bytes(raw)
    if args.pin:pin(files)
    print('Versioned input release verified.')
    return 0


if __name__=='__main__':raise SystemExit(main())
