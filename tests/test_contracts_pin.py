from pathlib import Path

from openpine_contracts import list_schema_ids

def test_contracts_pin_and_catalog() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert '"openpine-contracts==5.0.0rc3"' in text
    assert "git+" not in text
    assert "openpine.job.v1" in list_schema_ids()
