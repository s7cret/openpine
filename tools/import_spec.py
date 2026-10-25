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
        "schema_version": "openpine.spec.profile.2", "profile_id": f"openpine.pine-v{version}.spec.1",
        "language_version": version, "snapshot": snapshot, "reference": f"reference/v{version}.json",
        "default_when_unspecified": version == 1,
        "semantic_profile_ids": [source_manifest["semantic_profile_id"]]
                                if version == source_manifest["language_version"] else [],
        "inheritance": "explicit_only",
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
    existing_lock = read_json(root / "spec/lock.json") if (root / "spec/lock.json").exists() else {}
    version_pin = existing_lock.get("version_review")
    version_review = read_json(safe_path(root, version_pin["path"]) / "review.json") if version_pin else None
    language_pin = existing_lock.get("language_release")
    language = read_json(safe_path(root, language_pin["path"]) / "policy.json") if language_pin else None
    series_pin = existing_lock.get("series_release")
    series = read_json(safe_path(root, series_pin["path"]) / "series.json") if series_pin else None
    function_pin = existing_lock.get("function_release")
    functions = read_json(safe_path(root, function_pin["path"]) / "functions.json") if function_pin else None
    request_pin=existing_lock.get("request_release")
    requests=read_json(safe_path(root,request_pin["path"])/"requests.json") if request_pin else None
    strategy_pin=existing_lock.get('strategy_release')
    strategies=read_json(safe_path(root,strategy_pin['path'])/'strategies.json') if strategy_pin else None
    input_pin=existing_lock.get('input_release')
    inputs=read_json(safe_path(root,input_pin['path'])/'inputs.json') if input_pin else None
    exit_pin=existing_lock.get('exit_release')
    exits=read_json(safe_path(root,exit_pin['path'])/'exits.json') if exit_pin else None
    technical_pin=existing_lock.get('technical_release')
    technical=read_json(safe_path(root,technical_pin['path'])/'technical.json') if technical_pin else None
    composite_pin=existing_lock.get('composite_release')
    composite=read_json(safe_path(root,composite_pin['path'])/'composite.json') if composite_pin else None
    extra_extensions={}
    for key,filename,profile_key in (('loop_release','loops.json','loop_profile_ids'),('array_release','arrays.json','array_profile_ids'),('environment_release','environment.json','environment_profile_ids'),('signal_release','signals.json','signal_profile_ids'),('partial_exit_release','partial-exits.json','partial_exit_profile_ids'),('pending_entry_release','pending-entries.json','pending_entry_profile_ids'),('lot_release','lots.json','lot_profile_ids'),('order_group_release','order-groups.json','order_group_profile_ids'),('trailing_release','trailing.json','trailing_profile_ids'),('economics_release','economics.json','economics_profile_ids')):
        pin=existing_lock.get(key)
        if pin:extra_extensions[key]=(pin,read_json(safe_path(root,pin['path'])/filename),profile_key)
    for extension in (exits,technical,composite,*(value[1] for value in extra_extensions.values())):
        if extension and extension['base_manifest_sha256']!=sha(files['manifest.json']):
            raise SpecError('SPEC_PROVENANCE','New base inputs require new extension reviews.')
    if version_review and version_review["base_manifest_sha256"] != sha(files["manifest.json"]):
        raise SpecError("SPEC_PROVENANCE", "version review must be replaced when changing its base snapshot")
    if series and (series["base_manifest_sha256"] != sha(files["manifest.json"]) or not language_pin or
                   series["language_manifest_sha256"] != language_pin["manifest_sha256"]):
        raise SpecError("SPEC_PROVENANCE", "new inputs require a new series review")
    if functions and (functions["base_manifest_sha256"] != sha(files["manifest.json"]) or not series_pin or functions["series_manifest_sha256"] != series_pin["manifest_sha256"]):
        raise SpecError("SPEC_PROVENANCE", "new inputs require a new function review")
    for version in range(1, 7):
        selected = profile(version, destination_relative, source_manifest)
        if version_review and str(version) in version_review["versions"]:
            selected["semantic_profile_ids"] = [version_review["versions"][str(version)]["semantic_profile_id"]]
        if language:
            selected["language_profile_ids"] = [language["profiles"][str(version)]]
        if series:
            selected["series_profile_ids"] = [series["versions"][str(version)]["profile_id"]]
        if functions:
            selected["function_profile_ids"] = [functions["versions"][str(version)]["profile_id"]]
        if requests:
            selected["request_profile_ids"]=[requests["versions"][str(version)]["profile_id"]]
        if strategies:selected['strategy_profile_ids']=[strategies['versions'][str(version)]['profile_id']]
        if inputs:selected['input_profile_ids']=[inputs['versions'][str(version)]['profile_id']]
        if exits:selected['exit_profile_ids']=[exits['versions'][str(version)]['profile_id']]
        if technical:selected['technical_profile_ids']=[technical['versions'][str(version)]['profile_id']]
        if composite:selected['composite_profile_ids']=[composite['versions'][str(version)]['profile_id']]
        for pin,data,profile_key in extra_extensions.values():selected[profile_key]=[data['versions'][str(version)]['profile_id']]
        generated[f"spec/profiles/pine-v{version}.json"] = encode(selected)
    lock = {
        "schema_version": "openpine.spec.lock.1", "snapshot": destination_relative,
        "source_commit": source_commit, "manifest_sha256": sha(files["manifest.json"]),
        "semantic_hash": source_manifest["semantic_hash"],
        "semantic_profile_id": source_manifest["semantic_profile_id"],
        "schema_sha256": sha((root / "spec/schema/import.schema.json").read_bytes()),
        "profiles": {name: sha(raw) for name, raw in generated.items()},
    }
    if version_pin:
        lock["version_review"] = version_pin
    if language_pin:
        lock["language_release"] = language_pin
    if series_pin:
        lock["series_release"] = series_pin
    if function_pin:
        lock["function_release"] = function_pin
    if request_pin:
        lock["request_release"]=request_pin
    if strategy_pin:lock['strategy_release']=strategy_pin
    if input_pin:lock['input_release']=input_pin
    if exit_pin:lock['exit_release']=exit_pin
    if technical_pin:lock['technical_release']=technical_pin
    if composite_pin:lock['composite_release']=composite_pin
    lock.update({key:value[0] for key,value in extra_extensions.items()})
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
