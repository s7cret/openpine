from __future__ import annotations

import json

import pytest

from openpine.runtime.bulk_worker import BULK_MESSAGE_LIMIT_BYTES, chunk_bulk_frames
from openpine.runtime.isolated_worker import InteractiveWorkerSession, _BOOTSTRAP


def test_chunk_bulk_frames_cover_all_bars_under_line_limit() -> None:
    bars = [{"i": index, "pad": "x" * 400} for index in range(50)]
    frames = chunk_bulk_frames(bars, max_bytes=8_000)

    assert frames
    assert all(frame["kind"] == "BULK_BARS" for frame in frames)
    assert all(frame["last"] is False for frame in frames[:-1])
    assert frames[-1]["last"] is True
    flattened = [bar for frame in frames for bar in frame["bars"]]
    assert flattened == bars
    for frame in frames:
        encoded = json.dumps(frame, separators=(",", ":")).encode("utf-8")
        assert len(encoded) <= 8_000


def test_chunk_bulk_frames_default_limit_stays_under_worker_line_cap() -> None:
    bars = [{"i": index, "pad": "y" * 8_000} for index in range(20)]
    frames = chunk_bulk_frames(bars)
    assert frames[-1]["last"] is True
    for frame in frames:
        encoded = json.dumps(frame, separators=(",", ":")).encode("utf-8")
        assert len(encoded) <= BULK_MESSAGE_LIMIT_BYTES


def test_chunk_bulk_frames_rejects_a_bar_that_cannot_fit() -> None:
    with pytest.raises(ValueError, match="bulk bar exceeds"):
        chunk_bulk_frames([{"pad": "z" * 2_000_000}], max_bytes=1_000)


def test_worker_bootstrap_dispatches_bulk_backtest_to_run_bulk() -> None:
    from openpine.runtime.isolated_worker import _BOOTSTRAP

    assert "run_bulk" in _BOOTSTRAP
    assert "bulk_backtest" in _BOOTSTRAP


def test_isolated_run_uses_bulk_session_when_protocol_is_bulk(monkeypatch) -> None:
    from openpine.runtime import isolated_run

    captured: dict[str, object] = {}

    class _FakeBulk:
        def __init__(self, *args, **kwargs) -> None:
            captured["kwargs"] = kwargs
            captured["bars"] = None

        def __enter__(self) -> "_FakeBulk":
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def run_bars(self, envelopes, engine_config):
            captured["bars"] = list(envelopes)
            captured["engine_config"] = engine_config
            return {
                "ok": True,
                "bars_processed": len(envelopes),
                "intent_tape": [],
                "score_ledger_hash": "sha256:" + "a" * 64,
                "raw_result": {"status": "completed", "bars_processed": len(envelopes)},
            }

    monkeypatch.setattr(isolated_run, "BulkWorkerSession", _FakeBulk)

    class _Config:
        semantic_profile = "strict_5x"
        timeframe = "1m"
        isolated_protocol = "bulk_backtest"
        execution_context = {"run_id": "run"}
        admitted_manifest = {}
        generated_artifact = {}
        run_hash = "sha256:" + "b" * 64
        protocol_artifact_dir = "/tmp/protocol-bulk-test"
        instrument_id = "BINANCE:SOLUSDT"
        symbol = "SOLUSDT"
        bar_envelopes = [{"bar_content_hash": "sha256:" + "c" * 64}]
        initial_capital = 100_000.0
        start_time = 1
        end_time = 2

    result = isolated_run.run_isolated_artifact(
        b"print(1)\n",
        bars=[object()],
        config=_Config(),
    )
    assert captured["bars"] == _Config.bar_envelopes
    assert result["ok"] is True
    assert result["isolation"]["protocol"] == "openpine.worker.protocol.v2"
    assert result["isolation"]["mode"] == "bulk_backtest"


def test_interactive_session_still_requires_per_bar_roundtrip_by_default() -> None:
    assert "evaluate_bar" in InteractiveWorkerSession.__dict__
    assert "run_bars" not in InteractiveWorkerSession.__dict__


def test_run_bulk_admits_sealed_intents_without_repeating_schema() -> None:
    from openpine.runtime import rc6_worker_runtime as runtime

    source = runtime.run_bulk.__code__.co_names
    assert "admit_sealed_intent_tape" in source
    assert "require_live_tape" not in source


def test_run_bulk_converts_bar_envelopes_immediately() -> None:
    import inspect

    from openpine.runtime import rc6_worker_runtime as runtime

    text = inspect.getsource(runtime.run_bulk)
    assert "envelopes.extend" not in text
    assert "bar_admission.accept(item)" in text
    assert "engine_bars.append(admitted_bar)" in text


def test_bulk_bootstrap_drops_htf_bars_interactive_keeps_them() -> None:
    import openpine.runtime.isolated_worker as worker

    helper = getattr(worker, "htf_bars_for_bootstrap", None)
    assert helper is not None
    rows = [{"symbol": "SOLUSDT", "timeframe": "60", "time": 1}]
    assert helper(bulk_backtest=True, htf_bars=rows) == []
    assert helper(bulk_backtest=False, htf_bars=rows) == rows


def test_write_json_line_accepts_payload_over_one_mib() -> None:
    import io
    from types import SimpleNamespace

    from openpine.runtime.isolated_worker import IsolatedWorkerError

    session = InteractiveWorkerSession.__new__(InteractiveWorkerSession)
    session._closed = False
    session.bytes_sent = 0
    buf = io.StringIO()
    session.proc = SimpleNamespace(stdin=buf)
    payload = {"pad": "x" * 1_500_000}
    try:
        InteractiveWorkerSession._write_json_line(session, payload)
    except IsolatedWorkerError as exc:
        raise AssertionError("1.5 MiB worker line must be accepted") from exc
    assert len(buf.getvalue().encode("utf-8")) > 1_500_000
