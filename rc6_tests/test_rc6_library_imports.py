"""Pinned library source -> normal compiler -> both broker transports.

In-memory IPC is explicit. The last four cases require the real CI sandbox.
"""

import json

import pytest
from backtest_engine import BacktestConfig
from pine2ast.libraries import LibraryStore
from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
from openpine.run_identity import execution_context_from_admission
from openpine.runtime.rc6_worker_runtime import _engine_bar
from rc6_tests.test_rc6_bulk_execution import execute_bulk
from rc6_tests.test_rc6_deferred_exits import compare_modes
from rc6_tests.test_rc6_generated_checkpoint import advance
from rc6_tests.test_rc6_lifecycle import make_session
from rc6_tests.test_rc6_marketdata_boundary import OPENED, bar
from rc6_tests.test_rc6_worker_admission import ALL_COMMITS, _deployment, _manifest


def sources(version=6):
    return {
        "qa/State/1": f'//@version={version}\nlibrary("State")\nexport count(int step=1)=>\n    var int n=0\n    n+=step\n    n\n',
        "qa/Signal/1": f'//@version={version}\nlibrary("Signal")\nimport qa/State/1 as state\nexport quantity()=>state.count(1)+state.count(10)\nplot(close)\n',
    }


def prepare(version=6, on_close=False, recalc=False, source_override=None, libs=None):
    source = (
        source_override
        or f"""//@version={version}
strategy("locked libraries")
import qa/Signal/1 as signal
q=signal.quantity()
if bar_index==2 and strategy.position_size==0
    strategy.entry("library",strategy.long,qty=q)
"""
    )
    store = LibraryStore.create(sources(version) if libs is None else libs)
    compiled = NativeRC6CompilerAdapter().compile(
        source,
        library_store=store,
        source_name="library-consumer.pine",
        module_name="library_review",
        producer_commits={
            "pine2ast": ALL_COMMITS["pine2ast"],
            "ast2python": ALL_COMMITS["ast2python"],
        },
    )
    assert compiled.success, compiled.errors
    artifact = {
        name: getattr(compiled, name)
        for name in ("generated_artifact", "source_map", "consumer_bundle", "compile_meta")
    }
    artifact["python_code"] = compiled.python_code
    context = execution_context_from_admission(
        _deployment(),
        _manifest(),
        run_id="run-review-bulk",
        strategy_id="strategy-review-bulk",
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
    cfg = BacktestConfig(
        "SOLUSDT",
        "1m",
        OPENED,
        OPENED + 5 * 60000,
        initial_capital=100000,
        commission_type="none",
        commission_value=0,
        force_close_on_end=False,
        process_orders_on_close=on_close,
        calc_on_order_fills=recalc,
    )
    rows = [
        bar(
            open_time_utc_ms=OPENED + i * 60000,
            open=100 + i,
            high=101 + i,
            low=99 + i,
            close=100 + i,
        )
        for i in range(6)
    ]
    return (compiled, context, cfg), rows


@pytest.mark.parametrize("version", [5, 6])
@pytest.mark.parametrize("on_close", [False, True])
@pytest.mark.parametrize("recalc", [False, True])
def test_transitive_stateful_exports_drive_exact_matching_broker_orders(
    monkeypatch, tmp_path, version, on_close, recalc
):
    case, rows = prepare(version, on_close, recalc)
    result, tape = compare_modes(monkeypatch, tmp_path, case, rows)
    assert [(e["command_id"], e["bar_index"], float(e["qty"])) for e in tape] == [
        ("library", 2, 33)
    ]
    assert [(t.entry_price, t.qty) for t in result.open_trades] == [(102 if on_close else 103, 33)]
    deps = case[0].generated_artifact["external_library_dependency_hashes"]
    assert set(deps) == {"qa/Signal/1", "qa/State/1", "@linkage"}
    # Only the consumer generates events; the library demonstration plot was not imported.
    assert "plot(" not in case[0].compile_meta["library_linkage"]["sources"]["qa/State/1"]["text"]


@pytest.mark.parametrize("cut", [1, 3, 5])
def test_generated_checkpoint_preserves_linked_function_call_state(cut):
    source = '//@version=6\nstrategy("checkpoint imports")\nimport qa/Signal/1 as signal\nq=signal.quantity()\nif bar_index==2\n    strategy.entry("library",strategy.long,qty=q)\n'
    case, rows = prepare(source_override=source)
    whole = make_session(case)
    expected = advance(whole, rows)
    prefix = make_session(case)
    first = advance(prefix, rows, stop=cut)
    saved = json.loads(json.dumps(prefix.export_state()))
    resumed = make_session(case)
    resumed.restore_state(saved)
    rest = advance(resumed, rows, start=cut)
    assert first + rest == expected
    assert whole.export_state() == resumed.export_state()


def test_changed_transitive_library_rejects_old_checkpoint():
    case, rows = prepare()
    r = make_session(case)
    advance(r, rows, stop=1)
    saved = r.export_state()
    modified = sources()
    modified["qa/State/1"] = modified["qa/State/1"].replace("n+=step", "n+=step*2")
    other, _ = prepare(libs=modified)
    assert other[0].generated_artifact["content_hash"] != case[0].generated_artifact["content_hash"]
    with pytest.raises(ValueError, match="identity"):
        make_session(other).restore_state(saved)


@pytest.mark.parametrize("period", [2, 3, 2])
def test_input_overrides_remain_root_owned_and_library_ta_uses_them(monkeypatch, tmp_path, period):
    libs = {
        "qa/Signal/1": '//@version=6\nlibrary("Signal")\nexport avg(float x,simple int n)=>ta.sma(x,n)\n'
    }
    source = """//@version=6
strategy("input imports")
import qa/Signal/1 as signal
n=input.int(2)
x=signal.avg(close,n)
if bar_index==2 and x==101
    strategy.entry("selected",strategy.long,qty=1)
"""
    case, rows = prepare(source_override=source, libs=libs)
    out = execute_bulk(monkeypatch, case, bars=rows, params={"n": period})
    assert [e["command_id"] for e in out["intent_tape"]] == (["selected"] if period == 3 else [])


@pytest.mark.parametrize(
    "fault", ["no_store", "private", "missing_revision", "wrong_pine_version", "tamper"]
)
def test_invalid_import_admission_is_explicit_without_running_worker(fault):
    libs = sources()
    source = '//@version=6\nstrategy("bad")\nimport qa/Signal/1 as s\nx=s.quantity()\n'
    store = LibraryStore.create(libs)
    kwargs = {"library_store": store}
    if fault == "no_store":
        kwargs = {}
    elif fault == "private":
        source = source.replace("s.quantity()", "s.hidden()")
    elif fault == "missing_revision":
        source = source.replace("Signal/1", "Signal/2")
    elif fault == "wrong_pine_version":
        kwargs = {"library_store": LibraryStore.create(sources(5))}
    else:
        # A manually constructed store still undergoes hash admission before linking.
        changed = dict(store._sources)
        changed["qa/Signal/1"] += "// tamper\n"
        kwargs = {"library_store": LibraryStore(changed, store._lock)}
    result = NativeRC6CompilerAdapter().compile(
        source, producer_commits={"pine2ast": "a" * 40, "ast2python": "b" * 40}, **kwargs
    )
    assert not result.success and not result.python_code


@pytest.mark.parametrize("mode", ["interactive", "bulk_backtest"])
@pytest.mark.parametrize("on_close", [False, True])
def test_real_worker_executes_linked_libraries_without_library_files(tmp_path, mode, on_close):
    from openpine.runtime.isolated_run import run_isolated_artifact

    case, rows = prepare(on_close=on_close, recalc=True)
    compiled, context, cfg = case
    for name, value in dict(
        execution_context=context,
        admitted_manifest=_manifest(),
        instrument_id=context["instrument_id"],
        generated_artifact=compiled.generated_artifact,
        bar_envelopes=rows,
        run_hash="sha256:" + "1" * 64,
        protocol_artifact_dir=str(tmp_path / "protocol"),
        isolated_protocol=mode,
    ).items():
        setattr(cfg, name, value)
    # No lock, library path or library source is sent into the execution worker.
    result = run_isolated_artifact(
        compiled.python_code.encode(), bars=[_engine_bar(b) for b in rows], config=cfg, params={}
    )
    assert result["ok"] and result["bars_processed"] == 6
    assert [(e["command_id"], float(e["qty"])) for e in result["intent_tape"]] == [("library", 33)]
    assert [(t.entry_price, t.qty) for t in result["raw_result"].open_trades] == [
        (102 if on_close else 103, 33)
    ]


def test_configured_store_does_not_change_scripts_without_actual_imports():
    compiler = NativeRC6CompilerAdapter()
    source = '//@version=6\nstrategy("import qa/Signal/1")\n// import qa/Signal/1\nplot(close)\n'
    kwargs = {"producer_commits": {"pine2ast": "a" * 40, "ast2python": "b" * 40}}
    bare = compiler.compile(source, **kwargs)
    configured = compiler.compile(source, library_store=LibraryStore.create(sources()), **kwargs)
    assert bare.success and configured.success
    assert bare.generated_artifact == configured.generated_artifact
    assert bare.python_code == configured.python_code
