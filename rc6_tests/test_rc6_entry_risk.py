"""Entry-only risk controls through actual Pine compilation and both brokers."""

import pytest
from rc6_tests.test_rc6_deferred_exits import prepare, compare_modes
from rc6_tests.test_rc6_bulk_execution import execute_bulk
from rc6_tests.test_rc6_marketdata_boundary import OPENED

ROWS = [(100, 101, 99, 100)] * 5


def cap_case(version=6, direction="long", **settings):
    entry = (
        f'if bar_index==0\n    strategy.entry("A",strategy.{direction},qty=9)\n'
        if version == 6
        else f'strategy.entry("A",strategy.{direction},qty=9,when=time=={OPENED})\n'
    )
    # Global risk rule after entry must still apply to the same callback.
    return prepare(
        entry + "strategy.risk.max_position_size(3)\n", ROWS, version=version, **settings
    )


@pytest.mark.parametrize("version", range(1, 7))
@pytest.mark.parametrize("direction", ["long", "short"])
def test_risk_caps_do_not_drop_oversized_entry(monkeypatch, tmp_path, version, direction):
    case, rows = cap_case(version, direction)
    result, tape = compare_modes(monkeypatch, tmp_path, case, rows)
    assert [(t.entry_id, t.direction, t.qty) for t in result.open_trades] == [("A", direction, 3)]
    risks = [e for e in tape if e["kind"] == "risk"]
    assert len(risks) == len(rows)
    assert all(e["risk_rule"] == "max_position_size" and e["risk_value"] == "3" for e in risks)


@pytest.mark.parametrize("version", range(1, 7))
@pytest.mark.parametrize("direction", ["long", "short"])
def test_allowed_direction_closes_without_reverse_in_every_version(
    monkeypatch, tmp_path, version, direction
):
    other = "short" if direction == "long" else "long"
    rule = f"strategy.risk.allow_entry_in(strategy.direction.{direction})\n"
    if version == 6:
        body = (
            rule + f'if bar_index==0\n    strategy.entry("A",strategy.{direction},qty=5)\n'
            f'if bar_index==1\n    strategy.entry("B",strategy.{other},qty=1,limit=500)\n'
        )
    else:
        body = (
            rule + f'strategy.entry("A",strategy.{direction},qty=5,when=time=={OPENED})\n'
            f'strategy.entry("B",strategy.{other},qty=1,limit=500,when=time=={OPENED + 60000})\n'
        )
    case, rows = prepare(body, ROWS, version=version)
    result, tape = compare_modes(monkeypatch, tmp_path, case, rows)
    assert not result.open_trades
    assert [(t.entry_id, t.exit_id, t.qty) for t in result.closed_trades] == [("A", "B", 5)]
    assert all(e["risk_unit"] == direction for e in tape if e["kind"] == "risk")


@pytest.mark.parametrize("kind", ["market", "limit", "stop", "stop_limit"])
def test_deferred_orders_are_limited_again_at_fill(monkeypatch, tmp_path, kind):
    args = {
        "market": "",
        "limit": ",limit=100",
        "stop": ",stop=100",
        "stop_limit": ",stop=100,limit=100",
    }[kind]
    case, rows = prepare(
        "strategy.risk.max_position_size(5)\nif bar_index==0\n"
        f'    strategy.entry("A",strategy.long,qty=3{args})\n'
        f'    strategy.entry("B",strategy.long,qty=4{args})\n',
        ROWS,
        pyramiding=2,
    )
    result, _ = compare_modes(monkeypatch, tmp_path, case, rows)
    assert [(t.entry_id, t.qty) for t in result.open_trades] == [("A", 3), ("B", 2)]


@pytest.mark.parametrize("direction", ["long", "short"])
def test_order_is_not_limited_or_direction_blocked(monkeypatch, tmp_path, direction):
    other = "short" if direction == "long" else "long"
    case, rows = prepare(
        f"strategy.risk.allow_entry_in(strategy.direction.{other})\n"
        "strategy.risk.max_position_size(0)\nif bar_index==0\n"
        f'    strategy.entry("blocked",strategy.{direction},qty=1)\n'
        f'    strategy.order("free",strategy.{direction},qty=7)\n',
        ROWS,
    )
    result, _ = compare_modes(monkeypatch, tmp_path, case, rows)
    assert [(t.entry_id, t.qty) for t in result.open_trades] == [("free", 7)]


