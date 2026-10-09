"""Unbound tool aliases and oversized primaries fail before acceptance."""

import errno
import os
from pathlib import Path
import sys
import pytest
from openpine.verification import execution_process as proc
from openpine.verification import protected_qualification as q
from openpine.verification import qualification_public as public
from openpine.verification.execution_identity import clean_environment, hash_file
from tests.test_qualification_projection_boundaries import projection


@pytest.mark.parametrize(
    "mutation",
    ["two-unaliased", "relative-alias", "missing-alias", "unbound-node", "unbound-chromium"],
)
def test_unbound_tool_alias_never_launches_command(tmp_path, mutation):
    target = Path(sys.executable).resolve()
    spec = {"path": str(target), "sha256": hash_file(target)}
    inputs = {}
    if mutation == "two-unaliased":
        inputs = {"owner-tool:first": dict(spec), "owner-tool:second": dict(spec)}
    elif mutation == "relative-alias":
        inputs = {"owner-tool:python": {**spec, "declared_path": "relative-python"}}
    elif mutation == "missing-alias":
        inputs = {"owner-tool:python": {**spec, "declared_path": str(tmp_path / "missing-python")}}
    else:
        inputs = {
            "owner-tool:" + ("node" if mutation == "unbound-node" else "chromium"): {
                **spec,
                "declared_path": str(target),
            }
        }
    env = clean_environment({}, tmp_path / "private")
    env["PATH"] = str(tmp_path / "empty-path")
    out = tmp_path / "command"
    result = proc.run_logged(
        [str(target), "-c", "raise AssertionError('unbound tool ran')"],
        cwd=tmp_path,
        output=out,
        env=env,
        inputs=inputs,
        timeout=15,
    )
    assert result["ok"] is False and result["returncode"] is None
    assert result["status"] == "failed" and not (out / "process-family.json").exists()
    assert (out / "stdout.log").read_bytes() == b"" and (out / "stderr.log").read_bytes() == b""


@pytest.mark.parametrize("mutation", ["removed", "modified"])
def test_native_command_cannot_accept_changed_input_capture(tmp_path, mutation):
    source = tmp_path / "source.bin"
    source.write_bytes(b"exact input")
    spec = {"path": str(source), "sha256": hash_file(source)}
    script = "import pathlib,sys;p=pathlib.Path(sys.argv[1]);" + (
        "p.unlink()" if mutation == "removed" else "p.write_bytes(b'changed')"
    )
    out = tmp_path / "command"
    result = proc.run_logged(
        [sys.executable, "-c", script, str(source)],
        cwd=tmp_path,
        output=out,
        env=clean_environment({}, tmp_path / "private"),
        inputs={"input": spec},
        timeout=15,
    )
    assert result["returncode"] == 0 and not result["ok"] and result["status"] == "failed"
    assert (out / "input-0.bin").read_bytes() == b"exact input"
    assert "input provenance changed" in result["error"]
    if mutation == "removed":
        assert result["input_provenance"]["input"]["after_sha256"] is None


def test_closed_native_pidfd_cannot_be_reused_as_live_owned_handle():
    descriptor = os.pidfd_open(os.getpid())
    os.close(descriptor)
    with pytest.raises(OSError) as caught:
        proc._pidfd_alive(descriptor)
    assert caught.value.errno == errno.EBADF


@pytest.mark.parametrize("operation", ["read", "write"])
def test_qualification_primary_byte_budget_is_enforced_before_publication(tmp_path, operation):
    target = tmp_path / "primary.json"
    if operation == "read":
        target.write_bytes(b" " * (q.MAX_JSON + 1))
        with pytest.raises(ValueError, match="unbounded"):
            q.read_json(target)
    else:
        with pytest.raises(ValueError, match="byte budget"):
            q.write_primary(target, {"padding": "x" * q.MAX_JSON})
        assert not target.exists() and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("mutation", ["foreign", "symlink", "file"])
def test_projection_replace_does_not_remove_foreign_public_content(tmp_path, mutation):
    folder = tmp_path / "public"
    if mutation == "foreign":
        folder.mkdir()
        (folder / "private.txt").write_bytes(b"preserve")
    elif mutation == "symlink":
        target = tmp_path / "target"
        target.mkdir()
        folder.symlink_to(target, target_is_directory=True)
    else:
        folder.write_bytes(b"preserve")
    with pytest.raises(ValueError):
        public.replace_projection(folder, projection())
    if mutation == "foreign":
        assert (folder / "private.txt").read_bytes() == b"preserve"
    elif mutation == "file":
        assert folder.read_bytes() == b"preserve"
    else:
        assert folder.is_symlink()
