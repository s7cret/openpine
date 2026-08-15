"""Optional cgroup v2 memory.max for the isolated worker."""

from __future__ import annotations

from pathlib import Path

MEMORY_MAX_BYTES = 134217728


class CgroupError(RuntimeError):
    """Could not apply a cgroup memory limit."""


def apply_memory_max(
    cgroup_dir: str | Path, *, memory_max: int = MEMORY_MAX_BYTES
) -> Path:
    root = Path(cgroup_dir)
    if not root.is_dir():
        raise CgroupError("cgroup directory missing")
    target = root / "memory.max"
    if not target.exists():
        raise CgroupError("memory.max missing")
    try:
        target.write_text(f"{int(memory_max)}\n", encoding="ascii")
    except OSError as exc:
        raise CgroupError("cannot write memory.max") from exc
    return target


def worker_cgroup_argv(cgroup_dir: str | Path | None) -> list[str]:
    """bwrap does not create cgroups; parent applies memory.max separately."""
    if cgroup_dir is None:
        return []
    path = Path(cgroup_dir)
    apply_memory_max(path)
    return ["--dir", str(path)]
