"""Entry names remain literal even when all-entry exit scope is available."""

import pytest

from rc6_tests.test_rc6_deferred_exits import compare_modes, prepare


@pytest.mark.parametrize("name", ["*", "A:B", "all_entries"])
def test_literal_entry_name_remains_explicit(monkeypatch, tmp_path, name):
    case, rows = prepare(
        f'if bar_index==0\n    strategy.entry("{name}",strategy.long,qty=1)\n'
        f'    strategy.entry("other",strategy.long,qty=2)\n    strategy.exit("X","{name}",profit=500)\n',
        [(100, 101, 99, 100), (100, 106, 99, 100)],
        pyramiding=2,
    )
    result, tape = compare_modes(monkeypatch, tmp_path, case, rows)
    assert tape[-1]["from_entry"] == name and tape[-1]["schema_version"] == "2.2.0"
    assert [t.entry_id for t in result.closed_trades] == [name]
    assert [t.entry_id for t in result.open_trades] == ["other"]
