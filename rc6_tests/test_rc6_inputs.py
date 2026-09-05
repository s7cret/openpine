"""OP-02: real frontend, emitter, PineLib and broker. No source substitutions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backtest_engine import BacktestConfig
from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
from openpine.run_identity import execution_context_from_admission
from openpine.runtime.inputs import (
    InputBindingError,
    input_evidence,
    read_input_descriptors,
    resolve_inputs,
)
from openpine.runtime.isolated_run import IsolatedRunError, run_isolated_artifact
from openpine.runtime.rc6_worker_runtime import _session_from_request
from pinelib.core.values import is_na
from pinelib.runtime.metadata import BarValues
from rc6_tests.test_rc6_bulk_execution import execute_bulk
from rc6_tests.test_rc6_marketdata_boundary import OPENED, bar
from rc6_tests.test_rc6_worker_admission import ALL_COMMITS, _deployment, _manifest


def input_case(body: str, version: int = 6):
    compiled = NativeRC6CompilerAdapter().compile(
        f'//@version={version}\nstrategy("inputs")\n{body}\n',
        module_name="review_inputs",
        source_name="inputs.pine",
        producer_commits={key: ALL_COMMITS[key] for key in ("pine2ast", "ast2python")},
    )
    assert compiled.success, compiled.errors
    artifact = {
        "generated_artifact": compiled.generated_artifact,
        "python_code": compiled.python_code,
        "consumer_bundle": compiled.consumer_bundle,
        "source_map": compiled.source_map,
        "compile_meta": compiled.compile_meta,
    }
    context = execution_context_from_admission(
        _deployment(),
        _manifest(),
        run_id="run-inputs",
        strategy_id="strategy-inputs",
        artifact=artifact,
        data_snapshot_hash="sha256:" + "f" * 64,
        series_id="binance:spot:SOLUSDT:1m",
        instrument_id="binance:spot:SOLUSDT",
        exchange="binance",
        market="spot",
        symbol="SOLUSDT",
        timeframe="1m",
        semantic_profile="strict_5x",
        created_at_utc_ms=0,
    )
    config = BacktestConfig(
        "SOLUSDT",
        "1m",
        OPENED,
        OPENED + 120_000,
        initial_capital=100_000,
        commission_type="none",
        commission_value=0,
        force_close_on_end=False,
    )
    return compiled, context, config


def session_for(case, params=None, **extra):
    compiled, context, _ = case
    return _session_from_request(
        dict(
            source=compiled.python_code,
            generated_artifact=compiled.generated_artifact,
            execution_context=context,
            params={} if params is None else params,
            **extra,
        )
    )


def execute_series(session, count=10):
    for index in range(count):
        session.execute_bar(
            BarValues(
                open=index + 1,
                high=index + 2,
                low=index,
                close=index + 1,
                volume=1,
                time=OPENED + index * 60000,
                time_close=OPENED + index * 60000 + 59999,
            ),
            bar_index=index,
            last_bar_index=count - 1,
            strategy_values={},
        )


def variable_values(case, session, name):
    facts = case[0].consumer_bundle["semantic_facts"]["facts"]
    fact = next(
        row
        for row in facts
        if str(row.get("symbol_id", "")).startswith(f"user:vardeclaration:{name}:")
    )
    return session.session.series["series:" + fact["node_id"]].committed


@pytest.mark.parametrize("version", [5, 6])
def test_sma_overrides_2_and_7_produce_known_distinct_values(version):
    case = input_case('n=input.int(2,"Length",minval=1,maxval=10)\nm=ta.sma(close,n)', version)
    short, long = session_for(case), session_for(case, {"n": 7})
    execute_series(short)
    execute_series(long)
    a, b = variable_values(case, short, "m"), variable_values(case, long, "m")
    assert is_na(a[0]) and a[1:] == [i + 0.5 for i in range(1, 10)]
    assert all(is_na(value) for value in b[:6]) and b[6:] == [4, 5, 6, 7]
    assert short.inputs.values_hash != long.inputs.values_hash
    assert short.session.identity_hash != long.session.identity_hash
    assert short.inputs.get(read_input_descriptors(case[0].python_code)[0]["input_id"]) == 2


def test_compile_metadata_matches_sealed_module_descriptors():
    case = input_case("n=input.int(2)")
    assert tuple(case[0].compile_meta["input_descriptors"]) == read_input_descriptors(
        case[0].python_code
    )


def test_same_titles_preserve_independent_override_targets():
    case = input_case('fast=input.int(2,"Length")\nslow=input.int(7,"Length")\nm=fast+slow')
    rows = read_input_descriptors(case[0].python_code)
    session = session_for(case, {rows[0]["input_id"]: 3, rows[1]["input_id"]: 9})
    execute_series(session, 1)
    assert variable_values(case, session, "m") == [12]
    with pytest.raises(InputBindingError):
        session_for(case, {"Length": 99})


@pytest.mark.parametrize(
    "body,params,name,expected",
    [
        ("b=input.bool(true)", {"b": False}, "b", False),
        ("n=input.int(2)", {"n": 0}, "n", 0),
        ('s=input.string("text")', {"s": ""}, "s", ""),
        ("x=input.float(2.5)", {"x": 0}, "x", 0.0),
    ],
)
def test_falsey_values_reach_actual_pine_series(body, params, name, expected):
    case = input_case(body)
    session = session_for(case, params)
    execute_series(session, 1)
    assert variable_values(case, session, name) == [expected]


@pytest.mark.parametrize(
    "params,quantity",
    [({}, 2), ({"quantity": 7}, 7), ({"enabled": False}, None), ({"quantity": 0}, None)],
)
def test_bulk_real_broker_uses_inputs(monkeypatch, params, quantity):
    case = input_case(
        'quantity=input.int(2,"Qty",minval=0,maxval=10)\nenabled=input.bool(true)\n'
        'if bar_index == 0 and enabled and quantity > 0\n    strategy.entry("L",strategy.long,qty=quantity)'
    )
    result = execute_bulk(monkeypatch, case, params=params)
    trades = result["raw_result"]["open_trades"]
    assert ([trade["qty"] for trade in trades] if trades else []) == (
        [] if quantity is None else [quantity]
    )
    expected = input_evidence(resolve_inputs(case[0].python_code, params))
    for key, value in expected.items():
        assert result["raw_result"][key] == value


@pytest.mark.parametrize("params", [{"n": True}, {"n": 0}, {"n": 11}, {"unknown": 2}])
def test_invalid_input_is_rejected_before_process_spawn(monkeypatch, params):
    case = input_case("n=input.int(2,minval=1,maxval=10)")
    monkeypatch.setattr(
        "subprocess.Popen", lambda *_a, **_kw: pytest.fail("worker started before input validation")
    )
    with pytest.raises(IsolatedRunError, match="RC6_INPUT_INVALID"):
        run_isolated_artifact(
            case[0].python_code.encode(), bars=[], config=SimpleNamespace(), params=params
        )


def test_worker_rejects_claimed_hash_for_unapplied_values():
    case = input_case("n=input.int(2)")
    with pytest.raises(ValueError, match="parameter hash"):
        session_for(
            case, {"n": 7}, input_values_hash=resolve_inputs(case[0].python_code).values_hash
        )


def test_input_source_selects_numeric_chart_series_each_bar():
    case = input_case("src=input.source(close)\nm=ta.sma(src,2)")
    session = session_for(case, {"src": "high"})
    execute_series(session, 3)
    assert variable_values(case, session, "m")[1:] == [2.5, 3.5]


@pytest.mark.parametrize(
    "source", ["SCRIPT_METADATA = discover()", "SCRIPT_METADATA=()\nSCRIPT_METADATA=()"]
)
def test_descriptor_reader_never_executes_code(source):
    with pytest.raises(InputBindingError):
        read_input_descriptors(source)


def test_changed_descriptor_requires_new_artifact_hash():
    case = input_case("n=input.int(2)")
    compiled, context, _ = case
    with pytest.raises(ValueError, match="module hash"):
        _session_from_request(
            dict(
                source=compiled.python_code.replace("'default': 2", "'default': 7"),
                generated_artifact=compiled.generated_artifact,
                execution_context=context,
            )
        )


@pytest.mark.parametrize("version", range(1, 7))
def test_legacy_input_reaches_rc6_session_in_all_versions(version):
    case = input_case('n=input(2,"Length")', version)
    session = session_for(case, {"n": 0})
    execute_series(session, 1)
    assert variable_values(case, session, "n") == [0]


def test_admitted_run_hash_binds_actual_inputs_and_rejects_later_changes(tmp_path, monkeypatch):
    from openpine.run_identity import bind_isolated_execution, run_identity_path
    from openpine.runtime.rc6_marketdata import decode_canonical_bar
    import json

    case = input_case("n=input.int(2,minval=1,maxval=10)")
    compiled, _, config = case
    config.exchange, config.market_type = "binance", "spot"
    envelope = bar()
    artifact = {
        "generated_artifact": compiled.generated_artifact,
        "python_code": compiled.python_code,
        "consumer_bundle": compiled.consumer_bundle,
        "source_map": compiled.source_map,
        "compile_meta": compiled.compile_meta,
    }
    from dataclasses import replace

    deployment = replace(
        _deployment(),
        capabilities=_deployment().capabilities
        | {"broker_projection", "intent_tape_v2", "isolated_worker"},
    )
    monkeypatch.setenv("OPENPINE_BUILD_COMMIT", ALL_COMMITS["openpine"])
    hashes = []
    for value in (2, 7):
        run = bind_isolated_execution(
            config,
            data_dir=tmp_path,
            deployment=deployment,
            admitted_manifest=_manifest(),
            mode="backtest",
            run_id=f"input-{value}",
            strategy_id="strategy-inputs",
            artifact=artifact,
            bars=[decode_canonical_bar(envelope)],
            bar_envelopes=[envelope],
            supplemental_bars=None,
            created_at_utc_ms=0,
            params={"n": value},
        )
        persisted = json.loads(run_identity_path(tmp_path, f"input-{value}").read_text())
        assert persisted["config_hash"] == config.applied_config_hash == run["config_hash"]
        hashes.append(run["config_hash"])
    assert hashes[0] != hashes[1]
    monkeypatch.setattr(
        "subprocess.Popen", lambda *_a, **_kw: pytest.fail("changed inputs spawned worker")
    )
    with pytest.raises(IsolatedRunError, match="changed after run admission"):
        run_isolated_artifact(
            compiled.python_code.encode(), bars=[], config=config, params={"n": 2}
        )
