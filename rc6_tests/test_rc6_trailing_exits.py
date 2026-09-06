"""Actual compiled Pine trailing exits, with exact trade comparisons between modes."""

import pytest
from rc6_tests.test_rc6_deferred_exits import prepare, compare_modes
from rc6_tests.test_rc6_marketdata_boundary import OPENED


def trailing_case(version=6, direction="long", **settings):
    absolute = 110 if direction == "long" else 90
    exit_args = f"trail_price={absolute},trail_points=500,trail_offset=200"
    if version < 6:
        body = (
            f'strategy.entry("A",strategy.{direction},qty=2,when=time=={OPENED})\n'
            f'strategy.exit("X","A",{exit_args},when=time=={OPENED})\n'
        )
    else:
        body = (
            "varip bool sent=false\nif bar_index==0 and not sent\n    sent:=true\n"
            f'    strategy.entry("A",strategy.{direction},qty=2)\n'
            f'    strategy.exit("X","A",{exit_args})\n'
        )
    rows = [(100, 101, 99, 100), (100, 108, 99, 104), (104, 113, 103, 107)]
    if direction == "short":
        rows = [(200 - a, 200 - c, 200 - b, 200 - d) for a, b, c, d in rows]
    return prepare(body, rows, version=version, **settings)


@pytest.mark.parametrize("version", range(1, 7))
@pytest.mark.parametrize("direction", ["long", "short"])
def test_versioned_activation_drives_real_trades(monkeypatch, tmp_path, version, direction):
    case, rows = trailing_case(version, direction)
    result, tape = compare_modes(monkeypatch, tmp_path, case, rows)
    target = 106 if version == 6 else 111
    assert [(t.exit_price, t.qty) for t in result.closed_trades] == [
        (target if direction == "long" else 200 - target, 2)
    ]
    assert tape[-1]["schema_version"] == "2.5.0"
    assert tape[-1]["price_pair_policy"] == ("first_trigger" if version == 6 else "absolute_first")
    assert tape[-1]["trail_offset"] == "200"


@pytest.mark.parametrize("kind", ["market", "limit", "stop", "stop_limit"])
def test_trail_binds_after_actual_pending_fill(monkeypatch, tmp_path, kind):
    extra = {
        "market": "",
        "limit": ",limit=99",
        "stop": ",stop=101",
        "stop_limit": ",stop=101,limit=99",
    }[kind]
    case, rows = prepare(
        "if bar_index==0\n" + f'    strategy.entry("A",strategy.long,qty=1{extra})\n'
        '    strategy.exit("X","A",trail_points=200,trail_offset=100)\n',
        [(100, 101, 99, 100), (100, 104, 95, 96), (99, 112, 98, 100)],
    )
    result, tape = compare_modes(monkeypatch, tmp_path, case, rows)
    entry = {"market": 100, "limit": 99, "stop": 101, "stop_limit": 99}[kind]
    assert [(t.entry_price, t.exit_price) for t in result.closed_trades] == [
        (entry, 111 if kind in {"stop_limit", "limit"} else 103)
    ]
    assert tape[-1]["schema_version"] == "2.5.0"


@pytest.mark.parametrize("scope", ["named", "all"])
def test_repeated_id_independent_trails_and_partial_quantities(monkeypatch, tmp_path, scope):
    arg = '"A",' if scope == "named" else ""
    case, rows = prepare(
        'if bar_index==0\n    strategy.entry("A",strategy.long,qty=2)\n'
        'if bar_index==1\n    strategy.entry("A",strategy.long,qty=6)\n'
        f'if bar_index==2\n    strategy.exit("X",{arg}trail_points=1500,trail_offset=200,qty_percent=50)\n',
        [
            (100, 101, 99, 100),
            (100, 101, 99, 100),
            (110, 111, 109, 110),
            (110, 120, 109, 117),
            (117, 130, 116, 125),
        ],
        pyramiding=2,
    )
    result, tape = compare_modes(monkeypatch, tmp_path, case, rows)
    assert [(t.entry_price, t.exit_price, t.qty) for t in result.closed_trades] == [
        (100, 118, 1),
        (110, 128, 3),
    ]
    assert [t.qty for t in result.open_trades] == [1, 3]
    assert ("from_entry" in tape[-1]) == (scope == "named")


@pytest.mark.parametrize("offset", [0, 100])
def test_zero_offset_and_reissue_do_not_reset_or_duplicate(monkeypatch, tmp_path, offset):
    case, rows = prepare(
        'if bar_index==0\n    strategy.entry("A",strategy.long,qty=1)\n'
        f'strategy.exit("X","A",trail_points=500,trail_offset={offset})\n',
        [(100, 101, 99, 100), (100, 110, 99, 109), (109, 109, 105, 107)],
    )
    result, _ = compare_modes(monkeypatch, tmp_path, case, rows)
    assert [t.exit_price for t in result.closed_trades] == [105 if offset == 0 else 109]


@pytest.mark.parametrize(
    "arguments,fragment",
    [
        ("trail_price=105", "production-blocking diagnostics"),
        ("trail_offset=1", "production-blocking diagnostics"),
        ("trail_points=5,trail_offset=2,stop=95", "fixed stop plus trailing"),
        ("trail_price=105,trail_offset=2,loss=1", "fixed stop plus trailing"),
    ],
)
def test_unsupported_or_incomplete_shapes_fail_before_execution(arguments, fragment):
    from openpine.compile.native_rc6 import NativeRC6CompilerAdapter

    result = NativeRC6CompilerAdapter().compile(
        f'//@version=6\nstrategy("negative")\nstrategy.exit("X","A",{arguments})\n',
        producer_commits={"pine2ast": "a" * 40, "ast2python": "b" * 40},
    )
    assert not result.success and fragment in str(result.errors)


@pytest.mark.parametrize("mode", ["interactive", "bulk_backtest"])
@pytest.mark.parametrize("on_close", [False, True])
def test_real_worker_trailing_pair(tmp_path, mode, on_close):
    from openpine.runtime.isolated_run import run_isolated_artifact
    from openpine.runtime.rc6_worker_runtime import _engine_bar
    from rc6_tests.test_rc6_worker_admission import _manifest

    case, rows = trailing_case(process_orders_on_close=on_close, calc_on_order_fills=True)
    artifact, context, config = case
    for name, value in dict(
        execution_context=context,
        admitted_manifest=_manifest(),
        instrument_id=context["instrument_id"],
        generated_artifact=artifact.generated_artifact,
        bar_envelopes=rows,
        run_hash="sha256:" + "1" * 64,
        protocol_artifact_dir=str(tmp_path / "protocol"),
        isolated_protocol=mode,
    ).items():
        setattr(config, name, value)
    result = run_isolated_artifact(
        artifact.python_code.encode(), bars=[_engine_bar(b) for b in rows], config=config, params={}
    )
    assert result["ok"] and result["bars_processed"] == len(rows)
    assert [(t.exit_price, t.qty) for t in result["raw_result"].closed_trades] == [(106, 2)]
    assert result["intent_tape"][-1]["schema_version"] == "2.5.0"
