"""Offline admission and review of the local Pine specification snapshot."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
UNORDERED = {
    "assertions": "assertion_id", "decisions": "decision_id", "symbols": "symbol_id",
    "overloads": "overload_id", "rules": "rule_id", "profiles": "semantic_profile_id",
    "sources": "source_id",
}


class SpecError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def require_python() -> None:
    if sys.version_info[:2] != (3, 14):
        raise SpecError("PYTHON_VERSION", "OpenPine requires Python >=3.14,<3.15")


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise SpecError("SPEC_JSON", f"duplicate key: {key}")
        result[key] = value
    return result


def _constant(token: str):
    raise SpecError("SPEC_JSON", f"non-finite number: {token}")


def _float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise SpecError("SPEC_JSON", f"non-finite number: {token}")
    return value


def loads(raw: bytes | str):
    return json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant, parse_float=_float)


def read_json(path: Path):
    return loads(path.read_bytes())


def canonical(value):
    """Match the imported bundle's canonical form; ordered arguments stay ordered."""
    if isinstance(value, dict):
        result = {}
        for key, item in sorted(value.items()):
            if key in UNORDERED and isinstance(item, list) and all(
                isinstance(row, dict) and UNORDERED[key] in row for row in item
            ):
                item = sorted(item, key=lambda row: row[UNORDERED[key]])
            result[key] = canonical(item)
        return result
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return value


