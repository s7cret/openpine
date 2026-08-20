from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _acceptance_module():
    spec = importlib.util.spec_from_file_location(
        "verify_v5_sec_001", ROOT / "scripts" / "verify_v5_sec_001.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v5_sec_001_acceptance_is_real_and_complete() -> None:
    module = _acceptance_module()
    report = module.build_report()

    assert report["schema"] == "openpine.v5-sec-001.acceptance.v1"
    assert report["passed"] is True
    assert report["worker_uid"] > 0
    required_checks = {
        "captured_source_bytes",
        "parent_import_isolation",
    }
    assert required_checks <= set(report["checks"])
    assert set(report["checks"]) == set(module.CHECK_NAMES)
    assert all(report["checks"].values())
    assert report["evidence"]["scratch_bytes"] <= 16 * 1024 * 1024
    assert report["evidence"]["network"] == "blocked"
    assert 1 in report["evidence"]["visible_pids"]
