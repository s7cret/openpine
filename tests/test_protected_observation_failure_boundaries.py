"""Failed observation/disposal contracts; synthetic rows never qualify a worker."""

from pathlib import Path
from types import SimpleNamespace
import signal
import os
import subprocess
import pytest
from openpine.verification import protected_qualification as q
from openpine.verification.identity import read_json, write_json

UNIT = "openpine-worker-" + "a" * 32
OTHER = "openpine-worker-" + "b" * 32


def properties(**updates):
    value = {name: "" for name in q.PROPERTIES}
    value.update(ActiveState="inactive", SubState="dead", MainPID="0")
    value.update(updates)
    return "\n".join(name + "=" + value[name] for name in q.PROPERTIES)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "ActiveState=active",
        properties() + "\nActiveState=inactive",
        properties() + "\nForeign=x",
        properties() + "\ninvalid",
    ],
)
def test_systemd_observation_requires_complete_unique_properties(text):
    with pytest.raises(ValueError):
        q.parse_unit_properties(text)


@pytest.mark.parametrize(
    "value",
    [
        None,
        1,
        "openpine-worker-*",
        UNIT + ".service",
        UNIT + ";id",
        "../" + UNIT,
        "openpine-worker-" + "A" * 32,
    ],
)
def test_observer_rejects_nonowned_unit_before_any_command(monkeypatch, value):
    monkeypatch.setattr(
        q.subprocess, "run", lambda *a, **k: pytest.fail("untrusted unit reached systemctl")
    )
    with pytest.raises(ValueError):
        q.UnitObserver().snapshot(value)


@pytest.mark.parametrize(
    "code,text", [(1, ""), (0, "foreign.service loaded active"), (1, "permission denied")]
)
def test_failed_show_cannot_guess_that_unit_is_absent(monkeypatch, code, text):
    replies = iter(
        [
            subprocess.CompletedProcess([], 1, "", ""),
            subprocess.CompletedProcess([], code, text, ""),
        ]
    )
    monkeypatch.setattr(q.subprocess, "run", lambda *a, **k: next(replies))
    with pytest.raises(ValueError, match="cannot observe"):
        q.UnitObserver().snapshot(UNIT)


def test_absence_requires_exact_empty_manager_query(monkeypatch):
    replies = iter(
        [subprocess.CompletedProcess([], 1, "", ""), subprocess.CompletedProcess([], 0, "", "")]
    )
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        assert kw["timeout"] == 3
        return next(replies)

    monkeypatch.setattr(q.subprocess, "run", run)
    result = q.UnitObserver().snapshot(UNIT)
    assert result["members"] == [] and result["properties"]["ActiveState"] == "inactive"
    assert calls[1][-1] == UNIT + ".service" and calls[0][:3] == [
        "/usr/bin/sudo",
        "-n",
        "/usr/bin/systemctl",
    ]


@pytest.mark.parametrize(
    "group",
    [
        "/",
        "relative/" + UNIT + ".service",
        "/../../tmp/" + UNIT + ".service",
        "/system.slice/" + OTHER + ".service",
    ],
)
def test_foreign_or_unbounded_cgroup_path_is_refused(monkeypatch, group):
    monkeypatch.setattr(
        q.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess([], 0, properties(ControlGroup=group), ""),
    )
    with pytest.raises(ValueError, match="UUID subtree"):
        q.UnitObserver().snapshot(UNIT)


@pytest.mark.parametrize(
    "kind", ["zero", "negative", "nonnumeric", "symlink", "too-many", "nested"]
)
def test_cgroup_membership_is_bounded_and_confined(tmp_path, monkeypatch, kind):
    root = tmp_path / "cgroup"
    folder = root / "system.slice" / (UNIT + ".service")
    folder.mkdir(parents=True)
    member = folder / "cgroup.procs"
    member.write_text(
        {
            "zero": "0",
            "negative": "-7",
            "nonnumeric": "private",
            "too-many": " ".join(map(str, range(1, 4098))),
        }.get(kind, "23 24")
    )
    if kind == "symlink":
        target = tmp_path / "foreign"
        target.write_text("888")
        member.unlink()
        member.symlink_to(target)
    if kind == "nested":
        (folder / "child").mkdir()
        (folder / "child/cgroup.procs").write_text("24 25")
    actual_path = Path
    monkeypatch.setattr(
        q, "Path", lambda value: root if str(value) == "/sys/fs/cgroup" else actual_path(value)
    )
    monkeypatch.setattr(
        q.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            [], 0, properties(ControlGroup="/system.slice/" + UNIT + ".service"), ""
        ),
    )
    if kind == "nested":
        assert q.UnitObserver().snapshot(UNIT)["members"] == [23, 24, 25]
    else:
        with pytest.raises(ValueError):
            q.UnitObserver().snapshot(UNIT)


