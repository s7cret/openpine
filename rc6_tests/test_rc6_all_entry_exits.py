"""Compiled all-entry exits and per-fill relative prices across both transports."""

import pytest

from rc6_tests.test_rc6_deferred_exits import compare_modes, prepare


def universal_case(direction="long", *, empty=False, recalc=False, on_close=False):
    arg = 'from_entry="",' if empty else ""
    source = (
        "varip bool sent=false\nif bar_index==0 and not sent\n    sent:=true\n"
        f'    strategy.entry("A",strategy.{direction},qty=2)\n'
        f'    strategy.entry("B",strategy.{direction},qty=3)\n'
        f'    strategy.exit("X",{arg}profit=500,loss=2000)\n'
    )
    rows = [(100, 101, 99, 100), (100, 106, 99, 100), (100, 101, 99, 100)]
    if direction == "short":
        rows = [
            (200 - opened, 200 - low, 200 - high, 200 - closed)
            for opened, high, low, closed in rows
        ]
    return prepare(
        source, rows, pyramiding=2, calc_on_order_fills=recalc, process_orders_on_close=on_close
    )


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("recalc", [False, True])
@pytest.mark.parametrize("on_close", [False, True])
def test_compiled_unqualified_bracket_closes_each_entry(
    monkeypatch, tmp_path, direction, empty, recalc, on_close
):
    case, rows = universal_case(direction, empty=empty, recalc=recalc, on_close=on_close)
    result, tape = compare_modes(monkeypatch, tmp_path, case, rows)
    assert [e["kind"] for e in tape] == ["entry", "entry", "exit"]
    assert tape[-1]["exit_scope"] == "all_entries" and tape[-1]["schema_version"] == "2.3.0"
    assert "from_entry" not in tape[-1]
    assert [(t.entry_id, t.qty, t.exit_price) for t in result.closed_trades] == [
        ("A", 2, 105 if direction == "long" else 95),
        ("B", 3, 105 if direction == "long" else 95),
    ]
    assert not result.open_trades


@pytest.mark.parametrize("same_id", [False, True])
def test_one_exit_covers_later_fills_but_not_a_new_position(monkeypatch, tmp_path, same_id):
    later = "A" if same_id else "B"
    case, rows = prepare(
        'if bar_index==0\n    strategy.entry("A",strategy.long,qty=2)\n'
        'if bar_index==1\n    strategy.exit("X",profit=5000,loss=7000)\n'
        f'if bar_index==2\n    strategy.entry("{later}",strategy.long,qty=3)\n'
        'if bar_index==5\n    strategy.entry("new-position",strategy.long,qty=1)\n',
        [
            (100, 101, 99, 100),
            (100, 101, 99, 100),
            (110, 111, 109, 110),
            (110, 151, 109, 110),
            (110, 161, 109, 110),
            (110, 111, 109, 110),
            (110, 180, 109, 110),
        ],
        pyramiding=3,
    )
    result, tape = compare_modes(monkeypatch, tmp_path, case, rows)
    assert len([e for e in tape if e["kind"] == "exit"]) == 1
    assert [(t.entry_price, t.exit_price, t.qty) for t in result.closed_trades] == [
        (100, 150, 2),
        (110, 160, 3),
    ]
    assert [t.entry_id for t in result.open_trades] == ["new-position"]


@pytest.mark.parametrize("scope", ["all", "explicit"])
def test_repeated_ids_relative_exit_quantities_and_prices(monkeypatch, tmp_path, scope):
    arg = '"A",' if scope == "explicit" else ""
    case, rows = prepare(
        'if bar_index==0\n    strategy.entry("A",strategy.long,qty=2)\n'
        'if bar_index==1\n    strategy.entry("A",strategy.long,qty=6)\n'
        f'if bar_index==2\n    strategy.exit("X",{arg}profit=2000,qty_percent=50)\n',
        [
            (100, 101, 99, 100),
            (100, 101, 99, 100),
            (110, 111, 109, 110),
            (110, 125, 109, 110),
            (110, 135, 109, 110),
        ],
        pyramiding=2,
    )
    result, tape = compare_modes(monkeypatch, tmp_path, case, rows)
    assert [(t.entry_price, t.exit_price, t.qty) for t in result.closed_trades] == [
        (100, 120, 1),
        (110, 130, 3),
    ]
    assert [t.qty for t in result.open_trades] == [1, 3]
    assert tape[-1]["schema_version"] == ("2.2.0" if scope == "explicit" else "2.3.0")


