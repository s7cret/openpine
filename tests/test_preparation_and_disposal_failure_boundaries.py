"""Preparation/fixture refusal contracts; no row here is native qualification."""

from pathlib import Path

from types import SimpleNamespace
import os
import signal
import subprocess
import sys
import pytest
from openpine.verification import protected_qualification as q
from openpine.verification import qualification_hosted as h
from openpine.verification import execution_ci as ci
from openpine.verification.identity import read_json, write_json
from tests.test_protected_observation_failure_boundaries import UNIT


@pytest.mark.parametrize("mutation", ["foreign-origin", "missing-file"])
def test_fixture_admission_refuses_foreign_installed_origins(monkeypatch, mutation):

    def locate(name):
        return SimpleNamespace(
            __file__=None if mutation == "missing-file" else "/tmp/foreign-product/__init__.py"
        )

    monkeypatch.setattr(q.importlib, "import_module", locate)
    with pytest.raises(ValueError):
        q.installed_origins()


@pytest.mark.parametrize(
    "failure",
    [
        "bad-namespace",
        "namespace-loaded",
        "manifest-tampered",
        "commit-mismatch",
        "native-refused",
        "held-refused",
    ],
)
def test_fixture_instrumentation_restores_runtime_on_every_failure(tmp_path, monkeypatch, failure):
    # Modules and callbacks are unit fixtures; the installed runtime is never claimed.
    tests = tmp_path / "namespace"
    (tests / "rc6_tests").mkdir(parents=True)
    if failure == "bad-namespace":
        (tests / "openpine").mkdir()
    manifest = {
        "manifest_hash": "sha256:" + "a" * 64,
        "components": {name: {"sha": "a" * 40} for name in ci.COMPONENTS},
    }
    candidate = tmp_path / "candidate.json"
    write_json(candidate, manifest)
    spec = tmp_path / "spec.json"
    write_json(
        spec,
        {
            "tests_root": str(tests),
            "candidate_manifest": str(candidate),
            "candidate_manifest_sha256": q.sha256(candidate),
            "wheelhouse": str(tmp_path / "wheels"),
            "source_commits": {
                name: ("b" * 40 if failure == "commit-mismatch" else "a" * 40)
                for name in ci.COMPONENTS
            },
        },
    )
    if failure == "manifest-tampered":
        candidate.write_text("{}")
    folder = tmp_path / "output"
    folder.mkdir()
    monkeypatch.setattr(
        q, "installed_origins", lambda: {name: "/unit-contract-only/" + name for name in q.MODULES}
    )
    import openpine.admission as admission

    monkeypatch.setattr(admission, "load_active_deployment_identity", lambda *a: SimpleNamespace())
    from openpine.runtime import isolated_worker as worker

    if failure == "held-refused":

        def initializer(instance, *args, **kwargs):
            assert kwargs["bulk_idle_timeout_s"] == 240.0
            instance.unit_name = UNIT
            instance.proc = SimpleNamespace(pid=os.getpid())
            instance.hello = {"unit-contract-only": True}
            instance.protocol = SimpleNamespace(execution_context={"unit-contract-only": True})

        monkeypatch.setattr(worker.InteractiveWorkerSession, "__init__", initializer)
    original_init = worker.InteractiveWorkerSession.__init__
    native = SimpleNamespace(_manifest=None)

    def refused(*args):
        assert worker.InteractiveWorkerSession.__init__ is not original_init
        assert worker._worker_unit_name() == UNIT
        assert read_json(folder / "allocated-unit.json")["coordinator_pid"] == os.getpid()
        assert native._manifest() == manifest
        if failure == "held-refused":
            (folder / "release").touch()
            instance = object.__new__(worker.InteractiveWorkerSession)
            worker.InteractiveWorkerSession.__init__(instance)
            ready = read_json(folder / "ready.json")
            assert (
                ready["hello"] == {"unit-contract-only": True}
                and ready["coordinator_pid"] == os.getpid()
            )
        raise RuntimeError("native fixture intentionally refused")

    native.test_real_protected_worker_searches_native_array = refused
    libs = SimpleNamespace()
    market = SimpleNamespace()
    monkeypatch.setattr(worker, "_worker_unit_name", lambda: UNIT)
    import types

    for name, value in [
        ("test_rc6_library_imports", libs),
        ("test_rc6_marketdata_boundary", market),
        ("test_rc6_numeric_search_integration", native),
    ]:
        monkeypatch.setitem(sys.modules, "rc6_tests." + name, value)
    # Remove any previously imported unit-fixture namespace only for this test.
    existing = sys.modules.get("rc6_tests")
    monkeypatch.delitem(sys.modules, "rc6_tests", raising=False)
    if failure == "namespace-loaded":
        monkeypatch.setitem(sys.modules, "rc6_tests", types.ModuleType("rc6_tests"))
    try:
        with pytest.raises((ValueError, RuntimeError)):
            q.fixture(spec, folder, "interactive")
        assert worker.InteractiveWorkerSession.__init__ is original_init
        assert not (folder / "native-result.json").exists()
        if failure == "native-refused":
            assert market.STACK == manifest["manifest_hash"] and libs.ALL_COMMITS == {
                name: "a" * 40 for name in ci.COMPONENTS
            }
    finally:
        # fixture assigns this namespace directly, so restore it explicitly.
        if existing is None:
            sys.modules.pop("rc6_tests", None)
        else:
            sys.modules["rc6_tests"] = existing


