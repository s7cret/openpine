"""Cgroup path admission and real process-tree discovery without touching host cgroups."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from openpine.runtime import cgroup


def test_memory_and_pid_files_use_only_isolated_temporary_directory(tmp_path: Path):
    group = tmp_path / "synthetic-cgroup"
    group.mkdir()
    with pytest.raises(cgroup.CgroupError, match="memory.max missing"):
        cgroup.prepare_worker_cgroup(group)
    (group / "memory.max").write_text("unlimited\n")
    with pytest.raises(cgroup.CgroupError, match="cgroup.procs missing"):
        cgroup.prepare_worker_cgroup(group, memory_max=8_388_608)
    assert (group / "memory.max").read_text() == "8388608\n"
    (group / "cgroup.procs").write_text("")
    assert cgroup.prepare_worker_cgroup(group, memory_max=16_777_216) == group
    assert (group / "memory.max").read_text() == "16777216\n"
    assert cgroup.worker_cgroup_argv(None) == []
    assert cgroup.worker_cgroup_argv(group) == []
    cgroup.attach_worker(group, os.getpid())
    assert (group / "cgroup.procs").read_text() == f"{os.getpid()}\n"
    (group / "cgroup.procs").unlink()
    with pytest.raises(cgroup.CgroupError, match="cgroup.procs missing"):
        cgroup.attach_worker(group, os.getpid())
    with pytest.raises(cgroup.CgroupError, match="cgroup directory missing"):
        cgroup.apply_memory_max(tmp_path / "not-created")


def test_live_proc_children_discovery_and_temporary_attach_file(tmp_path: Path):
    group = tmp_path / "synthetic-cgroup"
    group.mkdir()
    (group / "cgroup.procs").write_text("")
    # The child has a real /proc task/children file; synthetic cgroup files never
    # write to /sys or attach a host process to a different kernel cgroup.
    parent = subprocess.Popen(
        [sys.executable, "-c", "import subprocess,sys,time; child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)']); print(child.pid,flush=True); time.sleep(20)"],
        stdout=subprocess.PIPE,
        text=True,
    )
    grandchild: int | None = None
    try:
        assert parent.stdout is not None
        grandchild = int(parent.stdout.readline().strip())
        assert cgroup.iter_children(parent.pid) == [grandchild]
        assert cgroup.walk_descendants(parent.pid) == [grandchild]
        cgroup.attach_worker_tree(group, parent.pid)
        assert (group / "cgroup.procs").read_text() == f"{grandchild}\n"
        assert cgroup.iter_children(-42) == []
    finally:
        if parent.stdout is not None:
            parent.stdout.close()
        for pid in (grandchild, parent.pid):
            if pid is not None:
                try:
                    os.kill(pid, 15)
                except ProcessLookupError:
                    pass
        parent.wait(timeout=5)


def test_temp_cgroup_write_failures_and_live_gateway_path_are_fail_closed(tmp_path: Path):
    group = tmp_path / "synthetic-cgroup"
    group.mkdir()
    (group / "memory.max").mkdir()
    (group / "cgroup.procs").mkdir()
    with pytest.raises(cgroup.CgroupError, match="cannot write memory.max"):
        cgroup.apply_memory_max(group)
    with pytest.raises(cgroup.CgroupError, match="cannot attach pid"):
        cgroup.attach_worker(group, os.getpid())
    # Query only: never write to the live kernel cgroup or its children.
    actual_gateway = cgroup.live_gateway_cgroup()
    with pytest.raises(cgroup.CgroupError, match="refuse live gateway cgroup"):
        cgroup._assert_not_gateway(actual_gateway)
    with pytest.raises(cgroup.CgroupError, match="refuse live gateway cgroup"):
        cgroup._assert_not_gateway(actual_gateway / "not-created-child")