@pytest.mark.parametrize("cancel", ['strategy.cancel("X")', "strategy.cancel_all()"])
def test_cancel_stops_existing_and_future_protection(monkeypatch, tmp_path, cancel):
    case, rows = prepare(
        'if bar_index==0\n    strategy.entry("A",strategy.long,qty=1)\n'
        'if bar_index==1\n    strategy.exit("X",profit=2000)\n'
        f'if bar_index==2\n    {cancel}\n    strategy.entry("B",strategy.long,qty=2)\n',
        [(100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100), (100, 150, 99, 100)],
        pyramiding=2,
    )
    result, _ = compare_modes(monkeypatch, tmp_path, case, rows)
    assert not result.closed_trades and len(result.open_trades) == 2


@pytest.mark.parametrize("version", range(1, 6))
def test_historical_when_and_omitted_scope(monkeypatch, tmp_path, version):
    from rc6_tests.test_rc6_marketdata_boundary import OPENED

    case, rows = prepare(
        f'strategy.entry("A",strategy.long,qty=1,when=time=={OPENED})\n'
        f'strategy.exit("X",profit=500,when=time=={OPENED})\n',
        [(100, 101, 99, 100), (100, 106, 99, 100)],
        version=version,
    )
    result, tape = compare_modes(monkeypatch, tmp_path, case, rows)
    assert [(t.entry_price, t.exit_price) for t in result.closed_trades] == [(100, 105)]
    assert [e["kind"] for e in tape] == ["entry", "exit"]


@pytest.mark.parametrize("mode", ["interactive", "bulk_backtest"])
@pytest.mark.parametrize("on_close", [False, True])
def test_real_worker_all_entry_scope_and_distinct_lots(tmp_path, mode, on_close):
    from openpine.runtime.isolated_run import run_isolated_artifact
    from openpine.runtime.rc6_worker_runtime import _engine_bar
    from rc6_tests.test_rc6_worker_admission import _manifest

    case, rows = universal_case(recalc=True, on_close=on_close)
    compiled, context, config = case
    for key, value in dict(
        execution_context=context,
        admitted_manifest=_manifest(),
        instrument_id=context["instrument_id"],
        generated_artifact=compiled.generated_artifact,
        bar_envelopes=rows,
        run_hash="sha256:" + "1" * 64,
        protocol_artifact_dir=str(tmp_path / "protocol"),
        isolated_protocol=mode,
    ).items():
        setattr(config, key, value)
    output = run_isolated_artifact(
        compiled.python_code.encode(), bars=[_engine_bar(b) for b in rows], config=config, params={}
    )
    assert output["ok"] and output["bars_processed"] == len(rows)
    assert output["intent_tape"][-1]["exit_scope"] == "all_entries"
    result = output["raw_result"]
    assert result.status == "completed" and not result.open_trades
    assert [(t.entry_id, t.qty, t.exit_price) for t in result.closed_trades] == [
        ("A", 2, 105),
        ("B", 3, 105),
    ]


@pytest.mark.parametrize("kind", ["limit", "stop", "stop_limit"])
def test_unqualified_protection_waits_for_price_entry(monkeypatch, tmp_path, kind):
    arg = {"limit": "limit=99", "stop": "stop=101", "stop_limit": "stop=101,limit=99"}[kind]
    prices = [
        (100, 101, 99, 100),
        (100, 104, 95, 96) if kind == "stop_limit" else (100, 103, 97, 100),
    ]
    case, rows = prepare(
        "if bar_index==0\n" + f'    strategy.entry("A",strategy.long,qty=2,{arg})\n'
        '    strategy.exit("X",profit=100,loss=100)\n',
        prices,
    )
    result, tape = compare_modes(monkeypatch, tmp_path, case, rows)
    expected = (101, 102) if kind == "stop" else (99, 98)
    assert [(t.entry_price, t.exit_price, t.qty) for t in result.closed_trades] == [(*expected, 2)]
    assert tape[-1]["exit_scope"] == "all_entries"
