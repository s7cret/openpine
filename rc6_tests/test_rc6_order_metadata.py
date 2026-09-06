"""Compiled Pine -> intent -> existing broker -> captured trade and fill events."""

from dataclasses import asdict
from types import SimpleNamespace
from collections.abc import Mapping
import pytest
from rc6_tests.test_rc6_deferred_exits import prepare
from rc6_tests.test_rc6_bulk_execution import execute_bulk
from rc6_tests.test_rc6_lifecycle import interactive
from rc6_tests.test_rc6_marketdata_boundary import OPENED

ROWS = [(100, 101, 99, 100), (100, 110, 94, 104), (104, 105, 103, 104)]


def event_context(value):
    """Both public transports retain values; bulk hydration is attribute-style."""
    if isinstance(value, Mapping):
        return dict(value)
    assert isinstance(value, SimpleNamespace), type(value)
    return vars(value)


def build(leg="profit", scope="named", text="override", disabled=False, **opts):
    arg = {
        "profit": "profit=500",
        "loss": "loss=500",
        "trailing": "trail_points=500,trail_offset=200",
    }[leg]
    named = '"A",' if scope == "named" else ""
    return prepare(
        "varip bool sent=false\nif bar_index==0 and not sent\n    sent:=true\n"
        '    strategy.entry("A",strategy.long,qty=2,comment="entry",alert_message="entry-alert")\n'
        f'    strategy.exit("X:public",{named}{arg},comment="fallback",alert_message="fallback-alert",'
        f'comment_{leg}="{text}",alert_{leg}="{text}",disable_alert={str(disabled).lower()})\n',
        ROWS,
        collect_events=True,
        **opts,
    )


def compare(monkeypatch, tmp_path, case, rows):
    bulk = execute_bulk(monkeypatch, case, bars=rows)
    result, tape, *_ = interactive(case, rows, tmp_path)
    assert result.status == "completed", result.errors
    assert tape == bulk["intent_tape"]
    assert [asdict(t) for t in result.closed_trades] == bulk["raw_result"]["closed_trades"]
    assert [asdict(e) for e in result.events] == bulk["raw_result"]["events"]
    return result, tape


@pytest.mark.parametrize("leg", ["profit", "loss", "trailing"])
@pytest.mark.parametrize("scope", ["named", "all"])
@pytest.mark.parametrize("text", ["override", ""])
def test_per_leg_text_reaches_exact_fill_in_both_modes(monkeypatch, tmp_path, leg, scope, text):
    case, rows = build(leg, scope, text)
    result, tape = compare(monkeypatch, tmp_path, case, rows)
    assert len(result.closed_trades) == 1
    assert result.closed_trades[0].entry_comment == "entry"
    assert result.closed_trades[0].exit_comment == text
    fills = [e.context for e in result.events if e.code == "ORDER_FILLED"]
    assert fills[-1]["alert_message"] == text and fills[-1]["exit_leg"] == leg
    assert fills[-1]["public_order_id"] == "X:public"
    assert tape[-1]["schema_version"] == "2.6.0"


@pytest.mark.parametrize("disabled", [False, True])
@pytest.mark.parametrize("command", ["entry", "order", "close", "close_all"])
def test_generic_alert_flags_are_not_lost_in_replay(monkeypatch, tmp_path, disabled, command):
    flag = str(disabled).lower()
    body = f'if bar_index==0\n    strategy.{command if command in {"entry", "order"} else "entry"}("A",strategy.long,qty=1,alert_message="ENTRY",disable_alert={flag})\n'
    if command in {"close", "close_all"}:
        target = '"A",' if command == "close" else ""
        body += f'if bar_index==1\n    strategy.{command}({target}immediately=true,comment="CLOSE",alert_message="",disable_alert={flag})\n'
    case, rows = prepare(body, ROWS, collect_events=True)
    result, _ = compare(monkeypatch, tmp_path, case, rows)
    fills = [e.context for e in result.events if e.code == "ORDER_FILLED"]
    assert len(fills) == (1 if command in {"entry", "order"} else 2)
    assert fills[-1]["alert_eligible"] is (not disabled)
    assert fills[-1]["alert_message"] == ("ENTRY" if command in {"entry", "order"} else "")


