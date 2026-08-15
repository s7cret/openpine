from pathlib import Path

import pytest

from openpine.runtime.isolated_worker import IsolatedWorkerError, admit_generated_source


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
