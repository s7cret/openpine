from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from openpine.runtime import engine, isolated_run, isolated_worker, rc6_worker_runtime
from openpine.workers import strategy_job_executor


def test_rc6_distribution_has_no_removed_compile_or_direct_pinelib_adapters() -> None:
    assert importlib.util.find_spec("openpine.compile.adapter") is None
    assert importlib.util.find_spec("openpine.data.direct_data_provider") is None


def test_rc6_runtime_sources_have_no_legacy_generated_entrypoint_heuristics() -> None:
    for module in (engine, isolated_run, isolated_worker, rc6_worker_runtime):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "generated_artifact.v2" not in source
        assert "_process_bar" not in source


def test_rc6_openpine_sources_do_not_import_removed_pinelib_domains() -> None:
    root = Path(__file__).parents[1] / "openpine"
    source = "\n".join(path.read_text() for path in root.rglob("*.py"))
    for token in (
        "pinelib.core.bar",
        "pinelib.strategy",
        "pinelib.plot",
        "generated_artifact.v2",
        "pine_runtime",
    ):
        assert token not in source
    assert re.search(r"\bPineRuntime\b", source) is None


def test_rc6_strategy_jobs_have_no_in_process_generated_class_path() -> None:
    source = Path(strategy_job_executor.__file__).read_text(encoding="utf-8")
    assert "strategy_loader" not in source
    assert "runtime_data_provider" not in source


def test_legacy_runtime_guard_allows_the_current_diagnostic_type():
    assert re.search(r"\bPineRuntime\b", "from pinelib.errors import PineRuntimeError") is None
    assert re.search(r"\bPineRuntime\b", "from pinelib import PineRuntime as Legacy")
