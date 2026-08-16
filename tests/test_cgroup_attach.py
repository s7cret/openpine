from __future__ import annotations

from pathlib import Path

import pytest

from openpine.runtime.cgroup import (
    CgroupError,
    attach_worker,
    live_gateway_cgroup,
    prepare_worker_cgroup,
)


def test_attach_worker_writes_pid(tmp_path: Path) -> None:
    (tmp_path / "cgroup.procs").write_text("", encoding="ascii")
    (tmp_path / "memory.max").write_text("max\n", encoding="ascii")
    attach_worker(tmp_path, 4242)
    assert "4242" in (tmp_path / "cgroup.procs").read_text(encoding="ascii")


def test_attach_missing_procs_fails(tmp_path: Path) -> None:
    (tmp_path / "memory.max").write_text("max\n", encoding="ascii")
    with pytest.raises(CgroupError, match="cgroup.procs"):
        attach_worker(tmp_path, 1)


def test_refuse_live_gateway_cgroup() -> None:
    gateway = live_gateway_cgroup()
    with pytest.raises(CgroupError, match="gateway"):
        attach_worker(gateway, 1)
    with pytest.raises(CgroupError, match="gateway"):
        prepare_worker_cgroup(gateway)


def test_prepare_worker_cgroup_is_not_bwrap_dir(tmp_path: Path) -> None:
    (tmp_path / "cgroup.procs").write_text("", encoding="ascii")
    (tmp_path / "memory.max").write_text("max\n", encoding="ascii")
    prepared = prepare_worker_cgroup(tmp_path, memory_max=64)
    assert prepared == tmp_path
    assert (tmp_path / "memory.max").read_text(encoding="ascii").strip() == "64"
    from openpine.runtime.cgroup import worker_cgroup_argv

    assert worker_cgroup_argv(tmp_path) == []


def test_evaluate_artifact_attaches_child_pid(tmp_path: Path) -> None:
    from openpine.runtime.isolated_worker import evaluate_artifact

    (tmp_path / "cgroup.procs").write_text("", encoding="ascii")
    (tmp_path / "memory.max").write_text("max\n", encoding="ascii")
    result = evaluate_artifact(b"VALUE = 1\n", timeout_s=5, cgroup_dir=tmp_path)
    assert result["ok"] is True
    pids = (tmp_path / "cgroup.procs").read_text(encoding="ascii").split()
    assert pids
    assert all(item.isdigit() for item in pids)


def test_attach_worker_tree_includes_forked_child(tmp_path: Path) -> None:
    import subprocess

    from openpine.runtime.cgroup import attach_worker_tree, iter_children

    (tmp_path / "cgroup.procs").write_text("", encoding="ascii")
    proc = subprocess.Popen(["/bin/bash", "-c", "sleep 0.4"])  # noqa: S603
    try:
        attach_worker_tree(tmp_path, proc.pid)
        written = set((tmp_path / "cgroup.procs").read_text(encoding="ascii").split())
        kids = {str(item) for item in iter_children(proc.pid)}
        assert str(proc.pid) in written or (kids & written)
    finally:
        proc.kill()
        proc.wait()
