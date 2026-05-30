"""Optional integration discovery for the 6-library Pine stack."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import ModuleType


CORE_LIBRARIES: tuple[str, ...] = (
    "pine2ast",
    "ast2python",
    "pinelib",
    "marketdata_provider",
    "backtest_engine",
    "optimizer",
)


@dataclass(frozen=True)
class LibraryStatus:
    """Import status for one external library."""

    name: str
    importable: bool
    version: str | None = None
    error: str | None = None


def import_library(name: str) -> ModuleType:
    """Import an installed external library by logical module name."""
    if name not in CORE_LIBRARIES:
        raise ValueError(f"Unsupported core library: {name}")
    return importlib.import_module(name)


def check_library(name: str) -> LibraryStatus:
    """Return import status for an installed core library."""
    try:
        module = import_library(name)
        version = (
            getattr(module, "__version__", None)
            or getattr(module, "PACKAGE_VERSION", None)
            or getattr(module, "RUNTIME_CONTRACT_VERSION", None)
        )
        return LibraryStatus(
            name=name,
            importable=True,
            version=str(version) if version is not None else None,
        )
    except Exception as exc:
        return LibraryStatus(
            name=name,
            importable=False,
            error=f"{type(exc).__name__}: {exc}",
        )


def check_core_libraries() -> list[LibraryStatus]:
    """Check all six core libraries required by OpenPine v3."""
    return [check_library(name) for name in CORE_LIBRARIES]
