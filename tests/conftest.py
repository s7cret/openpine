from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _cleanup_repo_runtime_artifacts():
    """Keep release-gate tests hermetic after tests that exercise default paths."""
    root = Path(__file__).resolve().parents[1]
    shutil.rmtree(root / ".openpine", ignore_errors=True)
    yield
    shutil.rmtree(root / ".openpine", ignore_errors=True)


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
