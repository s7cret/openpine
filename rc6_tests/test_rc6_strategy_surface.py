"""Compiled Pine -> host -> real broker, with both transports compared."""

from __future__ import annotations
from dataclasses import replace

import pytest

from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
from openpine.runtime.strategy_host import strategy_host_surface
from rc6_tests.test_rc6_bulk_execution import bulk_case as _base, execute_bulk
from rc6_tests.test_rc6_lifecycle import compile_case, interactive
from rc6_tests.test_rc6_marketdata_boundary import OPENED, bar


def compiled(body, **settings):
    case = compile_case(_base.__wrapped__(), '//@version=6\nstrategy("surface")\n' + body)
    return case[:2] + (
        replace(case[2], default_qty_value=2, end_time=OPENED + 240_000, **settings),
    )


def rows(count=5):
    return [bar(open_time_utc_ms=OPENED + i * 60_000) for i in range(count)]


SCENARIOS = [
    (
        'if bar_index == 0\n    strategy.order("L", strategy.long, qty=3)\nif bar_index == 1\n    strategy.order("S", strategy.short, qty=1)\nif bar_index == 2\n    strategy.close_all(immediately=true)\n',
        ["order", "order", "close_all"],
        2,
    ),
    (
        'if bar_index == 0\n    strategy.order("L", strategy.long)\n    strategy.cancel("L")\n',
        ["order", "cancel"],
        0,
    ),
    (
        'if bar_index == 0\n    strategy.order("L", strategy.long)\n    strategy.order("S", strategy.short)\n    strategy.cancel_all()\n',
        ["order", "order", "cancel_all"],
        0,
    ),
    (
        'if bar_index == 0\n    strategy.entry("L", strategy.long)\nif bar_index == 1\n    strategy.exit("X", "L", limit=102, stop=98)\n',
        ["entry", "exit"],
        1,
    ),
    (
        'if bar_index == 0\n    strategy.entry("L", strategy.long, qty=3)\nif bar_index == 1\n    strategy.exit("X", "L", limit=102, qty=1)\nif bar_index == 3\n    strategy.close_all(immediately=true)\n',
        ["entry", "exit", "close_all"],
        2,
    ),
    (
        'if bar_index == 0\n    strategy.entry("L", strategy.long)\nif bar_index == 1\n    strategy.exit("X", "L", limit=200, stop=50)\nif bar_index == 2\n    strategy.cancel("X")\nif bar_index == 3\n    strategy.close_all(immediately=true)\n',
        ["entry", "exit", "cancel", "close_all"],
        1,
    ),
]


@pytest.mark.parametrize("body,kinds,trades", SCENARIOS)
def test_command_tapes_trades_and_equity_match_transports(
    monkeypatch, tmp_path, body, kinds, trades
):
    case = compiled(body)
    bulk = execute_bulk(monkeypatch, case, bars=rows())
    result, tape, *_ = interactive(case, rows(), tmp_path)
    assert result.status == "completed"
    assert bulk["intent_tape"] == tape
    assert [x["kind"] for x in tape] == kinds
    assert len(bulk["raw_result"]["closed_trades"]) == len(result.closed_trades) == trades
    assert bulk["raw_result"]["final_equity"] == result.final_equity
    assert len(bulk["raw_result"]["open_trades"]) == len(result.open_trades) == 0


# All 17 bound scalars are exercised, including aggregate counts after
# incremental protocol trade rows have become empty. Flat NA is tested below.
STATE_CONDITIONS = {
    "position_size": "strategy.position_size == 0",
    "position_avg_price": "strategy.position_avg_price == 100",
    "position_entry_name": 'strategy.position_entry_name == "L"',
    "initial_capital": "strategy.initial_capital == 1010",
    "account_currency": 'strategy.account_currency == "USD"',
    "equity": "strategy.equity == 1012",
    "netprofit": "strategy.netprofit == 2",
    "openprofit": "strategy.openprofit == 0",
    "grossprofit": "strategy.grossprofit == 2",
    "grossloss": "strategy.grossloss == 0",
    "wintrades": "strategy.wintrades == 1",
    "losstrades": "strategy.losstrades == 0",
    "eventrades": "strategy.eventrades == 0",
    "opentrades": "strategy.opentrades == 0",
    "closedtrades": "strategy.closedtrades == 1",
    "max_drawdown": "strategy.max_drawdown >= 0",
    "max_runup": "strategy.max_runup >= 0",
}


