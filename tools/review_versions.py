"""Reproduce the explicit, bounded v1-v5 compatibility review from pinned inputs.

This records documentation-derived and reconstructed behavior separately from
external Pine execution evidence. It never edits the imported v6 snapshot.
"""

import argparse
import copy
import subprocess
from pathlib import Path

from tools.import_spec import catalog
from tools.specification import ROOT, SpecError, encode, file_map, read_json, sha, value_sha

RELEASE = "pine-versions-20261020.1"
RULES = ("rule.binding", "rule.overload", "rule.types", "rule.grammar", "rule.arguments.left_to_right",
         "rule.ema", "rule.ema.length", "rule.context.current", "rule.state.call_site_context",
         "rule.execution.skipped_call")
BASE = ROOT / "spec/bundles/pine-spec-20261020.1"


def prepare(source: Path):
    base_manifest = read_json(BASE / "manifest.json")
    base = read_json(BASE / "review/registry.json")
    base_assertions = {row["assertion_id"]: row for row in base["assertions"]}
    sources, files, versions = [], {}, {}
    for version in range(1, 6):
        raw = subprocess.check_output(["git", "-C", str(source), "show", f"{base_manifest['source_commit']}:catalog-v{version}.js"])
        if sha(raw) != base_manifest["source_inputs"][f"catalog-v{version}.js"] or raw != (source / f"catalog-v{version}.js").read_bytes():
            raise SpecError("SPEC_PROVENANCE", "Review input differs from the pinned source commit")
        data = catalog(source, version)
        pid = f"pine{version}.historical-core.1"
        registry = {key: copy.deepcopy(base[key]) for key in ("schema_version", "identity_policy")}
        registry.update(registry_revision=RELEASE, symbols=[], rules=[], assertions=[], decisions=[], profiles=[], tombstones=[])
        registry["documentation"] = {"en": "Bounded version-specific numeric/historical harness; see review.json.", "ru": "Ограниченный версионный numeric/historical harness; см. review.json."}
        certainty = "reconstructed" if version < 3 else "documentation_derived"
        caveats = ["No external Pine execution oracle; only the declared numeric/call subset is implemented."]
        if version < 5:
            caveats.append("EMA uses the SMA seed shown in saved v3/v4 documentation; native historical seed and NA behavior are not independently confirmed.")
        if version < 3:
            caveats.append("v1/v2 EMA seed and NA policy are an explicit reconstruction, not a recovered archive fact; named ema/abs arguments are uncovered.")

        def fact(subject, property_path, value, evidence, rationale):
            aid = f"assertion.v{version}.{subject}.{property_path}"
            did = f"decision.v{version}.{subject}.{property_path}"
            registry["assertions"].append({"assertion_id": aid, "subject_ref": subject, "property_path": property_path,
                "value": value, "scope": {"language_versions": [version], "semantic_profile_id": pid, "platform_profile_id": "historical.confirmed.1"},
                "method": "manual", "review_status": "corroborated", "issue_refs": [], "decision_ref": did, "evidence_refs": evidence})
            registry["decisions"].append({"decision_id": did, "assertion_refs": [aid], "selected_assertion": aid,
                "alternatives": [{"assertion_ref": aid, "reason": rationale}], "status": "accepted", "value_sha256": value_sha(value),
                "author": "Codex", "reviewer": "Codex", "date": "2026-10-20", "rationale": rationale,
                "test_refs": ["tests/test_versions.py", "conformance/versions/cases.json"]})
            return aid

        rules_source = f"source.v{version}.review"
        sources.append({"source_id": rules_source, "kind": "editorial_review", "language_version": version,
                        "basis": certainty, "note": "Explicit OpenPine subset policy, not automatic inheritance or platform execution proof."})
        for rule in base["rules"]:
            if rule["rule_id"] not in RULES:
                continue
            row = copy.deepcopy(rule)
            value = copy.deepcopy(base_assertions[row["semantic_assertion"]]["value"])
            if row["rule_id"] == "rule.ema" and version < 5:
                value.update(seed="sma_first_length_non_na", first_defined_result="length_non_na_sources", minimum_history="length")
            if row["rule_id"] == "rule.ema.length" and version < 3:
                value["parameter_name"] = "y"
            row["semantic_assertion"] = fact(row["rule_id"], "semantics", value, [rules_source],
                "Deliberate bounded compatibility policy. " + " ".join(caveats))
            registry["rules"].append(row)

        mapping = (("indicator", "study" if version < 5 else "indicator", "harness.indicator"),
                   ("math.abs", "abs" if version < 5 else "math.abs", "numeric.abs"),
                   ("ta.ema", "ema" if version < 5 else "ta.ema", "series.ema.sma_seed" if version < 5 else "series.ema"),
                   ("plot", "plot", "visual.plot"), ("close", "close", None))
        for base_name, name, operation in mapping:
            kind = "variable" if name == "close" else "function"
            entry = next(row for row in data["entries"] if row["name"] == name and row["category"] == ("variables" if kind == "variable" else "functions"))
            source_id = f"source.v{version}.{name}"
            sources.append({"source_id": source_id, "kind": "pinned_reference_review", "language_version": version,
                "entry_id": entry["id"], "source_record_sha256": value_sha(entry), "catalog_sha256": sha(raw),
                "urls": [row["url"] for row in entry["sources"]],
                "note": "Reviewed spelling and restricted signature only; all other catalog entries remain reference_only."})
            original = next(row for row in base["symbols"] if row["name"] == base_name and row["kind"] == kind)
            symbol = copy.deepcopy(original)
            symbol.update(symbol_id=f"symbol.v{version}.{kind}.{name}", name=name, namespace=name.rpartition('.')[0] or "global", language_versions=[version])
            if kind == "variable":
                value = copy.deepcopy(base_assertions[original["value_assertion_ref"]]["value"])
                symbol["value_assertion_ref"] = fact(symbol["symbol_id"], "value", value, [source_id], "Numeric close series normalized to the shared float IR.")
            else:
                original_overload = original["overloads"][0]
                value = copy.deepcopy(base_assertions[original_overload["signature_assertion"]]["value"])
                oid = f"overload.v{version}.{name}.harness"
                value["operation"] = operation
                old_names = [p["name"] for p in value["parameters"]]
                names = [p["name"] for p in entry["parameters"][:len(old_names)]]
                if base_name in ("ta.ema", "math.abs") and version < 3:
                    value["binding"]["named"] = False
                for parameter, parameter_name in zip(value["parameters"], names, strict=True):
                    parameter.update(name=parameter_name, parameter_id=f"parameter.{oid}.{parameter_name}")
                if value["result"]["kind"] == "dependent":
                    value["result"]["parameter"] = names[old_names.index(value["result"]["parameter"])]
                aid = fact(oid, "signature", value, [source_id, rules_source],
                    "Restricted numeric signature and harness defaults. " + " ".join(caveats))
                symbol["overloads"] = [{"overload_id": oid, "semantic_revision": "1", "signature_assertion": aid}]
            registry["symbols"].append(symbol)
        registry["profiles"] = [{"semantic_profile_id": pid, "language_version": version,
            "platform_profiles": ["historical.confirmed.1"], "scope": "Explicit close/abs/EMA and indicator/plot harness; " + certainty,
            "symbol_refs": [row["symbol_id"] for row in registry["symbols"]], "rule_refs": list(RULES),
            "known_unresolved": [], "uncovered": []}]
        filename = f"v{version}.json"
        files[filename] = encode(registry)
        versions[str(version)] = {"registry": filename, "semantic_sha256": sha(files[filename]), "semantic_profile_id": pid,
                                  "evidence_level": certainty, "caveats": caveats}
    files["evidence.json"] = encode({"sources": {"sources": sources}})
    review = {"schema_version": "openpine.spec.versions.1", "base_manifest_sha256": sha((BASE / "manifest.json").read_bytes()),
              "versions": versions, "external_pine_execution": False,
              "policy": "Corroborated here means accepted for the explicitly qualified OpenPine profile, not independently confirmed historical TradingView behavior."}
    files["review.json"] = encode(review)
    files["manifest.json"] = encode({"schema_version": "openpine.spec.versions-manifest.1", "release": RELEASE,
        "base_manifest_sha256": review["base_manifest_sha256"], "files": {name: sha(raw) for name, raw in files.items()}})
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = prepare(args.source)
    target = ROOT / "spec/bundles" / RELEASE
    expected = {name: sha(raw) for name, raw in files.items()}
    if target.exists():
        if file_map(target) != expected:
            raise SpecError("SPEC_IMMUTABLE", "Version review release differs; create a new release")
    elif args.check:
        raise SpecError("SPEC_IMPORT", "Version review release is missing")
    else:
        target.mkdir(parents=True)
        for name, raw in files.items():
            (target / name).write_bytes(raw)
    print("Version review release verified.")


if __name__ == "__main__":
    main()
