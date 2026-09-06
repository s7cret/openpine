"""Keep actual broker output availability through serialization and hydration.

An empty requested output is available; uncollected data is not. The parent must
not guess collection status from required_outputs or a synthesized empty list.
"""

import pytest

from backtest_engine import BacktestEngine
from openpine.runtime.bulk_worker import hydrate_bulk_raw_result
from rc6_tests.test_rc6_bulk_execution import execute_bulk
from rc6_tests.test_rc6_deferred_exits import prepare


@pytest.mark.parametrize("requested", [False, True])
@pytest.mark.parametrize("has_orders", [False, True])
def test_bulk_roundtrip_keeps_actual_output_availability(monkeypatch, requested, has_orders):
    body = ('if bar_index==0\n    strategy.entry("A",strategy.long,qty=1,alert_message="filled")\n'
            if has_orders else 'x=close\n')
    case, rows = prepare(body, [(100,101,99,100),(100,101,99,100)],
                         collect_events=False, required_outputs={"order_events"} if requested else set())
    observed = []
    original = BacktestEngine.run

    def capture(*args, **kwargs):
        result = original(*args, **kwargs)
        observed.append(result)
        return result

    monkeypatch.setattr(BacktestEngine, "run", capture)
    payload = execute_bulk(monkeypatch, case, bars=rows)
    assert len(observed) == 1
    expected = observed[0].available_outputs
    raw = payload["raw_result"]
    hydrated = hydrate_bulk_raw_result(payload)
    assert raw["available_outputs"] == hydrated.available_outputs == sorted(expected)
    assert ("order_events" in expected) is requested
    if not requested:
        assert observed[0].events is None and not raw["events"]
    else:
        fills = [event for event in hydrated.events if event.code == "ORDER_FILLED"]
        assert len(fills) == int(has_orders)
        if fills:
            assert fills[0].context.alert_message == "filled"
