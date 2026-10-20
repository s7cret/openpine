"""Reproduce the local artifact-defined statement policy; no remote oracle."""

import argparse
from tools.specification import ROOT, SpecError, encode, file_map, read_json, sha

RELEASE = "pine-language-20261021.1"


def prepare():
    lock = read_json(ROOT / "spec/lock.json")
    base = ROOT / lock["snapshot"]
    data = {
        "schema_version": "openpine.language.1",
        "base_manifest_sha256": lock["manifest_sha256"],
        "version_review_manifest_sha256": lock["version_review"]["manifest_sha256"],
        "grammar_sha256": sha((base / "review/grammar.json").read_bytes()),
        "profiles": {str(v): f"openpine.language.v{v}.1" for v in range(1, 7)},
        "versions": {str(v): {
            "language_version": v, "integer_division": "float" if v == 6 else "truncate",
            "logical_evaluation": "lazy" if v == 6 else "strict", "numeric_conditions": v < 6,
            "ordinary_initialization": "each_bar", "persistent_initialization": "first_execution",
            "comparison_na": False, "zero_division": "na",
        } for v in range(1, 7)},
        "review": {
            "author": "Codex", "date": "2026-10-21", "external_pine_execution": False,
            "basis": "User-approved artifact-defined model; historical platform equivalence is a separate stage.",
            "rule_refs": ["rule.grammar", "rule.scope", "rule.types", "rule.state.call_site_context", "rule.execution.skipped_call"],
            "decisions": [
                "Enable inferred/explicit numeric, bool and string declarations, reassignment, lexical if/else blocks and var in every version profile.",
                "Ordinary initializers execute per bar; var initializes at its first reached execution in that run. Reassignment preserves base type and strengthens its owner to series before binding any reads.",
                "Integer division truncates toward zero in v1-v5 and returns float in v6; float division always returns float. Division by zero yields typed numeric na.",
                "Conditions accept numeric-to-bool conversion in v1-v5; v6 requires bool. Comparisons involving numeric na yield false. Ternary and if branches are lazy in every version.",
                "and/or evaluate both operands in v1-v5 and short-circuit in v6. Unreached calls and skipped var initializers do not update their state.",
                "plot and declaration calls remain global; loops, functions, varip, history access and modulo are outside this release."
            ],
            "test_refs": ["tests/test_statements.py", "conformance/language/cases.json"],
        },
    }
    files = {"policy.json": encode(data)}
    files["manifest.json"] = encode({"schema_version": "openpine.language-manifest.1", "release": RELEASE,
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
            raise SpecError("SPEC_IMMUTABLE", "Language release differs; choose a new release")
    elif args.check:
        raise SpecError("SPEC_IMPORT", "Language release is missing")
    else:
        target.mkdir(parents=True)
        for name, raw in files.items():
            (target / name).write_bytes(raw)
    print("Statement-policy release verified.")


if __name__ == "__main__":
    main()
