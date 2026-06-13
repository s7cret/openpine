from __future__ import annotations

import shutil
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _cleanup_repo_runtime_artifacts():
    """Keep release-gate tests hermetic after tests that exercise default paths."""
    root = Path(__file__).resolve().parents[1]
    shutil.rmtree(root / ".openpine", ignore_errors=True)
    yield
    shutil.rmtree(root / ".openpine", ignore_errors=True)


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
