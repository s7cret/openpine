from pathlib import Path

from openpine_contracts import list_schema_ids

PIN = "af9ecbc455e9af83cdc609f6b6ff85c40fb6c8bb"


def test_contracts_pin_and_catalog() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert (
        "openpine-contracts @ git+https://github.com/s7cret/openpine-contracts.git@"
        f"{PIN}"
        in text
    )
    assert "openpine.job.v1" in list_schema_ids()
