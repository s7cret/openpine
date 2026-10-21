"""Reproduce the reviewed, bounded multi-context historical request profile."""

import argparse
from packages.contracts.contexts import request_policy
from tools.specification import ROOT,SpecError,encode,file_map,read_json,sha

RELEASE="pine-requests-20261022.2"


def prepare():
    lock=read_json(ROOT/"spec/lock.json");versions={}
    for version in range(1,7):
        name="security" if version<5 else "request.security"
        reference=read_json(ROOT/lock["snapshot"]/f"reference/v{version}.json")
        entries=[e for e in reference["entries"] if e["name"]==name or e["name"].startswith("barmerge.")]
        versions[str(version)]={"profile_id":f"openpine.requests.v{version}.2","policy":request_policy(version),
            "evidence":[{"entry_id":e["entry_id"],"source_record_sha256":e["source_record_sha256"]} for e in entries]}
    data={"schema_version":"openpine.request-spec.1","base_manifest_sha256":lock["manifest_sha256"],
        "version_review_manifest_sha256":lock["version_review"]["manifest_sha256"],"versions":versions,
        "review":{"date":"2026-10-22","external_pine_execution":False,
            "basis":"Artifact-defined confirmed history with explicit UTC/24x7 fixed and calendar boundaries; external conformance remains deferred.",
            "sources":["https://www.tradingview.com/pine-script-docs/v3/essential/context-switching-the-security-function/",
                "https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/"],
            "decisions":["Pine v1/v2 preserve implicit historical lookahead_on, with positional x/y/z/a and gaps=false.",
                "Pine v3/v4 use security(symbol,resolution,expression,gaps,lookahead); v5/v6 use request.security and timeframe. Default gaps/lookahead are off from v3.",
                "Named barmerge constants are required for v5/v6 flags; v3/v4 additionally accept literal bool. Unresolved v5 calc_bars_count defaults are not used.",
                "Only unconditional global calls with literal qualified symbol/timeframe and close or EMA(close,literal length) are admitted. Dynamic/nested/local requests and arbitrary expression captures are outside this release.",
                "Aligned minute bars or explicit UTC D/W/M calendars. Weeks open Monday and months use actual calendar lengths. No implicit fetch, resampling or exchange selection. Missing bars remain missing.",
                "Fixed requests preserve exact same/higher multiples. Calendar requests use availability at actual close/open boundaries, including weeks spanning a month boundary; higher calendar rank is D then W then M. Minute primaries must divide a UTC day.",
                "Off makes a requested bar available at its close before the coincident primary close; on intentionally exposes its final historical value at its open before the coincident primary open.",
                "Each written expression has independent state updated on requested bars; gaps_on emits only when a new availability event occurred since the previous primary observation. This gap/event mapping is an explicit artifact-defined model pending external conformance."],
            "test_refs":["tests/test_contexts.py","tests/test_calendar.py","conformance/contexts/cases.json"]}}
    files={"requests.json":encode(data)}
    files["manifest.json"]=encode({"schema_version":"openpine.request-manifest.1","release":RELEASE,
        "files":{name:sha(raw) for name,raw in files.items()}})
    return files


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--check",action="store_true");args=parser.parse_args()
    files=prepare();target=ROOT/"spec/bundles"/RELEASE
    if target.exists():
        if file_map(target)!={n:sha(raw) for n,raw in files.items()}:raise SpecError("SPEC_IMMUTABLE","Request release differs; choose a new release")
    elif args.check:raise SpecError("SPEC_IMPORT","Request release is missing")
    else:
        target.mkdir(parents=True)
        for name,raw in files.items():(target/name).write_bytes(raw)
    print("Historical multi-context request release verified.")


if __name__=="__main__":main()