def encode(value) -> bytes:
    return (json.dumps(canonical(value), ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def value_sha(value) -> str:
    return sha(encode(value))


def safe_path(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if not relative or path.is_absolute() or "\\" in relative or ":" in relative or any(
        part in (".", "..") for part in relative.split("/")
    ):
        raise SpecError("SPEC_PATH", f"invalid relative path: {relative}")
    result = root.joinpath(*path.parts)
    if not result.resolve().is_relative_to(root.resolve()):
        raise SpecError("SPEC_PATH", f"path leaves snapshot: {relative}")
    return result


def file_map(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise SpecError("SPEC_PATH", f"symlink in snapshot: {path.name}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = sha(path.read_bytes())
    return result


def verify_files(root: Path, expected: dict[str, str], manifest: str = "manifest.json") -> None:
    for name, digest in expected.items():
        path = safe_path(root, name)
        if not path.is_file() or sha(path.read_bytes()) != digest:
            raise SpecError("SPEC_HASH", f"missing or changed file: {name}")
    actual = file_map(root)
    actual.pop(manifest, None)
    if actual != expected:
        raise SpecError("SPEC_FILES", "snapshot file set differs from manifest")


def validate_schema(data, schema: dict) -> None:
    def local_refs(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("$ref", "$dynamicRef") and not value.startswith("#"):
                    raise SpecError("SPEC_SCHEMA", "schema must use local references")
                local_refs(value)
        elif isinstance(node, list):
            for item in node:
                local_refs(item)
    local_refs(schema)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(data)


def validate_document(data, definitions: dict, kind: str) -> None:
    validate_schema(data, {"$ref": f"#/$defs/{kind}", "$defs": definitions["$defs"]})


def index(rows: list[dict], key: str) -> dict:
    result = {row[key]: row for row in rows}
    if len(result) != len(rows):
        raise SpecError("SPEC_IDENTITY", f"duplicate {key}")
    return result


def semantic_projection(registry: dict) -> dict:
    excluded = {"documentation", "reviewer", "author", "date", "rationale", "evidence_refs",
                "test_refs", "review_events", "retrieved_on"}
    def walk(value, inside_value=False):
        if isinstance(value, dict):
            return {key: walk(item, inside_value or key == "value")
                    for key, item in value.items() if inside_value or key not in excluded}
        if isinstance(value, list):
            return [walk(item, inside_value) for item in value]
        return value
    return walk(registry)


def resolution_problem(assertion: dict, registry: dict) -> str | None:
    assertions = index(registry["assertions"], "assertion_id")
    decisions = index(registry["decisions"], "decision_id")
    decision = decisions.get(assertion.get("decision_ref"))
    if assertion["review_status"] != "corroborated" or not decision or decision["status"] != "accepted":
        return "fact has no accepted review"
    selected = assertions.get(decision["selected_assertion"])
    if not selected or selected["review_status"] != "corroborated":
        return "selected fact is not corroborated"
    if not {assertion["assertion_id"], selected["assertion_id"]} <= set(decision["assertion_refs"]):
        return "decision does not cover the fact"
    if (selected["subject_ref"], selected["property_path"]) != (
        assertion["subject_ref"], assertion["property_path"]
    ):
        return "selected fact belongs to another subject"
    scope, selected_scope = assertion["scope"], selected["scope"]
    if not set(scope["language_versions"]) <= set(selected_scope["language_versions"]) or any(
        scope[key] != selected_scope[key] for key in ("semantic_profile_id", "platform_profile_id")
    ):
        return "selected fact does not cover the scope"
    if decision["value_sha256"] != value_sha(selected["value"]):
        return "value differs from accepted decision"
    for other in assertions.values():
        other_scope = other["scope"]
        if (other["subject_ref"], other["property_path"]) != (
            assertion["subject_ref"], assertion["property_path"]
        ) or not set(scope["language_versions"]) & set(other_scope["language_versions"]) or any(
            scope[key] != other_scope[key] for key in ("semantic_profile_id", "platform_profile_id")
        ):
            continue
        if other["assertion_id"] not in decision["assertion_refs"] and (
            other["review_status"] == "unresolved" or value_sha(other["value"]) != value_sha(selected["value"])
        ):
            return "unreviewed competing fact"
        if other["assertion_id"] in decision["assertion_refs"] and not set(other["issue_refs"]) <= set(
            decision.get("resolves_issues", [])
        ):
            return "competing issue is unresolved"
    return None


def validate_registry(registry: dict, evidence: dict, schema: dict) -> None:
    validate_schema(registry, schema)
    assertions = index(registry["assertions"], "assertion_id")
    decisions = index(registry["decisions"], "decision_id")
    symbols = index(registry["symbols"], "symbol_id")
    rules = index(registry["rules"], "rule_id")
    sources = index(evidence["sources"]["sources"], "source_id")
    for assertion in assertions.values():
        if not set(assertion.get("evidence_refs", [])) <= sources.keys():
            raise SpecError("SPEC_EVIDENCE", "assertion has dangling source")
        decision = decisions.get(assertion.get("decision_ref"))
        if assertion["review_status"] == "corroborated":
            problem = resolution_problem(assertion, registry)
            if problem:
                raise SpecError("SPEC_REVIEW", f"{assertion['assertion_id']}: {problem}")
        if assertion["review_status"] == "unresolved" and decision:
            raise SpecError("SPEC_REVIEW", "unresolved fact has an active decision")
    for decision in decisions.values():
        if not set(decision["assertion_refs"]) <= assertions.keys():
            raise SpecError("SPEC_REVIEW", "decision has dangling assertion")
        if decision["status"] == "accepted" and not all(
            decision.get(key) for key in ("author", "reviewer", "date", "rationale", "test_refs")
        ):
            raise SpecError("SPEC_REVIEW", "accepted decision lacks review evidence")
    overloads, parameters = set(), set()
    for symbol in symbols.values():
        for overload in symbol["overloads"]:
            oid = overload["overload_id"]
            if oid in overloads:
                raise SpecError("SPEC_IDENTITY", "duplicate overload")
            overloads.add(oid)
            assertion = assertions.get(overload["signature_assertion"])
            if not assertion or assertion["subject_ref"] != oid or assertion["property_path"] != "signature":
                raise SpecError("SPEC_IDENTITY", "signature owner mismatch")
            for parameter in assertion["value"]["parameters"]:
                pid = parameter["parameter_id"]
                if pid in parameters or not pid.startswith(f"parameter.{oid}."):
                    raise SpecError("SPEC_IDENTITY", "invalid parameter owner")
                parameters.add(pid)
    for rule in rules.values():
        if rule["semantic_assertion"] not in assertions:
            raise SpecError("SPEC_IDENTITY", "rule has no semantic assertion")
    for profile in registry["profiles"]:
        if not set(profile["symbol_refs"]) <= symbols.keys() or not set(profile["rule_refs"]) <= rules.keys():
            raise SpecError("SPEC_IDENTITY", "profile has dangling member")
    retired = [row if isinstance(row, str) else row.get("id") for row in registry["tombstones"]]
    live = assertions.keys() | decisions.keys() | symbols.keys() | rules.keys() | overloads | parameters
    if None in retired or len(retired) != len(set(retired)) or set(retired) & live:
        raise SpecError("SPEC_IDENTITY", "invalid or reused tombstone")


def validate_identities(identities: dict) -> tuple[dict, dict]:
    aliases = index(identities["aliases"], "entry_id")
    overload_owners, parameter_owners, parameter_aliases = {}, {}, {}
    for alias in aliases.values():
        owner = (alias["symbol_id"], alias["language_version"])
        oid = alias["overload_id"]
        if oid in overload_owners and overload_owners[oid] != owner:
            raise SpecError("SPEC_IDENTITY", "overload alias has another owner")
        overload_owners[oid] = owner
    for parameter in identities["parameters"]:
        entry = aliases.get(parameter["entry_id"])
        key = (parameter["entry_id"], parameter["name"])
        if not entry or key in parameter_aliases:
            raise SpecError("SPEC_IDENTITY", "duplicate or dangling parameter alias")
        pid = parameter["parameter_id"]
        owner = (entry["overload_id"], parameter["name"], entry["language_version"])
        if not pid.startswith(f"parameter.{entry['overload_id']}.") or (
            pid in parameter_owners and parameter_owners[pid] != owner
        ):
            raise SpecError("SPEC_IDENTITY", "parameter alias has another owner")
        parameter_owners[pid] = owner
        parameter_aliases[key] = pid
    retired = [row if isinstance(row, str) else row.get("id") for row in identities["tombstones"]]
    live = aliases.keys() | overload_owners.keys() | parameter_owners.keys() | {
        alias["symbol_id"] for alias in aliases.values()
    }
    if None in retired or len(retired) != len(set(retired)) or set(retired) & live:
        raise SpecError("SPEC_IDENTITY", "invalid or reused alias tombstone")
    return aliases, parameter_aliases


@dataclass(frozen=True)
class Snapshot:
    lock: dict
    manifest: dict
    registry: dict
    profiles: dict[int, dict]
    references: dict[int, dict]

    def profile(self, language_version: int | None = None) -> dict:
        if language_version is None:
            language_version = next(version for version, profile in self.profiles.items()
                                    if profile["default_when_unspecified"])
        if type(language_version) is not int or language_version not in self.profiles:
            raise SpecError("LANGUAGE_VERSION_UNSUPPORTED", "expected Pine version 1..6")
        return copy.deepcopy(self.profiles[language_version])

    def resolve(self, assertion_id: str, language_version: int,
                platform_profile: str = "historical.confirmed.1"):
        profile = self.profile(language_version)
        assertions = index(self.registry["assertions"], "assertion_id")
        assertion = assertions.get(assertion_id)
        if not assertion:
            raise SpecError("SPEC_UNRESOLVED", f"unknown assertion: {assertion_id}")
        problem = resolution_problem(assertion, self.registry)
        if problem:
            raise SpecError("SPEC_UNRESOLVED", problem)
        scope = assertion["scope"]
        if language_version not in scope["language_versions"] or scope["semantic_profile_id"] not in (
            profile["semantic_profile_ids"]
        ) or scope["platform_profile_id"] != platform_profile:
            raise SpecError("PROFILE_UNCOVERED", "fact is outside the selected version/context profile")
        decision = index(self.registry["decisions"], "decision_id")[assertion["decision_ref"]]
        return copy.deepcopy(assertions[decision["selected_assertion"]]["value"])


def load_snapshot(root: Path = ROOT) -> Snapshot:
    require_python()
    root = root.resolve()
    definitions = read_json(root / "spec/schema/import.schema.json")
    lock = read_json(root / "spec/lock.json")
    validate_document(lock, definitions, "lock")
    if sha((root / "spec/schema/import.schema.json").read_bytes()) != lock["schema_sha256"]:
        raise SpecError("SPEC_HASH", "local schema differs from lock")
    bundle = safe_path(root, lock["snapshot"])
    if sha((bundle / "manifest.json").read_bytes()) != lock["manifest_sha256"]:
        raise SpecError("SPEC_HASH", "snapshot manifest differs from external lock")
    manifest = read_json(bundle / "manifest.json")
    validate_document(manifest, definitions, "manifest")
    if manifest["source_commit"] != lock["source_commit"]:
        raise SpecError("SPEC_PROVENANCE", "source commit differs from lock")
    verify_files(bundle, manifest["files"])
    source_bundle = safe_path(bundle, manifest["semantic_bundle"])
    source_manifest = read_json(source_bundle / "manifest.json")
    if source_manifest.get("schema_version") != "1.0.0" or source_manifest.get(
        "compatibility", {}
    ).get("reader_schema_major") != 1:
        raise SpecError("SPEC_SCHEMA", "unsupported source bundle schema")
    verify_files(source_bundle, source_manifest["files"])
    for key in ("semantic_hash", "semantic_profile_id"):
        if source_manifest[key] != lock[key]:
            raise SpecError("SPEC_HASH", f"source bundle {key} differs from lock")
    semantic = read_json(source_bundle / "semantic.json")
    for name in ("semantic", "documentation", "evidence"):
        if source_manifest[f"{name}_hash"] != sha((source_bundle / f"{name}.json").read_bytes()):
            raise SpecError("SPEC_HASH", f"source {name} hash mismatch")
    registry = read_json(bundle / "review/registry.json")
    evidence = read_json(source_bundle / "evidence.json")
    validate_registry(registry, evidence, read_json(source_bundle / "registry.schema.json"))
    if semantic_projection(registry) != semantic["registry"]:
        raise SpecError("SPEC_REVIEW", "reviewed registry differs from published semantic projection")
    if read_json(bundle / "review/grammar.json") != semantic["grammar"]:
        raise SpecError("SPEC_REVIEW", "grammar differs from published bundle")
    profiles, references = {}, {}
    identities = read_json(source_bundle / "identities.json")
    aliases, parameter_aliases = validate_identities(identities)
    for version in range(1, 7):
        relative = f"spec/profiles/pine-v{version}.json"
        path = root / relative
        if sha(path.read_bytes()) != lock["profiles"][relative]:
            raise SpecError("SPEC_HASH", f"version profile differs from lock: {version}")
        profile = read_json(path)
        reference = read_json(bundle / f"reference/v{version}.json")
        validate_document(profile, definitions, "profile")
        validate_document(reference, definitions, "reference")
        if profile["language_version"] != version or reference["language_version"] != version:
            raise SpecError("SPEC_VERSION", "profile/catalog language mismatch")
        if reference["source_catalog_sha256"] != manifest["source_inputs"][f"catalog-v{version}.js"]:
            raise SpecError("SPEC_PROVENANCE", "catalog provenance differs from input manifest")
        if profile["default_when_unspecified"] != (version == 1):
            raise SpecError("SPEC_VERSION", "Pine without a version annotation must select v1")
        allowed = [source_manifest["semantic_profile_id"]] if version == source_manifest["language_version"] else []
        if profile["semantic_profile_ids"] != allowed:
            raise SpecError("SPEC_VERSION", "unreviewed inheritance of another language profile")
        if profile["reference"] != f"reference/v{version}.json" or profile["snapshot"] != lock["snapshot"]:
            raise SpecError("SPEC_VERSION", "profile points to another snapshot")
        entries = index(reference["entries"], "entry_id")
        expected = {key: value for key, value in aliases.items() if value["language_version"] == version}
        if entries.keys() != expected.keys():
            raise SpecError("SPEC_IDENTITY", f"catalog identities differ for Pine v{version}")
        for key, entry in entries.items():
            alias = expected[key]
            if any(entry[field] != alias[field] for field in ("symbol_id", "overload_id", "category")):
                raise SpecError("SPEC_IDENTITY", f"alias owner mismatch: {key}")
            if entry["language_version"] != version:
                raise SpecError("SPEC_VERSION", "reference entry belongs to another version")
            names = [parameter["name"] for parameter in entry["parameters"]]
            if len(names) != len(set(names)):
                raise SpecError("SPEC_IDENTITY", "repeated catalog parameter")
            for parameter in entry["parameters"]:
                if parameter_aliases.get((key, parameter["name"])) != parameter["parameter_id"]:
                    raise SpecError("SPEC_IDENTITY", "catalog parameter differs from stable alias")
        profiles[version], references[version] = profile, reference
    catalog_parameters = {(entry["entry_id"], parameter["name"])
                          for reference in references.values() for entry in reference["entries"]
                          for parameter in entry["parameters"]}
    if catalog_parameters != parameter_aliases.keys():
        raise SpecError("SPEC_IDENTITY", "catalog parameter set differs from stable identities")
    return Snapshot(lock, manifest, registry, profiles, references)


def coverage(snapshot: Snapshot) -> dict:
    rows = []
    for version in range(1, 7):
        profile = snapshot.profiles[version]
        applicable = [row for row in snapshot.registry["assertions"]
                      if version in row["scope"]["language_versions"]]
        admitted = [row for row in applicable
                    if row["scope"]["semantic_profile_id"] in profile["semantic_profile_ids"]
                    and resolution_problem(row, snapshot.registry) is None]
        rows.append({
            "language_version": version,
            "reference_entries": len(snapshot.references[version]["entries"]),
            "reference_source_coverage": snapshot.references[version]["source_coverage"],
            "imported_applicable_assertions": len(applicable),
            "admitted_assertions": len(admitted),
            "unresolved_assertion_refs": sorted(row["assertion_id"] for row in applicable
                                                if resolution_problem(row, snapshot.registry)),
            "semantic_profile_ids": profile["semantic_profile_ids"],
            "openpine_implementation": {"status": "not_implemented", "compiled_programs": 0,
                                        "executed_programs": 0},
        })
    return {"schema_version": "openpine.spec.coverage.1", "snapshot": snapshot.lock["snapshot"],
            "versions": rows, "reference_entries": sum(row["reference_entries"] for row in rows),
            "meaning": "Imported specification inventory; not Pine compatibility or OpenPine execution proof."}
