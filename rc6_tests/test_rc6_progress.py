"""Progress is verified, bounded and forwarded without changing Pine execution."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openpine.runtime.bulk_worker import chunk_bulk_frames
from openpine.runtime.progress import ProgressError, ProgressReporter


def test_bar_frames_are_lazy_and_exactly_one_frame_is_final():
    consumed = []
    def bars():
        for n in range(1000):
            consumed.append(n)
            yield {"n": n, "pad": "x" * 80}
    frames = chunk_bulk_frames(bars(), max_bytes=400)
    assert not consumed
    first = next(frames)
    assert len(consumed) < 10
    output = [json.loads(row) for row in [first, *frames]]
    assert [row["n"] for frame in output for row in frame["bars"]] == list(range(1000))
    assert sum(frame["last"] for frame in output) == 1
    assert output[-1]["last"] is True


def test_progress_throttles_but_always_emits_initial_and_terminal_values():
    events, now = [], [0.0]
    progress = ProgressReporter(lambda *args: events.append(args), max_total=100, clock=lambda: now[0])
    progress.report(0, 100)
    for n in range(1, 100):
        now[0] = n / 1000
        progress.report(n, 100)
    progress.report(100, 100)
    progress.report(100, 100, force=True)
    assert events == [(0, 100), (100, 100)]


def test_partial_counter_is_never_fabricated_as_full_completion():
    events = []
    progress = ProgressReporter(lambda *args: events.append(args), max_total=10, clock=lambda: 0)
    progress.report(0, 10)
    progress.report(3, 10, force=True)
    assert events[-1] == (3, 10)


@pytest.mark.parametrize("done,total", [(-1, 10), (True, 10), (1, True), (11, 10), (2, 11), (1.5, 10), (1, 9), (0, 10)])
def test_progress_rejects_invalid_changed_or_regressing_counters(done, total):
    events = []
    progress = ProgressReporter(lambda *args: events.append(args), max_total=10)
    progress.report(1, 10)
    with pytest.raises(ProgressError):
        progress.report(done, total)
    assert events == [(1, 10)]


def test_adapter_forwards_progress_and_actual_counts_not_input_length(monkeypatch):
    from openpine.runtime.engine import BacktestEngineAdapter, BacktestRunConfig
    from openpine.runtime import isolated_run
    callback = Mock()
    config = BacktestRunConfig("SOLUSDT", "1m", 0, 60_000, semantic_profile="strict_5x")
    def run(source, *, bars, config, progress_callback, **kwargs):
        assert source == b"source"
        progress_callback(1, 2)
        return {"raw_result": SimpleNamespace(status="completed"), "bars_processed": 1,
                "result_manifest": {"kind": "verified"}}
    monkeypatch.setattr(isolated_run, "run_isolated_artifact", run)
    adapter = BacktestEngineAdapter()
    monkeypatch.setattr(adapter, "_to_engine_bar", lambda x: x)
    result = adapter.run_isolated(b"source", [object(), object()], config, progress_callback=callback)
    callback.assert_called_once_with(1, 2)
    assert result.bars_processed == 1
    assert result.result_manifest == {"kind": "verified"}


def test_cli_bytes_path_forwards_inputs_and_progress(monkeypatch):
    from openpine.cli import runtime_helpers as helpers
    captured, callback = {}, Mock()
    monkeypatch.setattr(helpers, "_prepare_strategy_backtest_runtime", lambda *a: (b"source", None))
    monkeypatch.setattr(helpers, "_build_progress_callback", lambda **kw: callback)
    class Adapter:
        def run_isolated(self, source, bars, config, **kwargs):
            captured.update(kwargs)
            kwargs["progress_callback"](1, 1)
            return "result"
    result, elapsed = helpers._run_strategy_backtest_adapter(
        adapter_cls=Adapter, strategy_class=b"source", bars=[object()], config=object(),
        params={"n": 7}, provider=None, console=None, perf_counter=lambda: 0)
    assert result == "result" and elapsed == 0
    assert captured["params"] == {"n": 7}
    callback.assert_called_once_with(1, 1)


@pytest.mark.parametrize("queue_full", [False, True])
def test_gateway_forwards_artifact_progress_without_queue_backpressure(monkeypatch, queue_full):
    import queue
    from openpine.gateway.routes import backtest as routes
    from tests.test_artifact_source_stamp import _bar, _config, _snapshot_hash, _spec
    output = queue.Queue(maxsize=1)
    if queue_full:
        output.put(("earlier",))
    seen = {}
    class Adapter:
        def run_isolated(self, source, bars, config, **kwargs):
            assert kwargs["params"] == {"qty": 7}
            kwargs["progress_callback"](1, 1)
            return "completed"
    monkeypatch.setattr("openpine.runtime.engine.BacktestEngineAdapter", Adapter)
    monkeypatch.setattr(routes, "_put_backtest_process_result", lambda _out, result: seen.update(result=result))
    monkeypatch.setattr(routes, "_put_backtest_process_error", lambda _out, exc: seen.update(error=exc))
    routes._artifact_backtest_process_entry(output, _spec(source=b"STAMPED", data_snapshot_hash=_snapshot_hash([_bar()])),
                                            [_bar()], _config(), {"qty": 7})
    assert seen == {"result": "completed"}
    assert output.get_nowait() == (("earlier",) if queue_full else ("progress", 1, 1))


def test_bar_frames_serialize_each_bar_once_and_enforce_utf8_size(monkeypatch):
    from openpine.runtime import bulk_worker
    original = bulk_worker.json.dumps
    serializations = []
    def dumps(value, **kwargs):
        serializations.append(value)
        return original(value, **kwargs)
    monkeypatch.setattr(bulk_worker.json, "dumps", dumps)
    rows = [{"n": n, "label": "Привет 🧪"} for n in range(100)]
    frames = list(chunk_bulk_frames(rows, max_bytes=500))
    assert serializations == rows
    assert all(len(frame.encode("utf-8")) <= 500 for frame in frames)
    assert [r for f in frames for r in json.loads(f)["bars"]] == rows


def test_serialized_sender_refuses_multiline_frames():
    import io
    from openpine.runtime.isolated_worker import InteractiveWorkerSession, IsolatedWorkerError
    session = InteractiveWorkerSession.__new__(InteractiveWorkerSession)
    session._closed, session.bytes_sent = False, 0
    stream = io.StringIO()
    session.proc = SimpleNamespace(stdin=stream)
    with pytest.raises(IsolatedWorkerError, match="one JSON line"):
        session._write_serialized_json_line('{}\n{}')
    assert stream.getvalue() == ""
    session._write_serialized_json_line('{"one":1}')
    assert stream.getvalue() == '{"one":1}\n'
    assert session.bytes_sent == 10
