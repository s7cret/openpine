"""Future source changes must not alter past no-lookahead trading decisions."""

import pytest
from rc6_tests.test_rc6_requests import ID, case_for, source_rows
from rc6_tests.test_rc6_bulk_execution import execute_bulk


@pytest.mark.parametrize("expression", ["close", "ta.sma(close,2)", "close[1]"])
def test_mutating_only_future_source_bars_preserves_past_decisions(monkeypatch, expression):
    body = (
        f'x=request.security("{ID}","5",{expression})\n'
        'if not na(x)\n    strategy.order("requested",strategy.long,qty=x)'
    )
    baseline, candles = case_for(body, sources=[source_rows(prices=(10, 20, 30))])
    mutated, _ = case_for(body, sources=[source_rows(prices=(10, 20, 999))])
    a = execute_bulk(monkeypatch, baseline, bars=candles)
    b = execute_bulk(monkeypatch, mutated, bars=candles)

    # Identity hashes intentionally change with the source; compare semantic
    # decision coordinates and parameters, not unrelated content hashes.
    def past(result):
        return [
            (x["bar_index"], x["kind"], x["command_id"], x.get("qty"))
            for x in result["intent_tape"]
            if x["bar_index"] < 14
        ]

    assert past(a) == past(b)
    assert past(a), "The comparison must include actual decisions, not two empty tapes"
