"""Reproduce the artifact-defined OHLCV/history/numeric/TA release, offline."""

import argparse
from tools.specification import ROOT, SpecError, encode, file_map, read_json, sha

RELEASE = "pine-series-20261021.1"


def prepare():
    lock = read_json(ROOT / "spec/lock.json")
    base = ROOT / lock["snapshot"]
    versions = {}
    for version in range(1, 7):
        entries = read_json(base / f"reference/v{version}.json")["entries"]
        apis, references = [], []
        legacy = version < 5
        numeric = ["int", "float"]
        nullable = numeric + (["bool"] if version < 6 else [])

        def evidence(name):
            return [{"entry_id": e["entry_id"], "source_record_sha256": e["source_record_sha256"]}
                    for e in entries if e["name"] == name]

        def parameter(name, types, default=None, qualifier="series"):
            return {"name": name, "types": types, "qualifier": qualifier, "default": default}

        def api(name, suffix, operation, parameters, result, result_rule="derived", effects=(), named=True):
            apis.append({"id": f"openpine.series.v{version}.{suffix}", "name": name,
                "operation": operation, "parameters": parameters, "result": result,
                "result_rule": result_rule, "effects": list(effects), "named": named and version >= 3,
                "evidence": evidence(name), "basis": "artifact_defined" if evidence(name) else "explicit_reconstruction"})

        for name in ("open", "high", "low", "volume", "bar_index"):
            references.append({"name": name, "base": "int" if name == "bar_index" else "float",
                               "evidence": evidence(name),
                               "basis": "artifact_defined" if evidence(name) else "explicit_reconstruction"})
        api("na", "na", "numeric.na", [parameter("x", nullable)], "bool")
        nz_names = ("x", "y") if legacy else ("source", "replacement")
        api("nz", "nz", "numeric.nz", [parameter(nz_names[0], nullable),
            parameter(nz_names[1], nullable, {"kind": "typed_zero"})], "float", "common")
        for cast in ("int", "float"):
            api(cast, cast, "numeric." + cast, [parameter("x", numeric)], cast)
        for operation in ("min", "max"):
            name = operation if legacy else "math." + operation
            for arity in range(2, 33):
                api(name, f"{operation}.{arity}", "numeric." + operation,
                    [parameter(f"number{i}", numeric) for i in range(arity)], "float", "common", named=False)
        round_name = "round" if legacy else "math.round"
        api(round_name, "round.integer", "numeric.round", [parameter("x" if legacy else "number", numeric)], "int")
        api(round_name, "round.precision", "numeric.round", [parameter("number", numeric),
            parameter("precision", ["int"])], "float")
        source, length = ("x", "y") if version < 3 else ("source", "length")
        prefix = "" if legacy else "ta."
        api(prefix + "sma", "sma", "series.sma", [parameter(source, ["float"]),
            parameter(length, ["int"])], "float", "fixed_series", ("reads_history", "updates_state"))
        api(prefix + "change", "change", "series.change", [parameter(source, numeric + ["bool"]),
            parameter(length, ["int"], {"kind": "literal", "value": 1})], "float", "source_series", ("reads_history", "updates_state"))
        for operation in ("cross", "crossover", "crossunder"):
            names = ("x", "y") if legacy else ("source1", "source2")
            api(prefix + operation, operation, "series." + operation,
                [parameter(n, ["float"]) for n in names], "bool", "fixed_series", ("reads_history", "updates_state"))
        versions[str(version)] = {"language_version": version,
            "profile_id": f"openpine.series.v{version}.1", "references": references, "apis": apis,
            "policy": {"history_limit": 100000, "history_cells": 1000000,
                "missing_bool": "false" if version == 6 else "na", "history_alignment": "bars",
                "skipped_expression": "hole", "variable_history": "end_of_bar",
                "skipped_call": "no_update", "sma_na": "last_length_non_na",
                "change_na": "include", "cross_missing": "false", "modulo": "floor",
                "round_ties": "positive_infinity", "round_precision": [-15, 15],
                "call_history_limit": 100000, "min_max_arity": [2, 32]}}
    data = {"schema_version": "openpine.series-spec.1", "base_manifest_sha256": lock["manifest_sha256"],
        "version_review_manifest_sha256": lock["version_review"]["manifest_sha256"],
        "language_manifest_sha256": lock["language_release"]["manifest_sha256"], "versions": versions,
        "review": {"author": "Codex", "date": "2026-10-21", "external_pine_execution": False,
            "basis": "User-approved model from pinned wiki artifacts, with explicit reconstructions for incomplete historical signatures.",
            "decisions": [
                "OHLCV .2 input accepts nullable finite prices and nonnegative volume; close-only .1 remains supported. Missing optional market slots are numeric na.",
                "History accepts numeric/bool series, integer offsets 0..100000, no chained indexing or literal indexing. Reference/variable history is bar-aligned; variables commit final values. Written expression histories have holes on skipped bars and never execute retrospectively.",
                "Bool history is na before v6 and false in v6. Legacy bool na uses three-valued and/or/not and false in branch selection; na/nz bool overloads are available only before v6.",
                "Stateful TA calls advance only when reached; SMA averages the last length non-na samples, change includes na and supports length zero, crossings require current strict and previous inclusive comparisons; missing comparisons are false.",
                "SMA/change length accepts series int within bounded storage. Round supports precision -15..15, ties toward positive infinity. Modulo uses floor quotient. min/max accept 2..32 positional values with common numeric type; nz shares the common source/replacement type.",
                "Enable casts and these bounded APIs in all six model profiles; absent historical function records and reconstructed optional defaults remain disclosed, not invented archive facts. v1/v2 calls are positional only."
            ], "test_refs": ["tests/test_series.py", "conformance/series/cases.json"]}}
    files = {"series.json": encode(data)}
    files["manifest.json"] = encode({"schema_version": "openpine.series-manifest.1", "release": RELEASE,
                                     "files": {name: sha(raw) for name, raw in files.items()}})
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = ROOT / "spec/bundles" / RELEASE
    files = prepare()
    if target.exists():
        if file_map(target) != {name: sha(raw) for name, raw in files.items()}:
            raise SpecError("SPEC_IMMUTABLE", "Series release differs; choose a new release")
    elif args.check:
        raise SpecError("SPEC_IMPORT", "Series release is missing")
    else:
        target.mkdir(parents=True)
        for name, raw in files.items():
            (target / name).write_bytes(raw)
    print("OHLCV/history/numeric/TA release verified.")


if __name__ == "__main__":
    main()
