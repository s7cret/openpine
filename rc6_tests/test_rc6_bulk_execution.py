"""Actual compiled Pine + PineLib + broker, without replacing either with mocks.

Only stdin/stdout are in-memory transports. Process isolation is covered by the
separate bubblewrap integration test in the mandatory CI environment.
"""
from __future__ import annotations

import io
import json
from dataclasses import replace

import pytest
from openpine.runtime.worker_capabilities import WORKER_CAPABILITIES

from backtest_engine import BacktestConfig
from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
from openpine.run_identity import execution_context_from_admission
from openpine.runtime.rc6_config import serialize_engine_config
from openpine.runtime.rc6_worker_runtime import RC6WorkerProtocol, run_bulk
from rc6_tests.test_rc6_marketdata_boundary import OPENED, bar
from rc6_tests.test_rc6_worker_admission import ALL_COMMITS, _deployment, _manifest

SOURCE = '''//@version=6
strategy("bulk sizing")
if bar_index == 0
    strategy.entry("L", strategy.long)
'''


@pytest.fixture
def bulk_case():
    compiled = NativeRC6CompilerAdapter().compile(
        SOURCE, module_name="review_bulk_sizing", source_name="bulk-sizing.pine",
        producer_commits={"pine2ast": ALL_COMMITS["pine2ast"], "ast2python": ALL_COMMITS["ast2python"]},
    )
    assert compiled.success, compiled.errors
    artifact = {"generated_artifact": compiled.generated_artifact, "python_code": compiled.python_code,
                "consumer_bundle": compiled.consumer_bundle, "source_map": compiled.source_map,
                "compile_meta": compiled.compile_meta}
    context = execution_context_from_admission(
        _deployment(), _manifest(), run_id="run-review-bulk", strategy_id="strategy-review-bulk",
        artifact=artifact, data_snapshot_hash="sha256:" + "f" * 64,
        series_id="binance:spot:SOLUSDT:1m", instrument_id="binance:spot:SOLUSDT",
        exchange="binance", market="spot", symbol="SOLUSDT", timeframe="1m",
        semantic_profile="strict_5x", created_at_utc_ms=0,
    )
    config = BacktestConfig("SOLUSDT", "1m", OPENED, OPENED + 120_000,
                            initial_capital=1010, default_qty_value=7,
                            commission_type="none", commission_value=0, force_close_on_end=False)
    return compiled, context, config


def execute_bulk(monkeypatch, case, *, config=None, bars=None, last_batch=True, params=None):
    compiled, context, default_config = case
    config = default_config if config is None else config
    generated = compiled.generated_artifact
    request = dict(generated_artifact=generated, execution_context=context,
                   source=compiled.python_code, engine_config=serialize_engine_config(config, "strict_5x"), params={} if params is None else params)
    protocol = RC6WorkerProtocol(context)
    protocol.append("HELLO", dict(worker_id=context["session_id"], protocol_version="2.3.0",
                                  capabilities=list(WORKER_CAPABILITIES)), 0)
    frames = [protocol.append("LOAD_ARTIFACT", dict(
        artifact_hash=generated["content_hash"], module_hash=generated["emitted_module_hash"],
        entrypoint_module=generated["entrypoint"]["module"], entrypoint_class="GeneratedScript",
    ), 0), protocol.append("INIT_RUN", dict(
        run_id=context["run_id"], run_hash="sha256:" + "1" * 64,
        execution_context_hash=context["content_hash"], execution_context=context,
        semantic_profile="strict_5x", capabilities=list(WORKER_CAPABILITIES),
    ), 0)]
    if bars is None:
        bars = [bar(open_time_utc_ms=OPENED + index * 60_000) for index in range(3)]
    frames += [{"kind": "BULK_BARS", "bars": bars, "last": last_batch}]
    output = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO("".join(json.dumps(row) + "\n" for row in frames)))
    monkeypatch.setattr("sys.stdout", output)
    assert run_bulk(request, RC6WorkerProtocol(context)) == 0
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    from openpine.runtime.bulk_result import BulkResultReceiver, result_identity
    from openpine.runtime.inputs import input_evidence, resolve_inputs
    from openpine.runtime.rc6_config import resolve_engine_config
    effective = resolve_engine_config(request["engine_config"], context)
    expected = {**input_evidence(resolve_inputs(compiled.python_code, params)),
                "effective_config_hash": effective.effective_config_hash}
    with BulkResultReceiver(result_identity(context, expected)) as receiver:
        result = None
        for row in messages:
            if row["kind"].startswith("BULK_RESULT"):
                result = receiver.accept(row)
        assert result is not None and receiver.finished
    return result


@pytest.mark.parametrize("kind,value,expected", [("fixed", 7, 7), ("cash", 202, 2), ("percent_of_equity", 20, 2)])
def test_bulk_broker_fills_effective_default_quantity(monkeypatch, bulk_case, kind, value, expected):
    config = replace(bulk_case[2], default_qty_type=kind, default_qty_value=value)
    result = execute_bulk(monkeypatch, bulk_case, config=config)
    assert result["bars_processed"] == 3
    assert len(result["raw_result"]["open_trades"]) == 1
    assert result["raw_result"]["open_trades"][0]["qty"] == expected


def test_bulk_does_not_execute_open_tail(monkeypatch, bulk_case):
    rows = [bar(), bar(open_time_utc_ms=OPENED + 60_000),
            bar(open_time_utc_ms=OPENED + 120_000, finality="OPEN")]
    result = execute_bulk(monkeypatch, bulk_case, bars=rows)
    assert result["bars_processed"] == 2


def test_bulk_result_contains_actual_intents_and_equity(monkeypatch, bulk_case):
    result = execute_bulk(monkeypatch, bulk_case)
    assert len(result["intent_tape"]) == 1
    assert result["intent_tape"][0]["qty"] == "7"
    assert len(result["raw_result"]["equity_curve"]) == 3


def test_bulk_does_not_publish_success_after_engine_failure(monkeypatch, bulk_case):
    from types import SimpleNamespace
    monkeypatch.setattr("backtest_engine.BacktestEngine.run", lambda *_a, **_k: SimpleNamespace(
        status="failed", errors=["injected failure"], bars_processed=0, score_ledger_hash="bad"))
    with pytest.raises(ValueError, match="did not complete"):
        execute_bulk(monkeypatch, bulk_case)
