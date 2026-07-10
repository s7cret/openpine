"""Deterministic and bounded source distribution helpers for OpenPine."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

MAX_SOURCE_FILE_BYTES = 20 * 1024 * 1024
MAX_SOURCE_TOTAL_BYTES = 100 * 1024 * 1024
_HASH_CHUNK_BYTES = 1024 * 1024

_EXCLUDED_NAMES = {
    ".coverage",
    ".DS_Store",
    ".env",
}
_EXCLUDED_DIRS = {
    ".cache",
    ".eggs",
    ".git",
    ".hg",
    ".marketdata-cache",
    ".mypy_cache",
    ".nox",
    ".openpine",
    ".pytest_cache",
    ".ruff_cache",
    ".runtime",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "backups",
    "build",
    "develop-eggs",
    "dist",
    "downloads",
    "eggs",
    "env",
    "htmlcov",
    "logs",
    "node_modules",
    "openpine.egg-info",
    "sdist",
    "site",
    "target",
    "venv",
    "wheels",
}
_EXCLUDED_SUFFIXES = {
    ".db",
    ".duckdb",
    ".egg",
    ".log",
    ".parquet",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".tsbuildinfo",
    ".whl",
    ".zip",
}
_RESEARCH_RUNTIME_DIRS = {
    ".cache",
    "artifacts",
    "cache",
    "caches",
    "data",
    "results",
}

_FRONTEND_RUNTIME_ROOTS = {"openpine-ui"}
_FRONTEND_RUNTIME_DIRS = {"dist", "node_modules"}
# Preserve exact hygiene paths for legacy non-Git release checks while pruning
# potentially huge cache, VCS, dependency, and research payload trees.
_FALLBACK_SCAN_DIRS = _EXCLUDED_DIRS - {"build", "dist"}


class DistributionSizeError(ValueError):
    """A source distribution exceeded its hard per-file or total size cap."""


class DistributionSourceError(RuntimeError):
    """Source candidates could not be selected safely."""


@dataclass(frozen=True)
class DistributionManifest:
    root: str
    file_count: int
    byte_count: int
    hygiene_errors: tuple[str, ...]


def _is_research_runtime_path(relative_path: Path) -> bool:
    return (
        len(relative_path.parts) >= 3
        and relative_path.parts[0] == "research"
        and any(part in _RESEARCH_RUNTIME_DIRS for part in relative_path.parts[1:-1])
    )


def _is_excluded(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    if path.name in _EXCLUDED_NAMES:
        return True
    if path.suffix.lower() in _EXCLUDED_SUFFIXES:
        return True
    if any(part in _EXCLUDED_DIRS for part in rel.parts):
        return True
    return _is_research_runtime_path(rel)


def _is_frontend_runtime_artifact(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    return (
        len(rel.parts) >= 2
        and rel.parts[0] in _FRONTEND_RUNTIME_ROOTS
        and rel.parts[1] in _FRONTEND_RUNTIME_DIRS
    )


def _validated_tracked_file(root: Path, relative_path: Path) -> Path | None:
    path = root
    final_metadata = None
    for part in relative_path.parts:
        path /= part
        try:
            final_metadata = path.lstat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise DistributionSourceError(
                f"failed to inspect tracked source path {relative_path.as_posix()}: {exc}"
            ) from exc
        if stat.S_ISLNK(final_metadata.st_mode):
            component = path.relative_to(root).as_posix()
            raise DistributionSourceError(
                "tracked source has symlinked component "
                f"{component}: {relative_path.as_posix()}"
            )

    try:
        resolved_path = path.resolve(strict=True)
    except OSError as exc:
        raise DistributionSourceError(
            f"failed to resolve tracked source path {relative_path.as_posix()}: {exc}"
        ) from exc
    try:
        resolved_path.relative_to(root)
    except ValueError as exc:
        raise DistributionSourceError(
            "tracked source escapes the distribution root: "
            f"{relative_path.as_posix()} resolves to {resolved_path}"
        ) from exc

    if final_metadata is None or not stat.S_ISREG(final_metadata.st_mode):
        return None
    return path


def _git_tracked_candidates(root: Path) -> list[Path] | None:
    try:
        top_level = subprocess.run(
            ["git", "-C", os.fspath(root), "rev-parse", "--show-toplevel"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None
    if top_level.returncode != 0:
        return None

    checkout_root = Path(os.fsdecode(top_level.stdout.rstrip(b"\r\n"))).resolve()
    if checkout_root != root:
        raise DistributionSourceError(
            "distribution root is nested inside a Git checkout: "
            f"requested {root}; checkout root is {checkout_root}"
        )

    tracked = subprocess.run(
        ["git", "-C", os.fspath(root), "ls-files", "-z", "--cached"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if tracked.returncode != 0:
        detail = os.fsdecode(tracked.stderr).strip() or f"exit code {tracked.returncode}"
        raise DistributionSourceError(f"failed to enumerate Git-tracked source files: {detail}")

    candidates: list[Path] = []
    for raw_path in tracked.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative_path = Path(os.fsdecode(raw_path))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise DistributionSourceError(
                f"Git returned an unsafe tracked path: {os.fsdecode(raw_path)!r}"
            )
        path = _validated_tracked_file(root, relative_path)
        if path is not None:
            candidates.append(path)
    return sorted(candidates, key=lambda path: path.relative_to(root).as_posix())


def _should_prune_fallback_dir(relative_path: Path) -> bool:
    if any(part in _FALLBACK_SCAN_DIRS for part in relative_path.parts):
        return True
    return _is_research_runtime_path(relative_path / "placeholder")


def _fallback_candidates(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for current_dir, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(current_dir)
        retained_dirs: list[str] = []
        for dirname in sorted(dirnames):
            path = current / dirname
            relative_path = path.relative_to(root)
            if path.is_symlink() or _should_prune_fallback_dir(relative_path):
                continue
            retained_dirs.append(dirname)
        dirnames[:] = retained_dirs

        for filename in sorted(filenames):
            path = current / filename
            if path.is_symlink():
                continue
            if path.is_file():
                candidates.append(path)
    return sorted(candidates, key=lambda path: path.relative_to(root).as_posix())


def _source_candidates(root: Path) -> list[Path]:
    tracked = _git_tracked_candidates(root)
    if tracked is not None:
        return tracked
    return _fallback_candidates(root)


def _validate_size_caps(
    files: list[Path],
    root: Path,
    *,
    max_file_bytes: int,
    max_total_bytes: int,
) -> int:
    if max_file_bytes <= 0 or max_total_bytes <= 0:
        raise DistributionSizeError("distribution size caps must be positive byte counts")

    total_bytes = 0
    for path in files:
        relative_path = path.relative_to(root).as_posix()
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise DistributionSourceError(
                f"failed to stat source file {relative_path}: {exc}"
            ) from exc
        if size > max_file_bytes:
            raise DistributionSizeError(
                "source file exceeds per-file size cap: "
                f"{relative_path} is {size} bytes; cap is {max_file_bytes} bytes"
            )
        total_bytes += size
        if total_bytes > max_total_bytes:
            raise DistributionSizeError(
                "source distribution exceeds total size cap: "
                f"{total_bytes} bytes after {relative_path}; cap is {max_total_bytes} bytes"
            )
    return total_bytes


def _source_selection(
    root: Path,
    *,
    max_file_bytes: int,
    max_total_bytes: int,
) -> tuple[list[Path], list[Path], int]:
    root = root.resolve()
    if not root.is_dir():
        raise DistributionSourceError(f"distribution root is not a directory: {root}")
    candidates = _source_candidates(root)
    files = [path for path in candidates if not _is_excluded(path, root)]
    files.sort(key=lambda path: path.relative_to(root).as_posix())
    total_bytes = _validate_size_caps(
        files,
        root,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
    )
    return candidates, files, total_bytes


def source_files(
    root: Path,
    *,
    max_file_bytes: int = MAX_SOURCE_FILE_BYTES,
    max_total_bytes: int = MAX_SOURCE_TOTAL_BYTES,
) -> list[Path]:
    """Return deterministic, bounded source files for a release.

    Git checkouts use only files in the index. Non-Git roots use a sorted
    filesystem fallback intended for isolated temporary test roots.
    """

    _, files, _ = _source_selection(
        root,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
    )
    return files


def distribution_manifest(
    root: Path,
    *,
    max_file_bytes: int = MAX_SOURCE_FILE_BYTES,
    max_total_bytes: int = MAX_SOURCE_TOTAL_BYTES,
) -> DistributionManifest:
    root = root.resolve()
    candidates, files, total_bytes = _source_selection(
        root,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
    )
    hygiene_errors = []
    for path in candidates:
        rel = path.relative_to(root)
        parts = rel.parts
        if _is_frontend_runtime_artifact(path, root):
            continue
        if "dist" in parts or "build" in parts or path.suffix.lower() == ".zip":
            hygiene_errors.append(rel.as_posix())
    return DistributionManifest(
        root=root.name,
        file_count=len(files),
        byte_count=total_bytes,
        hygiene_errors=tuple(sorted(hygiene_errors)),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_bounded(
    source,
    destination,
    *,
    relative_path: str,
    max_file_bytes: int,
    max_total_bytes: int,
    streamed_total_bytes: int,
) -> int:
    file_bytes = 0
    while chunk := source.read(_HASH_CHUNK_BYTES):
        file_bytes += len(chunk)
        if file_bytes > max_file_bytes:
            raise DistributionSizeError(
                "source file exceeds per-file size cap while streaming: "
                f"{relative_path} is at least {file_bytes} bytes; cap is {max_file_bytes} bytes"
            )
        actual_total = streamed_total_bytes + file_bytes
        if actual_total > max_total_bytes:
            raise DistributionSizeError(
                "source distribution exceeds total size cap while streaming: "
                f"{actual_total} bytes after {relative_path}; cap is {max_total_bytes} bytes"
            )
        destination.write(chunk)
    return file_bytes


def _open_regular_source_fd(root: Path, relative_path: Path) -> int:
    """Open a regular source file without following any symlink component."""

    if (
        relative_path.is_absolute()
        or not relative_path.parts
        or ".." in relative_path.parts
    ):
        raise DistributionSourceError(
            f"unsafe source path while opening archive input: {relative_path.as_posix()}"
        )
    if (
        os.open not in os.supports_dir_fd
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
    ):
        raise DistributionSourceError(
            "safe component-wise source opening is unsupported on this platform"
        )

    close_on_exec = getattr(os, "O_CLOEXEC", 0)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | close_on_exec
    file_flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_NONBLOCK", 0)
        | close_on_exec
    )
    directory_fds: list[int] = []
    source_fd: int | None = None
    try:
        directory_fds.append(os.open(root, directory_flags))
        for component in relative_path.parts[:-1]:
            directory_fds.append(
                os.open(component, directory_flags, dir_fd=directory_fds[-1])
            )
        source_fd = os.open(
            relative_path.parts[-1], file_flags, dir_fd=directory_fds[-1]
        )
        metadata = os.fstat(source_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise DistributionSourceError(
                "source file is not regular while opening archive input: "
                f"{relative_path.as_posix()}"
            )
        return source_fd
    except OSError as exc:
        raise DistributionSourceError(
            f"failed to open source file safely {relative_path.as_posix()}: {exc}"
        ) from exc
    except BaseException:
        if source_fd is not None:
            os.close(source_fd)
            source_fd = None
        raise
    finally:
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)


def build_zip(
    root: Path,
    output: Path,
    *,
    archive_root: str | None = None,
    max_file_bytes: int = MAX_SOURCE_FILE_BYTES,
    max_total_bytes: int = MAX_SOURCE_TOTAL_BYTES,
) -> str:
    root = root.resolve()
    files = source_files(
        root,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
    )
    archive_root = archive_root or root.name
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(fd)
    temporary_output = Path(temporary_name)
    streamed_total_bytes = 0
    try:
        with zipfile.ZipFile(
            temporary_output, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            for path in files:
                rel = path.relative_to(root).as_posix()
                info = zipfile.ZipInfo(f"{archive_root}/{rel}")
                info.date_time = (1980, 1, 1, 0, 0, 0)
                info.create_system = 3
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                source_fd = _open_regular_source_fd(root, Path(rel))
                with os.fdopen(source_fd, "rb") as source, archive.open(
                    info, "w"
                ) as destination:
                    streamed_total_bytes += _copy_bounded(
                        source,
                        destination,
                        relative_path=rel,
                        max_file_bytes=max_file_bytes,
                        max_total_bytes=max_total_bytes,
                        streamed_total_bytes=streamed_total_bytes,
                    )
        os.replace(temporary_output, output)
    except BaseException:
        temporary_output.unlink(missing_ok=True)
        raise
    return _sha256_file(output)


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