@pytest.mark.parametrize(
    "failure",
    ["release-exists", "timeout", "nonzero", "missing", "malformed", "not-object", "wrong-result"],
)
def test_neighbour_completion_does_not_promote_failed_native_primary(tmp_path, failure):
    folder = tmp_path / "neighbour"
    folder.mkdir()
    result = {"neighbour_completed": False, "case_error": "none"}
    if failure == "release-exists":
        (folder / "release").touch()
    elif failure in {"malformed", "not-object"}:
        (folder / "native-result.json").write_text("{" if failure == "malformed" else "[]")
    elif failure == "wrong-result":
        write_json(folder / "native-result.json", {"ok": True, "bars": 6})

    def wait(**kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("owned", 15)
        return 9 if failure == "nonzero" else 0

    q._complete_neighbour(SimpleNamespace(wait=wait), folder, result)
    assert result["neighbour_completed"] is False and result["case_error"] != "none"
    assert result.get("neighbour_completion_state") != "observed-true"


@pytest.mark.parametrize("fault", q.FAULTS)
@pytest.mark.parametrize(
    "failure",
    [
        "shared-unit",
        "not-live",
        "shared-member",
        "foreign-coordinator",
        "invalid-family",
        "observation-timeout",
        "interrupted",
    ],
)
def test_failed_fault_harness_retains_failure_before_disposal(
    tmp_path, monkeypatch, fault, failure
):
    # The native fixture is deliberately not launched. Every attempt must fail.
    processes = []
    handles = []
    sent = []
    stopped = []
    observations = {}

    class Process:
        def __init__(self, *args, **kwargs):
            self.pid = 700 + len(processes)
            self.gate = os.dup(kwargs["pass_fds"][0])
            processes.append(self)

        def wait(self, timeout=None):
            if self.gate is not None:
                os.close(self.gate)
                self.gate = None
            return -int(signal.SIGKILL) if fault == "controller-sigkill" else 1

    class Handle:
        def __init__(self, pid):
            self.pid = pid
            self.closed = False
            handles.append(self)

        def alive(self):
            return not self.closed

        def send(self, sig):
            sent.append((self.pid, int(sig)))

        def close(self):
            self.closed = True

    def ready(path, process, deadline):
        if failure == "interrupted":
            raise KeyboardInterrupt("private fixture interruption")
        return {
            "unit": UNIT if process.pid == 700 or failure == "shared-unit" else OTHER,
            "coordinator_pid": 800 + process.pid - 700,
        }

    def snapshot(self, unit, known_group=""):
        observations[unit] = observations.get(unit, 0) + 1
        if failure == "observation-timeout" and observations[unit] > 1:
            raise subprocess.TimeoutExpired("unit-show", 3)
        pid = 900 if unit == UNIT or failure == "shared-member" else 901
        return {
            "unit": unit,
            "properties": {
                "ActiveState": "inactive" if failure == "not-live" else "active",
                "MainPID": str(pid),
                "ControlGroup": "/system.slice/" + unit + ".service",
            },
            "members": [pid],
        }

    from openpine.runtime import isolated_worker

    monkeypatch.setattr(q.subprocess, "Popen", Process)
    monkeypatch.setattr(q, "StableProcess", Handle)
    monkeypatch.setattr(q, "wait_ready", ready)
    monkeypatch.setattr(q.UnitObserver, "snapshot", snapshot)
    monkeypatch.setattr(
        q, "cleaned_unit", lambda *a: True
    )  # contract only; not an observation receipt
    import psutil

    monkeypatch.setattr(
        psutil,
        "Process",
        lambda pid: SimpleNamespace(
            parents=lambda: [] if failure == "foreign-coordinator" else [SimpleNamespace(pid=700)]
        ),
    )
    monkeypatch.setattr(isolated_worker, "_stop_worker_unit", lambda unit: stopped.append(unit))
    monkeypatch.setattr(q.time, "sleep", lambda x: None)
    # A real on-disk invalid family prevents every late-stage fixture from succeeding.
    output = tmp_path / "attempt"
    spec = tmp_path / "binding.json"
    spec.write_text("{}")
    original = q.write_primary

    def write(path, value):
        original(path, value)
        if path.name == "before.json":
            folder = output / "affected/command"
            folder.mkdir()
            write_json(folder / "process-family.json", {})
            if fault != "controller-sigkill":
                write_json(
                    folder / "command.json",
                    {"status": "timeout" if fault == "timeout" else "failed"},
                )

    monkeypatch.setattr(q, "write_primary", write)
    result = q.run_fault(spec, output, "interactive", fault)
    assert not result["ok"] and not result["neighbour_completed"]
    primary = read_json(output / "automatic-result.json")
    assert primary["ok"] is False and "forced_disposal_ok" not in primary
    assert primary["case_error"] != "none"
    expected_stage = {
        "invalid-family": "family-receipt",
        "observation-timeout": "automatic-observation",
    }.get(failure, "setup")
    assert primary["case_stage"] == expected_stage
    if failure in {"invalid-family", "observation-timeout"} and fault != "timeout":
        assert sent
    assert all(h.closed for h in handles)
    assert set(stopped).issubset({UNIT, OTHER})
    assert (output / "forced-disposal.json").is_file()


@pytest.mark.parametrize("mode,fault", [("unknown", "timeout"), ("interactive", "unknown")])
def test_invalid_fault_selection_does_not_create_attempt(tmp_path, mode, fault):
    with pytest.raises(ValueError):
        q.run_fault(tmp_path / "spec", tmp_path / "attempt", mode, fault)
    assert not (tmp_path / "attempt").exists()
