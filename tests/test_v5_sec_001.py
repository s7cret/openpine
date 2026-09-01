from __future__ import annotations

import inspect
from pathlib import Path

from openpine.runtime import isolated_run, isolated_worker, rc6_worker_runtime

ROOT = Path(__file__).resolve().parents[1]


def test_v5_security_gate_is_superseded_by_rc6_native_worker() -> None:
    assert not (ROOT / "scripts" / "verify_v5_sec_001.py").exists()
    bootstrap = isolated_worker._BOOTSTRAP
    runtime_source = inspect.getsource(rc6_worker_runtime)
    isolated_source = inspect.getsource(isolated_run)
    assert "openpine_rc6_worker_runtime" in bootstrap
    assert "RC6WorkerProtocol" in bootstrap
    assert "openpine.generated_artifact.v3" in runtime_source
    assert "GeneratedScript" in runtime_source
    assert "_process_bar" not in runtime_source + isolated_source
