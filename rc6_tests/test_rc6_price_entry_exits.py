"""Actual compiled price-entry brackets; both IPC modes and real workers.

Synthetic OHLC examples specify the path explicitly, not a TradingView oracle.
"""

from __future__ import annotations

import pytest

from rc6_tests.test_rc6_deferred_exits import compare_modes, prepare


def price_case(kind, direction="long", *, recalc=False, on_close=False):
    def price(value):
        return value if direction == "long" else 200 - value
    args = {
        "limit": f"limit={price(99)}",
        "stop": f"stop={price(101)}",
        "stop_limit": f"stop={price(101)},limit={price(99)}",
    }[kind]
    rows = (
        [(100, 101, 99, 100), (100, 104, 95, 96)]
        if kind == "stop_limit"
        else [(100, 101, 99, 100), (100, 103, 97, 100)]
    )
    if direction == "short":
        rows = [
            (200 - opened, 200 - low, 200 - high, 200 - closed)
            for opened, high, low, closed in rows
        ]
    # Admitted mintick is 0.01. The bracket is one price unit, not 100.
    body = (
        "varip bool sent=false\nif bar_index==0 and not sent\n    sent:=true\n"
        f'    strategy.entry("L",strategy.{direction},qty=2,{args})\n'
        '    strategy.exit("X","L",profit=100,loss=100)\n'
    )
    case, candles = prepare(
        body, rows, calc_on_order_fills=recalc, process_orders_on_close=on_close
    )
    entry, exit_price = (101, 102) if kind == "stop" else (99, 98)
    return case, candles, (price(entry), price(exit_price))


@pytest.mark.parametrize("kind", ["limit", "stop", "stop_limit"])
@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("recalc", [False, True])
@pytest.mark.parametrize("on_close", [False, True])
def test_compiled_price_entry_bracket_matches_both_modes(
    monkeypatch, tmp_path, kind, direction, recalc, on_close
):
    case, candles, expected = price_case(kind, direction, recalc=recalc, on_close=on_close)
    result, tape = compare_modes(monkeypatch, tmp_path, case, candles)
    assert [e["kind"] for e in tape] == ["entry", "exit"]
    assert [
        (t.entry_price, t.exit_price, t.qty, t.entry_bar_index, t.exit_bar_index)
        for t in result.closed_trades
    ] == [(*expected, 2, 1, 1)]
    assert not result.open_trades


@pytest.mark.parametrize("recalc", [False, True])
def test_compiled_bracket_cannot_see_pre_entry_low_or_replay_it_at_close(
    monkeypatch, tmp_path, recalc
):
    case, candles = prepare(
        'if bar_index==0\n    strategy.entry("L",strategy.long,qty=1,stop=103)\n'
        '    strategy.exit("X","L",stop=99,limit=110)\n',
        [(100, 101, 99, 100), (100, 106, 98, 102)],
        calc_on_order_fills=recalc,
    )
    result, tape = compare_modes(monkeypatch, tmp_path, case, candles)
    assert not result.closed_trades
    assert [(t.entry_price, t.qty) for t in result.open_trades] == [(103, 1)]
    assert [e["kind"] for e in tape] == ["entry", "exit"]


@pytest.mark.parametrize("version", range(1, 6))
def test_historical_named_when_for_price_entry_bracket(monkeypatch, tmp_path, version):
    from rc6_tests.test_rc6_marketdata_boundary import OPENED

    case, candles = prepare(
        f'strategy.entry("L",strategy.long,qty=1,limit=99,when=time=={OPENED})\n'
        f'strategy.exit("X","L",profit=100,loss=100,when=time=={OPENED})\n',
        [(100, 101, 99, 100), (100, 103, 97, 100)],
        version=version,
    )
    result, _ = compare_modes(monkeypatch, tmp_path, case, candles)
    assert [(t.entry_price, t.exit_price, t.qty) for t in result.closed_trades] == [(99, 98, 1)]


@pytest.mark.parametrize("kind", ["limit", "stop", "stop_limit"])
@pytest.mark.parametrize("mode", ["interactive", "bulk_backtest"])
def test_real_worker_price_entry_bracket(tmp_path, kind, mode):
    from openpine.runtime.isolated_run import run_isolated_artifact
    from openpine.runtime.rc6_worker_runtime import _engine_bar
    from rc6_tests.test_rc6_worker_admission import _manifest

    case, candles, expected = price_case(kind, recalc=True, on_close=True)
    compiled, context, config = case
    for name, value in dict(
        execution_context=context,
        admitted_manifest=_manifest(),
        instrument_id=context["instrument_id"],
        generated_artifact=compiled.generated_artifact,
        bar_envelopes=candles,
        run_hash="sha256:" + "1" * 64,
        protocol_artifact_dir=str(tmp_path / "protocol"),
        isolated_protocol=mode,
    ).items():
        setattr(config, name, value)
    result = run_isolated_artifact(
        compiled.python_code.encode(),
        bars=[_engine_bar(b) for b in candles],
        config=config,
        params={},
    )
    assert result["ok"] and result["bars_processed"] == 2
    assert [e["kind"] for e in result["intent_tape"]] == ["entry", "exit"]
    raw = result["raw_result"]
    assert raw.status == "completed" and not raw.open_trades
    assert [
        (t.entry_price, t.exit_price, t.qty, t.entry_bar_index, t.exit_bar_index)
        for t in raw.closed_trades
    ] == [(*expected, 2, 1, 1)]
