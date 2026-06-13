"""Deterministic source distribution helpers for OpenPine."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

_EXCLUDED_NAMES = {
    ".coverage",
    ".DS_Store",
    ".env",
}
_EXCLUDED_DIRS = {
    ".git",
    ".openpine",
    ".marketdata-cache",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".runtime",
    ".venv",
    "__pycache__",
    "backups",
    "build",
    "dist",
    "env",
    "logs",
    "node_modules",
    "openpine.egg-info",
    "venv",
}
_EXCLUDED_SUFFIXES = {".db", ".duckdb", ".log", ".parquet", ".pyc", ".pyo", ".sqlite", ".tsbuildinfo", ".zip"}


@dataclass(frozen=True)
class DistributionManifest:
    root: str
    file_count: int
    byte_count: int
    hygiene_errors: tuple[str, ...]


def _is_excluded(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    if path.name in _EXCLUDED_NAMES:
        return True
    if path.suffix in _EXCLUDED_SUFFIXES:
        return True
    return any(part in _EXCLUDED_DIRS for part in rel.parts)


def source_files(root: Path) -> list[Path]:
    files = [
        path
        for path in root.rglob("*")
        if path.is_file() and not _is_excluded(path, root)
    ]
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def distribution_manifest(root: Path) -> DistributionManifest:
    files = source_files(root)
    hygiene_errors = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        parts = rel.parts
        if (
            "dist" in parts
            or "build" in parts
            or path.suffix == ".zip"
        ):
            hygiene_errors.append(rel.as_posix())
    return DistributionManifest(
        root=root.name,
        file_count=len(files),
        byte_count=sum(path.stat().st_size for path in files),
        hygiene_errors=tuple(sorted(hygiene_errors)),
    )


def build_zip(root: Path, output: Path, *, archive_root: str | None = None) -> str:
    archive_root = archive_root or f"{root.name}"
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in source_files(root):
            rel = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(f"{archive_root}/{rel}")
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
    return hashlib.sha256(output.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m openpine.distribution")
    sub = parser.add_subparsers(dest="command", required=True)
    p_manifest = sub.add_parser("manifest")
    p_manifest.add_argument("--root", default=".")
    p_zip = sub.add_parser("build-zip")
    p_zip.add_argument("--root", default=".")
    p_zip.add_argument("--output", required=True)
    p_zip.add_argument("--archive-root", default=None)
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if args.command == "manifest":
        manifest = distribution_manifest(root)
        print(json.dumps(asdict(manifest), indent=2, sort_keys=True))
        return 1 if manifest.hygiene_errors else 0
    if args.command == "build-zip":
        digest = build_zip(root, Path(args.output).resolve(), archive_root=args.archive_root)
        print(json.dumps({"output": str(Path(args.output).resolve()), "sha256": digest}, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
