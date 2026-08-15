from pathlib import Path

from openpine_contracts import list_schema_ids

PIN = "51e32ebaaf02eecb81443e8ca7e89b2543cb25a3"


def test_contracts_pin_and_catalog() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert (
        "openpine-contracts @ git+https://github.com/s7cret/openpine-contracts.git@"
        f"{PIN}"
        in text
    )
    assert "openpine.job.v1" in list_schema_ids()