@pytest.mark.parametrize(
    "failure",
    [
        "spawn",
        "pidfd",
        "ready",
        "ready-allocation",
        "allocated-invalid",
        "stop",
        "wait",
        "close",
        "primary-write",
    ],
)
def test_startup_disposal_never_rewrites_failed_automatic_primary(tmp_path, monkeypatch, failure):
    output = tmp_path / "attempt"
    spec = tmp_path / "spec.json"
    spec.write_text("{}")
    owned = []
    closed = []

    class Proc:
        pid = 456

        def __init__(self, gate):
            self.gate = os.dup(gate)

        def wait(self, timeout=None):
            if self.gate is not None:
                os.close(self.gate)
                self.gate = None
            if failure == "wait":
                raise subprocess.TimeoutExpired("unit-controller", timeout)
            return 1

    class Handle:
        def __init__(self, pid):
            if failure == "pidfd":
                raise OSError("pidfd unavailable")
            self.pid = pid

        def alive(self):
            return True

        def send(self, sig):
            owned.append(int(sig))

        def close(self):
            closed.append(self.pid)
            if failure == "close":
                raise OSError("retained descriptor close failed")

    def spawn(*args, **kwargs):
        if failure == "spawn":
            raise OSError("controller spawn refused")
        return Proc(kwargs["pass_fds"][0])

    def ready(path, *args):
        folder = path.parent
        if failure in {"ready-allocation", "allocated-invalid", "stop"}:
            write_json(
                folder / "allocated-unit.json",
                {"unit": "foreign-unit" if failure == "allocated-invalid" else UNIT},
            )
        raise TimeoutError("readiness refused")

    def stop(unit):
        assert unit == UNIT
        owned.append(unit)
        if failure == "stop":
            raise RuntimeError("unit cleanup refused")

    from openpine.runtime import isolated_worker

    monkeypatch.setattr(q.subprocess, "Popen", spawn)
    monkeypatch.setattr(q, "StableProcess", Handle)
    monkeypatch.setattr(q, "wait_ready", ready)
    monkeypatch.setattr(isolated_worker, "_stop_worker_unit", stop)
    monkeypatch.setattr(
        q.UnitObserver,
        "snapshot",
        lambda self, unit, group="": {
            "unit": unit,
            "properties": {"ActiveState": "active"},
            "members": [1],
        },
    )
    original = q.write_primary

    def write(path, value):
        if failure == "primary-write" and path.name == "automatic-result.json":
            raise OSError("persist refused")
        original(path, value)

    monkeypatch.setattr(q, "write_primary", write)
    result = q.run_fault(spec, output, "interactive", "timeout")
    assert not result["ok"] and not result["neighbour_completed"]
    disposal = read_json(output / "forced-disposal.json")
    if failure in {
        "allocated-invalid",
        "stop",
        "wait",
        "close",
        "primary-write",
        "ready-allocation",
    }:
        assert disposal["errors"]
    if failure != "primary-write":
        primary = read_json(output / "automatic-result.json")
        assert primary["ok"] is False and "forced_disposal_ok" not in primary
    assert all(
        value == UNIT or value in [int(signal.SIGTERM), int(signal.SIGKILL)] for value in owned
    )


