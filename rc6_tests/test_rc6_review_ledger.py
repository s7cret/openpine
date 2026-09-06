"""Validate review-accounting consistency, not semantic or TradingView acceptance."""

import json
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
LEDGER = json.loads((ROOT / "docs/RC6_REVIEW_36.json").read_text())


def test_all_original_review_ids_exist_exactly_once():
    tasks = LEDGER["tasks"]
    assert [row["id"] for row in tasks] == [f"OP-{i:02d}" for i in range(1, 37)]
    assert LEDGER["schema"] == "openpine.review.acceptance.v1"
    assert re.fullmatch("[0-9a-f]{64}", LEDGER["source_spec_sha256"])
    assert re.fullmatch("[0-9a-f]{40}", LEDGER["snapshot_base"])


@pytest.mark.parametrize("record", LEDGER["tasks"], ids=lambda row: row["id"])
def test_task_status_preserves_remaining_work_and_existing_evidence(record):
    assert record["title"].strip() and record["implemented"].strip()
    assert record["status"] in {"accepted", "partial", "unverified"}
    if record["status"] == "accepted":
        assert not record["remaining"].strip()
        assert record["evidence_paths"]
    else:
        assert record["remaining"].strip()
    for path in record["evidence_paths"]:
        assert not Path(path).is_absolute() and ".." not in Path(path).parts
        assert (ROOT / path).is_file(), path
    assert f"**{record['id']}**" in (ROOT / "docs/RC6_REVIEW_36.md").read_text()
