"""CLI refusal and private archive contracts never assert native qualification."""

import json
import sys
import pytest
from openpine.verification import qualification_hosted as h
from openpine.verification import protected_qualification as q
from tests.test_hosted_primary_archive_boundaries import archive_inputs


@pytest.mark.parametrize("args", [["execute"], ["run"], ["archive"], ["preflight"]])
def test_hosted_cli_requires_identity_or_spec_before_execution(tmp_path, monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["driver", *args, "--work", str(tmp_path / "work")])
    with pytest.raises(SystemExit) as caught:
        h.main()
    assert caught.value.code == 2 and not (tmp_path / "work").exists()


@pytest.mark.parametrize("public", [False, True])
def test_archive_cli_keeps_private_failures_closed(tmp_path, monkeypatch, capsys, public):
    host, work, output, identity = archive_inputs(tmp_path)
    args = [
        "driver",
        "archive",
        "--host",
        str(host),
        "--work",
        str(work),
        "--approved-sha",
        identity["approved_sha"],
        "--archive-output",
        str(output),
        "--run-id",
        identity["run_id"],
        "--source-hash",
        identity["expected_source_hash"],
        "--expected-candidate-manifest-sha256",
        identity["expected_candidate_manifest_sha256"],
        "--max-bytes",
        str(identity["max_bytes"]),
        "--retention-days",
        str(identity["retention_days"]),
    ]
    if public:
        args.append("--public-approved")
    monkeypatch.setattr(sys, "argv", args)
    if public:
        with pytest.raises(SystemExit) as caught:
            h.main()
        assert caught.value.code == 2 and not output.exists()
    else:
        assert h.main() == 0
        value = json.loads(capsys.readouterr().out)
        assert not value["raw_primaries_durable"] and not value["full_qualification_accepted"]
        assert value["file_count"] > 0


@pytest.mark.parametrize(
    "failure",
    [
        OSError("private sentinel"),
        ValueError("private sentinel"),
        KeyError("private sentinel"),
        TypeError("private sentinel"),
    ],
)
def test_archive_cli_does_not_print_private_exception_text(tmp_path, monkeypatch, capsys, failure):
    host, work, output, identity = archive_inputs(tmp_path)
    monkeypatch.setattr(h, "archive_primaries", lambda *a, **k: (_ for _ in ()).throw(failure))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "driver",
            "archive",
            "--host",
            str(host),
            "--work",
            str(work),
            "--approved-sha",
            identity["approved_sha"],
            "--archive-output",
            str(output),
            "--run-id",
            identity["run_id"],
            "--source-hash",
            identity["expected_source_hash"],
            "--expected-candidate-manifest-sha256",
            identity["expected_candidate_manifest_sha256"],
            "--max-bytes",
            str(identity["max_bytes"]),
            "--retention-days",
            str(identity["retention_days"]),
        ],
    )
    assert h.main() == 1
    assert json.loads(capsys.readouterr().out) == {
        "archived": False,
        "raw_primaries_durable": False,
        "full_qualification_accepted": False,
    }


@pytest.mark.parametrize("action", ["run", "preflight", "execute"])
def test_hosted_cli_propagates_failed_action_without_success_claim(tmp_path, monkeypatch, action):
    calls = []

    def prepare(*a, **k):
        calls.append(k)
        return 1

    def execute(*a):
        calls.append(a)
        return 1

    monkeypatch.setattr(h, "prepare_and_run", prepare)
    monkeypatch.setattr(h, "execute", execute)
    args = ["driver", action, "--work", str(tmp_path / "work")]
    if action == "execute":
        args += ["--spec", str(tmp_path / "spec")]
    else:
        args += ["--host", str(tmp_path / "host"), "--approved-sha", "a" * 40]
    monkeypatch.setattr(sys, "argv", args)
    assert h.main() == 1 and len(calls) == 1
    if action != "execute":
        assert calls[0]["preflight"] == (action == "preflight")


@pytest.mark.parametrize("action", ["fixture", "controller", "fault"])
def test_native_fault_cli_rejects_missing_capabilities_or_preserves_failure(
    tmp_path, monkeypatch, action
):
    args = [
        "fault-cli",
        action,
        "--spec",
        str(tmp_path / "spec"),
        "--output",
        str(tmp_path / "out"),
        "--mode",
        "interactive",
    ]
    monkeypatch.setattr(q, "fixture", lambda *a: (_ for _ in ()).throw(ValueError("unit refusal")))
    monkeypatch.setattr(sys, "argv", args)
    if action == "fixture":
        with pytest.raises(ValueError, match="unit refusal"):
            q.main()
    else:
        with pytest.raises(SystemExit) as caught:
            q.main()
        assert caught.value.code == 2


@pytest.mark.parametrize("action", ["controller", "fault"])
def test_native_cli_dispatch_preserves_declared_mode_and_fault(tmp_path, monkeypatch, action):
    calls = []
    monkeypatch.setattr(q, "controller", lambda *a: calls.append(a))
    monkeypatch.setattr(q, "run_fault", lambda *a: calls.append(a) or {"ok": False})
    args = [
        "fault-cli",
        action,
        "--spec",
        str(tmp_path / "spec"),
        "--output",
        str(tmp_path / "out"),
        "--mode",
        "bulk_backtest",
        "--fault",
        "sigint",
        "--timeout",
        "17",
        "--gate-fd",
        "5",
    ]
    monkeypatch.setattr(sys, "argv", args)
    assert q.main() == (1 if action == "fault" else 0)
    assert calls[0][2] == "bulk_backtest"
    if action == "fault":
        assert calls[0][3] == "sigint"
    else:
        assert calls[0][3:] == (17, 5)
