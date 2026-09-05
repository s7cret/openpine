"""Provider timeframe and admitted point value survive both execution transports."""

from dataclasses import replace

import pytest

from openpine_contracts import seal_content_hash
from openpine.runtime.rc6_worker_runtime import _pine_timeframe, _session_from_request
from openpine.runtime.rc6_config import serialize_engine_config
from rc6_tests.test_rc6_bulk_execution import bulk_case as _base, execute_bulk
from rc6_tests.test_rc6_lifecycle import compile_case, interactive
from rc6_tests.test_rc6_marketdata_boundary import OPENED, bar


@pytest.mark.parametrize(
    "source,period",
    [("1h", "60"), ("4h", "240"), ("1m", "1"), ("1M", "1M"), ("1D", "1D"), ("5s", "5S")],
)
def test_chart_timeframe_translation(source, period):
    assert _pine_timeframe(source) == period


def metadata_case(timeframe="1h", pointvalue="50"):
    result = compile_case(
        _base.__wrapped__(),
        """//@version=6
strategy("metadata")
if bar_index == 0 and timeframe.period == "60" and syminfo.pointvalue == 50
    strategy.order("metadata-ok", strategy.long, qty=1)
""",
    )
    compiled, context, config = result
    context = seal_content_hash(
        {
            **context,
            "timeframe": timeframe,
            "series_id": "binance:spot:SOLUSDT:" + timeframe,
            "pointvalue": pointvalue,
        },
        schema_id="openpine.execution_context.v1",
    )
    config = replace(config, timeframe=timeframe, end_time=OPENED + 2 * 3600000)
    return compiled, context, config


def test_pointvalue_in_session_comes_from_admitted_context():
    compiled, context, config = metadata_case()
    session = _session_from_request(
        {
            "generated_artifact": compiled.generated_artifact,
            "execution_context": context,
            "source": compiled.python_code,
            "engine_config": serialize_engine_config(config, "strict_5x"),
        }
    )
    assert session.session.instrument.pointvalue == 50
    assert session.session.timeframe.period == "60"


def test_actual_hourly_pine_uses_admitted_point_value_in_both_modes(monkeypatch, tmp_path):
    case = metadata_case()
    candles = [bar(timeframe="1h", open_time_utc_ms=OPENED + i * 3600000) for i in range(3)]
    bulk = execute_bulk(monkeypatch, case, bars=candles)
    result, tape, *_ = interactive(case, candles, tmp_path)
    assert bulk["intent_tape"] == tape
    assert [event["command_id"] for event in tape] == ["metadata-ok"]
    assert result.status == "completed"
    assert len(result.open_trades) == 1 and result.open_trades[0].qty == 1


@pytest.mark.parametrize("mode", ["interactive", "bulk_backtest"])
def test_real_worker_hourly_metadata(tmp_path, mode):
    from openpine.runtime.isolated_run import run_isolated_artifact
    from openpine.runtime.rc6_worker_runtime import _engine_bar
    from rc6_tests.test_rc6_worker_admission import _manifest

    compiled, context, config = metadata_case()
    candles = [bar(timeframe="1h", open_time_utc_ms=OPENED + i * 3600000) for i in range(3)]
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
    assert result["ok"] and result["bars_processed"] == 3
    assert [event["command_id"] for event in result["intent_tape"]] == ["metadata-ok"]
