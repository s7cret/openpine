#!/usr/bin/env python3
"""Hashed pip-install of a candidate wheelhouse. Does not tag or rewrite 4.0.2."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
from email.parser import Parser
from pathlib import Path
from zipfile import ZipFile

STACK_NAMES = {
    "ast2python",
    "backtest-engine",
    "marketdata-provider",
    "openpine",
    "openpine-contracts",
    "optimizer",
    "pine2ast",
    "pinelib",
}


class WheelhouseInstallError(RuntimeError):
    """Candidate wheelhouse cannot be installed closed."""


def wheel_hashes_from_candidate(candidate: dict) -> dict[str, str]:
    if candidate.get("stage") != "wheel-bound":
        raise WheelhouseInstallError("wheel-bound candidate required for install")
    components = candidate.get("components")
    if not isinstance(components, dict) or not components:
        raise WheelhouseInstallError("candidate components missing")
    hashes: dict[str, str] = {}
    for name, row in components.items():
        wheel = row.get("wheel") if isinstance(row, dict) else None
        if not isinstance(wheel, dict):
            raise WheelhouseInstallError(f"wheel identity missing: {name}")
        filename = wheel.get("filename")
        digest = wheel.get("sha256")
        if not isinstance(filename, str) or not filename.endswith(".whl"):
            raise WheelhouseInstallError(f"wheel filename invalid: {name}")
        if (
            not isinstance(digest, str)
            or not digest.startswith("sha256:")
            or len(digest) != 71
        ):
            raise WheelhouseInstallError(f"wheel hash invalid: {name}")
        if filename in hashes:
            raise WheelhouseInstallError(f"duplicate wheel filename: {filename}")
        hashes[filename] = digest
    return hashes


def load_candidate(path: Path) -> dict:
    resolver_path = Path(__file__).with_name("resolve_stack_candidate.py")
    spec = importlib.util.spec_from_file_location("resolve_stack_candidate", resolver_path)
    if spec is None or spec.loader is None:
        raise WheelhouseInstallError("candidate resolver unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        candidate = module.load_candidate(path)
    except module.CandidateSelectionError as exc:
        raise WheelhouseInstallError(str(exc)) from exc
    wheel_hashes_from_candidate(candidate)
    return candidate


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def normalize_name(value: str) -> str:
    return value.replace("_", "-").replace(".", "-").lower()


def verify_local_hashes(directory: Path, wheels: dict[str, str]) -> None:
    found = sorted(path for path in directory.glob("*.whl") if path.is_file())
    if not found:
        raise WheelhouseInstallError("wheelhouse is empty")
    for path in found:
        expected = wheels.get(path.name)
        if not expected:
            raise WheelhouseInstallError(f"missing hash: {path.name}")
        actual = file_sha256(path)
        if actual != expected:
            raise WheelhouseInstallError(f"hash mismatch: {path.name}")


def _requirement_name(item: str) -> tuple[str, str, bool, bool]:
    body, _, marker = item.partition(";")
    body = body.strip()
    extra = "extra" in marker
    vcs = "@" in body or body.startswith("git+")
    name = body.split("@", 1)[0].strip()
    for sep in ("[", " ", "<", ">", "=", "!"):
        if sep in name:
            name = name.split(sep, 1)[0]
    return normalize_name(name), body, vcs, extra


def third_party_requirements(wheel: Path) -> list[str]:
    with ZipFile(wheel) as archive:
        metadata_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        parsed = Parser().parsestr(archive.read(metadata_name).decode("utf-8"))
    rows: list[str] = []
    seen: set[str] = set()
    for item in parsed.get_all("Requires-Dist", []) or []:
        name, spec, vcs, extra = _requirement_name(item)
        if extra or vcs or name in STACK_NAMES:
            continue
        if spec in seen:
            continue
        seen.add(spec)
        rows.append(spec)
    return rows


def wheel_identity(path: Path) -> tuple[str, str]:
    stem = path.name[: -len(".whl")]
    parts = stem.split("-")
    if len(parts) < 5:
        raise WheelhouseInstallError(f"unparseable wheel name: {path.name}")
    return normalize_name(parts[0]), parts[1]


def render_hashed_requirements(entries: list[tuple[Path, str]]) -> str:
    lines: list[str] = []
    for path, digest in entries:
        name, version = wheel_identity(path)
        hexdigest = digest.split(":", 1)[1] if digest.startswith("sha256:") else digest
        lines.append(f"{name}=={version} --hash=sha256:{hexdigest}")
    return "\n".join(lines) + "\n"


def install_argv(
    requirements: Path,
    *,
    python: str,
    find_links: Path | list[Path] | None = None,
    allow_resolver: bool = False,
) -> list[str]:
    if allow_resolver:
        raise WheelhouseInstallError("no-deps required: VCS sibling pins must not resolve")
    argv = [python, "-m", "pip", "install", "--require-hashes", "--no-deps"]
    links: list[Path] = []
    if isinstance(find_links, Path):
        links = [find_links]
    elif find_links:
        links = list(find_links)
    if links:
        argv.append("--no-index")
        for link in links:
            argv.extend(["--find-links", str(link)])
    argv.extend(["-r", str(requirements)])
    return argv


def collect_local_wheels(directory: Path) -> list[Path]:
    return sorted(path for path in directory.glob("*.whl") if path.is_file())


def load_wheelhouse(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "openpine.wheelhouse.v1":
        raise WheelhouseInstallError("unknown wheelhouse schema")
    if payload.get("not_a_release") is not True:
        raise WheelhouseInstallError("wheelhouse must be not_a_release")
    wheels = payload.get("wheels")
    if not isinstance(wheels, dict) or not wheels:
        raise WheelhouseInstallError("wheelhouse has no wheels")
    return payload


def download_argv(requirements: list[str], dest: Path, *, python: str) -> list[str]:
    return [
        python,
        "-m",
        "pip",
        "download",
        "--only-binary",
        ":all:",
        "-d",
        str(dest),
        *requirements,
    ]


def download_third_party(requirements: list[str], dest: Path, *, python: str) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if not requirements:
        return
    subprocess.check_call(download_argv(requirements, dest, python=python))  # noqa: S603


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--deps", type=Path)
    parser.add_argument("--requirements", type=Path)
    parser.add_argument("--python", default="python3")
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    wheelhouse = args.wheelhouse.resolve()
    deps = (args.deps or wheelhouse / "deps").resolve()
    candidate = load_candidate(args.candidate.resolve())
    verify_local_hashes(wheelhouse, wheel_hashes_from_candidate(candidate))
    locals_ = collect_local_wheels(wheelhouse)
    third_party: list[str] = []
    seen: set[str] = set()
    for wheel in locals_:
        for spec in third_party_requirements(wheel):
            if spec not in seen:
                seen.add(spec)
                third_party.append(spec)
    download_third_party(third_party, deps, python=args.python)
    entries = [(path, file_sha256(path)) for path in locals_]
    entries.extend((path, file_sha256(path)) for path in collect_local_wheels(deps))
    text = render_hashed_requirements(entries)
    requirements = args.requirements or wheelhouse / "hashed-requirements.txt"
    requirements.write_text(text, encoding="utf-8")
    print(requirements)
    print("third_party", len(third_party), "hashed", len(entries))
    if args.install:
        subprocess.check_call(  # noqa: S603
            install_argv(
                requirements,
                python=args.python,
                find_links=[wheelhouse, deps],
            )
        )
        subprocess.check_call([args.python, "-m", "pip", "check"])  # noqa: S603
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
