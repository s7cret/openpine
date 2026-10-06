#!/usr/bin/env python3
"""Clean source staging and exact package payload checks for existing owners.

Build attempts are retained outside the checkout. Ignored build/lib, egg-info,
and old wheels are never inputs and are never deleted by this owner.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

MODULES = {
    "openpine": "openpine",
    "openpine-contracts": "openpine_contracts",
    "pine2ast": "pine2ast",
    "ast2python": "ast2python",
    "pinelib": "pinelib",
    "backtest-engine": "backtest_engine",
    "marketdata-provider": "marketdata_provider",
    "optimizer": "optimizer",
}


def _hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _write_new(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def package_tree(package: Path) -> dict[str, str]:
    """Every runtime/resource file is mandatory; only bytecode is derived."""
    if package.is_symlink() or not package.is_dir():
        raise ValueError("package root must be a regular directory")
    files = {}
    for path in sorted(package.rglob("*")):
        relative = path.relative_to(package)
        if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_symlink():
            raise ValueError("package tree contains a symlink")
        if path.is_file():
            files[relative.as_posix()] = _hash(path.read_bytes())
        elif not path.is_dir():
            raise ValueError("package tree contains a nonregular file")
    if not files:
        raise ValueError("package tree is empty")
    return files


def source_package_tree(source: Path) -> dict:
    metadata = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
    distribution = metadata["project"]["name"].lower().replace("_", "-")
    if distribution not in MODULES:
        raise ValueError("unknown stack distribution: " + distribution)
    module = MODULES[distribution]
    files = package_tree(source / module)
    return {
        "module": module,
        "files": files,
        "tree_sha256": _hash(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()),
    }


def _compare(expected: dict[str, str], actual: dict[str, str], label: str) -> None:
    missing = sorted(expected.keys() - actual.keys())
    extra = sorted(actual.keys() - expected.keys())
    changed = sorted(
        name for name in expected.keys() & actual.keys() if expected[name] != actual[name]
    )
    if missing or extra or changed:
        raise ValueError(
            f"{label} package tree mismatch: missing={missing}, extra={extra}, changed={changed}"
        )


def verify_wheel_tree(source: Path, wheel: Path, *, expected: dict | None = None) -> dict:
    observed = source_package_tree(source)
    if expected is not None and observed != expected:
        raise ValueError("source package tree changed during build")
    expected = observed if expected is None else expected
    module = expected["module"]
    payload = {}
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if len(set(names)) != len(names):
            raise ValueError("wheel contains duplicate members")
        for name in names:
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts or "\\" in name:
                raise ValueError("wheel contains an unsafe member")
            if name.endswith("/") or pure.parts[0].endswith(".dist-info"):
                continue
            if not name.startswith(module + "/"):
                raise ValueError("wheel contains an unexpected runtime payload: " + name)
            payload[name[len(module) + 1 :]] = _hash(archive.read(name))
    _compare(expected["files"], payload, "wheel")
    return expected


def check_installed(expected_rows: dict) -> dict:
    """Check the actual installed tree, including files absent from RECORD."""
    prefix = Path(sys.prefix).resolve()
    observed = {}
    if not sys.flags.isolated:
        raise ValueError("installed tree check requires Python isolated mode")
    for name, row in sorted(expected_rows.items()):
        expected = row["package_tree"]
        module = importlib.import_module(expected["module"])
        if module.__file__ is None:
            raise ValueError("installed package has no file origin")
        package = Path(module.__file__).resolve(strict=True).parent
        if not package.is_relative_to(prefix):
            raise ValueError("installed package origin escapes the environment")
        _compare(expected["files"], package_tree(package), "installed " + name)
        observed[name] = {"origin": str(package), "tree_sha256": expected["tree_sha256"]}
    return {"isolated": True, "prefix": str(prefix), "components": observed}


def stage_git_source(source: Path, attempt: Path) -> Path:
    """Export exactly HEAD, preserving the checkout and every old output."""
    source = source.resolve(strict=True)
    attempt = attempt.absolute()
    if any(path.is_symlink() for path in (attempt, *attempt.parents)):
        raise ValueError("build attempt crosses a symlink")
    if attempt.resolve().is_relative_to(source) or source.is_relative_to(attempt.resolve()):
        raise ValueError("build attempt overlaps source")
    git = shutil.which("git")
    if git is None:
        raise ValueError("git is required for clean source staging")

    def query(*argv: str) -> str:
        return subprocess.check_output([git, "-C", str(source), *argv], text=True).strip()  # noqa: S603

    head = query("rev-parse", "HEAD")
    if query("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("clean source build requires a clean Git checkout")
    attempt.mkdir(parents=True, exist_ok=False)
    archive_path = attempt / "source.tar"
    subprocess.check_call(  # noqa: S603 -- exact committed archive and Git executable
        [git, "-C", str(source), "archive", "--format=tar", "--output=" + str(archive_path), head]
    )
    destination = attempt / "source"
    destination.mkdir()
    with tarfile.open(archive_path) as archive:
        for member in archive:
            pure = PurePosixPath(member.name)
            if pure.is_absolute() or ".." in pure.parts or "\\" in member.name:
                raise ValueError("unsafe committed source archive member")
            if pure.parts[0] in {"build", "dist"} or pure.parts[0].endswith(".egg-info"):
                raise ValueError("committed build output cannot be a source input")
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError("source archive contains a nonregular file")
            target = destination.joinpath(*pure.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            content = archive.extractfile(member)
            if content is None:
                raise ValueError("missing committed source archive member")
            with content, target.open("xb") as output:
                shutil.copyfileobj(content, output)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
    if query("rev-parse", "HEAD") != head or query(
        "status", "--porcelain", "--untracked-files=all"
    ):
        raise ValueError("checkout changed during clean source staging")
    _write_new(
        attempt / "source.json",
        {
            "source_commit": head,
            "archive_sha256": _hash(archive_path.read_bytes()),
            "package_tree": source_package_tree(destination),
        },
    )
    return destination


def build_wheel(
    source: Path, outdir: Path, *, no_isolation: bool = False, attempt: Path | None = None
) -> dict:
    """Use a new tracked-source build attempt and reject any package drift."""
    source = source.resolve(strict=True)
    outdir = outdir.absolute()
    if any(path.is_symlink() for path in (outdir, *outdir.parents)):
        raise ValueError("wheel output crosses a symlink")
    if outdir.resolve().is_relative_to(source) or source.is_relative_to(outdir.resolve()):
        raise ValueError("wheel output overlaps source")
    outdir.mkdir(parents=True, exist_ok=True)
    if attempt is None:
        attempts = outdir / "build-attempts"
        attempts.mkdir(exist_ok=True)
        # mkdtemp reserves a unique name; stage_git_source admits new paths only.
        attempt = Path(tempfile.mkdtemp(prefix=source.name + "-", dir=attempts)) / "build"
    staged = stage_git_source(source, attempt)
    expected = source_package_tree(staged)
    built = attempt / "wheelhouse"
    built.mkdir()
    argv = [sys.executable, "-I", "-m", "build", "--wheel", "--outdir", str(built), str(staged)]
    if no_isolation:
        argv.insert(4, "--no-isolation")
    subprocess.check_call(argv)  # noqa: S603 -- exact interpreter, fixed build owner argv
    wheels = list(built.glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("clean build must produce exactly one wheel")
    wheel = wheels[0]
    tree = verify_wheel_tree(staged, wheel, expected=expected)
    report = {"wheel": wheel.name, "wheel_sha256": _hash(wheel.read_bytes()), "package_tree": tree}
    _write_new(attempt / "wheel.json", report)
    # Never overwrite a prior candidate. Failed and successful attempts remain.
    with wheel.open("rb") as src, (outdir / wheel.name).open("xb") as dst:
        shutil.copyfileobj(src, dst)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    installed = subparsers.add_parser("check-installed")
    installed.add_argument("--expected", type=Path, required=True)
    args = parser.parse_args()
    rows = json.loads(args.expected.read_text(encoding="utf-8"))
    print(json.dumps(check_installed(rows), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
