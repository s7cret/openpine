"""Published metadata delta preserves the original callable denominator."""

from pathlib import Path
import shutil
import json

import pytest

from openpine.verification.builtins import build_builtin_surface
from openpine.verification.callable_migration import reviewed_surface_lock
from openpine.verification.evidence_index import compare_surface_lock
from openpine.verification.identity import read_json, seal

ROOT = Path(__file__).resolve().parents[1]


def test_current_migration_matches_surface_without_hiding_historical_refusal():
    surface = build_builtin_surface()
    old = read_json(ROOT / "verification/stage2-callable-lock.json")
    current = reviewed_surface_lock(ROOT)
    historical = compare_surface_lock(surface, old)
    assert not historical["ok"] and len(historical["contract_changed"]) == 6
    assert historical["added"] == historical["removed"] == []
    assert compare_surface_lock(surface, current)["ok"]
    assert len(current["rows"]) == len(old["rows"]) == 2390
    assert all(
        row["status"] == "UNAVAILABLE"
        for row in surface["rows"]
        if row["pine_version"] == 6 and "array.binary_search" in row["symbol_id"]
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "drop-row",
        "unrelated-contract",
        "v5-catalog",
        "publication",
        "authority",
        "duplicate-delta",
        "drop-delta",
        "reverse-delta",
    ],
)
def test_resealed_unreviewed_migrations_still_fail_closed(tmp_path, mutation):
    folder = tmp_path / "verification"
    folder.mkdir()
    for name in (
        "stage2-callable-lock.json",
        "stage2-callable-current-lock.json",
        "stage2-sort-field-migration.json",
    ):
        shutil.copyfile(ROOT / "verification" / name, folder / name)
    current = read_json(folder / "stage2-callable-current-lock.json")
    migration = read_json(folder / "stage2-sort-field-migration.json")
    if mutation == "drop-row":
        current["rows"].pop()
    elif mutation == "unrelated-contract":
        current["rows"][0]["contract_hash"] = "sha256:" + "0" * 64
    elif mutation == "v5-catalog":
        current["catalogs"]["5"] = current["catalogs"]["6"]
    elif mutation == "publication":
        migration["publication_commit"] = "0" * 40
    elif mutation == "authority":
        migration["authority_url"] = "https://untrusted.invalid"
    elif mutation == "duplicate-delta":
        migration["changes"].append(migration["changes"][0])
    elif mutation == "drop-delta":
        migration["changes"].pop()
    else:
        migration["changes"][0]["after_contract_hash"] = migration["changes"][0][
            "before_contract_hash"
        ]
    current = seal({k: v for k, v in current.items() if k != "content_hash"})
    migration["current_lock_hash"] = current["content_hash"]
    migration = seal({k: v for k, v in migration.items() if k != "content_hash"})
    (folder / "stage2-callable-current-lock.json").write_text(json.dumps(current))
    (folder / "stage2-sort-field-migration.json").write_text(json.dumps(migration))
    with pytest.raises(ValueError):
        reviewed_surface_lock(tmp_path)