@pytest.mark.parametrize("direction", ["long", "short"])
def test_reversal_cap_counts_resulting_position_not_transaction(monkeypatch, tmp_path, direction):
    other = "short" if direction == "long" else "long"
    case, rows = prepare(
        "strategy.risk.max_position_size(3)\nif bar_index==0\n"
        f'    strategy.entry("A",strategy.{direction},qty=2)\n'
        f'if bar_index==1\n    strategy.entry("B",strategy.{other},qty=10)\n',
        ROWS,
    )
    result, _ = compare_modes(monkeypatch, tmp_path, case, rows)
    assert result.closed_trades[0].qty == 2
    assert [(t.direction, t.qty) for t in result.open_trades] == [(other, 3)]


def test_applied_input_changes_cap_without_cross_run_leak(monkeypatch):
    case, rows = prepare(
        "n=input.int(3)\nstrategy.risk.max_position_size(n)\n"
        'if bar_index==0\n    strategy.entry("A",strategy.long,qty=9)\n',
        ROWS,
    )
    expected = [2, 0, 4, 2]
    for limit in expected:
        result = execute_bulk(monkeypatch, case, bars=rows, params={"n": limit})
        assert sum(t["qty"] for t in result["raw_result"]["open_trades"]) == limit
    assert case[2].max_position_size is None


@pytest.mark.parametrize(
    "body",
    [
        "strategy.risk.max_position_size(na)",
        "strategy.risk.max_position_size(true)",
        "strategy.risk.max_position_size(2,when=true)",
        "strategy.risk.allow_entry_in(strategy.direction.long,when=true)",
        "if false\n    strategy.risk.max_position_size(2)",
        "strategy.risk.allow_entry_in(strategy.direction.long)\nstrategy.risk.allow_entry_in(strategy.direction.short)",
        "strategy.risk.max_drawdown(5,strategy.percent_of_equity)",
        "strategy.risk.max_intraday_loss(5,strategy.percent_of_equity)",
    ],
)
def test_unsupported_or_invalid_static_risk_fails_closed(body):
    from openpine.compile.native_rc6 import NativeRC6CompilerAdapter

    r = NativeRC6CompilerAdapter().compile(
        '//@version=6\nstrategy("bad")\n' + body + "\n",
        producer_commits={"pine2ast": "a" * 40, "ast2python": "b" * 40},
    )
    assert not r.success


@pytest.mark.parametrize("on_close", [False, True])
@pytest.mark.parametrize("mode", ["interactive", "bulk_backtest"])
def test_real_worker_entry_risk_limits_and_closes(tmp_path, on_close, mode):
    from openpine.runtime.isolated_run import run_isolated_artifact
    from openpine.runtime.rc6_worker_runtime import _engine_bar
    from rc6_tests.test_rc6_worker_admission import _manifest

    body = (
        "strategy.risk.max_position_size(3)\nstrategy.risk.allow_entry_in(strategy.direction.long)\n"
        "varip int sent=-1\nif bar_index!=sent\n    sent:=bar_index\n"
        '    if bar_index==0\n        strategy.entry("A",strategy.long,qty=10)\n'
        '    if bar_index==2\n        strategy.entry("B",strategy.short,qty=1,limit=500)\n'
    )
    case, rows = prepare(body, ROWS, process_orders_on_close=on_close, calc_on_order_fills=True)
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
    result = run_isolated_artifact(
        compiled.python_code.encode(), bars=[_engine_bar(b) for b in rows], config=config, params={}
    )
    assert result["ok"] and result["bars_processed"] == len(rows)
    broker = result["raw_result"]
    assert not broker.errors and not broker.open_trades
    assert [(t.entry_id, t.exit_id, t.qty) for t in broker.closed_trades] == [("A", "B", 3)]
    risks = [e for e in result["intent_tape"] if e["kind"] == "risk"]
    assert {e["risk_rule"] for e in risks} == {"max_position_size", "allow_entry_in"}
