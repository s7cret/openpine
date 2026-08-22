"""Exact OpenPine build identity used by persisted contract envelopes."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from openpine import __version__

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_COMPILER_COMPONENTS = ("pine2ast", "ast2python", "pinelib", "openpine-contracts")


class BuildIdentityError(RuntimeError):
    """No exact producer identity is available for a mutating operation."""


@dataclass(frozen=True, slots=True)
class BuildIdentity:
    version: str
    commit: str

    @property
    def contract_version(self) -> str:
        match = re.fullmatch(r"(\d+\.\d+\.\d+)(a|b|rc)(\d+)", self.version)
        if match is None:
            return self.version
        return f"{match.group(1)}-{match.group(2)}.{match.group(3)}"


def current_build_identity() -> BuildIdentity:
    configured = os.environ.get("OPENPINE_BUILD_COMMIT", "").strip()
    if configured:
        if _SHA40.fullmatch(configured) is None:
            raise BuildIdentityError("OPENPINE_BUILD_COMMIT must be 40 lowercase hex")
        return BuildIdentity(version=__version__, commit=configured)

    root = Path(__file__).resolve().parents[1]
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "-C", str(root), "rev-parse", "HEAD"],  # noqa: S607
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BuildIdentityError("exact OpenPine producer commit is unavailable") from exc
    commit = result.stdout.strip()
    if result.returncode != 0 or _SHA40.fullmatch(commit) is None:
        raise BuildIdentityError("exact OpenPine producer commit is unavailable")
    return BuildIdentity(version=__version__, commit=commit)


def compiler_producer_commits() -> dict[str, str]:
    """Return the exact compiler-stack producer map for artifact sealing."""

    configured = os.environ.get("OPENPINE_PRODUCER_COMMITS_JSON", "").strip()
    if configured:
        try:
            payload = json.loads(configured)
        except json.JSONDecodeError as exc:
            raise BuildIdentityError("OPENPINE_PRODUCER_COMMITS_JSON is invalid JSON") from exc
        if not isinstance(payload, dict) or set(payload) != set(_COMPILER_COMPONENTS):
            raise BuildIdentityError(
                "OPENPINE_PRODUCER_COMMITS_JSON has an invalid component set"
            )
        commits = {component: payload[component] for component in _COMPILER_COMPONENTS}
        if not all(isinstance(value, str) and _SHA40.fullmatch(value) for value in commits.values()):
            raise BuildIdentityError(
                "OPENPINE_PRODUCER_COMMITS_JSON values must be exact Git SHAs"
            )
        return commits

    home = Path.home()
    roots = {
        "pine2ast": home / "pine2ast",
        "ast2python": home / "ast2python",
        "pinelib": home / "pinelib",
        "openpine-contracts": home / "openpine-contracts",
    }
    resolved_commits: dict[str, str] = {}
    for component, root in roots.items():
        try:
            result = subprocess.run(  # noqa: S603
                ["git", "-C", str(root), "rev-parse", "HEAD"],  # noqa: S607
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BuildIdentityError(
                f"exact compiler producer commit is unavailable: {component}"
            ) from exc
        commit = result.stdout.strip()
        if result.returncode != 0 or _SHA40.fullmatch(commit) is None:
            raise BuildIdentityError(
                f"exact compiler producer commit is unavailable: {component}"
            )
        resolved_commits[component] = commit
    return resolved_commits
