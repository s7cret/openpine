"""Import a reviewed local source checkout without a runtime repository dependency."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

from tools.specification import (
    ROOT, SpecError, coverage, encode, file_map, index, load_snapshot, loads,
    read_json, require_python, safe_path, sha, value_sha, verify_files,
)


def catalog(source: Path, version: int) -> dict:
    raw = (source / f"catalog-v{version}.js").read_text(encoding="utf-8")
    prefix = f"window.PINE_CATALOG=window.PINE_CATALOG||{{}};window.PINE_CATALOG[{version}]="
    if not raw.startswith(prefix) or not raw.rstrip().endswith(";"):
        raise SpecError("SPEC_IMPORT", f"unexpected catalog wrapper for v{version}")
    data = loads(raw[len(prefix):].rstrip()[:-1])
    if data["version"] != version:
        raise SpecError("SPEC_VERSION", "catalog version mismatch")
    index(data["entries"], "id")
    return data


def reference_projection(data: dict, source_digest: str) -> dict:
    entries = []
    for entry in data["entries"]:
        # These are source spellings and metadata, never executable signatures/defaults.
        parameters = []
        for parameter in entry["parameters"]:
            claim = parameter.get("default_contract", {})
            parameters.append({
                "parameter_id": parameter["parameter_id"], "name": parameter["name"],
                "type_spellings": parameter.get("types", []),
                "requiredness_claim": parameter.get("state", "unknown"),
                "default_claim": {"state": claim.get("state", "not_documented"),
                                  "spellings": claim.get("values", []),
                                  "evidence_sha256": value_sha(claim.get("evidence", []))},
            })
        entries.append({
            "entry_id": entry["id"], "language_version": entry["v"],
            "name": entry["name"], "category": entry["category"],
            "namespace": entry["namespace"], "symbol_id": entry["symbol_id"],
            "overload_id": entry["overload_contract_id"],
            "structure_role": entry["structure"]["role"],
            "family_id": entry["family"]["id"], "basis": entry["basis"],
            "syntax_spellings": entry["syntax"], "parameters": parameters,
            "result_type_spellings": entry["result"]["types"],
            "sources": entry["sources"], "source_record_sha256": value_sha(entry),
            "review_status": "reference_only", "executable_contract": False,
        })
    return {
        "schema_version": "openpine.spec.reference.1", "language_version": data["version"],
        "source_snapshot": data["snapshot"], "source_coverage": data["coverage"],
        "source_catalog_sha256": source_digest,
        "source_issues": data["source_issues"],
        "entries": sorted(entries, key=lambda row: row["entry_id"]),
    }


def prepare(source: Path, source_commit: str, release: str) -> tuple[dict[str, bytes], dict]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,79}", release):
        raise SpecError("SPEC_IMPORT", "invalid release name")
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise SpecError("SPEC_IMPORT", "source commit must be a complete Git SHA")
    source_lock = read_json(source / "consumer/lock.json")
    bundle = safe_path(source, source_lock["bundle"])
    if sha((bundle / "manifest.json").read_bytes()) != source_lock["manifest_sha256"]:
        raise SpecError("SPEC_HASH", "source bundle differs from its published lock")
    source_manifest = read_json(bundle / "manifest.json")
    verify_files(bundle, source_manifest["files"])
    if source_manifest["language_version"] != 6 or source_manifest["semantic_hash"] != source_lock["semantic_hash"]:
        raise SpecError("SPEC_IMPORT", "unsupported or inconsistent source bundle")
    files = {}
    inputs = {}
    def take(source_name: str, destination_name: str):
        raw = safe_path(source, source_name).read_bytes()
        inputs[source_name] = sha(raw)
        files[destination_name] = raw
    semantic_bundle = "semantic/" + bundle.name
    for name in ["manifest.json", *source_manifest["files"]]:
        take(f"{source_lock['bundle']}/{name}", f"{semantic_bundle}/{name}")
    for name in ("registry.json", "grammar.json"):
        take(f"spec/{name}", f"review/{name}")
    for name in ("LICENSE", "NOTICE", "THIRD_PARTY.md"):
        take(name, f"attribution/{name}")
    for version in range(1, 7):
        name = f"catalog-v{version}.js"
        digest = sha((source / name).read_bytes())
        inputs[name] = digest
        files[f"reference/v{version}.json"] = encode(reference_projection(catalog(source, version), digest))
    manifest = {
        "schema_version": "openpine.spec.snapshot.1", "release": release,
        "source_commit": source_commit, "source_inputs": inputs,
        "semantic_bundle": semantic_bundle,
        "files": {name: sha(raw) for name, raw in sorted(files.items())},
        "import_policy": "reference metadata is not an executable contract; upstream code is excluded",
    }
    files["manifest.json"] = encode(manifest)
    return files, source_manifest


def profile(version: int, snapshot: str, source_manifest: dict) -> dict:
    return {
        "schema_version": "openpine.spec.profile.1", "profile_id": f"openpine.pine-v{version}.spec.1",
        "language_version": version, "snapshot": snapshot, "reference": f"reference/v{version}.json",
        "default_when_unspecified": version == 1,
        "semantic_profile_ids": [source_manifest["semantic_profile_id"]]
                                if version == source_manifest["language_version"] else [],
        "inheritance": "explicit_only", "execution_support": "not_implemented",
    }


def import_snapshot(source: Path, release: str, root: Path = ROOT, check: bool = False) -> None:
    require_python()
    source = source.resolve()
    source_commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    recorded_lock = subprocess.check_output(
        ["git", "-C", str(source), "show", f"{source_commit}:consumer/lock.json"]
    )
    if recorded_lock != (source / "consumer/lock.json").read_bytes():
        raise SpecError("SPEC_PROVENANCE", "source bundle selection differs from recorded commit")
    files, source_manifest = prepare(source, source_commit, release)
    inputs = loads(files["manifest.json"])["source_inputs"]
    for path, digest in inputs.items():
        recorded = subprocess.check_output(["git", "-C", str(source), "show", f"{source_commit}:{path}"])
        if sha(recorded) != digest:
            raise SpecError("SPEC_PROVENANCE", f"source differs from recorded commit: {path}")
    destination_relative = f"spec/bundles/{release}"
    destination = root / destination_relative
    if destination.exists() and file_map(destination) != {name: sha(raw) for name, raw in files.items()}:
        raise SpecError("SPEC_IMMUTABLE", "release already exists with different contents; choose a new release")
    generated = {}
    for version in range(1, 7):
        generated[f"spec/profiles/pine-v{version}.json"] = encode(profile(version, destination_relative, source_manifest))
    lock = {
        "schema_version": "openpine.spec.lock.1", "snapshot": destination_relative,
        "source_commit": source_commit, "manifest_sha256": sha(files["manifest.json"]),
        "semantic_hash": source_manifest["semantic_hash"],
        "semantic_profile_id": source_manifest["semantic_profile_id"],
        "schema_sha256": sha((root / "spec/schema/import.schema.json").read_bytes()),
        "profiles": {name: sha(raw) for name, raw in generated.items()},
    }
    generated["spec/lock.json"] = encode(lock)
    if check:
        if not destination.exists():
            raise SpecError("SPEC_IMPORT", "snapshot has not been imported")
        for name, raw in generated.items():
            if safe_path(root, name).read_bytes() != raw:
                raise SpecError("SPEC_IMPORT", f"generated file is stale: {name}")
    else:
        destination.mkdir(parents=True, exist_ok=True)
        for name, raw in files.items():
            path = safe_path(destination, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        for name, raw in generated.items():
            path = safe_path(root, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    snapshot = load_snapshot(root)
    report = encode(coverage(snapshot))
    coverage_path = root / "spec/coverage.json"
    if check:
        if coverage_path.read_bytes() != report:
            raise SpecError("SPEC_IMPORT", "coverage report is stale")
    else:
        coverage_path.write_bytes(report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="local, clean source checkout")
    parser.add_argument("--release", required=True)
    parser.add_argument("--check", action="store_true", help="verify deterministic reproduction without writing")
    args = parser.parse_args()
    import_snapshot(args.source, args.release, check=args.check)
    print("Specification import verified." if args.check else "Specification imported and verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
