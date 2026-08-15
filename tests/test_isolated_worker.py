from pathlib import Path

import pytest

from openpine.runtime.isolated_worker import (
    IsolatedWorkerError,
    admit_generated_source,
    inspect_generated_in_process,
)


def test_safe_generated_source_is_admitted(tmp_path: Path) -> None:
    path = tmp_path / "generated_strategy.py"
    path.write_text("from pinelib import PineRuntime\n\nclass GeneratedStrategy:\n    pass\n")
    admission = admit_generated_source(path)
    assert "pinelib" in admission.imports
    assert admission.forbidden == ()


def test_socket_import_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "generated_strategy.py"
    path.write_text("import socket\n")
    with pytest.raises(IsolatedWorkerError, match="socket"):
        admit_generated_source(path)


def test_inspect_generated_in_separate_process(tmp_path: Path) -> None:
    path = tmp_path / "generated_strategy.py"
    path.write_text("class GeneratedStrategy:\n    pass\n")
    payload = inspect_generated_in_process(path)
    assert payload["ok"] is True
    assert "GeneratedStrategy" in payload["classes"]
