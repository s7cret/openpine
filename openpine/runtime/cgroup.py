"""Optional cgroup v2 memory.max and pid attach for the isolated worker."""

from __future__ import annotations

import time
from pathlib import Path

MEMORY_MAX_BYTES = 134217728


class CgroupError(RuntimeError):
    """Could not apply a cgroup memory limit or attach a worker pid."""


def live_gateway_cgroup() -> Path:
    text = Path("/proc/self/cgroup").read_text(encoding="ascii").strip()
    relative = text.split(":", 2)[-1]
    return Path("/sys/fs/cgroup") / relative.lstrip("/")


def _assert_not_gateway(path: Path) -> None:
    try:
        gateway = live_gateway_cgroup().resolve()
        resolved = path.resolve()
    except OSError as exc:
        raise CgroupError("cannot resolve cgroup") from exc
    if resolved == gateway or gateway in resolved.parents:
        raise CgroupError("refuse live gateway cgroup")


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


def attach_worker(cgroup_dir: str | Path, pid: int) -> None:
    root = Path(cgroup_dir)
    _assert_not_gateway(root)
    procs = root / "cgroup.procs"
    if not procs.exists():
        raise CgroupError("cgroup.procs missing")
    try:
        procs.write_text(f"{int(pid)}\n", encoding="ascii")
    except OSError as exc:
        raise CgroupError("cannot attach pid") from exc


def iter_children(pid: int) -> list[int]:
    path = Path(f"/proc/{int(pid)}/task/{int(pid)}/children")
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="ascii")
    except OSError:
        return []
    return [int(item) for item in text.split() if item.isdigit()]


def walk_descendants(pid: int) -> list[int]:
    found: list[int] = []
    stack = [int(pid)]
    seen: set[int] = {int(pid)}
    while stack:
        current = stack.pop()
        for child in iter_children(current):
            if child in seen:
                continue
            seen.add(child)
            found.append(child)
            stack.append(child)
    return found


def attach_worker_tree(cgroup_dir: str | Path, pid: int) -> None:
    attach_worker(cgroup_dir, pid)
    pending = walk_descendants(pid)
    for _ in range(20):
        if not pending:
            time.sleep(0.01)
            pending = walk_descendants(pid)
        if not pending:
            break
        leftover: list[int] = []
        for child in pending:
            try:
                attach_worker(cgroup_dir, child)
            except CgroupError:
                leftover.append(child)
        if not leftover:
            extra = [item for item in walk_descendants(pid) if item not in pending]
            if not extra:
                return
            pending = extra
            continue
        pending = leftover
    extra = walk_descendants(pid)
    for child in extra:
        try:
            attach_worker(cgroup_dir, child)
        except CgroupError:
            continue


def prepare_worker_cgroup(
    cgroup_dir: str | Path, *, memory_max: int = MEMORY_MAX_BYTES
) -> Path:
    root = Path(cgroup_dir)
    _assert_not_gateway(root)
    apply_memory_max(root, memory_max=memory_max)
    if not (root / "cgroup.procs").exists():
        raise CgroupError("cgroup.procs missing")
    return root


def worker_cgroup_argv(cgroup_dir: str | Path | None) -> list[str]:
    """bwrap cannot attach cgroups; parent writes cgroup.procs."""
    if cgroup_dir is None:
        return []
    prepare_worker_cgroup(cgroup_dir)
    return []
