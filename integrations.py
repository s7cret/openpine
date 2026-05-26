"""Optional integration discovery for the 6-library Pine stack."""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType


LIBRARY_PATHS: dict[str, Path] = {
    "pine2ast": Path("[local-home]/pine2ast"),
    "ast2python": Path("[local-home]/ast2python"),
    "pinelib": Path("[local-home]/pinelib"),
    "marketdata_provider": Path("[local-home]/marketdata-provider"),
    "backtest_engine": Path("[local-home]/backtest_engine"),
    "optimizer": Path("[local-home]/optimizer"),
}


@dataclass(frozen=True)
class LibraryStatus:
    """Import status for one external library."""

    name: str
    path: str
    importable: bool
    version: str | None = None
    error: str | None = None


def ensure_library_path(name: str) -> None:
    """Put a known local library path on sys.path if it exists."""
    path = LIBRARY_PATHS.get(name)
    if path is not None and path.exists():
        path_s = str(path)
        if path_s not in sys.path:
            sys.path.insert(0, path_s)


def import_library(name: str) -> ModuleType:
    """Import a local external library by logical module name."""
    ensure_library_path(name)
    return importlib.import_module(name)


def check_library(name: str) -> LibraryStatus:
    """Return import status for a configured local library."""
    path = LIBRARY_PATHS.get(name)
    try:
        module = import_library(name)
        version = (
            getattr(module, "__version__", None)
            or getattr(module, "PACKAGE_VERSION", None)
            or getattr(module, "RUNTIME_CONTRACT_VERSION", None)
        )
        return LibraryStatus(
            name=name,
            path=str(path or ""),
            importable=True,
            version=str(version) if version is not None else None,
        )
    except Exception as exc:
        return LibraryStatus(
            name=name,
            path=str(path or ""),
            importable=False,
            error=f"{type(exc).__name__}: {exc}",
        )


def check_core_libraries() -> list[LibraryStatus]:
    """Check all six core libraries required by OpenPine v3."""
    return [check_library(name) for name in LIBRARY_PATHS]
