"""Helpers for Pine strategy declaration arguments used by runtime adapters."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping


def strategy_declaration_defaults() -> dict[str, Any]:
    """Return Pine/Pinelib defaults for strategy() declaration arguments."""

    from pinelib.strategy.models import StrategyDeclaration

    return asdict(StrategyDeclaration())


def normalize_strategy_declaration_args(
    decl_args: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge artifact declaration args over Pine strategy() defaults.

    AST2Python/Pinelib generated strategies validate the runtime config against
    the effective strategy declaration. When Pine source omits optional
    strategy() arguments, generated code uses Pinelib's StrategyDeclaration
    defaults, not OpenPine gateway fallback constants. Keep every caller on the
    same defaults so omitted fields such as initial_capital and pyramiding do
    not create generated declaration/config mismatches at runtime.
    """

    merged = strategy_declaration_defaults()
    if decl_args:
        merged.update(dict(decl_args))
    return merged


def artifact_strategy_declaration_args(artifact: Mapping[str, Any] | None) -> dict[str, Any]:
    """Extract and normalize strategy declaration args from an artifact dict."""

    compile_meta = (artifact or {}).get("compile_meta", {})
    declaration = compile_meta.get("translation_metadata", {}).get("declaration", {})
    return normalize_strategy_declaration_args(declaration.get("arguments", {}))
