"""Canonical runtime NA aliases retain the established static host semantics."""

import ast

import pytest

from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
from openpine.runtime.strategy_host import (
    StrategyHostError,
    _canonical_na_aliases,
    _missing_literal,
    validate_strategy_host,
)


def compile_source(body, version=6):
    return NativeRC6CompilerAdapter().compile(
        f'//@version={version}\nstrategy("canonical NA")\n{body}\n',
        producer_commits={"pine2ast": "a" * 40, "ast2python": "b" * 40},
    )


@pytest.mark.parametrize("alias", ["_PineLibNA", "other_missing", "na"])
def test_import_identity_not_spelling_defines_known_na(alias):
    tree = ast.parse(f"from pinelib.core.values import na as {alias}")
    known = _canonical_na_aliases(tree)
    assert known == {alias}
    assert _missing_literal(ast.parse(alias, mode="eval").body, known)
    assert not _missing_literal(ast.parse("unrelated", mode="eval").body, known)


@pytest.mark.parametrize(
    "binding",
    [
        "_PineLibNA=7",
        "del _PineLibNA",
        "def f(_PineLibNA): pass",
        "def _PineLibNA(): pass",
        "class _PineLibNA: pass",
        "from other_module import na as _PineLibNA",
        "import other_module as _PineLibNA",
        "from pinelib.core.values import na as _PineLibNA",
        "from other_module import *",
        "[_PineLibNA for _PineLibNA in []]",
        "try:\n    pass\nexcept Exception as _PineLibNA:\n    pass",
        "match 1:\n    case _PineLibNA:\n        pass",
    ],
)
def test_rebound_or_shadowed_alias_is_not_assumed_to_be_missing(binding):
    tree = ast.parse("from pinelib.core.values import na as _PineLibNA\n" + binding)
    assert not _canonical_na_aliases(tree)


@pytest.mark.parametrize(
    "declaration",
    [
        "from other_module import na as _PineLibNA",
        "from .pinelib.core.values import na as _PineLibNA",
        "_PineLibNA=None",
        "def f():\n    from pinelib.core.values import na as _PineLibNA",
    ],
)
def test_only_unambiguous_absolute_module_import_is_a_literal(declaration):
    assert not _canonical_na_aliases(ast.parse(declaration))


@pytest.mark.parametrize("text", ["False", "0", "''", "some_value", "missing()"])
def test_false_zero_empty_and_dynamic_expressions_remain_active(text):
    assert not _missing_literal(ast.parse(text, mode="eval").body, frozenset())
    assert _missing_literal(ast.parse("None", mode="eval").body, frozenset())


@pytest.mark.parametrize("version", [4, 5, 6])
def test_explicit_na_slots_do_not_create_phantom_trailing_stop(version):
    result = compile_source(
        'strategy.exit("X","L", stop=90, trail_points=na, trail_offset=na)', version
    )
    assert result.success, result.errors
    assert "_PineLibNA" in result.python_code
    validate_strategy_host(result.python_code, version)


def test_renamed_import_stays_missing_but_rebound_alias_is_not_guessed():
    result = compile_source('strategy.exit("X","L", stop=90, trail_points=na, trail_offset=na)')
    assert result.success, result.errors
    renamed = result.python_code.replace("_PineLibNA", "other_missing")
    validate_strategy_host(renamed, 6)
    with pytest.raises(StrategyHostError, match="fixed stop plus trailing"):
        validate_strategy_host(renamed + "\nother_missing=1\n", 6)


@pytest.mark.parametrize(
    "body",
    [
        "strategy.risk.max_position_size(na)",
        'strategy.exit("X", "L", limit=na, stop=na)',
        'strategy.exit("X", "L", trail_points=5, trail_offset=na)',
    ],
)
def test_invalid_missing_values_fail_before_worker_staging(body):
    result = compile_source(body)
    assert not result.success
