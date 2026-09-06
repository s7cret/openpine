"""Actual Pine version controls mixed TP/SL selection, across both transports."""

import pytest

from rc6_tests.test_rc6_deferred_exits import prepare, compare_modes
from rc6_tests.test_rc6_marketdata_boundary import OPENED


def price_case(version=6, direction="long", leg="profit", scope="named", relative=200):
    absolute = {"profit": 105, "loss": 95}[leg]
    if direction == "short":
        absolute = 200 - absolute
    arg = '"A",' if scope == "named" else ""
    body = (
        (
            f'strategy.entry("A",strategy.{direction},qty=1,when=time=={OPENED})\n'
            f'strategy.exit("X",{arg}{leg}={relative},{"limit" if leg == "profit" else "stop"}={absolute},when=time=={OPENED})\n'
        )
        if version < 6
        else (
            "if bar_index==0\n" + f'    strategy.entry("A",strategy.{direction},qty=1)\n'
            f'    strategy.exit("X",{arg}{leg}={relative},{"limit" if leg == "profit" else "stop"}={absolute})\n'
        )
    )
    rows = [(100, 101, 99, 100), (100, 106, 94, 100)]
    if direction == "short":
        rows = [(200 - opened, 200 - low, 200 - high, 200 - closed) for opened, high, low, closed in rows]
    return prepare(body, rows, version=version)


@pytest.mark.parametrize("version", range(1, 7))
@pytest.mark.parametrize("leg", ["profit", "loss"])
@pytest.mark.parametrize("direction", ["long", "short"])
def test_same_pine_parameters_have_versioned_trades(monkeypatch, tmp_path, version, leg, direction):
    case, rows = price_case(version, direction, leg)
    result, tape = compare_modes(monkeypatch, tmp_path, case, rows)
    sign = (1 if direction == "long" else -1) * (1 if leg == "profit" else -1)
    assert [(t.exit_price, t.qty) for t in result.closed_trades] == [
        (100 + sign * (2 if version == 6 else 5), 1)
    ]
    event = tape[-1]
    assert event[leg] == "200" and event["schema_version"] == ("2.4.0" if version == 6 else "2.2.0")
    assert event.get("price_pair_policy") == ("first_trigger" if version == 6 else None)


@pytest.mark.parametrize("scope", ["named", "all"])
@pytest.mark.parametrize("relative", [0, 1000])
def test_zero_is_real_and_nearest_can_be_absolute(monkeypatch, tmp_path, scope, relative):
    case, rows = price_case(scope=scope, relative=relative)
    result, tape = compare_modes(monkeypatch, tmp_path, case, rows)
    assert [t.exit_price for t in result.closed_trades] == [100 if relative == 0 else 105]
    assert tape[-1]["schema_version"] == "2.4.0"
    assert ("from_entry" in tape[-1]) == (scope == "named")
    assert (tape[-1].get("exit_scope") == "all_entries") == (scope == "all")


@pytest.mark.parametrize("kind", ["market", "limit", "stop", "stop_limit"])
def test_policy_waits_for_each_pending_entry_real_price(monkeypatch, tmp_path, kind):
    params = {
        "market": "",
        "limit": ",limit=99",
        "stop": ",stop=101",
        "stop_limit": ",stop=101,limit=99",
    }[kind]
    body = (
        "if bar_index==0\n"
        + f'    strategy.entry("A",strategy.long,qty=1{params})\n'
        + '    strategy.exit("X","A",profit=200,limit=110)\n'
    )
    # stop-limit is activated before a later return to 99; a subsequent bar reaches TP.
    case, rows = prepare(body, [(100, 101, 99, 100), (100, 104, 95, 96), (99, 112, 98, 100)])
    result, tape = compare_modes(monkeypatch, tmp_path, case, rows)
    entry = {"market": 100, "limit": 99, "stop": 101, "stop_limit": 99}[kind]
    assert [(t.entry_price, t.exit_price) for t in result.closed_trades] == [(entry, entry + 2)]
    assert tape[-1]["price_pair_policy"] == "first_trigger"


