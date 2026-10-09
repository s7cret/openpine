"""Admit only the published six-row metadata delta, preserving the frozen lock."""

from __future__ import annotations

from pathlib import Path

from openpine.verification.evidence_index import KEY_FIELDS, key_of
from openpine.verification.identity import read_json, verify

_PARENT = "sha256:a952fe691e4002903b2fc24db5f7ab5b16605a8d349dea51312561db9f42e012"
_BEFORE_CATALOG = "sha256:68565f5498c837ec4c7c50c1dc75a33c0c20f31f16a2f9dca773cdb4188331c5"
_AFTER_CATALOG = "sha256:dd0ea5dba67134134e33619ba760a2368f662514deae98fa78c47cbeecd575ae"
_PAIRS = {
    "NAMESPACE_FUNCTION": (
        "sha256:3fea10a27acbc73114ec3912393af293c20d779700b8d83f58943c0dec0eaf47",
        "sha256:174e017d398ddef2759937350e1a80600f5f736ac3f857aa27d0ab0ab7e8e6e9",
    ),
    "METHOD": (
        "sha256:6514c320027ce13c987fc4ff2f97039539bfb8e4ca7be671ab16e003a5b8c404",
        "sha256:28855876b6f72a7334e4a71b87fd9c967450153820aa671c007f33c48c8613de",
    ),
}


def reviewed_surface_lock(root: Path) -> dict:
    """Metadata identity only. Bindings and independent observations stay required."""
    historical = read_json(root / "verification/stage2-callable-lock.json")
    current = read_json(root / "verification/stage2-callable-current-lock.json")
    migration = read_json(root / "verification/stage2-sort-field-migration.json")
    verify(historical, "openpine.callable_denominator.v1")
    verify(current, "openpine.callable_denominator.v1")
    verify(migration, "openpine.callable_metadata_migration.v1")
    if (
        historical["content_hash"] != _PARENT
        or migration["parent_lock_hash"] != _PARENT
        or migration["current_lock_hash"] != current["content_hash"]
        or migration["publication_commit"] != "1bc8ddb72ae6a30b209b5491622dd386b3ade7f5"
        or migration["publication_fixture_hash"]
        != "sha256:a1d211f1268255a2f04f400aaa5c5ef516b7c75fc115abbbbbdb275ef97347f8"
        or migration["authority_url"]
        != "https://www.tradingview.com/pine-script-docs/release-notes/#binary-search-in-udt-arrays"
        or historical["denominator_kind"] != current["denominator_kind"]
    ):
        raise ValueError("unreviewed callable metadata migration")
    catalogs = {**historical["catalogs"], "6": _AFTER_CATALOG}
    if historical["catalogs"]["6"] != _BEFORE_CATALOG or current["catalogs"] != catalogs:
        raise ValueError("migration changes an unreviewed catalog")
    before = {key_of(row): row for row in historical["rows"]}
    after = {key_of(row): row for row in current["rows"]}
    if len(before) != 2390 or len(after) != len(current["rows"]) or set(before) != set(after):
        raise ValueError("migration must retain all 2390 callable identities")
    expected = {}
    for kind, form in (("function", "NAMESPACE_FUNCTION"), ("method", "METHOD")):
        for name in (
            "array.binary_search",
            "array.binary_search_leftmost",
            "array.binary_search_rightmost",
        ):
            symbol = f"pine:{kind}:{name}"
            key = (6, symbol, symbol + "#canonical", form)
            old_hash, new_hash = _PAIRS[form]
            expected[key] = {
                **dict(zip(KEY_FIELDS, key)),
                "before_contract_hash": old_hash,
                "after_contract_hash": new_hash,
            }
    observed = {key_of(row): row for row in migration["changes"]}
    if len(observed) != len(migration["changes"]) or observed != expected:
        raise ValueError("migration is not the exact published six-row delta")
    for key, old_row in before.items():
        wanted = dict(old_row)
        if key in expected:
            if old_row["contract_hash"] != expected[key]["before_contract_hash"]:
                raise ValueError("migration parent contract mismatch")
            wanted["contract_hash"] = expected[key]["after_contract_hash"]
        if after[key] != wanted:
            raise ValueError("migration changes an unreviewed callable")
    return current
