"""Version manifest route.

Exposes a read-only ``GET /api/version`` endpoint that lists every Python
package backing the OpenPine stack together with its installed version and
on-disk location. Designed for the Settings page "Modules" panel: the UI
needs stable, file-path-shaped identifiers it can display as text without
giving the user any way to mutate them.

The endpoint resolves each module through ``importlib.util.find_spec`` and
``importlib.metadata.version`` so it works equally well for workspace
checkouts and PyPI installs. Missing packages are reported with
``version: null`` and ``installed: false`` so the UI can show a clear
"not installed" hint rather than 500.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import platform
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from openpine.gateway.deps import GatewayState, get_state

router = APIRouter(tags=["version"])


# Canonical list of modules that compose the OpenPine stack. The order here
# is the render order on the Settings page, so keep it user-meaningful:
# orchestrator first, then parser/compiler pipeline, then runtime, then
# market data. Anything new added to the stack should be appended.
_TRACKED_MODULES: tuple[str, ...] = (
    "openpine",
    "pine2ast",
    "ast2python",
    "pinelib",
    "marketdata_provider",
    "backtest_engine",
    "optimizer",
)


def _module_origin(name: str) -> str | None:
    """Return the on-disk path of the module's __init__.py, if discoverable.

    Uses ``importlib.util.find_spec`` which works for both regular packages
    and PEP 660 editable installs. Returns ``None`` when the module cannot
    be located at all.
    """
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return None
    if spec is None:
        return None
    if spec.origin is not None:
        return spec.origin
    # Namespace packages: spec.origin is None, but submodule_search_locations
    # points at the directory.
    locations = getattr(spec, "submodule_search_locations", None)
    if locations:
        first = next(iter(locations), None)
        if first:
            return str(Path(str(first)).joinpath("__init__.py"))
    return None


def _module_summary(name: str) -> str | None:
    try:
        meta = importlib.metadata.metadata(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    if meta is None:
        return None
    # PackageMetadata is an email.message.Message under the hood; access
    # the Summary header through its mapping protocol. Use a typed cast
    # to satisfy strict type checkers without runtime overhead.
    summary: str | None = None
    try:
        from typing import cast
        from email.message import Message as _EmailMessage
        summary = cast(_EmailMessage, meta).get("Summary")
    except Exception:
        summary = None
    if not summary:
        return None
    # Collapse whitespace and strip trailing dot for a single-line label.
    return " ".join(str(summary).split()).rstrip(".")


def _module_record(name: str) -> dict[str, Any]:
    origin = _module_origin(name)
    installed = origin is not None
    try:
        version = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        version = None
    return {
        "name": name,
        "version": version,
        "installed": installed,
        "path": origin,
        "summary": _module_summary(name) if installed else None,
    }


def _build_runtime_info() -> dict[str, Any]:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "node": platform.node(),
    }


@router.get("/version")
async def get_version_manifest(
    state: GatewayState = Depends(get_state),
) -> dict[str, Any]:
    """Return the installed OpenPine stack manifest.

    The response shape is stable: callers (UI, scripts, smoke tests) can
    rely on every entry below being present, even if its value is null.
    """
    return {
        "modules": [_module_record(name) for name in _TRACKED_MODULES],
        "runtime": _build_runtime_info(),
    }
