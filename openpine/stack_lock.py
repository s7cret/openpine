"""Packaged identity and coordinated-ref validation for the OpenPine stack."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from openpine import __version__

STACK_LOCK_PATH = Path(__file__).with_name("stack-lock.json")
STACK_LOCK_SCHEMA = "openpine.stack-lock.v1"
EXPECTED_COMPONENTS = (
    "openpine",
    "pine2ast",
    "ast2python",
    "pinelib",
    "backtest_engine",
    "marketdata_provider",
    "optimizer",
)
EXPECTED_COMPONENT_METADATA: dict[str, tuple[str, str]] = {
    "openpine": ("openpine", "s7cret/openpine"),
    "pine2ast": ("pine2ast", "s7cret/pine2ast"),
    "ast2python": ("ast2python", "s7cret/ast2python"),
    "pinelib": ("pinelib", "s7cret/pinelib"),
    "backtest_engine": ("backtest-engine", "s7cret/backtest_engine"),
    "marketdata_provider": ("marketdata-provider", "s7cret/marketdata-provider"),
    "optimizer": ("optimizer", "s7cret/optimizer"),
}
_NONZERO_SHA40 = re.compile(r"(?!0{40}$)[0-9a-f]{40}")
_NONZERO_SHA256 = re.compile(r"(?!0{64}$)[0-9a-f]{64}")


def _github_repository_from_url(origin: str) -> str | None:
    """Return ``owner/repository`` for an exact GitHub origin URL."""

    value = origin.strip()
    if "://" in value:
        parsed = urlsplit(value)
        scheme = parsed.scheme.casefold()
        allowed_ports = {"https": 443, "ssh": 22, "git": 9418}
        if scheme not in allowed_ports:
            return None
        try:
            port = parsed.port
        except ValueError:
            return None
        if port not in {None, allowed_ports[scheme]}:
            return None
        if parsed.hostname is None or parsed.hostname.casefold() != "github.com":
            return None
        if parsed.query or parsed.fragment:
            return None
        path = parsed.path
    else:
        match = re.fullmatch(r"(?:[^@/:\s]+@)?github\.com:(.+)", value, re.I)
        if match is None:
            return None
        path = match.group(1)

    parts = path.strip("/").split("/")
    if len(parts) != 2 or not all(parts):
        return None
    owner, repository = parts
    if repository.endswith(".git"):
        repository = repository[:-4]
    if not repository or any(character.isspace() for character in owner + repository):
        return None
    return f"{owner}/{repository}"


def load_stack_lock(path: Path | None = None) -> dict[str, Any]:
    payload = json.loads((path or STACK_LOCK_PATH).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("stack lock must be a JSON object")  # noqa: TRY004
    return payload


def package_tree_identity(package_root: Path) -> str:
    """Hash shipped OpenPine package sources, excluding the self-referential lock."""

    package_root = package_root.resolve()
    digest = hashlib.sha256()
    candidates = sorted(
        (
            path
            for path in package_root.rglob("*")
            if path.is_file()
            and path.name != STACK_LOCK_PATH.name
            and "__pycache__" not in path.parts
            and path.suffix not in {".pyc", ".pyo"}
        ),
        key=lambda path: path.relative_to(package_root).as_posix(),
    )
    for path in candidates:
        relative = path.relative_to(package_root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def validate_stack_lock(
    lock: Mapping[str, Any], *, root: Path | None = None
) -> tuple[str, ...]:
    """Validate schema, allowlisted identities and optional product pin coherence."""

    errors: list[str] = []
    if lock.get("schema") != STACK_LOCK_SCHEMA:
        errors.append(f"stack lock schema must be {STACK_LOCK_SCHEMA}")
    if lock.get("release") != __version__:
        errors.append(f"stack lock release must match package version {__version__}")
    components = lock.get("components")
    if not isinstance(components, list):
        return (*errors, "stack lock components must be a list")
    names = [item.get("name") if isinstance(item, dict) else None for item in components]
    if names != list(EXPECTED_COMPONENTS):
        errors.append(f"stack lock components must be {list(EXPECTED_COMPONENTS)!r}")
    for index, item in enumerate(components):
        if not isinstance(item, dict):
            errors.append("stack lock component must be an object")
            continue
        name = str(item.get("name", "<unknown>"))
        expected_metadata = EXPECTED_COMPONENT_METADATA.get(name)
        if expected_metadata is not None:
            expected_package, expected_repository = expected_metadata
            if item.get("package") != expected_package:
                errors.append(
                    f"stack lock component {name} package must be {expected_package}"
                )
            if item.get("repository") != expected_repository:
                errors.append(
                    f"stack lock component {name} repository must be {expected_repository}"
                )
        if item.get("version") != __version__:
            errors.append(f"stack lock component {name} version must be {__version__}")
        if index == 0 and name == "openpine":
            if "commit" in item:
                errors.append(
                    "stack lock component openpine must use tree_sha256, not a commit that omits dirty sources"
                )
            if _NONZERO_SHA256.fullmatch(str(item.get("tree_sha256", ""))) is None:
                errors.append("stack lock component openpine tree_sha256 must be a nonzero SHA-256")
        elif _NONZERO_SHA40.fullmatch(str(item.get("commit", ""))) is None:
            errors.append(
                f"stack lock component {name} commit is not a nonzero immutable SHA"
            )
        if not isinstance(item.get("contracts"), dict):
            errors.append(f"stack lock component {name} contracts must be an object")
    if root is not None:
        errors.extend(_pin_coherence_errors(lock, Path(root)))
    return tuple(errors)


def _pin_coherence_errors(lock: Mapping[str, Any], root: Path) -> list[str]:
    """Validate lock SHAs against dependency URLs and immutable Stack CI refs."""

    errors: list[str] = []
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
            "project"
        ]
        dependencies = tuple(str(item) for item in project.get("dependencies", ()))
    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as exc:
        return [f"stack lock dependency pins unavailable: {type(exc).__name__}: {exc}"]
    try:
        workflow = (root / ".github" / "workflows" / "stack-ci.yml").read_text(
            encoding="utf-8"
        )
    except OSError as exc:
        return [f"stack lock stack-ci refs unavailable: {type(exc).__name__}: {exc}"]

    workflow_refs = {
        repository: commit
        for repository, commit in re.findall(
            r"repository:\s*([^\s]+)\s*\n\s*ref:\s*([0-9a-f]{40})", workflow
        )
    }
    components = lock.get("components")
    if not isinstance(components, list):
        return errors
    for item in components[1:]:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name", "<unknown>"))
        package = str(item.get("package", ""))
        repository = str(item.get("repository", ""))
        commit = str(item.get("commit", ""))
        expected_dependency = (
            f"{package} @ git+https://github.com/{repository}.git@{commit}"
        )
        if expected_dependency not in dependencies:
            errors.append(f"stack lock component {name} dependency ref does not match lock")
        if workflow_refs.get(repository) != commit:
            errors.append(f"stack lock component {name} stack-ci ref does not match lock")
    return errors


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


def _component_repo(root: Path, repository: str) -> Path | None:
    short_name = repository.rsplit("/", 1)[-1]
    configured = os.environ.get("PINE_STACK_ROOT")
    bases = [Path(configured)] if configured else []
    bases.extend((root.parent, Path.home()))
    candidates = (short_name, short_name.replace("-", "_"), short_name.replace("_", "-"))
    for base in bases:
        for candidate in candidates:
            path = base / candidate
            if (path / ".git").exists():
                return path
    return None


def validate_stack_refs(
    lock: Mapping[str, Any], *, root: Path, verify_remote: bool = True
) -> tuple[str, ...]:
    """Verify sibling SHAs in local/remote Git and committed package metadata."""

    errors: list[str] = []
    components = lock.get("components")
    if not isinstance(components, list):
        return ("stack lock components must be a list",)
    for item in components[1:]:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name", "<unknown>"))
        repository = str(item.get("repository", ""))
        commit = str(item.get("commit", ""))
        version = str(item.get("version", ""))
        repo = _component_repo(root, repository)
        if repo is None:
            errors.append(f"stack lock component {name} local repository is unavailable")
            continue
        origin = _git(repo, "remote", "get-url", "origin")
        actual_repository = (
            None
            if origin.returncode != 0
            else _github_repository_from_url(origin.stdout)
        )
        if actual_repository is None or actual_repository.casefold() != repository.casefold():
            errors.append(f"stack lock component {name} local origin does not match {repository}")
        exists = _git(repo, "cat-file", "-e", f"{commit}^{{commit}}")
        if exists.returncode != 0:
            errors.append(f"stack lock component {name} commit is unavailable in local git")
            continue
        metadata = _git(repo, "show", f"{commit}:pyproject.toml")
        if metadata.returncode != 0:
            errors.append(f"stack lock component {name} committed pyproject.toml is unavailable")
        else:
            try:
                committed_version = str(tomllib.loads(metadata.stdout)["project"]["version"])
            except (KeyError, tomllib.TOMLDecodeError) as exc:
                errors.append(
                    f"stack lock component {name} committed version is unreadable: {exc}"
                )
            else:
                if committed_version != version:
                    errors.append(
                        f"stack lock component {name} committed version {committed_version} "
                        f"!= lock version {version}"
                    )
        if verify_remote:
            remote = _git(repo, "ls-remote", "origin")
            advertised = {
                line.split()[0]
                for line in remote.stdout.splitlines()
                if line.split() and _NONZERO_SHA40.fullmatch(line.split()[0])
            }
            if remote.returncode != 0:
                errors.append(f"stack lock component {name} remote refs are unavailable")
            elif commit not in advertised:
                errors.append(f"stack lock component {name} commit is not remote-resolvable")
    return tuple(errors)


def stack_lock_identity(lock: Mapping[str, Any] | None = None) -> str:
    payload = dict(lock) if lock is not None else load_stack_lock()
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stack_lock_summary(lock: Mapping[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(lock) if lock is not None else load_stack_lock()
    errors = validate_stack_lock(payload)
    if errors:
        raise ValueError("; ".join(errors))
    components = []
    for item in payload["components"]:
        identity = (
            {"tree_sha256": item["tree_sha256"]}
            if item["name"] == "openpine"
            else {"commit": item["commit"]}
        )
        components.append(
            {
                "name": item["name"],
                "version": item["version"],
                **identity,
                "contracts": item["contracts"],
            }
        )
    source_tree_sha256 = package_tree_identity(STACK_LOCK_PATH.parent)
    expected_tree_sha256 = str(payload["components"][0]["tree_sha256"])
    return {
        "schema": payload["schema"],
        "release": payload["release"],
        "sha256": stack_lock_identity(payload),
        "source_tree_sha256": source_tree_sha256,
        "source_tree_matches": source_tree_sha256 == expected_tree_sha256,
        "components": components,
    }
