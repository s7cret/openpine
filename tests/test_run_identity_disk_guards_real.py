"""Run identity disk records cannot become directory escapes or corrupt replays."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from openpine_contracts import AdmitError

from openpine.run_identity import persist_run_identity, run_identity_path


def test_regular_data_directory_and_identity_directory_are_required(tmp_path: Path):
    payload = {"run_id": "safe", "content_hash": "sha256:" + "a" * 64}
    data_file = tmp_path / "not-a-directory"
    data_file.write_text("must survive")
    with pytest.raises(AdmitError, match="data directory") as error:
        persist_run_identity(data_file, "safe", payload)
    assert error.value.code == "RUN_IDENTITY_INVALID"
    assert data_file.read_text() == "must survive"

    run_directory = tmp_path / "run-identities"
    run_directory.write_text("must survive")
    with pytest.raises(AdmitError, match="identity directory is invalid") as error:
        persist_run_identity(tmp_path, "safe", payload)
    assert error.value.code == "RUN_IDENTITY_INVALID"
    assert run_directory.read_text() == "must survive"


def test_existing_nonregular_or_corrupt_record_is_never_reused(tmp_path: Path):
    payload = {"run_id": "safe", "content_hash": "sha256:" + "a" * 64}
    run_directory = tmp_path / "run-identities"
    run_directory.mkdir()
    target = run_identity_path(tmp_path, "safe")
    target.mkdir()
    with pytest.raises(AdmitError, match="regular file") as error:
        persist_run_identity(tmp_path, "safe", payload)
    assert error.value.code == "RUN_IDENTITY_INVALID"
    target.rmdir()
    target.write_text("{broken")
    with pytest.raises(AdmitError, match="corrupt") as error:
        persist_run_identity(tmp_path, "safe", payload)
    assert error.value.code == "RUN_IDENTITY_CONFLICT"
    assert target.read_text() == "{broken"
    target.write_text(json.dumps(payload))
    assert persist_run_identity(tmp_path, "safe", payload) == target
    assert json.loads(target.read_text()) == payload
