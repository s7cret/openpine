"""Only the process transport is replaced; compilation, Pine and broker are real."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from optimizer import RunnerRequest

from openpine.optimizer.isolated_runner import IsolatedOptimizerRunner
from openpine.run_identity import execution_data_snapshot_hash, run_identity_path
from openpine.runtime.inputs import InputBindingError
from openpine.runtime.rc6_marketdata import decode_canonical_bar
from rc6_tests.test_rc6_bulk_execution import execute_bulk
from rc6_tests.test_rc6_inputs import input_case
from rc6_tests.test_rc6_marketdata_boundary import OPENED, bar
from rc6_tests.test_rc6_worker_admission import _manifest
from tests.rc4_fixtures import run_identity


@pytest.fixture
def runner_case(monkeypatch, tmp_path):
    case = input_case(
        "quantity=input.int(2,minval=1,maxval=10)\n"
        'if bar_index == 0\n    strategy.entry("L",strategy.long,qty=quantity)'
    )
    compiled, context, config = case
    config.exchange, config.market_type = "binance", "spot"
    rows = [bar(open_time_utc_ms=OPENED + i * 60_000) for i in range(3)]
    bars = [decode_canonical_bar(row) for row in rows]
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

    class InMemoryTransport:
        def run_isolated(self, source, bars, config, **kwargs):
            assert source.decode() == compiled.python_code
            payload = execute_bulk(
                monkeypatch,
                (compiled, config.execution_context, config),
                bars=rows,
                params=kwargs["params"],
            )
            seen.append((config, payload))
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
        instrument_id=context["instrument_id"],
        generated_artifact=compiled.generated_artifact,
        bar_envelopes=rows,
        run_identity=run_identity(context, data_snapshot_hash=snapshot),
        data_dir=tmp_path,
        protocol_artifact_dir=str(tmp_path / "protocol"),
    )
    return runner, seen, tmp_path


def request(quantity=2, trial_id=1):
    return RunnerRequest(
        params={"quantity": quantity},
        trial_id=trial_id,
        required_metrics={"final_equity"},
        required_outputs={"equity_curve"},
        early_stop_conditions=[],
    )


def test_optimizer_trials_apply_distinct_inputs_and_reproduce_result(runner_case):
    import json

    runner, seen, directory = runner_case
    original_outputs = set(runner.config.required_outputs)
    short, long, repeat = [runner(request(q, i)) for i, q in enumerate((2, 7, 2), 1)]
    assert not short.diagnostics and not long.diagnostics
    assert short.metrics != long.metrics and short.metrics == repeat.metrics
    assert short.hashes["input_values_hash"] != long.hashes["input_values_hash"]
    assert short.hashes["engine_config_hash"] == repeat.hashes["engine_config_hash"]
    assert len(seen[0][1]["raw_result"]["equity_curve"]) == 3
    for response, (config, _) in zip((short, long, repeat), seen):
        run = json.loads(
            run_identity_path(directory, config.execution_context["run_id"]).read_text()
        )
        assert run["config_hash"] == response.hashes["engine_config_hash"]
        assert run["content_hash"] == response.hashes["run_identity_hash"]
    assert runner.base_params == {} and runner.config.required_outputs == original_outputs


@pytest.mark.parametrize(
    "field,value",
    [
        ("seed", 7),
        ("range", (0, 1)),
        ("early_stop_conditions", [{"metric": "net_profit"}]),
        ("required_outputs", {"imaginary"}),
        ("trial_id", -1),
        ("trial_id", True),
    ],
)
def test_unsupported_requests_fail_before_execution(runner_case, field, value):
    runner, seen, _ = runner_case
    with pytest.raises((ValueError, RuntimeError)):
        runner(replace(request(), **{field: value}))
    assert seen == []


def test_invalid_input_is_not_persisted_as_a_trial(runner_case):
    runner, seen, directory = runner_case
    with pytest.raises(InputBindingError):
        runner(request(0))
    assert seen == [] and not run_identity_path(directory, "run-inputs.trial-1").exists()


def test_changed_canonical_envelope_is_rejected(runner_case):
    runner, seen, _ = runner_case
    runner.bar_envelopes[0]["close"] = "999"
    with pytest.raises(RuntimeError, match="envelope identity"):
        runner(request())
    assert seen == []


def test_host_adapter_preserves_trial_outputs_and_warmup_settings():
    from openpine.runtime.engine import BacktestRunConfig, BacktestEngineAdapter
    from openpine.runtime.inputs import applied_config_hash, resolve_inputs

    host = BacktestRunConfig(
        "SOLUSDT", "1m", OPENED, OPENED + 120_000, semantic_profile="strict_5x"
    )
    for name, value in {
        "required_outputs": {"equity_curve"},
        "required_metrics": {"final_equity"},
        "warmup_policy": "CALC_ONLY",
        "min_pre_bars": 7,
        "score_end_policy": "LEAVE_OPEN",
    }.items():
        object.__setattr__(host, name, value)
    native = BacktestEngineAdapter()._to_engine_config(host)
    assert native.required_outputs == {"equity_curve"} and native.min_pre_bars == 7
    assert native.score_end_policy == "LEAVE_OPEN"
    inputs = resolve_inputs("VALUE=1")
    assert applied_config_hash(host, inputs) == applied_config_hash(native, inputs)


def test_optimizer_identity_rejects_unknown_objects_instead_of_hashing_type():
    from openpine.optimizer.isolated_runner import _stable_hash

    with pytest.raises(TypeError, match="unsupported optimizer identity"):
        _stable_hash(object())


def test_optimizer_base_hash_attests_engine_and_applied_inputs(runner_case):
    from openpine.runtime.inputs import applied_config_hash, resolve_inputs

    runner, _, _ = runner_case
    inputs = resolve_inputs(runner.source, runner.base_params)
    assert runner.engine_config_hash == applied_config_hash(runner.config, inputs)
    changed = replace(runner.config, initial_capital=12345)
    assert applied_config_hash(changed, inputs) != runner.engine_config_hash