@pytest.mark.parametrize("name,condition", STATE_CONDITIONS.items())
def test_scalar_state_drives_actual_pine_orders(monkeypatch, tmp_path, name, condition):
    evaluation_bar = 1 if name in {"position_avg_price", "position_entry_name"} else 3
    source = (
        'if bar_index == 0\n    strategy.entry("L", strategy.long)\n'
        'if bar_index == 1\n    strategy.close("L", immediately=true)\n'
        f'if bar_index == {evaluation_bar} and {condition}\n    strategy.order("verified", strategy.long, qty=1)\n'
    )
    case = compiled(source, currency="USD")
    bulk = execute_bulk(monkeypatch, case, bars=rows())
    result, tape, *_ = interactive(case, rows(), tmp_path)
    assert tape == bulk["intent_tape"]
    assert any(e["command_id"] == "verified" for e in tape), name
    assert len(result.open_trades) == 1 and result.open_trades[0].qty == 1


@pytest.mark.parametrize(
    "source,fragment",
    [
        ("strategy.risk.max_position_size(5)", "strategy.risk.max_position_size"),
        ("strategy.risk.allow_entry_in(strategy.direction.long)", "strategy.risk.allow_entry_in"),
        ("float p = strategy.closedtrades.profit(0)", "strategy.closedtrades.profit"),
        ("float p = strategy.margin_liquidation_price", "strategy.margin_liquidation_price"),
        ('strategy.exit("X", stop=99)', "from_entry"),
        ('strategy.exit("X", "L", trail_points=10, trail_offset=5)', "unsupported host parameters"),
        ('strategy.exit("X", "L", limit=105, profit=4)', "relative/absolute"),
        ('strategy.exit("X", "L", stop=99, comment_loss="SL")', "unsupported host parameters"),
    ],
)
def test_unavailable_host_surface_fails_at_compilation_even_in_unexecuted_branch(source, fragment):
    local = not source.startswith("strategy.risk.")
    program = (
        '//@version=6\nstrategy("negative")\n' + ("if false\n    " if local else "") + source + "\n"
    )
    result = NativeRC6CompilerAdapter().compile(
        program, producer_commits={"pine2ast": "a" * 40, "ast2python": "b" * 40}
    )
    assert not result.success
    if "closedtrades.profit" in source:
        assert "A2P_DELEGATED_RESULT_REQUIRES_COMMIT" in result.errors[0]
    else:
        assert "RC6_HOST_CAPABILITY" in result.errors[0]
        assert fragment in result.errors[0]
        assert (
            "generated line" if source.startswith("float ") else f"Pine line {4 if local else 3}"
        ) in result.errors[0]


@pytest.mark.parametrize("version", range(1, 6))
def test_historical_named_when_reaches_the_broker(monkeypatch, version):
    base = _base.__wrapped__()
    case = compile_case(
        base,
        f'//@version={version}\nstrategy("when")\nstrategy.order("suppressed", strategy.long, when=false)\nstrategy.cancel_all(when=true)\n',
    )
    result = execute_bulk(monkeypatch, case, bars=rows(2))
    assert [event["kind"] for event in result["intent_tape"]] == ["cancel_all", "cancel_all"]
    assert [event["sequence"] for event in result["intent_tape"]] == [0, 1]
    assert not result["raw_result"]["open_trades"]


def test_registry_matches_all_exercised_operations_and_values():
    surface = strategy_host_surface()
    assert len(surface["commands"]) == 7
    assert set(surface["state_values"]) == {"strategy." + name for name in STATE_CONDITIONS}
    assert surface["content_hash"].startswith("sha256:")
    case = compiled('strategy.order("L", strategy.long)\n')
    assert case[0].compile_meta["strategy_host"]["surface_hash"] == surface["content_hash"]


def test_aggregate_counts_survive_multiple_trade_deltas(monkeypatch, tmp_path):
    body = """if bar_index == 0 or bar_index == 2 or bar_index == 4
    strategy.order("L", strategy.long, qty=1)
if bar_index == 1 or bar_index == 3 or bar_index == 5
    strategy.close("L", immediately=true)
if bar_index == 7 and strategy.closedtrades == 3 and strategy.wintrades == 1 and strategy.losstrades == 1 and strategy.eventrades == 1 and strategy.netprofit == 0 and strategy.grossprofit == 1 and strategy.grossloss == 1
    strategy.order("verified", strategy.long, qty=1)
"""
    case = compiled(body)
    case = case[:2] + (replace(case[2], end_time=OPENED + 8 * 60_000),)
    candles = [
        bar(
            open_time_utc_ms=OPENED + i * 60_000,
            open=100,
            high=102,
            low=98,
            close=101 if i == 1 else 99 if i == 3 else 100,
        )
        for i in range(9)
    ]
    bulk = execute_bulk(monkeypatch, case, bars=candles)
    result, tape, *_ = interactive(case, candles, tmp_path)
    assert bulk["intent_tape"] == tape
    assert [e["command_id"] for e in tape][-1] == "verified"
    assert result.total_trades == 3 and result.open_trades[0].qty == 1