@pytest.mark.parametrize(
    "failure",
    ["missing-git", "wrong-sha", "unsupported-runtime", "disk-reserve", "prepare-refused"],
)
def test_candidate_preparation_refuses_unsafe_inputs_before_manifest_publication(
    tmp_path, monkeypatch, failure
):
    host = tmp_path / "host"
    host.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    state = SimpleNamespace(at=lambda stage: None, commits={})
    monkeypatch.setattr(
        h.shutil, "which", lambda name: None if failure == "missing-git" else "/usr/bin/git"
    )
    monkeypatch.setattr(
        h.subprocess, "check_output", lambda *a, **k: ("b" if failure == "wrong-sha" else "a") * 40
    )
    monkeypatch.setattr(h.sys, "_is_gil_enabled", lambda: failure != "unsupported-runtime")
    monkeypatch.setattr(
        h.shutil,
        "disk_usage",
        lambda *a: SimpleNamespace(free=0 if failure == "disk-reserve" else 4000000000),
    )
    monkeypatch.setattr(
        ci, "prepare", lambda *a: (_ for _ in ()).throw(RuntimeError("prepare refused"))
    )
    with pytest.raises((ValueError, RuntimeError)):
        h._prepare_candidate(host, work, "a" * 40, state)
    assert not (work / "private/candidate.json").exists()


@pytest.mark.parametrize(
    "failure", ["wrong-prefix", "foreign-placement", "first-fault", "late-fault"]
)
def test_matrix_checkpoint_records_incomplete_execution_on_error(tmp_path, monkeypatch, failure):
    spec = tmp_path / "binding.json"
    folder = tmp_path / "matrix"
    folder.mkdir()
    write_json(
        spec,
        {
            "prefix": str(tmp_path / "foreign") if failure == "wrong-prefix" else sys.prefix,
            "placement": "C" if failure == "foreign-placement" else "A",
        },
    )
    calls = []

    def refused(spec, output, mode, fault):
        calls.append((mode, fault))
        if failure == "first-fault" or len(calls) == 3:
            raise OSError("unit fault runner refused")
        return {"mode": mode, "fault": fault, "ok": False}

    monkeypatch.setattr(h, "run_fault", refused)
    with pytest.raises((ValueError, OSError)):
        h.execute(spec, folder)
    if failure in {"first-fault", "late-fault"}:
        value = read_json(folder / "matrix.json")
        assert value["complete"] is False
        assert len(value["cases"]) == (0 if failure == "first-fault" else 2)
        assert not any(row["ok"] for row in value["cases"])
    else:
        assert not (folder / "matrix.json").exists()


@pytest.mark.parametrize(
    "failure", ["missing-wheels", "empty-finalizer-input", "finalizer-runtime-error"]
)
def test_real_candidate_materialization_cannot_publish_manifest_without_wheels(
    tmp_path, monkeypatch, failure
):
    from openpine.verification.qualification_public import declared_commits
    from scripts import finalize_stack_candidate

    host = Path(__file__).resolve().parents[1]
    approved = subprocess.check_output(  # noqa: S603 -- fixed read-only Git argv
        ["/usr/bin/git", "-C", str(host), "rev-parse", "HEAD"], text=True
    ).strip()  # noqa: S603 -- fixed read-only Git argv
    work = tmp_path / "run"
    work.mkdir()
    state = h.DiagnosticState(host, work, approved, False, commits=declared_commits(host, approved))

    def prepare_no_wheels(host, prepared, version):
        assert version == "3.13"
        (prepared / "bundle/wheelhouse").mkdir(parents=True)

    monkeypatch.setattr(ci, "prepare", prepare_no_wheels)
    if failure != "missing-wheels":

        def broken_selector(source, wheelhouse, destination):
            destination.mkdir()
            return destination

        monkeypatch.setattr(h, "candidate_wheelhouse", broken_selector)
    if failure == "finalizer-runtime-error":
        monkeypatch.setattr(
            finalize_stack_candidate,
            "finalize_candidate",
            lambda *a: (_ for _ in ()).throw(RuntimeError("private finalizer fault")),
        )
    with pytest.raises(h.QualificationFailure) as caught:
        h._prepare_candidate(host, work, approved, state)
    assert caught.value.code == (
        "missing-input" if failure == "missing-wheels" else "invalid-input"
    )
    assert not (work / "private/candidate.json").exists()
    assert not state.publish()["full_qualification_accepted"]
