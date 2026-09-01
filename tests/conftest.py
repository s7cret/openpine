from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from tests.admission_helpers import make_deployment_identity
from tests.rc4_fixtures import admitted_manifest

_REAL_RMTREE = shutil.rmtree


@pytest.fixture(autouse=True)
def _bind_exact_test_admission_identity(monkeypatch, request):
    """Route/CLI tests use a nonzero, structured deployment identity."""

    if request.node.path.name == "test_admission.py":
        yield
        return

    monkeypatch.setenv("OPENPINE_BUILD_COMMIT", "1" * 40)

    from openpine import admission
    from openpine.gateway import side_effects

    original = side_effects.require_http_admit

    def require_test_identity(state: object, mode: str) -> None:
        if getattr(state, "admission_identity", None) is None:
            setattr(state, "admission_identity", make_deployment_identity())
        if getattr(state, "admitted_manifest", None) is None:
            setattr(state, "admitted_manifest", admitted_manifest())
        original(state, mode)

    monkeypatch.setattr(side_effects, "require_http_admit", require_test_identity)
    for module_name in (
        "openpine.gateway.routes.backtest",
        "openpine.gateway.routes.trading",
    ):
        module = sys.modules.get(module_name)
        if module is not None:
            monkeypatch.setattr(module, "require_http_admit", require_test_identity)
    monkeypatch.setattr(
        admission,
        "admit_configured_deployment",
        lambda *, mode: admission.admit_deployment(
            mode=mode, deployment=make_deployment_identity()
        ),
    )
    yield


@pytest.fixture(autouse=True)
def _cleanup_repo_runtime_artifacts():
    """Keep release-gate tests hermetic after tests that exercise default paths."""
    root = Path(__file__).resolve().parents[1]
    _REAL_RMTREE(root / ".openpine", ignore_errors=True)
    yield
    _REAL_RMTREE(root / ".openpine", ignore_errors=True)


@pytest.fixture(autouse=True)
def _cleanup_backtest_terminal_state():
    """Synthetic run IDs are reusable only across isolated test cases."""

    def clear() -> None:
        module = sys.modules.get("openpine.gateway.routes.backtest")
        if module is None:
            return
        with module._ACTIVE_BACKTEST_WORKERS_LOCK:
            module._TERMINAL_BACKTEST_RUNS.clear()
            module._TERMINAL_BACKTEST_OUTCOMES.clear()

    clear()
    yield
    clear()


@pytest.fixture
def job_store(tmp_path: Path):
    """Real transactional Job v1 store for mutating route tests."""

    from openpine.jobs.persist import JobV1Store

    store = JobV1Store(tmp_path / "jobs-v1.sqlite")
    try:
        yield store
    finally:
        store.close()


# Keep async tests runnable when pytest-asyncio plugin autoload is disabled.
def pytest_pyfunc_call(pyfuncitem):
    import asyncio
    import inspect

    testfunction = pyfuncitem.obj
    if not inspect.iscoroutinefunction(testfunction):
        return None
    kwargs = {
        name: pyfuncitem.funcargs[name]
        for name in pyfuncitem._fixtureinfo.argnames
        if name in pyfuncitem.funcargs
    }
    asyncio.run(testfunction(**kwargs))
    return True