def test_pine_v6_positional_comment_is_not_misbound_as_oca_type(monkeypatch, tmp_path):
    # Parameter 13 is comment, not the non-existent Pine exit oca_type argument.
    body = 'if bar_index==0\n    strategy.entry("A",strategy.long,qty=1)\n'
    body += '    strategy.exit("X","A",na,na,500,na,na,na,na,na,na,"group","POSITIONAL")\n'
    case, rows = prepare(body, ROWS, collect_events=True)
    result, tape = compare(monkeypatch, tmp_path, case, rows)
    assert result.closed_trades[0].exit_comment == "POSITIONAL"
    assert tape[-1]["comment"] == "POSITIONAL"


def test_na_leg_metadata_falls_back_without_turning_into_text(monkeypatch, tmp_path):
    case, rows = prepare(
        'if bar_index==0\n    strategy.entry("A",strategy.long,qty=1)\n'
        '    strategy.exit("X","A",profit=500,comment="general",comment_profit=na,alert_message="general-alert",alert_profit=na)\n',
        ROWS,
        collect_events=True,
    )
    result, tape = compare(monkeypatch, tmp_path, case, rows)
    assert result.closed_trades[0].exit_comment == "general"
    assert tape[-1]["schema_version"] == "2.2.0"
    assert [e.context for e in result.events if e.code == "ORDER_FILLED"][-1][
        "alert_message"
    ] == "general-alert"


@pytest.mark.parametrize("mode", ["interactive", "bulk_backtest"])
@pytest.mark.parametrize("disabled", [False, True])
def test_real_worker_order_metadata(tmp_path, mode, disabled):
    from openpine.runtime.isolated_run import run_isolated_artifact
    from openpine.runtime.rc6_worker_runtime import _engine_bar
    from rc6_tests.test_rc6_worker_admission import _manifest

    case, rows = build("trailing", disabled=disabled, calc_on_order_fills=True)
    artifact, ctx, cfg = case
    for k, v in dict(
        execution_context=ctx,
        admitted_manifest=_manifest(),
        instrument_id=ctx["instrument_id"],
        generated_artifact=artifact.generated_artifact,
        bar_envelopes=rows,
        run_hash="sha256:" + "1" * 64,
        protocol_artifact_dir=str(tmp_path / "protocol"),
        isolated_protocol=mode,
    ).items():
        setattr(cfg, k, v)
    result = run_isolated_artifact(
        artifact.python_code.encode(), bars=[_engine_bar(b) for b in rows], config=cfg, params={}
    )
    assert result["ok"]
    raw = result["raw_result"]
    if isinstance(raw, dict):
        assert raw["closed_trades"][0]["exit_comment"] == "override"
        fill = [e["context"] for e in raw["events"] if e["code"] == "ORDER_FILLED"][-1]
    else:
        assert raw.closed_trades[0].exit_comment == "override"
        fill = [e.context for e in raw.events if e.code == "ORDER_FILLED"][-1]
    fill = event_context(fill)
    assert fill["alert_message"] == "override" and fill["alert_eligible"] is (not disabled)


@pytest.mark.parametrize("version", [5, 6])
def test_named_metadata_with_explicit_na_inactive_prices(monkeypatch, tmp_path, version):
    condition = "bar_index==0" if version == 6 else f"time=={OPENED}"
    body = (
        f'if {condition}\n    strategy.entry("A",strategy.long,qty=1)\n'
        '    strategy.exit("X","A",profit=500,stop=na,trail_price=na,trail_points=na,trail_offset=na,comment_profit="TP",alert_profit="done")\n'
    )
    case, rows = prepare(body, ROWS, collect_events=True, version=version)
    result, tape = compare(monkeypatch, tmp_path, case, rows)
    assert result.closed_trades[0].exit_comment == "TP"
    assert tape[-1]["schema_version"] == "2.6.0"


def test_required_order_events_retains_metadata_without_collect_flag(monkeypatch, tmp_path):
    case, rows = build()
    case[2].collect_events = False
    case[2].required_outputs = {"order_events"}
    result, _ = compare(monkeypatch, tmp_path, case, rows)
    assert "order_events" in result.available_outputs
    assert [e.context for e in result.events if e.code == "ORDER_FILLED"][-1][
        "alert_message"
    ] == "override"
