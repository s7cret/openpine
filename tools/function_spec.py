"""Reproduce the explicitly reviewed scalar user-function model for v1-v6."""

import argparse
from tools.specification import ROOT, SpecError, encode, file_map, read_json, sha

RELEASE = "pine-functions-20261021.1"


def prepare():
    lock = read_json(ROOT / "spec/lock.json")
    versions = {}
    for version in range(1, 7):
        reference = read_json(ROOT / lock["snapshot"] / f"reference/v{version}.json")
        selected = [entry for entry in reference["entries"] if any(term in entry["name"].lower()
            for term in ("функц", "function", "=>", "if как", "if /"))]
        versions[str(version)] = {"language_version": version, "profile_id": f"openpine.functions.v{version}.1",
            "evidence": [{"entry_id": e["entry_id"], "source_record_sha256": e["source_record_sha256"]} for e in selected],
            "basis": "artifact_defined_with_explicit_historical_reconstruction",
            "policy": {"definition_scope": "global", "parameters": "required_scalar_named_or_positional",
                "parameter_writes": "forbidden", "global_writes": "forbidden", "captures": "globals_visible_at_definition",
                "forward_calls": "acyclic", "return": "last_expression", "if_values": "lazy_scalar_branches",
                "missing_numeric_branch": "na", "missing_bool_branch": "false" if version == 6 else "na",
                "missing_string_branch": "requires_else", "specialization": "written_call_chain",
                "history": "bar_aligned_per_instance", "result_qualifier": "all_dependencies",
                "max_definitions": 64, "max_parameters": 16, "max_call_depth": 12, "max_instances": 128,
                "max_expanded_nodes": 8192, "max_ir_depth": 128}}
    data = {"schema_version": "openpine.function-spec.1", "base_manifest_sha256": lock["manifest_sha256"],
        "version_review_manifest_sha256": lock["version_review"]["manifest_sha256"],
        "language_manifest_sha256": lock["language_release"]["manifest_sha256"],
        "series_manifest_sha256": lock["series_release"]["manifest_sha256"], "versions": versions,
        "review": {"author": "Codex", "date": "2026-10-21", "external_pine_execution": False,
            "basis": "User-approved scalar function/if-expression model from pinned wiki artifacts; incomplete older sources are explicitly reconstructed.",
            "decisions": [
                "Enable global arrow definitions, scalar inferred/typed parameters, positional/named required arguments, multiline last-expression returns and value-if in every v1-v6 profile.",
                "Global captures refer to bindings visible at definition; reading them requires initialization before the call. Caller locals are not captured. Globals and parameters cannot be reassigned by a function; locals may be shadowed and reassigned.",
                "Forward calls are allowed in an acyclic static graph. Recursion, nested definitions, overloads, parameter defaults, tuple returns, methods and first-class functions are outside this release.",
                "Each written call chain gets independently specialized bindings, parameters, var and stateful builtin call sites. History follows the already pinned bar-aligned model, including holes and final variable values.",
                "Argument expressions execute once in source order before parameter initialization. Function result qualifier conservatively includes all arguments and body dependencies.",
                "Value-if executes only one branch, checks both, promotes int/float returns, and synthesizes numeric na, legacy bool na or v6 false when else is absent. String-valued if requires else.",
                "Unused definitions receive structural/name/write/cycle checks; type specialization is performed at each reached-in-source call, including calls in statically unchosen branches. Limits bound compile expansion and IR admission."
            ], "test_refs": ["tests/test_functions.py", "conformance/functions/cases.json"]}}
    files = {"functions.json": encode(data)}
    files["manifest.json"] = encode({"schema_version": "openpine.function-manifest.1", "release": RELEASE,
        "files": {name: sha(raw) for name, raw in files.items()}})
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = prepare()
    target = ROOT / "spec/bundles" / RELEASE
    if target.exists():
        if file_map(target) != {name: sha(raw) for name, raw in files.items()}:
            raise SpecError("SPEC_IMMUTABLE", "Function release differs; choose a new release")
    elif args.check:
        raise SpecError("SPEC_IMPORT", "Function release is missing")
    else:
        target.mkdir(parents=True)
        for name, raw in files.items():
            (target / name).write_bytes(raw)
    print("Scalar function/if-value release verified.")


if __name__ == "__main__":
    main()
