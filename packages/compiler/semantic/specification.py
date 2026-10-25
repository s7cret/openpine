"""Offline admission and review of the local Pine specification snapshot."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[3]
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


def _check_schema(schema: dict) -> None:
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


def validate_schema(data, schema: dict) -> None:
    _check_schema(schema)
    Draft202012Validator(schema).validate(data)


class _DocumentSchemas:
    """Private schema snapshot, checked once during one specification load."""

    def __init__(self, definitions: dict):
        schema = copy.deepcopy(definitions)
        _check_schema(schema)
        self._definitions = schema["$defs"]
        self._validators = {}

    def validate(self, data, kind: str) -> None:
        if kind not in self._definitions:
            raise SpecError("SPEC_SCHEMA", f"unknown document schema: {kind}")
        if kind not in self._validators:
            # Only this known wrapper is constructed after the entire schema was
            # checked. The private copy cannot change through the caller's dict.
            pointer = kind.replace("~", "~0").replace("/", "~1")
            self._validators[kind] = Draft202012Validator({
                "$ref": f"#/$defs/{pointer}", "$defs": self._definitions,
            })
        self._validators[kind].validate(data)


def validate_document(data, definitions: dict | _DocumentSchemas, kind: str) -> None:
    context = definitions if isinstance(definitions, _DocumentSchemas) else _DocumentSchemas(definitions)
    context.validate(data, kind)


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
    grammar: dict
    version_registries: dict[int, dict] = field(default_factory=dict)
    version_review: dict = field(default_factory=dict)
    language: dict = field(default_factory=dict)
    series: dict = field(default_factory=dict)
    functions: dict = field(default_factory=dict)
    requests: dict = field(default_factory=dict)
    strategies: dict = field(default_factory=dict)
    inputs: dict = field(default_factory=dict)
    exits: dict = field(default_factory=dict)
    technical: dict = field(default_factory=dict)
    composite: dict = field(default_factory=dict)
    loops: dict = field(default_factory=dict)
    arrays: dict = field(default_factory=dict)
    environment: dict = field(default_factory=dict)
    signals: dict = field(default_factory=dict)
    partial_exits: dict = field(default_factory=dict)
    pending_entries: dict = field(default_factory=dict)
    lots: dict = field(default_factory=dict)
    order_groups: dict = field(default_factory=dict)
    trailing: dict = field(default_factory=dict)
    economics: dict = field(default_factory=dict)
    risk: dict = field(default_factory=dict)
    performance: dict = field(default_factory=dict)

    def language_policy(self, version):
        self.profile(version)
        if not self.language:
            raise SpecError("SPEC_UNRESOLVED", "No statement policy is pinned")
        return copy.deepcopy(self.language["versions"][str(version)])

    def registry_for(self, language_version: int) -> dict:
        self.profile(language_version)
        if language_version == 6:
            return self.registry
        if language_version not in self.version_registries:
            raise SpecError("SPEC_UNRESOLVED", "No admitted registry for this language version")
        return self.version_registries[language_version]

    def semantic_identity(self, language_version: int) -> tuple[str, str, tuple[str, ...]]:
        self.profile(language_version)
        if language_version == 6:
            profile, digest, notes = self.lock["semantic_profile_id"], self.lock["semantic_hash"], ()
        else:
            row = self.version_review["versions"][str(language_version)]
            profile, digest, notes = row["semantic_profile_id"], row["semantic_sha256"], tuple(row["caveats"])
        if self.language:
            profile += "+" + self.language["profiles"][str(language_version)]
            digest = value_sha({"core": digest, "language": value_sha(self.language)})
        if self.series:
            profile += "+" + self.series["versions"][str(language_version)]["profile_id"]
            digest = value_sha({"previous": digest, "series": value_sha(self.series)})
        if self.functions:
            profile += "+" + self.functions["versions"][str(language_version)]["profile_id"]
            digest = value_sha({"previous": digest, "functions": value_sha(self.functions)})
        if self.requests:
            profile += "+" + self.requests["versions"][str(language_version)]["profile_id"]
            digest = value_sha({"previous":digest,"requests":value_sha(self.requests)})
        if self.strategies:
            row=self.strategies['versions'][str(language_version)]
            profile+='+'+row['profile_id'];digest=value_sha({'previous':digest,'strategies':value_sha(self.strategies)})
            if language_version<3:notes+=('Strategy signatures/defaults are reconstructed from legacy names/examples; external equivalence is unverified.',)
        if self.exits:
            row=self.exits['versions'][str(language_version)]
            profile+='+'+row['profile_id'];digest=value_sha({'previous':digest,'exits':value_sha(self.exits)})
            if language_version<3:notes+=('Protective exit/cancel signatures are explicitly reconstructed from incomplete legacy artifacts.',)
        if self.composite:
            row=self.composite['versions'][str(language_version)]
            profile+='+'+row['profile_id'];digest=value_sha({'previous':digest,'composite':value_sha(self.composite)})
            notes+=('Composite results and statistical windows use the bounded artifact-defined model; legacy BB v1-v3 is reconstructed and external equivalence is unverified.',)
        if self.technical:
            row=self.technical['versions'][str(language_version)]
            profile+='+'+row['profile_id'];digest=value_sha({'previous':digest,'technical':value_sha(self.technical)})
            notes+=('Technical-analysis overloads are a bounded artifact-derived model; missing historical signatures are explicitly reconstructed.',)
        if self.loops:
            row = self.loops['versions'][str(language_version)]
            profile += '+' + row['profile_id']
            digest = value_sha({'previous': digest, 'loops': value_sha(self.loops)})
            notes += ('Loops use versioned documented availability and bounded artifact-defined execution; historical reconstructions and platform equivalence remain unverified.',)
        if self.arrays:
            row = self.arrays['versions'][str(language_version)]
            profile += '+' + row['profile_id']
            digest = value_sha({'previous': digest, 'arrays': value_sha(self.arrays)})
            notes += ('Arrays use a bounded primitive-reference heap in v4-v6; historical reconstruction and external equivalence are unverified.',)
        if self.environment:
            row=self.environment['versions'][str(language_version)]
            profile += '+'+row['profile_id']
            digest=value_sha({'previous':digest,'environment':value_sha(self.environment)})
            notes += ('UTC environment and bar phases follow pinned artifact-defined rules; external platform equivalence is unverified.',)
        if self.signals:
            from packages.contracts.signals import NOTE
            row=self.signals['versions'][str(language_version)];profile+='+'+row['profile_id']
            digest=value_sha({'previous':digest,'signals':value_sha(self.signals)});notes+=(NOTE,)
        if self.partial_exits:
            from packages.contracts.partial_exits import NOTE
            row=self.partial_exits['versions'][str(language_version)];profile+='+'+row['profile_id']
            digest=value_sha({'previous':digest,'partial_exits':value_sha(self.partial_exits)});notes+=(NOTE,)
        if self.pending_entries:
            from packages.contracts.pending_entries import NOTE
            row=self.pending_entries['versions'][str(language_version)];profile+='+'+row['profile_id']
            digest=value_sha({'previous':digest,'pending_entries':value_sha(self.pending_entries)});notes+=(NOTE,)
        if self.lots:
            from packages.contracts.lots import NOTE
            row=self.lots['versions'][str(language_version)];profile+='+'+row['profile_id']
            digest=value_sha({'previous':digest,'lots':value_sha(self.lots)});notes+=(NOTE,)
        if self.order_groups:
            from packages.contracts.order_groups import NOTE
            row=self.order_groups['versions'][str(language_version)];profile+='+'+row['profile_id']
            digest=value_sha({'previous':digest,'order_groups':value_sha(self.order_groups)});notes+=(NOTE,)
        if self.trailing:
            from packages.contracts.trailing import NOTE
            row=self.trailing['versions'][str(language_version)];profile+='+'+row['profile_id']
            digest=value_sha({'previous':digest,'trailing':value_sha(self.trailing)});notes+=(NOTE,)
        if self.economics:
            from packages.contracts.economics import NOTE
            row=self.economics['versions'][str(language_version)];profile+='+'+row['profile_id']
            digest=value_sha({'previous':digest,'economics':value_sha(self.economics)});notes+=(NOTE,)
        if self.risk:
            from packages.contracts.risk import NOTE
            row=self.risk['versions'][str(language_version)];profile+='+'+row['profile_id']
            digest=value_sha({'previous':digest,'risk':value_sha(self.risk)});notes+=(NOTE,)
        if self.performance:
            from packages.contracts.performance import NOTE
            row=self.performance['versions'][str(language_version)];profile+='+'+row['profile_id']
            digest=value_sha({'previous':digest,'performance':value_sha(self.performance)});notes+=(NOTE,)
        return profile, digest, notes

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
        registry = self.registry
        if assertion_id not in {row["assertion_id"] for row in registry["assertions"]} and language_version in self.version_registries:
            registry = self.version_registries[language_version]
        assertions = index(registry["assertions"], "assertion_id")
        assertion = assertions.get(assertion_id)
        if not assertion:
            raise SpecError("SPEC_UNRESOLVED", f"unknown assertion: {assertion_id}")
        problem = resolution_problem(assertion, registry)
        if problem:
            raise SpecError("SPEC_UNRESOLVED", problem)
        scope = assertion["scope"]
        if language_version not in scope["language_versions"] or scope["semantic_profile_id"] not in (
            profile["semantic_profile_ids"]
        ) or scope["platform_profile_id"] != platform_profile:
            raise SpecError("PROFILE_UNCOVERED", "fact is outside the selected version/context profile")
        decision = index(registry["decisions"], "decision_id")[assertion["decision_ref"]]
        return copy.deepcopy(assertions[decision["selected_assertion"]]["value"])


def load_version_review(root, lock, definitions, registry_schema):
    pin = lock.get("version_review")
    if pin is None:
        return {}, {}
    directory = safe_path(root, pin["path"])
    if sha((directory / "manifest.json").read_bytes()) != pin["manifest_sha256"]:
        raise SpecError("SPEC_HASH", "version review manifest differs from lock")
    manifest = read_json(directory / "manifest.json")
    validate_document(manifest, definitions, "versions_manifest")
    verify_files(directory, manifest["files"])
    review = read_json(directory / "review.json")
    validate_document(review, definitions, "versions_review")
    if manifest["base_manifest_sha256"] != lock["manifest_sha256"] or review["base_manifest_sha256"] != lock["manifest_sha256"]:
        raise SpecError("SPEC_PROVENANCE", "version review belongs to another base snapshot")
    evidence = read_json(directory / "evidence.json")
    registries = {}
    for version in range(1, 6):
        row = review["versions"][str(version)]
        path = safe_path(directory, row["registry"])
        if sha(path.read_bytes()) != row["semantic_sha256"]:
            raise SpecError("SPEC_HASH", "version semantic identity differs from its registry")
        registry = read_json(path)
        version_schema = copy.deepcopy(registry_schema)
        version_schema["$defs"]["profile"]["properties"]["language_version"] = {"const": version}
        validate_registry(registry, evidence, version_schema)
        expected_scope = {"language_versions": [version], "semantic_profile_id": row["semantic_profile_id"], "platform_profile_id": "historical.confirmed.1"}
        if any(fact["scope"] != expected_scope for fact in registry["assertions"]) or any(
            symbol["language_versions"] != [version] for symbol in registry["symbols"]
        ) or len(registry["profiles"]) != 1 or registry["profiles"][0]["language_version"] != version or registry["profiles"][0]["semantic_profile_id"] != row["semantic_profile_id"]:
            raise SpecError("SPEC_VERSION", "version review has cross-version members")
        registries[version] = registry
    return registries, review


def load_snapshot(root: Path = ROOT) -> Snapshot:
    require_python()
    root = root.resolve()
    definitions = _DocumentSchemas(read_json(root / "spec/schema/import.schema.json"))
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
    registry_schema = read_json(source_bundle / "registry.schema.json")
    validate_registry(registry, evidence, registry_schema)
    if semantic_projection(registry) != semantic["registry"]:
        raise SpecError("SPEC_REVIEW", "reviewed registry differs from published semantic projection")
    if read_json(bundle / "review/grammar.json") != semantic["grammar"]:
        raise SpecError("SPEC_REVIEW", "grammar differs from published bundle")
    version_registries, version_review = load_version_review(root, lock, definitions, registry_schema)
    language = {}
    if "language_release" in lock:
        pin = lock["language_release"]
        directory = safe_path(root, pin["path"])
        if sha((directory / "manifest.json").read_bytes()) != pin["manifest_sha256"]:
            raise SpecError("SPEC_HASH", "language release differs from lock")
        language_manifest = read_json(directory / "manifest.json")
        validate_document(language_manifest, definitions, "language_manifest")
        verify_files(directory, language_manifest["files"])
        language = read_json(directory / "policy.json")
        validate_document(language, definitions, "language_policy")
        if language["base_manifest_sha256"] != lock["manifest_sha256"] or language["version_review_manifest_sha256"] != lock["version_review"]["manifest_sha256"] or language["grammar_sha256"] != sha((bundle / "review/grammar.json").read_bytes()):
            raise SpecError("SPEC_PROVENANCE", "statement policy belongs to other core inputs")
        for version in range(1, 7):
            if language["versions"][str(version)]["language_version"] != version:
                raise SpecError("SPEC_VERSION", "statement policy version differs from its row")
    series = {}
    if "series_release" in lock:
        pin = lock["series_release"]
        directory = safe_path(root, pin["path"])
        if sha((directory / "manifest.json").read_bytes()) != pin["manifest_sha256"]:
            raise SpecError("SPEC_HASH", "series release differs from lock")
        series_manifest = read_json(directory / "manifest.json")
        validate_document(series_manifest, definitions, "series_manifest")
        verify_files(directory, series_manifest["files"])
        series = read_json(directory / "series.json")
        validate_document(series, definitions, "series_policy")
        if any(series[key] != lock[pin_key]["manifest_sha256"] for key, pin_key in (
            ("version_review_manifest_sha256", "version_review"), ("language_manifest_sha256", "language_release"))) or series["base_manifest_sha256"] != lock["manifest_sha256"]:
            raise SpecError("SPEC_PROVENANCE", "series policy belongs to other inputs")
    functions = {}
    if "function_release" in lock:
        pin = lock["function_release"]
        directory = safe_path(root, pin["path"])
        if sha((directory / "manifest.json").read_bytes()) != pin["manifest_sha256"]:
            raise SpecError("SPEC_HASH", "function release differs from lock")
        function_manifest = read_json(directory / "manifest.json")
        validate_document(function_manifest, definitions, "function_manifest")
        verify_files(directory, function_manifest["files"])
        functions = read_json(directory / "functions.json")
        validate_document(functions, definitions, "function_policy")
        if functions["base_manifest_sha256"] != lock["manifest_sha256"] or any(
            functions[key] != lock[pin_key]["manifest_sha256"] for key, pin_key in (
                ("version_review_manifest_sha256", "version_review"), ("language_manifest_sha256", "language_release"),
                ("series_manifest_sha256", "series_release"))):
            raise SpecError("SPEC_PROVENANCE", "function policy belongs to other inputs")
    requests = {}
    if "request_release" in lock:
        pin=lock["request_release"];directory=safe_path(root,pin["path"])
        if sha((directory/"manifest.json").read_bytes())!=pin["manifest_sha256"]:
            raise SpecError("SPEC_HASH","Request release differs from lock")
        request_manifest=read_json(directory/"manifest.json")
        validate_document(request_manifest,definitions,"request_manifest");verify_files(directory,request_manifest["files"])
        requests=read_json(directory/"requests.json");validate_document(requests,definitions,"request_policy")
        if requests["base_manifest_sha256"]!=lock["manifest_sha256"] or requests["version_review_manifest_sha256"]!=lock["version_review"]["manifest_sha256"]:
            raise SpecError("SPEC_PROVENANCE","Request policy belongs to other inputs")
    strategies={}
    if 'strategy_release' in lock:
        pin=lock['strategy_release'];directory=safe_path(root,pin['path'])
        if sha((directory/'manifest.json').read_bytes())!=pin['manifest_sha256']:raise SpecError('SPEC_HASH','Strategy release differs from lock.')
        strategy_manifest=read_json(directory/'manifest.json');validate_document(strategy_manifest,definitions,'strategy_manifest')
        verify_files(directory,strategy_manifest['files']);strategies=read_json(directory/'strategies.json')
        validate_document(strategies,definitions,'strategy_policy')
        if strategies['base_manifest_sha256']!=lock['manifest_sha256'] or strategies['version_review_manifest_sha256']!=lock['version_review']['manifest_sha256']:
            raise SpecError('SPEC_PROVENANCE','Strategy policy belongs to other inputs.')
    inputs={}
    if 'input_release' in lock:
        pin=lock['input_release'];directory=safe_path(root,pin['path'])
        if sha((directory/'manifest.json').read_bytes())!=pin['manifest_sha256']:raise SpecError('SPEC_HASH','Input release differs from lock.')
        input_manifest=read_json(directory/'manifest.json');validate_document(input_manifest,definitions,'input_manifest')
        verify_files(directory,input_manifest['files']);inputs=read_json(directory/'inputs.json')
        validate_document(inputs,definitions,'input_policy')
        if inputs['base_manifest_sha256']!=lock['manifest_sha256'] or inputs['version_review_manifest_sha256']!=lock['version_review']['manifest_sha256']:
            raise SpecError('SPEC_PROVENANCE','Input policy belongs to other inputs.')
    technical={}
    if 'technical_release' in lock:
        pin=lock['technical_release'];directory=safe_path(root,pin['path'])
        if sha((directory/'manifest.json').read_bytes())!=pin['manifest_sha256']:raise SpecError('SPEC_HASH','Technical release differs from lock.')
        technical_manifest=read_json(directory/'manifest.json');validate_document(technical_manifest,definitions,'technical_manifest')
        verify_files(directory,technical_manifest['files']);technical=read_json(directory/'technical.json')
        validate_document(technical,definitions,'technical_policy')
        if technical['base_manifest_sha256']!=lock['manifest_sha256'] or technical['version_review_manifest_sha256']!=lock['version_review']['manifest_sha256']:
            raise SpecError('SPEC_PROVENANCE','Technical policy belongs to other inputs.')
    exits={}
    composite={}
    if 'composite_release' in lock:
        pin=lock['composite_release'];directory=safe_path(root,pin['path'])
        if sha((directory/'manifest.json').read_bytes())!=pin['manifest_sha256']:raise SpecError('SPEC_PROVENANCE','Composite manifest differs from lock.')
        manifest_composite=read_json(directory/'manifest.json');validate_document(manifest_composite,definitions,'composite_manifest')
        verify_files(directory,manifest_composite['files']);composite=read_json(directory/'composite.json')
        validate_document(composite,definitions,'composite_policy')
        if composite['base_manifest_sha256']!=lock['manifest_sha256'] or composite['version_review_manifest_sha256']!=lock['version_review']['manifest_sha256']:
            raise SpecError('SPEC_PROVENANCE','Composite policy belongs to other inputs.')
    loops = {}
    if 'loop_release' in lock:
        pin = lock['loop_release']; directory = safe_path(root, pin['path'])
        if sha((directory/'manifest.json').read_bytes()) != pin['manifest_sha256']:raise SpecError('SPEC_HASH', 'Loop release differs from lock.')
        loop_manifest = read_json(directory/'manifest.json'); validate_document(loop_manifest, definitions, 'loop_manifest')
        verify_files(directory, loop_manifest['files']); loops = read_json(directory/'loops.json')
        validate_document(loops, definitions, 'loop_policy')
        if loops['base_manifest_sha256'] != lock['manifest_sha256'] or loops['version_review_manifest_sha256'] != lock['version_review']['manifest_sha256']:
            raise SpecError('SPEC_PROVENANCE', 'Loop policy belongs to other inputs.')
    arrays = {}
    if 'array_release' in lock:
        pin = lock['array_release'];directory = safe_path(root,pin['path'])
        if sha((directory/'manifest.json').read_bytes()) != pin['manifest_sha256']:raise SpecError('SPEC_HASH','Array release differs from lock.')
        array_manifest = read_json(directory/'manifest.json');validate_document(array_manifest,definitions,'array_manifest')
        verify_files(directory,array_manifest['files']);arrays = read_json(directory/'arrays.json')
        validate_document(arrays,definitions,'array_policy')
        if arrays['base_manifest_sha256'] != lock['manifest_sha256'] or arrays['version_review_manifest_sha256'] != lock['version_review']['manifest_sha256']:
            raise SpecError('SPEC_PROVENANCE','Array policy belongs to other inputs.')
    pending_entries={}
    if 'pending_entry_release' in lock:
        pin=lock['pending_entry_release'];directory=safe_path(root,pin['path'])
        if sha((directory/'manifest.json').read_bytes())!=pin['manifest_sha256']:raise SpecError('SPEC_HASH','Pending entry manifest differs.')
        em=read_json(directory/'manifest.json');validate_document(em,definitions,'pending_entry_manifest');verify_files(directory,em['files'])
        pending_entries=read_json(directory/'pending-entries.json');validate_document(pending_entries,definitions,'pending_entry_policy')
        if pending_entries['base_manifest_sha256']!=lock['manifest_sha256'] or pending_entries['version_review_manifest_sha256']!=lock['version_review']['manifest_sha256']:raise SpecError('SPEC_PROVENANCE','Pending entries belong to other inputs.')
    lots={}
    if 'lot_release' in lock:
        pin=lock['lot_release'];directory=safe_path(root,pin['path'])
        if sha((directory/'manifest.json').read_bytes())!=pin['manifest_sha256']:raise SpecError('SPEC_HASH','Lot manifest differs.')
        em=read_json(directory/'manifest.json');validate_document(em,definitions,'lot_manifest');verify_files(directory,em['files'])
        lots=read_json(directory/'lots.json');validate_document(lots,definitions,'lot_policy')
        if lots['base_manifest_sha256']!=lock['manifest_sha256'] or lots['version_review_manifest_sha256']!=lock['version_review']['manifest_sha256']:raise SpecError('SPEC_PROVENANCE','Lots belong to other inputs.')
    order_groups={}
    if 'order_group_release' in lock:
        pin=lock['order_group_release'];directory=safe_path(root,pin['path'])
        if sha((directory/'manifest.json').read_bytes())!=pin['manifest_sha256']:raise SpecError('SPEC_HASH','Order group manifest differs.')
        em=read_json(directory/'manifest.json');validate_document(em,definitions,'order_group_manifest');verify_files(directory,em['files'])
        order_groups=read_json(directory/'order-groups.json');validate_document(order_groups,definitions,'order_group_policy')
        if order_groups['base_manifest_sha256']!=lock['manifest_sha256'] or order_groups['version_review_manifest_sha256']!=lock['version_review']['manifest_sha256']:raise SpecError('SPEC_PROVENANCE','Order groups belong to other inputs.')
    trailing={}
    if 'trailing_release' in lock:
        pin=lock['trailing_release'];directory=safe_path(root,pin['path'])
        if sha((directory/'manifest.json').read_bytes())!=pin['manifest_sha256']:raise SpecError('SPEC_HASH','Trailing manifest differs.')
        em=read_json(directory/'manifest.json');validate_document(em,definitions,'trailing_manifest');verify_files(directory,em['files'])
        trailing=read_json(directory/'trailing.json');validate_document(trailing,definitions,'trailing_policy')
        if trailing['base_manifest_sha256']!=lock['manifest_sha256'] or trailing['version_review_manifest_sha256']!=lock['version_review']['manifest_sha256']:raise SpecError('SPEC_PROVENANCE','Trailings belong to other inputs.')
    economics={}
    if 'economics_release' in lock:
        pin=lock['economics_release'];directory=safe_path(root,pin['path'])
        if sha((directory/'manifest.json').read_bytes())!=pin['manifest_sha256']:raise SpecError('SPEC_HASH','Economic manifest differs.')
        em=read_json(directory/'manifest.json');validate_document(em,definitions,'economics_manifest');verify_files(directory,em['files'])
        economics=read_json(directory/'economics.json');validate_document(economics,definitions,'economics_policy')
        if economics['base_manifest_sha256']!=lock['manifest_sha256'] or economics['version_review_manifest_sha256']!=lock['version_review']['manifest_sha256']:raise SpecError('SPEC_PROVENANCE','Economics belong to other inputs.')
    risk={}
    if 'risk_release' in lock:
        pin=lock['risk_release'];directory=safe_path(root,pin['path'])
        if sha((directory/'manifest.json').read_bytes())!=pin['manifest_sha256']:raise SpecError('SPEC_HASH','Risk manifest differs.')
        em=read_json(directory/'manifest.json');validate_document(em,definitions,'risk_manifest');verify_files(directory,em['files'])
        risk=read_json(directory/'risk.json');validate_document(risk,definitions,'risk_policy')
        if risk['base_manifest_sha256']!=lock['manifest_sha256'] or risk['version_review_manifest_sha256']!=lock['version_review']['manifest_sha256']:raise SpecError('SPEC_PROVENANCE','Risks belong to other inputs.')
    performance={}
    if 'performance_release' in lock:
        pin=lock['performance_release'];directory=safe_path(root,pin['path'])
        if sha((directory/'manifest.json').read_bytes())!=pin['manifest_sha256']:raise SpecError('SPEC_HASH','Performance manifest differs.')
        em=read_json(directory/'manifest.json');validate_document(em,definitions,'performance_manifest');verify_files(directory,em['files'])
        performance=read_json(directory/'performance.json');validate_document(performance,definitions,'performance_policy')
        if performance['base_manifest_sha256']!=lock['manifest_sha256'] or performance['version_review_manifest_sha256']!=lock['version_review']['manifest_sha256']:raise SpecError('SPEC_PROVENANCE','Performances belong to other inputs.')
    partial_exits={}
    if 'partial_exit_release' in lock:
        pin=lock['partial_exit_release'];directory=safe_path(root,pin['path'])
        if sha((directory/'manifest.json').read_bytes())!=pin['manifest_sha256']:raise SpecError('SPEC_HASH','Partial exit manifest differs.')
        manifest_partial=read_json(directory/'manifest.json');validate_document(manifest_partial,definitions,'partial_exit_manifest');verify_files(directory,manifest_partial['files'])
        partial_exits=read_json(directory/'partial-exits.json');validate_document(partial_exits,definitions,'partial_exit_policy')
        if partial_exits['base_manifest_sha256']!=lock['manifest_sha256'] or partial_exits['version_review_manifest_sha256']!=lock['version_review']['manifest_sha256']:raise SpecError('SPEC_PROVENANCE','Partial exits belong to other inputs.')
    signals={}
    if 'signal_release' in lock:
        pin=lock['signal_release'];directory=safe_path(root,pin['path'])
        if sha((directory/'manifest.json').read_bytes())!=pin['manifest_sha256']:raise SpecError('SPEC_HASH','Signal manifest differs.')
        sm=read_json(directory/'manifest.json');validate_document(sm,definitions,'signal_manifest');verify_files(directory,sm['files'])
        signals=read_json(directory/'signals.json');validate_document(signals,definitions,'signal_policy')
        if signals['base_manifest_sha256']!=lock['manifest_sha256'] or signals['version_review_manifest_sha256']!=lock['version_review']['manifest_sha256']:raise SpecError('SPEC_PROVENANCE','Signals belong to other inputs.')
    environment = {}
    if 'environment_release' in lock:
        pin=lock['environment_release'];directory=safe_path(root,pin['path'])
        if sha((directory/'manifest.json').read_bytes())!=pin['manifest_sha256']:raise SpecError('SPEC_HASH','Environment release differs from lock.')
        em=read_json(directory/'manifest.json');validate_document(em,definitions,'environment_manifest')
        verify_files(directory,em['files']);environment=read_json(directory/'environment.json')
        validate_document(environment,definitions,'environment_policy')
        if environment['base_manifest_sha256']!=lock['manifest_sha256'] or environment['version_review_manifest_sha256']!=lock['version_review']['manifest_sha256']:
            raise SpecError('SPEC_PROVENANCE','Environment belongs to other inputs.')
    if 'exit_release' in lock:
        pin=lock['exit_release'];directory=safe_path(root,pin['path'])
        if sha((directory/'manifest.json').read_bytes())!=pin['manifest_sha256']:raise SpecError('SPEC_HASH','Exit release differs from lock.')
        exit_manifest=read_json(directory/'manifest.json');validate_document(exit_manifest,definitions,'exit_manifest')
        verify_files(directory,exit_manifest['files']);exits=read_json(directory/'exits.json')
        validate_document(exits,definitions,'exit_policy')
        if exits['base_manifest_sha256']!=lock['manifest_sha256'] or exits['version_review_manifest_sha256']!=lock['version_review']['manifest_sha256']:
            raise SpecError('SPEC_PROVENANCE','Exit policy belongs to other inputs.')
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
        if version in version_registries:
            allowed = [version_review["versions"][str(version)]["semantic_profile_id"]]
        if profile["semantic_profile_ids"] != allowed:
            raise SpecError("SPEC_VERSION", "unreviewed inheritance of another language profile")
        if profile.get("language_profile_ids", []) != ([language["profiles"][str(version)]] if language else []):
            raise SpecError("SPEC_VERSION", "unreviewed statement-profile selection")
        if profile.get("series_profile_ids", []) != ([series["versions"][str(version)]["profile_id"]] if series else []):
            raise SpecError("SPEC_VERSION", "unreviewed series-profile selection")
        if profile.get("function_profile_ids", []) != ([functions["versions"][str(version)]["profile_id"]] if functions else []):
            raise SpecError("SPEC_VERSION", "unreviewed function-profile selection")
        if profile.get("request_profile_ids",[])!=([requests["versions"][str(version)]["profile_id"]] if requests else []):
            raise SpecError("SPEC_VERSION","unreviewed request-profile selection")
        if profile.get('strategy_profile_ids',[])!=([strategies['versions'][str(version)]['profile_id']] if strategies else []):
            raise SpecError('SPEC_VERSION','Unreviewed strategy-profile selection.')
        if profile.get('input_profile_ids',[])!=([inputs['versions'][str(version)]['profile_id']] if inputs else []):
            raise SpecError('SPEC_VERSION','Unreviewed input-profile selection.')
        if profile.get('exit_profile_ids',[])!=([exits['versions'][str(version)]['profile_id']] if exits else []):
            raise SpecError('SPEC_VERSION','Unreviewed exit-profile selection.')
        if profile.get('composite_profile_ids',[])!=([composite['versions'][str(version)]['profile_id']] if composite else []):
            raise SpecError('SPEC_VERSION','Unreviewed composite-profile selection.')
        if profile.get('technical_profile_ids',[])!=([technical['versions'][str(version)]['profile_id']] if technical else []):
            raise SpecError('SPEC_VERSION','Unreviewed technical-profile selection.')
        if profile.get('loop_profile_ids', []) != ([loops['versions'][str(version)]['profile_id']] if loops else []):
            raise SpecError('SPEC_VERSION', 'Unreviewed loop-profile selection.')
        if profile.get('array_profile_ids', []) != ([arrays['versions'][str(version)]['profile_id']] if arrays else []):
            raise SpecError('SPEC_VERSION','Unreviewed array-profile selection.')
        if profile.get('partial_exit_profile_ids',[])!=([partial_exits['versions'][str(version)]['profile_id']] if partial_exits else []):raise SpecError('SPEC_VERSION','Unreviewed partial exit profile.')
        if profile.get('pending_entry_profile_ids',[])!=([pending_entries['versions'][str(version)]['profile_id']] if pending_entries else []):raise SpecError('SPEC_VERSION','Unreviewed pending entry profile.')
        if profile.get('lot_profile_ids',[])!=([lots['versions'][str(version)]['profile_id']] if lots else []):raise SpecError('SPEC_VERSION','Unreviewed lot profile.')
        if profile.get('order_group_profile_ids',[])!=([order_groups['versions'][str(version)]['profile_id']] if order_groups else []):raise SpecError('SPEC_VERSION','Unreviewed order group profile.')
        if profile.get('trailing_profile_ids',[])!=([trailing['versions'][str(version)]['profile_id']] if trailing else []):raise SpecError('SPEC_VERSION','Unreviewed trailing profile.')
        if profile.get('economics_profile_ids',[])!=([economics['versions'][str(version)]['profile_id']] if economics else []):raise SpecError('SPEC_VERSION','Unreviewed economics profile.')
        if profile.get('risk_profile_ids',[])!=([risk['versions'][str(version)]['profile_id']] if risk else []):raise SpecError('SPEC_VERSION','Unreviewed risk profile.')
        if profile.get('performance_profile_ids',[])!=([performance['versions'][str(version)]['profile_id']] if performance else []):raise SpecError('SPEC_VERSION','Unreviewed performance profile.')
        if profile.get('signal_profile_ids',[])!=([signals['versions'][str(version)]['profile_id']] if signals else []):raise SpecError('SPEC_VERSION','Unreviewed signal profile.')
        if profile.get('environment_profile_ids',[]) != ([environment['versions'][str(version)]['profile_id']] if environment else []):raise SpecError('SPEC_VERSION','Unreviewed environment selection.')
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
    if loops:
        from packages.contracts.loops import loop_policy
        for version in range(1, 7):
            row = loops['versions'][str(version)]
            if row['policy'] != loop_policy(version) or row['profile_id'] != f'openpine.loops.v{version}.1':
                raise SpecError('IMPLEMENTATION_MISSING', 'Loop policy/profile differs.')
            expected = [{'entry_id': e['entry_id'], 'source_record_sha256': e['source_record_sha256']}
                for e in references[version]['entries'] if e['name'] in ('for', 'while', 'break', 'continue')]
            if row['evidence'] != expected:raise SpecError('SPEC_PROVENANCE', 'Loop evidence differs from its version catalog.')
    if composite:
        from packages.contracts.composite import composite_policy,composite_apis
        for version in range(1,7):
            row=composite['versions'][str(version)]
            apis=[{k:v for k,v in api.items() if k not in ('evidence','basis')} for api in row['apis']]
            if row['policy']!=composite_policy(version) or apis!=composite_apis(version):raise SpecError('IMPLEMENTATION_MISSING','Composite signatures/policy differ.')
            if row['profile_id']!=f'openpine.composite.v{version}.1':raise SpecError('SPEC_VERSION','Composite profile version differs.')
            for api in row['apis']:
                expected=[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in references[version]['entries'] if e['name']==api['name']]
                if api['evidence']!=expected:raise SpecError('SPEC_PROVENANCE','Composite evidence differs from version/name catalog.')
                if api['basis']!=('artifact_defined_with_explicit_reconstruction' if expected else 'explicit_reconstruction'):raise SpecError('SPEC_EVIDENCE','Composite reconstruction status differs.')
    if technical:
        from packages.contracts.technical import technical_policy,technical_apis
        for version in range(1,7):
            row=technical['versions'][str(version)];entries=index(references[version]['entries'],'entry_id')
            if row['policy']!=technical_policy(version):raise SpecError('IMPLEMENTATION_MISSING','Technical policy differs.')
            apis=[{k:v for k,v in api.items() if k not in ('evidence','basis')} for api in row['apis']]
            if apis!=technical_apis(version):raise SpecError('IMPLEMENTATION_MISSING','Technical signatures differ.')
            for api in row['apis']:
                expected=[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in references[version]['entries'] if e['name']==api['name']]
                if api['evidence']!=expected:raise SpecError('SPEC_PROVENANCE','Technical evidence differs from version/name catalog.')
                basis='artifact_defined_with_explicit_reconstruction' if expected else 'explicit_reconstruction'
                if api['basis']!=basis:raise SpecError('SPEC_EVIDENCE','Technical reconstruction status differs.')
    if inputs:
        from packages.contracts.inputs import input_policy
        for version in range(1,7):
            row=inputs['versions'][str(version)]
            if row['policy']!=input_policy(version):raise SpecError('IMPLEMENTATION_MISSING','Input policy is not implemented.')
            entries=index(references[version]['entries'],'entry_id')
            if not row['evidence']:raise SpecError('SPEC_EVIDENCE','Input profile lacks version evidence.')
            for proof in row['evidence']:
                entry=entries.get(proof['entry_id'])
                if entry is None or entry['source_record_sha256']!=proof['source_record_sha256'] or not (entry['name']=='input' or entry['name'].startswith('input.')):
                    raise SpecError('SPEC_PROVENANCE','Input evidence differs from its version catalog.')
    if exits:
        from packages.contracts.protection import exit_policy
        for version in range(1,7):
            row=exits['versions'][str(version)];entries=index(references[version]['entries'],'entry_id')
            if row['policy']!=exit_policy(version):raise SpecError('IMPLEMENTATION_MISSING','Exit policy is not implemented.')
            names=set()
            for proof in row['evidence']:
                entry=entries.get(proof['entry_id'])
                if entry is None or entry['source_record_sha256']!=proof['source_record_sha256']:raise SpecError('SPEC_PROVENANCE','Exit evidence differs from its version catalog.')
                names.add(entry['name'])
            if names!={'strategy.exit','strategy.cancel','strategy.cancel_all'}:raise SpecError('SPEC_EVIDENCE','Incomplete exit evidence.')
    if strategies:
        from packages.contracts.trading import strategy_policy
        for version in range(1,7):
            row=strategies['versions'][str(version)]
            if row['policy']!=strategy_policy(version):raise SpecError('IMPLEMENTATION_MISSING','Strategy policy is not implemented.')
            entries=index(references[version]['entries'],'entry_id')
            for proof in row['evidence']:
                entry=entries.get(proof['entry_id'])
                if entry is None or entry['source_record_sha256']!=proof['source_record_sha256']:raise SpecError('SPEC_PROVENANCE','Strategy evidence differs from its version catalog.')
    if requests:
        from packages.contracts.contexts import request_policy
        for version in range(1,7):
            row=requests["versions"][str(version)]
            if row["policy"]!=request_policy(version):raise SpecError("IMPLEMENTATION_MISSING","Request policy is not implemented")
            entries=index(references[version]["entries"],"entry_id")
            for proof in row["evidence"]:
                entry=entries.get(proof["entry_id"])
                if entry is None or entry["source_record_sha256"]!=proof["source_record_sha256"]:
                    raise SpecError("SPEC_PROVENANCE","Request evidence differs from its version catalog")
    if functions:
        for version in range(1, 7):
            row = functions["versions"][str(version)]
            if row["language_version"] != version or row["profile_id"] != f"openpine.functions.v{version}.1":
                raise SpecError("SPEC_VERSION", "function policy belongs to another version")
            entries = index(references[version]["entries"], "entry_id")
            for proof in row["evidence"]:
                entry = entries.get(proof["entry_id"])
                if entry is None or entry["source_record_sha256"] != proof["source_record_sha256"]:
                    raise SpecError("SPEC_PROVENANCE", "function evidence differs from its version catalog")
    if series:
        for version in range(1, 7):
            row = series["versions"][str(version)]
            if row["language_version"] != version or row["profile_id"] != f"openpine.series.v{version}.1":
                raise SpecError("SPEC_VERSION", "series policy belongs to another version")
            entries = index(references[version]["entries"], "entry_id")
            api_ids = set()
            for item in row["apis"]:
                if item["id"] in api_ids or not item["id"].startswith(f"openpine.series.v{version}."):
                    raise SpecError("SPEC_IDENTITY", "duplicate or foreign series API identity")
                api_ids.add(item["id"])
                if len({p["name"] for p in item["parameters"]}) != len(item["parameters"]):
                    raise SpecError("SPEC_IDENTITY", "duplicate series API parameter")
            if len({r["name"] for r in row["references"]}) != len(row["references"]):
                raise SpecError("SPEC_IDENTITY", "duplicate series reference")
            for item in [*row["apis"], *row["references"]]:
                if item["basis"] == "artifact_defined" and not item["evidence"]:
                    raise SpecError("SPEC_EVIDENCE", "artifact-defined API lacks evidence")
                for proof in item["evidence"]:
                    entry = entries.get(proof["entry_id"])
                    if not entry or entry["name"] != item["name"] or entry["source_record_sha256"] != proof["source_record_sha256"]:
                        raise SpecError("SPEC_PROVENANCE", "series evidence differs from pinned catalog entry")
    if version_registries:
        directory = safe_path(root, lock["version_review"]["path"])
        for source in read_json(directory / "evidence.json")["sources"]["sources"]:
            if source["kind"] != "pinned_reference_review":
                continue
            reference = references[source["language_version"]]
            entry = index(reference["entries"], "entry_id").get(source["entry_id"])
            if entry is None or entry["source_record_sha256"] != source["source_record_sha256"] or reference["source_catalog_sha256"] != source["catalog_sha256"]:
                raise SpecError("SPEC_PROVENANCE", "review evidence differs from pinned catalog entry")
    catalog_parameters = {(entry["entry_id"], parameter["name"])
                          for reference in references.values() for entry in reference["entries"]
                          for parameter in entry["parameters"]}
    if catalog_parameters != parameter_aliases.keys():
        raise SpecError("SPEC_IDENTITY", "catalog parameter set differs from stable identities")
    if arrays:
        from packages.contracts.arrays import array_policy, array_apis
        for version in range(1,7):
            row = arrays['versions'][str(version)]
            if row['policy'] != array_policy(version) or row['apis'] != array_apis(version):raise SpecError('IMPLEMENTATION_MISSING','Array ABI/policy differs.')
            names = {api['name'] for api in row['apis']} | ({'for...in'} if version >= 5 else set())
            expected = [{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in references[version]['entries'] if e['name'] in names]
            if row['evidence'] != expected:raise SpecError('SPEC_PROVENANCE','Array evidence differs from the version catalog.')
    if pending_entries:
        from packages.contracts.pending_entries import entry_policy,entry_apis
        for v in range(1,7):
            row=pending_entries['versions'][str(v)]
            if row['profile_id']!=f'openpine.pending-entries.v{v}.1' or row['policy']!=entry_policy(v) or encode(row['apis'])!=encode(entry_apis(v)):raise SpecError('IMPLEMENTATION_MISSING','Pending entry ABI differs.')
            expected=[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in references[v]['entries'] if e['name']=='strategy.entry']
            if row['evidence']!=expected:raise SpecError('SPEC_PROVENANCE','Pending entry evidence differs.')
    if lots:
        from packages.contracts.lots import lot_policy,lot_apis
        for v in range(1,7):
            row=lots['versions'][str(v)]
            if row['profile_id']!=f'openpine.lots.v{v}.1' or row['policy']!=lot_policy(v) or encode(row['apis'])!=encode(lot_apis(v)):raise SpecError('IMPLEMENTATION_MISSING','Lot ABI differs.')
            expected=[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in references[v]['entries'] if e['name'] in ('strategy','strategy.entry','strategy.close','strategy.exit')]
            if row['evidence']!=expected:raise SpecError('SPEC_PROVENANCE','Lot evidence differs.')
    if order_groups:
        from packages.contracts.order_groups import group_policy,group_apis,EVIDENCE_NAMES
        for v in range(1,7):
            row=order_groups['versions'][str(v)]
            if row['profile_id']!=f'openpine.order-groups.v{v}.1' or row['policy']!=group_policy(v) or encode(row['apis'])!=encode(group_apis(v)):raise SpecError('IMPLEMENTATION_MISSING','Order group ABI differs.')
            expected=[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in references[v]['entries'] if e['name'] in EVIDENCE_NAMES]
            if row['evidence']!=expected:raise SpecError('SPEC_PROVENANCE','Order group evidence differs.')
    if trailing:
        from packages.contracts.trailing import trailing_policy,trailing_apis,source_review,NOTE as trailing_note
        if trailing['review']['sources']!=[source_review()] or trailing['review']['basis']!=trailing_note or trailing['review']['external_pine_execution'] is not False:raise SpecError('SPEC_PROVENANCE','Trailing review differs from the reviewed source decisions.')
        for v in range(1,7):
            row=trailing['versions'][str(v)]
            if row['profile_id']!=f'openpine.trailing.v{v}.1' or row['policy']!=trailing_policy(v) or encode(row['apis'])!=encode(trailing_apis(v)):raise SpecError('IMPLEMENTATION_MISSING','Trailing ABI differs.')
            expected=[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in references[v]['entries'] if e['name']=='strategy.exit']
            if row['evidence']!=expected:raise SpecError('SPEC_PROVENANCE','Trailing evidence differs.')
    if economics:
        from packages.contracts.economics import economics_policy,economics_apis,source_review,NOTE as economics_note,NAMES
        if economics['review']['sources']!=[source_review()] or economics['review']['basis']!=economics_note or economics['review']['external_pine_execution'] is not False:raise SpecError('SPEC_PROVENANCE','Economic review differs from the reviewed source decisions.')
        for v in range(1,7):
            row=economics['versions'][str(v)]
            if row['profile_id']!=f'openpine.economics.v{v}.1' or row['policy']!=economics_policy(v) or encode(row['apis'])!=encode(economics_apis(v)):raise SpecError('IMPLEMENTATION_MISSING','Economic ABI differs.')
            expected=[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in references[v]['entries'] if e['name'] in NAMES]
            if row['evidence']!=expected:raise SpecError('SPEC_PROVENANCE','Economic evidence differs.')
    if risk:
        from packages.contracts.risk import risk_policy,risk_apis,source_review,NOTE as risk_note,NAMES
        if risk['review']['sources']!=[source_review()] or risk['review']['basis']!=risk_note or risk['review']['external_pine_execution'] is not False:raise SpecError('SPEC_PROVENANCE','Risk review differs from the reviewed source decisions.')
        for v in range(1,7):
            row=risk['versions'][str(v)]
            if row['profile_id']!=f'openpine.risk.v{v}.1' or row['policy']!=risk_policy(v) or encode(row['apis'])!=encode(risk_apis(v)):raise SpecError('IMPLEMENTATION_MISSING','Risk ABI differs.')
            expected=[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in references[v]['entries'] if e['name'] in NAMES]
            if row['evidence']!=expected:raise SpecError('SPEC_PROVENANCE','Risk evidence differs.')
    if performance:
        from packages.contracts.performance import policy,apis,NAMES,NOTE
        if performance['review']['basis']!=NOTE or performance['review']['external_pine_execution'] is not False:raise SpecError('SPEC_PROVENANCE','Performance review differs.')
        for v in range(1,7):
            row=performance['versions'][str(v)]
            if row['profile_id']!=f'openpine.performance.v{v}.1' or row['policy']!=policy(v) or encode(row['apis'])!=encode(apis(v)):raise SpecError('IMPLEMENTATION_MISSING','Performance ABI differs.')
            expected=[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in references[v]['entries'] if e['name'] in NAMES]
            if row['evidence']!=expected:raise SpecError('SPEC_PROVENANCE','Performance evidence differs.')
    if partial_exits:
        from packages.contracts.partial_exits import partial_policy,partial_apis
        for v in range(1,7):
            row=partial_exits['versions'][str(v)]
            if row['profile_id']!=f'openpine.partial-exits.v{v}.1' or row['policy']!=partial_policy(v) or encode(row['apis'])!=encode(partial_apis(v)):raise SpecError('IMPLEMENTATION_MISSING','Partial exit ABI differs.')
            expected=[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in references[v]['entries'] if e['name'] in ('strategy.close','strategy.exit')]
            if row['evidence']!=expected:raise SpecError('SPEC_PROVENANCE','Partial exit evidence differs.')
    if signals:
        from packages.contracts.signals import signal_apis,signal_policy
        for v in range(1,7):
            row=signals['versions'][str(v)]
            if row['profile_id']!=f'openpine.signals.v{v}.1' or row['policy']!=signal_policy(v) or [{k:x for k,x in a.items() if k not in ('basis','evidence')} for a in row['apis']]!=signal_apis(v):raise SpecError('IMPLEMENTATION_MISSING','Signal ABI differs.')
            for api in row['apis']:
                expected=[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in references[v]['entries'] if e['name']==api['name']]
                if not expected or api['evidence']!=expected or api['basis']!='artifact_defined_with_explicit_reconstruction':raise SpecError('SPEC_PROVENANCE','Signal evidence differs.')
    if environment:
        from packages.contracts.environment import environment_policy,environment_references,environment_apis
        for v in range(1,7):
            row=environment['versions'][str(v)]
            if row['policy']!=environment_policy(v) or row['references']!=environment_references(v) or row['apis']!=environment_apis(v):raise SpecError('IMPLEMENTATION_MISSING','Environment ABI differs.')
            names={e['name'] for e in (*row['references'],*row['apis'])}
            evidence=[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in references[v]['entries'] if e['name'] in names]
            if row['evidence']!=evidence:raise SpecError('SPEC_PROVENANCE','Environment evidence differs.')
    return Snapshot(lock, manifest, registry, profiles, references, semantic["grammar"], version_registries, version_review, language, series, functions, requests, strategies,inputs,exits,technical,composite,loops,arrays,environment,signals,partial_exits,pending_entries,lots,order_groups,trailing,economics,risk,performance)


def coverage(snapshot: Snapshot) -> dict:
    rows = []
    for version in range(1, 7):
        profile = snapshot.profiles[version]
        registries = [snapshot.registry]
        if version in snapshot.version_registries:
            registries.append(snapshot.version_registries[version])
        applicable = [(row, registry) for registry in registries for row in registry["assertions"]
                      if version in row["scope"]["language_versions"]]
        admitted = [row for row, registry in applicable
                    if row["scope"]["semantic_profile_id"] in profile["semantic_profile_ids"]
                    and resolution_problem(row, registry) is None]
        rows.append({
            "language_version": version,
            "reference_entries": len(snapshot.references[version]["entries"]),
            "reference_source_coverage": snapshot.references[version]["source_coverage"],
            "imported_applicable_assertions": len(applicable),
            "admitted_assertions": len(admitted),
            "unresolved_assertion_refs": sorted(row["assertion_id"] for row, registry in applicable
                                                if resolution_problem(row, registry)),
            "semantic_profile_ids": profile["semantic_profile_ids"],
            "language_profile_ids": profile.get("language_profile_ids", []),
            "series_profile_ids": profile.get("series_profile_ids", []),
            "function_profile_ids": profile.get("function_profile_ids", []),
            "request_profile_ids": profile.get("request_profile_ids", []),
            'strategy_profile_ids':profile.get('strategy_profile_ids',[]),
            'input_profile_ids':profile.get('input_profile_ids',[]),
            'exit_profile_ids':profile.get('exit_profile_ids',[]),
            'technical_profile_ids':profile.get('technical_profile_ids',[]),
            'composite_profile_ids':profile.get('composite_profile_ids',[]),
            'loop_profile_ids':profile.get('loop_profile_ids',[]),
            'array_profile_ids':profile.get('array_profile_ids',[]),
            'environment_profile_ids':profile.get('environment_profile_ids',[]),
            'signal_profile_ids':profile.get('signal_profile_ids',[]),
            'partial_exit_profile_ids':profile.get('partial_exit_profile_ids',[]),
            'pending_entry_profile_ids':profile.get('pending_entry_profile_ids',[]),
            'lot_profile_ids':profile.get('lot_profile_ids',[]),
            'order_group_profile_ids':profile.get('order_group_profile_ids',[]),
            'trailing_profile_ids':profile.get('trailing_profile_ids',[]),
            'economics_profile_ids':profile.get('economics_profile_ids',[]),
            'risk_profile_ids':profile.get('risk_profile_ids',[]),
            'performance_profile_ids':profile.get('performance_profile_ids',[]),
            "evidence_level": snapshot.version_review.get("versions", {}).get(str(version), {}).get("evidence_level", "documentation_derived"),
        })
    return {"schema_version": "openpine.spec.coverage.2", "snapshot": snapshot.lock["snapshot"],
            "versions": rows, "reference_entries": sum(row["reference_entries"] for row in rows),
            "implementation_coverage": "Reported separately by the conformance gate.",
            "meaning": "Imported specification inventory; not Pine compatibility or OpenPine execution proof."}
