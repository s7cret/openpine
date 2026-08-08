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

import importlib
import importlib.metadata
import importlib.util
import json
import platform
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from openpine.gateway.deps import GatewayState, get_state
from openpine.stack_lock import package_tree_identity, stack_lock_summary

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
        from email.message import Message as _EmailMessage
        from typing import cast
        summary = cast(_EmailMessage, meta).get("Summary")
    except Exception:
        summary = None
    if not summary:
        return None
    # Collapse whitespace and strip trailing dot for a single-line label.
    return " ".join(str(summary).split()).rstrip(".")


def _runtime_version(name: str) -> str | None:
    try:
        module = importlib.import_module(name)
    except Exception:
        return None
    for attribute in ("__version__", "PACKAGE_VERSION", "VERSION"):
        value = getattr(module, attribute, None)
        if value is not None:
            return str(value)
    return None


def _distribution_vcs_commit(name: str) -> str | None:
    """Return the exact VCS commit recorded by pip, when one is available."""

    try:
        distribution = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    try:
        raw = distribution.read_text("direct_url.json")
    except (OSError, UnicodeError):
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    vcs_info = payload.get("vcs_info") if isinstance(payload, dict) else None
    commit = vcs_info.get("commit_id") if isinstance(vcs_info, dict) else None
    normalized = str(commit).lower() if commit is not None else ""
    if len(normalized) != 40 or any(char not in "0123456789abcdef" for char in normalized):
        return None
    return normalized


def _module_record(
    name: str,
    *,
    lock_version: str | None = None,
    lock_commit: str | None = None,
    lock_tree_sha256: str | None = None,
) -> dict[str, Any]:
    origin = _module_origin(name)
    installed = origin is not None
    module_version = _runtime_version(name) if installed else None
    try:
        distribution_version = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        distribution_version = None
    version = distribution_version or module_version
    version_conforms = bool(
        installed
        and lock_version is not None
        and distribution_version == lock_version
        and module_version in {None, lock_version}
    )
    lock_identity = lock_tree_sha256 or lock_commit
    installed_identity: str | None = None
    if installed and origin is not None and lock_tree_sha256 is not None:
        try:
            installed_identity = package_tree_identity(Path(origin).parent)
        except OSError:
            installed_identity = None
    elif installed and lock_commit is not None:
        installed_identity = _distribution_vcs_commit(name)
    identity_conforms = bool(
        lock_identity is not None and installed_identity == lock_identity
    )
    return {
        "name": name,
        "version": version,
        "module_version": module_version,
        "distribution_version": distribution_version,
        "lock_version": lock_version,
        "lock_identity": lock_identity,
        "installed_identity": installed_identity,
        "identity_conforms": identity_conforms,
        "conforms_to_lock": version_conforms and identity_conforms,
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
    stack_lock = stack_lock_summary()
    lock_components = {
        str(item["name"]): item for item in stack_lock["components"]
    }
    modules = [
        _module_record(
            name,
            lock_version=(
                str(lock_components[name]["version"])
                if name in lock_components
                else None
            ),
            lock_commit=(
                str(lock_components[name]["commit"])
                if name in lock_components and "commit" in lock_components[name]
                else None
            ),
            lock_tree_sha256=(
                str(lock_components[name]["tree_sha256"])
                if name in lock_components and "tree_sha256" in lock_components[name]
                else None
            ),
        )
        for name in _TRACKED_MODULES
    ]
    return {
        "modules": modules,
        "runtime": _build_runtime_info(),
        "stack_lock": stack_lock,
        "stack_conforms": bool(stack_lock["source_tree_matches"])
        and all(item["conforms_to_lock"] for item in modules),
    }