@pytest.mark.parametrize("scope", ["named", "all"])
def test_repeated_fills_choose_different_members_of_same_pair(monkeypatch, tmp_path, scope):
    arg = '"A",' if scope == "named" else ""
    case, rows = prepare(
        'if bar_index==0\n    strategy.entry("A",strategy.long,qty=2)\n'
        'if bar_index==1\n    strategy.entry("A",strategy.long,qty=6)\n'
        f'if bar_index==2\n    strategy.exit("X",{arg}profit=2000,limit=125,qty_percent=50)\n',
        [
            (100, 101, 99, 100),
            (100, 101, 99, 100),
            (110, 111, 109, 110),
            (110, 121, 109, 110),
            (110, 126, 109, 110),
        ],
        pyramiding=2,
    )
    result, tape = compare_modes(monkeypatch, tmp_path, case, rows)
    assert [(t.entry_price, t.exit_price, t.qty) for t in result.closed_trades] == [
        (100, 120, 1),
        (110, 125, 3),
    ]
    assert [t.qty for t in result.open_trades] == [1, 3]
    assert tape[-1]["price_pair_policy"] == "first_trigger"


def test_nonfinite_na_member_does_not_select_a_zero_relative_distance(monkeypatch, tmp_path):
    case, rows = prepare(
        'float p=na\nif bar_index==0\n    strategy.entry("A",strategy.long,qty=1)\n'
        '    strategy.exit("X","A",profit=p,limit=105)\n',
        [(100, 101, 99, 100), (100, 106, 99, 100)],
    )
    result, tape = compare_modes(monkeypatch, tmp_path, case, rows)
    assert [t.exit_price for t in result.closed_trades] == [105]
    assert "profit" not in tape[-1] and "price_pair_policy" not in tape[-1]
    assert tape[-1]["schema_version"] == "2.2.0"


def test_formerly_rejected_exact_mixed_program_is_admitted():
    from openpine.compile.native_rc6 import NativeRC6CompilerAdapter

    result = NativeRC6CompilerAdapter().compile(
        '//@version=6\nstrategy("mixed")\nstrategy.exit("X","L",limit=105,profit=4)\n',
        producer_commits={"pine2ast": "a" * 40, "ast2python": "b" * 40},
    )
    assert result.success, result.errors
    assert "strategy.exit" in result.compile_meta["strategy_host"]["required"]


@pytest.mark.parametrize("mode", ["interactive", "bulk_backtest"])
@pytest.mark.parametrize("on_close", [False, True])
def test_real_worker_dual_pairs(tmp_path, mode, on_close):
    from openpine.runtime.isolated_run import run_isolated_artifact
    from openpine.runtime.rc6_worker_runtime import _engine_bar
    from rc6_tests.test_rc6_worker_admission import _manifest

    # Both profit and loss are nonzero here. Zero is tested on the actual broker above.
    case, rows = prepare(
        "varip bool sent=false\nif bar_index==0 and not sent\n    sent:=true\n"
        '    strategy.entry("A",strategy.long,qty=2)\n    strategy.entry("B",strategy.long,qty=3)\n'
        '    strategy.exit("X",profit=300,limit=110,loss=400,stop=90)\n',
        [(100, 101, 99, 100), (100, 108, 98, 100)],
        pyramiding=2,
        process_orders_on_close=on_close,
        calc_on_order_fills=True,
    )
    artifact, context, config = case
    for key, value in dict(
        execution_context=context,
        admitted_manifest=_manifest(),
        instrument_id=context["instrument_id"],
        generated_artifact=artifact.generated_artifact,
        bar_envelopes=rows,
        run_hash="sha256:" + "1" * 64,
        protocol_artifact_dir=str(tmp_path / "protocol"),
        isolated_protocol=mode,
    ).items():
        setattr(config, key, value)
    output = run_isolated_artifact(
        artifact.python_code.encode(), bars=[_engine_bar(b) for b in rows], config=config, params={}
    )
    assert output["ok"] and output["bars_processed"] == len(rows)
    result = output["raw_result"]
    assert result.status == "completed" and not result.errors
    assert [(t.entry_id, t.qty, t.exit_price) for t in result.closed_trades] == [
        ("A", 2, 103),
        ("B", 3, 103),
    ]
    event = output["intent_tape"][-1]
    assert event["schema_version"] == "2.4.0" and event["exit_scope"] == "all_entries"
    assert event["price_pair_policy"] == "first_trigger"
