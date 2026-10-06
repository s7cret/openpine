"""Validate additive requirement accounting without admitting product evidence."""

from __future__ import annotations

from collections import Counter
import hashlib
from pathlib import Path, PurePosixPath

from openpine.verification.identity import digest, read_json, verify

SOURCE_PATH = "docs/OPENPINE_5_0_REMAINING_SPEC_2026-10-06.md"
SOURCE_SHA256 = "3f09ed3901f8ecf1262ffa983c6eaf52a51ceaf2087abe98db2093d45d9ca622"
HISTORICAL_LEDGER_HASH = "sha256:240e7415ac0fa50b127acaa3c901c725395fdb1e16bde0d78a1c674872e3b705"
CONTRACT_HASH = "sha256:3812132aa81565fca17f860d51c7645996b0ac649b01b09ea2b143ed1dd239ce"
MATRIX_HASH = "sha256:a1a244d0013982314c8dcf3312453922a5743a2a02df2b079d9dc7237fdf148e"
REPOS = frozenset(
    {
        "openpine",
        "pine2ast",
        "ast2python",
        "pinelib",
        "backtest_engine",
        "marketdata-provider",
        "optimizer",
        "openpine-contracts",
    }
)
IMMUTABLE_FIELDS = (
    "id",
    "title",
    "owner",
    "repo",
    "source",
    "work_type",
    "consumers",
    "consumer_slices",
    "applicability",
    "op_mapping",
    "existing_matrix_ids",
    "work",
    "acceptance",
    "reference",
    "work_package",
    "scope_rule",
    "start_after",
)
RECORD_FIELDS = frozenset(IMMUTABLE_FIELDS) | {
    "status",
    "remaining_reason",
    "execution_receipts",
    "nodeids_status",
    "references",
    "identities",
    "raw_receipts",
}
PYTHON_SUPPORT = {
    "implementation": "CPython",
    "requires_python": ">=3.13,<3.14",
    "gil_enabled": True,
    "free_threaded_supported": False,
    "mandatory_minors": ["3.13"],
    "amendment": "Approved ordinary CPython 3.13 GIL policy supersedes older multi-minor "
    "claims in the source; Pine 1-6 unchanged.",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _path(value: str) -> None:
    _require(isinstance(value, str) and bool(value), "missing reference path")
    path = PurePosixPath(value)
    _require(
        not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in value
        and str(path) == value,
        "reference path must be canonical and relative",
    )


def normative_contract(binding: dict) -> dict:
    """Frozen import contract; work status and uncollected receipts are separate."""
    return {
        "requirements": [
            {key: row[key] for key in IMMUTABLE_FIELDS} for row in binding["requirements"]
        ],
        **{
            key: binding[key]
            for key in (
                "op_mapping",
                "stage2_items",
                "historical_baseline",
                "cross_cutting_obligations",
                "reference_catalog",
            )
        },
    }


def validate_review_ledger(ledger: dict, matrix: dict | None = None) -> dict:
    """Require the frozen denominator even if callers delete or reseal its binding."""
    _require(isinstance(ledger, dict), "invalid review ledger")
    original = {key: value for key, value in ledger.items() if key != "remaining_spec_binding"}
    _require(digest(original) == HISTORICAL_LEDGER_HASH, "historical OP ledger changed")
    binding = ledger.get("remaining_spec_binding")
    if not isinstance(binding, dict):
        raise ValueError("missing remaining specification binding")
    _require(
        binding.get("schema_id") == "openpine.remaining_spec_binding.v1",
        "invalid remaining binding schema",
    )
    _require(
        binding.get("source")
        == {
            "path": SOURCE_PATH,
            "sha256": SOURCE_SHA256,
            "lines": 1259,
            "bytes": 144443,
        },
        "remaining source identity changed",
    )
    _require(
        binding.get("historical_ledger_hash") == HISTORICAL_LEDGER_HASH,
        "original ledger identity changed",
    )
    _require(
        binding.get("python_support") == PYTHON_SUPPORT, "unsupported mandatory Python or GIL claim"
    )
    _require(
        binding.get("registry_complete") is True
        and binding.get("full_stage2_accepted") is False
        and binding.get("full_release_accepted") is False,
        "accounting completeness cannot promote product acceptance",
    )
    _require(
        binding.get("scope") == "requirement accounting; no full product acceptance",
        "remaining binding is accounting only",
    )
    _require(
        binding.get("execution_policy") == "verification/execution-policy.json"
        and binding.get("stage_plan") == "verification/stages.json",
        "remaining registry detached from existing execution owners",
    )
    rows = binding.get("requirements")
    if not isinstance(rows, list):
        raise ValueError("remaining denominator must contain all 68 requirements")
    _require(
        isinstance(rows, list) and len(rows) == 68 and binding.get("requirement_count") == 68,
        "remaining denominator must contain all 68 requirements",
    )
    for row in rows:
        _require(
            isinstance(row, dict) and set(row) == RECORD_FIELDS,
            "remaining record fields changed or are missing",
        )
        _require(row["repo"] in REPOS, "unknown requirement repository")
        _require(
            row["status"] in {"partial", "toqualify", "toimplement", "blocked"},
            "full-scope closure requires applicable primary-evidence admission",
        )
        _require(
            isinstance(row["remaining_reason"], str) and bool(row["remaining_reason"].strip()),
            "unclosed requirement needs a concrete remaining reason",
        )
        _require(
            row["execution_receipts"] == [],
            "execution receipts require owner admission; P0 has none",
        )
        _require(
            row["raw_receipts"] == {"descriptors": [], "status": "not executed for full scope"},
            "raw receipts require owner admission; P0 has none",
        )
        references = row["references"]
        _require(
            isinstance(references, dict)
            and set(references)
            == {
                "implementation",
                "independent_basis",
                "tests",
            },
            "missing implementation, independent basis or test linkage",
        )
        for key, kind in (
            ("implementation", "code"),
            ("independent_basis", "evidence"),
            ("tests", "tests"),
        ):
            ref = references[key]
            _require(
                isinstance(ref, dict)
                and ref.get("bundle") == row["reference"]
                and ref.get("kind") == kind,
                "requirement reference bundle changed",
            )
        # A filename is a seed, not proof of collected parametrized obligations.
        _require(
            references["tests"].get("nodeids") == [],
            "nodeids require frozen owner collection; cannot invent them in accounting",
        )
        _require(
            references
            == {
                "implementation": {"bundle": row["reference"], "kind": "code"},
                "independent_basis": {
                    "bundle": row["reference"],
                    "kind": "evidence",
                    "status": "requires independent applicable review",
                },
                "tests": {
                    "bundle": row["reference"],
                    "kind": "tests",
                    "nodeids": [],
                    "status": "pending owner collection and scope review",
                },
            },
            "seed reference status cannot claim evidence admission",
        )
        identities = row["identities"]
        _require(
            identities
            == {
                "historical_baseline": "r2_prepared_inputs",
                "candidate_hash": None,
                "execution_plan_hash": None,
                "inventory_hash": None,
                "package_identity": None,
                "status": "not collected for this full requirement scope",
            },
            "full-scope execution identities remain uncollected",
        )
    ids = [row["id"] for row in rows]
    _require(len(set(ids)) == 68, "duplicate remaining requirement")
    try:
        contract = normative_contract(binding)
    except (KeyError, TypeError) as error:
        raise ValueError("incomplete remaining import contract") from error
    # This independent code anchor rejects mutations even if the ledger is rehashed.
    _require(
        binding.get("contract_hash") == CONTRACT_HASH and digest(contract) == CONTRACT_HASH,
        "remaining source/owner/type/OP/reference contract changed",
    )
    for bundle in binding["reference_catalog"].values():
        for references in bundle.values():
            for reference in references:
                if "component" in reference:
                    _require(reference["component"] in REPOS, "unknown reference repository")
                    _path(reference["path"])
                else:
                    _path(reference["historical_preparation_path"])
    mappings = binding["op_mapping"]
    _require(
        [row["id"] for row in mappings] == [f"OP-{n:02d}" for n in range(1, 37)],
        "original OP denominator is incomplete",
    )
    for mapping in mappings:
        inverse = [row["id"] for row in rows if mapping["id"] in row["op_mapping"]]
        _require(
            set(mapping["requirements"]) == set(inverse)
            and len(mapping["requirements"]) == len(inverse),
            "inconsistent bidirectional OP mapping",
        )
    covered = {item for row in rows for item in row["existing_matrix_ids"]}
    _require(
        covered == {row["id"] for row in binding["stage2_items"]},
        "existing Stage 2 item linkage is incomplete",
    )
    _require(binding.get("stage2_matrix_hash") == MATRIX_HASH, "Stage 2 baseline changed")
    if matrix is not None:
        verify(matrix, "openpine.stage2_remaining_matrix.v1")
        owners = [{"id": row["id"], "owner": row["owner"]} for row in matrix["items"]]
        _require(
            owners == binding["stage2_items"] and matrix.get("content_hash") == MATRIX_HASH,
            "existing Stage 2 items or owners changed",
        )
    return {
        "source_sha256": SOURCE_SHA256,
        "contract_hash": CONTRACT_HASH,
        "requirement_count": 68,
        "op_count": 36,
        "stage2_item_count": 16,
        "registry_complete": True,
        "status_counts": dict(Counter(row["status"] for row in rows)),
        "unclosed_requirements": [
            {
                "id": row["id"],
                "owner": row["owner"],
                "status": row["status"],
                "remaining_reason": row["remaining_reason"],
            }
            for row in rows
        ],
        "full_stage2_accepted": False,
        "full_release_accepted": False,
    }


def read_review_ledger(root: Path, matrix: dict | None = None) -> dict:
    ledger = read_json(root / "docs/RC6_REVIEW_36.json")
    result = validate_review_ledger(ledger, matrix)
    source = (root / SOURCE_PATH).read_bytes()
    _require(
        hashlib.sha256(source).hexdigest() == SOURCE_SHA256,
        "remaining source bytes differ from the imported specification",
    )
    return result
