"""Execution-context and restore admission with no synthetic stage acceptance."""

from types import SimpleNamespace
import json
import pytest
from openpine import run_identity as identity
from openpine.runtime import isolated_worker as worker
from openpine.verification import execution_ci as ci
from openpine.verification.execution_identity import environment_snapshot
from openpine_contracts import seal_content_hash, verify_content_hash
from tests.admission_helpers import make_sealed_artifact
from tests.rc4_fixtures import execution_context, admitted_manifest
from tests.test_ci_control_plane_real import _source_roots


@pytest.mark.parametrize("mutation", ["valid", "base-hash", "missing-generated", "source-tamper"])
def test_derived_context_is_sealed_to_exact_generated_artifact(mutation):
    artifact = make_sealed_artifact()
    context = execution_context()
    if mutation == "base-hash":
        context["content_hash"] = "sha256:" + "0" * 64
    elif mutation == "missing-generated":
        artifact.pop("generated_artifact")
    elif mutation == "source-tamper":
        artifact["python_code"] += "\n# modified"
    args = dict(
        run_id="independent-run",
        strategy_id="independent-strategy",
        artifact=artifact,
        data_snapshot_hash="sha256:" + "e" * 64,
        series_id="test:S:1m",
        instrument_id="test:S",
        exchange="test",
        market="spot",
        symbol="S",
        timeframe="1m",
        semantic_profile="strict_5x",
    )
    if mutation != "valid":
        with pytest.raises((ValueError, identity.AdmitError)):
            identity.derive_execution_context(context, **args)
    else:
        result = identity.derive_execution_context(context, **args)
        assert verify_content_hash(result, schema_id="openpine.execution_context.v1")
        assert (
            result["run_id"] == "independent-run"
            and result["session_id"] == "independent-run:worker"
        )
        assert result["generated_artifact_hash"] == artifact["generated_artifact"]["content_hash"]
        assert (
            result["data_snapshot_hash"] == args["data_snapshot_hash"]
            and result["source_hash"] == artifact["generated_artifact"]["source_hash"]
        )
        assert context["run_id"] == "run-test"


@pytest.mark.parametrize(
    "mutation",
    [
        "source-size",
        "invalid-context",
        "context-hash",
        "zero-run",
        "instrument",
        "profile",
        "timeframe",
        "params",
        "artifact-dir",
        "bulk-config",
    ],
)
def test_interactive_admission_refuses_invalid_request_before_spawn(
    tmp_path, monkeypatch, mutation
):
    artifact = make_sealed_artifact()
    generated = artifact["generated_artifact"]
    source = artifact["python_code"].encode()
    context = execution_context(
        generated_artifact_hash=generated["content_hash"],
        source_hash=generated["source_hash"],
        emitted_module_hash=generated["emitted_module_hash"],
    )
    context["producer_commits"]["ast2python"] = generated["producer"]["commit"]
    context.pop("content_hash")
    context = seal_content_hash(context, schema_id="openpine.execution_context.v1")
    args = dict(
        source=source,
        execution_context=context,
        instrument_id="test:S",
        admitted_manifest=admitted_manifest(),
        generated_artifact=generated,
        run_hash="sha256:" + "a" * 64,
        protocol_artifact_dir=tmp_path / "artifacts",
        semantic_profile="strict_5x",
        chart_timeframe="1m",
    )
    if mutation == "source-size":
        args["source"] = b"x" * 500001
    elif mutation == "invalid-context":
        args["execution_context"] = {}
    elif mutation == "context-hash":
        context["content_hash"] = "sha256:" + "0" * 64
    elif mutation == "zero-run":
        args["run_hash"] = "sha256:" + "0" * 64
    elif mutation == "instrument":
        args["instrument_id"] = " "
    elif mutation == "profile":
        args["semantic_profile"] = "foreign"
    elif mutation == "timeframe":
        args["chart_timeframe"] = " "
    elif mutation == "params":
        args["params"] = []
    elif mutation == "artifact-dir":
        args["protocol_artifact_dir"] = ""
    else:
        args.update(bulk_backtest=True, engine_config=None)
    monkeypatch.setattr(
        worker.subprocess, "Popen", lambda *a, **k: pytest.fail("invalid request spawned")
    )
    with pytest.raises(worker.IsolatedWorkerError):
        worker.InteractiveWorkerSession(**args)
    assert not (tmp_path / "artifacts").exists()


@pytest.mark.parametrize("mutation", ["wrong-commits", "provenance-change", "environment-change"])
def test_restore_refuses_identity_drift_without_publishing_restored_receipt(
    tmp_path, monkeypatch, mutation
):
    # Source extraction is real; command/venv callbacks are contract fixtures only.
    roots = _source_roots(tmp_path)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    source = ci.create_source_archive(roots, bundle / "sources.tar.gz")
    env = environment_snapshot()
    ci.bundle_manifest(
        bundle,
        source=source,
        environment=env,
        source_pins={name: "a" * 40 for name in ci.COMPONENTS if name != "openpine"},
        host_commit="a" * 40,
    )

    def provenance(repo, *args):
        if mutation == "provenance-change":
            (repo / "unexpected.txt").write_text("changed")

    monkeypatch.setattr(ci, "restore_git_provenance", provenance)
    monkeypatch.setattr(
        ci.venv, "EnvBuilder", lambda **kw: SimpleNamespace(create=lambda path: None)
    )
    calls = []

    def run(self, argv, **kw):
        calls.append(argv)
        if "environment_snapshot" in " ".join(argv):
            return json.dumps({**env, "foreign": "value"})
        return ""

    monkeypatch.setattr(ci.Commands, "run", run)
    work = tmp_path / "restore"
    commits = {name: "b" * 40 for name in ci.COMPONENTS} if mutation == "wrong-commits" else None
    with pytest.raises(ValueError):
        ci.restore(bundle, work, expected_commits=commits)
    assert not (work / "restored.json").exists()
    assert len(calls) == (3 if mutation == "environment-change" else 0)


@pytest.mark.parametrize("closed,commit", [(True, None), (False, None)])
def test_finalize_and_heartbeat_refuse_uncommitted_or_dead_session(closed, commit):
    session = object.__new__(worker.InteractiveWorkerSession)
    session._closed = closed
    session._last_commit = commit
    session.proc = SimpleNamespace(poll=lambda: 1)
    with pytest.raises(worker.IsolatedWorkerError):
        session.heartbeat()
    if closed:
        assert session.finalize() == {"kind": "FINALIZE"}
    else:
        with pytest.raises(worker.IsolatedWorkerError, match="committed bar"):
            session.finalize()