def test_oca_cancel_group_reaches_broker_and_only_one_side_fills(monkeypatch, tmp_path):
    case = compiled("""if bar_index == 0
    strategy.order("L", strategy.long, stop=102, oca_name="pair", oca_type=strategy.oca.cancel)
    strategy.order("S", strategy.short, stop=99, oca_name="pair", oca_type=strategy.oca.cancel)
if bar_index == 2
    strategy.close_all(immediately=true)
""")
    candles = [bar(open_time_utc_ms=OPENED + i * 60_000, high=103, low=98) for i in range(5)]
    bulk = execute_bulk(monkeypatch, case, bars=candles)
    result, tape, *_ = interactive(case, candles, tmp_path)
    assert bulk["intent_tape"] == tape
    assert result.total_trades == 1 and len(result.closed_trades) == 1
    assert result.closed_trades[0].direction == "short"
    assert [e["oca_type"] for e in tape if e["kind"] == "order"] == ["cancel", "cancel"]


def test_external_artifact_with_missing_host_is_rejected_before_staging(tmp_path, monkeypatch):
    from ast2python.api import compile_consumer_bundle
    from ast2python.lowering import load_pinelib_target_manifest
    from pine2ast.hardening.consumer_bundle import build_consumer_bundle
    from openpine.run_identity import execution_context_from_admission
    from openpine.runtime import isolated_worker
    from rc6_tests.test_rc6_worker_admission import ALL_COMMITS, _deployment, _manifest
    from unittest.mock import Mock

    source = '//@version=6\nstrategy("external")\nstrategy.risk.max_position_size(2)\n'
    bundle = build_consumer_bundle(source, producer_commit=ALL_COMMITS["pine2ast"])
    result = compile_consumer_bundle(
        bundle,
        target=load_pinelib_target_manifest(),
        module_name="external_host",
        producer_commit=ALL_COMMITS["ast2python"],
        expected_pine2ast_commit=ALL_COMMITS["pine2ast"],
    )
    artifact = {
        "generated_artifact": result.artifact.to_dict(),
        "python_code": result.emitted.code,
        "consumer_bundle": bundle,
        "source_map": result.emitted.source_map.to_dict(),
    }
    context = execution_context_from_admission(
        _deployment(),
        _manifest(),
        run_id="run-test",
        strategy_id="strategy-test",
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
    stage = Mock(side_effect=AssertionError("unexpected package staging"))
    process = Mock(side_effect=AssertionError("unexpected process creation"))
    monkeypatch.setattr(isolated_worker, "_stage_trusted_packages", stage)
    monkeypatch.setattr(isolated_worker.subprocess, "Popen", process)
    with pytest.raises(isolated_worker.IsolatedWorkerError, match="RC6_HOST_CAPABILITY"):
        isolated_worker.InteractiveWorkerSession(
            result.emitted.code.encode(),
            context,
            context["instrument_id"],
            _manifest(),
            artifact["generated_artifact"],
            "sha256:" + "1" * 64,
            tmp_path,
            semantic_profile="strict_5x",
            chart_timeframe="1m",
        )
    stage.assert_not_called()
    process.assert_not_called()


@pytest.mark.parametrize("mode", ["interactive", "bulk_backtest"])
def test_real_worker_strategy_surface(tmp_path, mode):
    """Real process, no IPC substitution; mandatory in the sandbox-enabled CI."""
    from openpine.runtime.isolated_run import run_isolated_artifact
    from openpine.runtime.rc6_worker_runtime import _engine_bar
    from rc6_tests.test_rc6_worker_admission import _manifest

    case = compiled("""if bar_index == 0
    strategy.order("cancelled", strategy.long, limit=50)
    strategy.cancel("cancelled")
    strategy.order("L", strategy.long, qty=3)
if bar_index == 1
    strategy.exit("X", "L", qty=1, limit=102)
if bar_index == 3 and strategy.closedtrades == 1 and strategy.position_size == 2
    strategy.cancel_all()
    strategy.close_all(immediately=true)
""")
    artifact, context, config = case
    candles = rows()
    for name, value in dict(
        execution_context=context,
        admitted_manifest=_manifest(),
        instrument_id=context["instrument_id"],
        generated_artifact=artifact.generated_artifact,
        bar_envelopes=candles,
        run_hash="sha256:" + "1" * 64,
        protocol_artifact_dir=str(tmp_path / "protocol"),
        isolated_protocol=mode,
    ).items():
        setattr(config, name, value)
    result = run_isolated_artifact(
        artifact.python_code.encode(),
        bars=[_engine_bar(b) for b in candles],
        config=config,
        params={},
    )
    assert result["ok"] is True and result["raw_result"].status == "completed"
    assert result["bars_processed"] == 5
    assert [e["kind"] for e in result["intent_tape"]] == [
        "order",
        "cancel",
        "order",
        "exit",
        "cancel_all",
        "close_all",
    ]
    assert [t.qty for t in result["raw_result"].closed_trades] == [1, 2]
    assert not result["raw_result"].open_trades
