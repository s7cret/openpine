"""Preflight compiler-emitted strategy delegation without executing Python.

Runs at compilation and again on the hash-bound module before worker startup.
Only literal host identities and the canonical argument envelope are accepted.
This is capability admission, not a replacement for the process sandbox.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
import hashlib
import json
from typing import Any

from backtest_engine.core.strategy_capabilities import (
    DELEGATION_SCHEMA_ID,
    OWNER,
    STRATEGY_COMMANDS,
    STRATEGY_CONSTANTS,
    STRATEGY_STATE_VALUES,
    validate_exit_shape,
)


class StrategyHostError(ValueError):
    code = "RC6_HOST_CAPABILITY"


def strategy_host_surface() -> dict[str, Any]:
    """Derive supported names from the same registry the dispatcher consumes."""
    body = {
        "schema_id": "openpine.strategy_host.v1",
        "owner": OWNER,
        "delegation_schema_id": DELEGATION_SCHEMA_ID,
        "commands": {
            name: {
                "parameters": list(spec.parameters),
                "required": list(spec.required),
                "unsupported_parameters": sorted(spec.unsupported_parameters),
            }
            for name, spec in STRATEGY_COMMANDS.items()
        },
        "constants": sorted(STRATEGY_CONSTANTS),
        "state_values": sorted(STRATEGY_STATE_VALUES),
        "constraints": [
            "historical_tail_arguments_named",
            "entry_risk_global_declarations_only",
            "entry_risk_inputs_fixed_per_run",
            "entry_risk_single_direction_declaration",
            "max_position_size_clips_entry_not_order",
            "exit_explicit_entry_with_price_path_deferral",
            "exit_all_entry_position_lifetime_v1",
            "intent_all_entry_exit_v2_3",
            "exit_relative_prices_per_opening_fill",
            "exit_v6_first_trigger_pairs_intent_v2_4",
            "fill_metadata_captured_per_leg_intent_v2_6",
            "no_external_alert_delivery",
            "trailing_explicit_offset_and_activation",
            "trailing_fixed_stop_combination_unavailable",
            "trailing_versioned_activation_per_fill",
        ],
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return {**body, "content_hash": "sha256:" + hashlib.sha256(encoded).hexdigest()}


def _literal(node: ast.AST | None, label: str) -> Any:
    try:
        return ast.literal_eval(node)
    except (TypeError, ValueError, SyntaxError, RecursionError) as exc:
        raise StrategyHostError(f"{label} must be compiler-emitted literal data") from exc


def _arguments(node: ast.AST | None) -> tuple[list[ast.AST], dict[str, ast.AST]]:
    if not isinstance(node, ast.Dict) or len(node.keys) != 2:
        raise StrategyHostError("strategy argument envelope is malformed")
    keys = [_literal(key, "argument envelope key") for key in node.keys]
    if sorted(keys) != ["named", "positional"]:
        raise StrategyHostError("strategy argument envelope requires positional and named")
    entries = dict(zip(keys, node.values))
    pos, named = entries["positional"], entries["named"]
    if not isinstance(pos, (ast.List, ast.Tuple)) or not isinstance(named, ast.Dict):
        raise StrategyHostError("strategy argument containers must be static")
    names = [_literal(key, "strategy named argument") for key in named.keys]
    if any(type(key) is not str for key in names) or len(set(names)) != len(names):
        raise StrategyHostError("strategy named arguments contain duplicate/invalid keys")
    return pos.elts, dict(zip(names, named.values))


def _location(keywords: Mapping[str, ast.AST], fallback: int) -> str:
    span = keywords.get("source_span")
    if isinstance(span, ast.Call):
        fields = {item.arg: item.value for item in span.keywords}
        line = fields.get("start_line")
        if isinstance(line, ast.Constant) and type(line.value) is int:
            return f"Pine line {line.value}"
    return f"generated line {fallback}"


def _canonical_na_aliases(tree: ast.Module) -> frozenset[str]:
    """Recognize stable compiler imports, not a magic variable spelling.

    Conservatively reject aliases bound elsewhere, including nested shadowing.
    This is static capability analysis, not an evaluator or a sandbox substitute.
    """
    imports: dict[str, ast.alias] = {}
    ambiguous: set[str] = set()
    for statement in tree.body:
        if (
            isinstance(statement, ast.ImportFrom)
            and statement.level == 0
            and statement.module == "pinelib.core.values"
        ):
            for alias in statement.names:
                if alias.name == "na":
                    name = alias.asname or alias.name
                    if name in imports:
                        ambiguous.add(name)
                    imports[name] = alias
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    return frozenset()  # A wildcard can overwrite any name.
                name = alias.asname or (
                    alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name
                )
                if name in imports and alias is not imports[name]:
                    ambiguous.add(name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            ambiguous.add(node.id)
        elif isinstance(node, ast.arg):
            ambiguous.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            ambiguous.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            ambiguous.add(node.name)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            ambiguous.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            ambiguous.add(node.rest)
    return frozenset(imports).difference(ambiguous)


def _missing_literal(node: ast.AST | None, na_aliases: frozenset[str]) -> bool:
    return (isinstance(node, ast.Constant) and node.value is None) or (
        isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in na_aliases
    )


def validate_strategy_host(source: str | bytes | ast.Module, pine_version: int) -> dict[str, Any]:
    if type(pine_version) is not int or not 1 <= pine_version <= 6:
        raise StrategyHostError("exact Pine version 1..6 is required")
    tree = source if isinstance(source, ast.Module) else ast.parse(source)
    na_aliases = _canonical_na_aliases(tree)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    required = set()
    direction_declarations = 0
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            continue
        operation = call.func.attr
        if operation not in {"dispatch_delegated", "resolve_delegated_value"}:
            continue
        keywords = {item.arg: item.value for item in call.keywords}
        owner = _literal(keywords.get("owner"), "delegated owner")
        if owner != OWNER:
            continue  # Other namespaces have their own admission boundaries.
        where = _location(keywords, call.lineno)
        capability = _literal(keywords.get("capability_id"), "strategy capability")
        try:
            if call.args or None in keywords or len(keywords) != len(call.keywords):
                raise StrategyHostError("malformed delegated invocation")
            if _literal(keywords.get("schema_id"), "strategy schema") != DELEGATION_SCHEMA_ID:
                raise StrategyHostError("strategy delegation schema mismatch")
            if type(capability) is not str:
                raise StrategyHostError("strategy capability must be a string")
            required.add(capability)
            if operation == "resolve_delegated_value":
                if capability not in STRATEGY_CONSTANTS | STRATEGY_STATE_VALUES:
                    raise StrategyHostError("no bound state/constant handler")
                continue
            spec = STRATEGY_COMMANDS.get(capability)
            if spec is None:
                raise StrategyHostError("no bound command handler")
            if (
                _literal(keywords.get("symbol_id"), "strategy symbol"),
                _literal(keywords.get("overload_id"), "strategy overload"),
            ) != (spec.symbol_id, spec.overload_id):
                raise StrategyHostError("strategy symbol/overload binding mismatch")
            pos, named = _arguments(keywords.get("arguments"))
            bound = spec.bind(pos, named, pine_version)
            if capability == "strategy.risk.allow_entry_in":
                direction_declarations += 1
                if direction_declarations > 1:
                    raise StrategyHostError(
                        "multiple direction rules are not yet admitted by this host"
                    )
            if capability.startswith("strategy.risk."):
                # Conditional declarations cannot acquire ordinary branch semantics.
                # Until dependency-safe extraction exists, reject them explicitly
                # instead of silently disabling a Pine risk rule with `if false`.
                statement = parents.get(call)
                function = parents.get(statement)
                owner_class = parents.get(function)
                if (
                    not isinstance(statement, ast.Expr)
                    or not isinstance(function, ast.FunctionDef)
                    or function.name != "run"
                    or not isinstance(owner_class, ast.ClassDef)
                    or owner_class.name != "GeneratedScript"
                ):
                    raise StrategyHostError(
                        "risk rules require unconditional global declarations in this host"
                    )
                if capability == "strategy.risk.max_position_size":
                    value = bound["contracts"]
                    if _missing_literal(value, na_aliases) or isinstance(
                        value, (ast.Constant, ast.UnaryOp)
                    ):
                        from backtest_engine.core.risk_rules import validate_position_limit

                        validate_position_limit(
                            None
                            if _missing_literal(value, na_aliases)
                            else _literal(value, "max_position_size")
                        )
            if capability == "strategy.exit":
                active = {
                    name for name, value in bound.items() if not _missing_literal(value, na_aliases)
                }
                validate_exit_shape(active)
                entry = bound.get("from_entry")
                if _missing_literal(entry, na_aliases) or (
                    isinstance(entry, ast.Constant) and type(entry.value) is not str
                ):
                    raise StrategyHostError(
                        "exit from_entry must be a string; omitted or empty means all entries"
                    )
                if not active.intersection(
                    {"profit", "limit", "loss", "stop", "trail_price", "trail_points"}
                ):
                    raise StrategyHostError("exit requires a supported active price leg")
        except (TypeError, ValueError) as exc:
            raise StrategyHostError(f"{capability} at {where}: {exc}") from exc
    return {"surface_hash": strategy_host_surface()["content_hash"], "required": sorted(required)}
