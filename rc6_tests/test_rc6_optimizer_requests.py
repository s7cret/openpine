"""Trial-bound request snapshots execute real Pine; only IPC is replaced in unit cases."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from optimizer import RunnerRequest
from openpine_contracts import seal_content_hash, verify_content_hash

from openpine.optimizer.isolated_runner import IsolatedOptimizerRunner
from openpine.run_identity import execution_data_snapshot_hash, run_identity_path
from openpine.runtime.inputs import applied_config_hash, resolve_inputs
from openpine.runtime.request_data import rebind_request_manifest
from openpine.runtime.rc6_marketdata import decode_canonical_bar
from rc6_tests.test_rc6_bulk_execution import execute_bulk
from rc6_tests.test_rc6_requests import ID, case_for, source_rows
from rc6_tests.test_rc6_worker_admission import _manifest
from tests.rc4_fixtures import run_identity


def request(period, trial_id=1):
    return RunnerRequest({"period": period}, trial_id, {"final_equity"}, {"equity_curve"}, [])


def runner_case(tmp_path, monkeypatch=None, mode="bulk_backtest"):
    case, rows = case_for(
        f'period=input.int(2,minval=1,maxval=4)\nx=request.security("{ID}","5",ta.sma(close,period))\n'
        'if bar_index==9 and x==15\n    strategy.entry("matched",strategy.long,qty=1)\n'
        "if bar_index==11\n    strategy.close_all(immediately=true)",
        sources=[source_rows(prices=(10, 20, 30, 40))],
        count=20,
    )
    compiled, context, config = case
    config.exchange, config.market_type = "binance", "spot"
    config.isolated_protocol = mode
    from backtest_engine.models.bar import to_contract_bar
    from marketdata_provider.contracts import InstrumentKey, parse_timeframe

    instrument = InstrumentKey(exchange="binance", market="spot", symbol="SOLUSDT")
    timeframe = parse_timeframe("1m")
    bars = [
        to_contract_bar(
            decode_canonical_bar(row), instrument=instrument, timeframe=timeframe, closed=True
        )
        for row in rows
    ]
    snapshot = execution_data_snapshot_hash(
        bars=bars,
        supplemental_bars=None,
        exchange="binance",
        market="spot",
        symbol="SOLUSDT",
        timeframe="1m",
        start_ms=config.start_time,
        end_ms=config.end_time,
        finality_policy="CLOSED_BAR_ONLY",
    )
    seen = []
    if monkeypatch is not None:

        class InMemoryTransport:
            def run_isolated(self, source, bars, trial, **kwargs):
                payload = execute_bulk(
                    monkeypatch,
                    (compiled, trial.execution_context, trial),
                    bars=rows,
                    params=kwargs["params"],
                )
                seen.append((trial, payload))
                return SimpleNamespace(raw_result=SimpleNamespace(**payload["raw_result"]))

        monkeypatch.setattr(
            "openpine.optimizer.isolated_runner.BacktestEngineAdapter", InMemoryTransport
        )
    runner = IsolatedOptimizerRunner(
        source=compiled.python_code.encode(),
        bars=bars,
        config=config,
        expected_data_snapshot_hash=snapshot,
        execution_context=context,
        admitted_manifest=_manifest(),
        instrument_id=ID,
        generated_artifact=compiled.generated_artifact,
        bar_envelopes=rows,
        run_identity=run_identity(context, data_snapshot_hash=snapshot),
        data_dir=tmp_path,
        protocol_artifact_dir=str(tmp_path / "protocol"),
    )
    return runner, seen, case, rows


def test_repeated_reordered_trials_match_independent_backtests(monkeypatch, tmp_path):
    runner, seen, case, rows = runner_case(tmp_path, monkeypatch)
    original = deepcopy(runner.config.request_manifest)
    outputs = [runner(request(period, i)) for i, period in enumerate((2, 3, 2), 1)]
    assert all(not response.diagnostics for response in outputs)
    assert outputs[0].metrics == outputs[2].metrics != outputs[1].metrics
    for response, (trial, payload) in zip(outputs, seen):
        manifest = trial.request_manifest
        assert manifest["datasets"] == original["datasets"]
        assert manifest["execution_context_hash"] == trial.execution_context["content_hash"]
        assert verify_content_hash(manifest, schema_id="openpine.request_snapshots.v1")
        inputs = resolve_inputs(case[0].python_code, payload["raw_result"]["effective_inputs"])
        assert response.hashes["engine_config_hash"] == applied_config_hash(trial, inputs)
        persisted = json.loads(
            run_identity_path(tmp_path, trial.execution_context["run_id"]).read_text()
        )
        assert persisted["config_hash"] == response.hashes["engine_config_hash"]
        assert response.hashes["source_request_manifest_hash"] == original["content_hash"]
        # Replay the selected trial as an ordinary backtest with the same admitted inputs.
        direct = execute_bulk(
            monkeypatch,
            (case[0], trial.execution_context, trial),
            bars=rows,
            params=payload["raw_result"]["effective_inputs"],
        )
        assert direct["intent_tape"] == payload["intent_tape"]
        assert direct["raw_result"]["closed_trades"] == payload["raw_result"]["closed_trades"]
        assert direct["raw_result"]["final_equity"] == payload["raw_result"]["final_equity"]
        assert (
            direct["raw_result"]["score_ledger_hash"] == payload["raw_result"]["score_ledger_hash"]
        )
    assert runner.config.request_manifest == original
    assert len({r.hashes["trial_request_manifest_hash"] for r in outputs}) == 3
    fresh, _, _, _ = runner_case(tmp_path / "reordered", monkeypatch)
    assert fresh(request(3, 2)).metrics == outputs[1].metrics
    assert fresh(request(2, 1)).metrics == outputs[0].metrics


@pytest.mark.parametrize("fault", ["data", "root", "foreign_context"])
def test_rebind_does_not_launder_tampered_or_unrelated_snapshots(monkeypatch, tmp_path, fault):
    runner, seen, _, _ = runner_case(tmp_path, monkeypatch)
    manifest = runner.config.request_manifest
    if fault == "data":
        manifest["datasets"][0]["bars"][0]["close"] = "999"
    elif fault == "root":
        manifest["content_hash"] = "sha256:" + "f" * 64
    else:
        manifest["execution_context_hash"] = "sha256:" + "e" * 64
        runner.config.request_manifest = seal_content_hash(
            manifest, schema_id="openpine.request_snapshots.v1"
        )
    with pytest.raises(ValueError):
        runner(request(2))
    assert not seen
    assert not run_identity_path(tmp_path, runner.execution_context["run_id"] + ".trial-1").exists()


def test_rebind_rejects_semantic_context_change(monkeypatch, tmp_path):
    runner, _, _, _ = runner_case(tmp_path, monkeypatch)
    target = seal_content_hash(
        {**runner.execution_context, "pointvalue": "50"}, schema_id="openpine.execution_context.v1"
    )
    with pytest.raises(ValueError, match="cannot change execution semantics"):
        rebind_request_manifest(runner.config.request_manifest, runner.execution_context, target)


def test_host_conversion_preserves_detached_request_manifest():
    from openpine.runtime.engine import BacktestEngineAdapter, BacktestRunConfig

    host = BacktestRunConfig(
        "S",
        "1m",
        0,
        1000,
        semantic_profile="strict_5x",
        request_manifest={"datasets": [{"bars": [{"close": "10"}]}]},
    )
    native = BacktestEngineAdapter()._to_engine_config(host)
    assert native.request_manifest == host.request_manifest
    native.request_manifest["datasets"][0]["bars"][0]["close"] = "11"
    assert host.request_manifest["datasets"][0]["bars"][0]["close"] == "10"


@pytest.mark.parametrize(
    "value", [{1: "numeric", "1": "text"}, {"value": float("nan")}, {"value": float("inf")}]
)
def test_ambiguous_or_nonfinite_identity_is_rejected(value):
    from openpine.optimizer.isolated_runner import _stable_hash

    with pytest.raises((ValueError, TypeError)):
        _stable_hash(value)


@pytest.mark.parametrize("mode", ["interactive", "bulk_backtest"])
def test_real_optimizer_worker_requests_and_repeat(tmp_path, mode):
    runner, _, _, _ = runner_case(tmp_path, mode=mode)
    responses = [runner(request(period, i)) for i, period in enumerate((2, 3, 2), 1)]
    assert all(not response.diagnostics for response in responses)
    assert responses[0].metrics == responses[2].metrics != responses[1].metrics
    assert all(r.raw_result.status == "completed" for r in responses)
    # Independent IDs/paths permit overlap. Use actual worker processes, never
    # the in-memory test transport (which temporarily replaces stdin/stdout).
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(runner, request(period, trial_id))
            for period, trial_id in [(3, 12), (2, 11)]
        ]
        parallel = [future.result() for future in futures]
    assert all(not response.diagnostics for response in parallel)
    assert parallel[0].metrics == responses[1].metrics
    assert parallel[1].metrics == responses[0].metrics

    def fills(response):
        return [
            (t.entry_bar_index, t.exit_bar_index, t.direction, t.qty, t.entry_price, t.exit_price)
            for t in response.raw_result.closed_trades
        ]

    assert fills(parallel[0]) == fills(responses[1])
    assert fills(parallel[1]) == fills(responses[0])


def test_ordinary_numeric_metrics_do_not_become_broker_compute_switches(monkeypatch, tmp_path):
    from dataclasses import replace
    from optimizer import OptimizerConfig
    from optimizer.core.trial_runner import run_one

    runner, seen, _, _ = runner_case(tmp_path, monkeypatch)
    config = OptimizerConfig(
        output_dir=tmp_path,
        timeout_per_trial_sec=0,
        report_profiles=False,
        use_profile_auto_constraints=False,
    )
    trial = run_one(1, {"period": 2}, runner, config, "space", "config")
    assert trial.status == "completed", trial.error_message
    assert trial.metrics["net_profit"] == 1.0
    assert seen[0][0].required_metrics == set()
    assert not seen[0][1]["raw_result"]["errors"]
    response = runner(replace(request(2, 2), required_metrics={"sharpe_ratio"}))
    assert seen[-1][0].required_metrics == {"sharpe"}
    assert response.metrics["sharpe_ratio"] == seen[-1][1]["raw_result"]["sharpe_ratio"]


@pytest.mark.parametrize("metric", ["unknown", "status", "errors", "config_snapshot"])
def test_unbound_or_nonnumeric_metric_is_rejected_before_execution(monkeypatch, tmp_path, metric):
    from dataclasses import replace

    runner, seen, _, _ = runner_case(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="unsupported optimizer result metrics"):
        runner(replace(request(2), required_metrics={metric}))
    assert not seen


def test_special_ratio_calculation_switches_use_canonical_broker_names():
    from openpine.optimizer.isolated_runner import _engine_metric_requirements

    assert _engine_metric_requirements({"net_profit", "final_equity", "total_trades"}) == set()
    assert _engine_metric_requirements({"sharpe_ratio", "sortino_ratio"}) == {"sharpe", "sortino"}
