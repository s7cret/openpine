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


def test_host_independently_rejects_an_external_exit_without_price_legs():
    import ast

    from openpine.runtime.strategy_host import StrategyHostError, validate_strategy_host
    from rc6_tests.test_rc6_strategy_surface import compiled

    artifact = compiled('strategy.exit("X", stop=99)\n')[0]
    tree = ast.parse(artifact.python_code)
    removed = 0
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            continue
        if call.func.attr != "dispatch_delegated":
            continue
        keywords = {item.arg: item.value for item in call.keywords}
        if ast.literal_eval(keywords["capability_id"]) != "strategy.exit":
            continue
        envelope = keywords["arguments"]
        for key, value in zip(envelope.keys, envelope.values, strict=True):
            if ast.literal_eval(key) == "named":
                for index, name in reversed(list(enumerate(value.keys))):
                    if ast.literal_eval(name) == "stop":
                        value.keys.pop(index)
                        value.values.pop(index)
                        removed += 1
    assert removed == 1
    with pytest.raises(StrategyHostError, match="supported active price leg"):
        validate_strategy_host(tree, 6)
