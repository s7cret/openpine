"""Real pipe framing and disk collision checks at the isolated-worker boundary."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from openpine.runtime.isolated_worker import (
    InteractiveWorkerSession,
    IsolatedWorkerError,
    _read_available_stderr,
    _reap_worker_process_bounded,
)


def _session(proc: subprocess.Popen, tmp_path: Path) -> InteractiveWorkerSession:
    session = object.__new__(InteractiveWorkerSession)
    session.proc = proc
    session.timeout_s = 3.0
    session.max_line_bytes = 1024
    session._stdout_buffer = bytearray()
    session.bytes_received = 0
    session.bytes_sent = 0
    session._closed = False
    session.protocol_artifact_dir = tmp_path
    return session


def _wire_process(script: str, *args: str) -> subprocess.Popen:
    return subprocess.Popen(  # noqa: S603 - static sys.executable fixture child, never shell input
        [sys.executable, "-c", script, *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _finish(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=3)
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        if pipe is not None:
            pipe.close()


def test_fragmented_json_lines_preserve_wire_lengths_and_order(tmp_path: Path) -> None:
    first = b'{"kind":"HELLO","value":1}\n'
    second = b'{"kind":"NEXT","value":2}\n'
    script = ('import sys,time; a=bytes.fromhex(sys.argv[1]); b=bytes.fromhex(sys.argv[2]); '
        'sys.stdout.buffer.write(a[:5]);sys.stdout.buffer.flush();time.sleep(.02);'
        'sys.stdout.buffer.write(a[5:]+b);sys.stdout.buffer.flush()')
    proc = _wire_process(script, first.hex(), second.hex())
    try:
        session = _session(proc, tmp_path)
        assert session._read_message(require_protocol=False) == {"kind": "HELLO", "value": 1}
        assert session._read_message(require_protocol=False) == {"kind": "NEXT", "value": 2}
        assert session.bytes_received == len(first) + len(second)
    finally:
        _finish(proc)


@pytest.mark.parametrize("wire,reason", [
    (b"{", "malformed worker output"),
    (b"\xff\n", "malformed worker output"),
    (b"[1,2]\n", "worker response must be an object"),
    (b'{"schema_id":"openpine.worker.protocol.v2"}\n', "invalid worker protocol response"),
    (b'{"error_type":"Rejected","detail":"bad manifest"}\n', "Rejected: bad manifest"),
])
def test_real_worker_output_fails_closed(tmp_path: Path, wire: bytes, reason: str) -> None:
    proc = _wire_process("import sys;sys.stdout.buffer.write(bytes.fromhex(sys.argv[1]));sys.stdout.buffer.flush()", wire.hex())
    try:
        session = _session(proc, tmp_path)
        with pytest.raises(IsolatedWorkerError, match=reason):
            session._read_message()
        if wire == b"{":
            assert session.bytes_received == 1  # final unterminated diagnostic is not discarded
    finally:
        _finish(proc)


def test_real_worker_exit_surfaces_stderr_diagnostic(tmp_path: Path) -> None:
    proc = _wire_process("import sys;sys.stderr.write('worker failure: rejected source\\n');sys.stderr.flush()")
    try:
        session = _session(proc, tmp_path)
        with pytest.raises(IsolatedWorkerError, match="worker failure: rejected source"):
            session._read_message(require_protocol=False)
        assert proc.wait(timeout=2) == 0
    finally:
        _finish(proc)


def test_worker_stderr_read_and_bounded_reap_use_actual_child(tmp_path: Path) -> None:
    proc = _wire_process("import sys,time;sys.stderr.write('proof\\n');sys.stderr.flush();time.sleep(20)")
    try:
        assert proc.stderr is not None
        for _ in range(100):
            if __import__("select").select([proc.stderr], [], [], .02)[0]:
                break
        assert _read_available_stderr(proc) == "proof\n"
        _reap_worker_process_bounded(proc, timeout=.05)
        assert proc.returncode is not None and proc.returncode != 0
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        assert proc.stdin.closed and proc.stdout.closed and proc.stderr.closed
    finally:
        if proc.poll() is None:
            _finish(proc)


def test_protocol_artifact_collision_keeps_original_file_bytes(tmp_path: Path) -> None:
    session = object.__new__(InteractiveWorkerSession)
    session.protocol_artifact_dir = tmp_path
    first = {"artifact_hash": "sha256:" + "a" * 64, "bytes": b"sealed-result-1",
        "schema_id": "openpine.worker.artifact.v1", "codec": "json", "size_bytes": 15}
    result = session._persist_artifact(first)
    path = tmp_path / ("a" * 64 + ".json")
    assert path.read_bytes() == first["bytes"]
    assert result["uri"] == path.resolve().as_uri()
    assert result["size_bytes"] == 15
    assert session._persist_artifact(first) == result
    with pytest.raises(IsolatedWorkerError, match="protocol artifact hash collision"):
        session._persist_artifact({**first, "bytes": b"untrusted-replacement"})
    assert path.read_bytes() == b"sealed-result-1"


def test_closed_worker_refuses_untrusted_serialization(tmp_path: Path) -> None:
    proc = _wire_process("import time;time.sleep(20)")
    try:
        session = _session(proc, tmp_path)
        session._closed = True
        with pytest.raises(IsolatedWorkerError, match="interactive worker is closed"):
            session._write_serialized_json_line(json.dumps({"x": 1}))
        session._closed = False
        with pytest.raises(IsolatedWorkerError, match="one JSON line"):
            session._write_serialized_json_line("{\"x\":1}\n{\"y\":2}")
    finally:
        _finish(proc)
