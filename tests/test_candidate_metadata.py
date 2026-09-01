from __future__ import annotations

import tomllib
from pathlib import Path

from openpine import __version__
from openpine.release import _dependency_errors

ROOT = Path(__file__).resolve().parents[1]
STACK = {
    "pine2ast",
    "ast2python",
    "pinelib",
    "backtest-engine",
    "marketdata-provider",
    "optimizer",
    "openpine-contracts",
}


def test_candidate_package_metadata_has_one_exact_version_truth() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]

    assert project["version"] == __version__ == "5.0.0rc6"
    dependencies = [str(item) for item in project["dependencies"]]
    stack_rows = {
        row.split("==", 1)[0]: row for row in dependencies if "==" in row
    }
    assert set(stack_rows) == STACK
    assert all(row == f"{name}==5.0.0rc6" for name, row in stack_rows.items())
    assert not any("git+" in row or " @ " in row for row in dependencies)
    assert _dependency_errors(project) == []
