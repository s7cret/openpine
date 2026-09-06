"""Discover literal request contexts in compiler-emitted ABI calls without execution.

Unresolved values stay unresolved. This is early dataset admission, not an
automatic network loader, a Pine parser, or a substitute for runtime validation.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from collections.abc import Mapping

from marketdata_provider.timeframes import to_pine_timeframe
from pinelib.request.snapshots import normalized_period
from pinelib.runtime.metadata import TimeframeContext

_REQUEST_ABI = {"security_v1": "security", "security_lower_tf_v1": "security_lower_tf"}


@dataclass(frozen=True, slots=True)
class RequestRequirement:
    kind: str
    symbol: str | None
    timeframe: str | None
    ignore_invalid_timeframe: bool | None
    generated_line: int
    parent_timeframe: str | None = None
    context_known: bool = False


def compiled_request_requirements(
    tree: ast.Module, context: Mapping
) -> tuple[RequestRequirement, ...]:
    aliases = {
        alias.asname or alias.name: _REQUEST_ABI[alias.name]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "pinelib.abi.compiled_request"
        for alias in node.names
        if alias.name in _REQUEST_ABI
    }
    expression_aliases = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "pinelib.abi.compiled_request"
        for alias in node.names
        if alias.name == "CompiledRequestExpression"
    }
    methods = {
        method.name: method
        for cls in tree.body
        if isinstance(cls, ast.ClassDef) and cls.name == "GeneratedScript"
        for method in cls.body
        if isinstance(method, ast.FunctionDef)
    }

    def expression_method(node):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in expression_aliases
            and len(node.args) >= 3
        ):
            name = node.args[2]
            if isinstance(name, ast.Constant) and type(name.value) is str:
                return name.value
        return None

    child_names = {name for node in ast.walk(tree) if (name := expression_method(node))}
    requirements = []
    visited = set()

    def walk(scope, parent_symbol, parent_period, depth=0):
        if depth > 32:
            raise ValueError("RC6_REQUEST_DATA: generated request dependency depth exceeds limit")
        for node in ast.walk(scope):
            if (
                not isinstance(node, ast.Call)
                or not isinstance(node.func, ast.Name)
                or node.func.id not in aliases
            ):
                continue
            kwargs = {item.arg: item.value for item in node.keywords}

            def text(name):
                value = kwargs.get(name)
                return (
                    value.value
                    if isinstance(value, ast.Constant) and type(value.value) is str
                    else None
                )

            symbol, period = text("symbol"), text("timeframe")
            if symbol == "":
                symbol = parent_symbol
            if period == "":
                period = parent_period
            if period is not None:
                period = normalized_period(period)
            flag = kwargs.get("ignore_invalid_timeframe")
            requirements.append(
                RequestRequirement(
                    aliases[node.func.id],
                    symbol,
                    period,
                    False
                    if flag is None
                    else flag.value
                    if isinstance(flag, ast.Constant) and type(flag.value) is bool
                    else None,
                    node.lineno,
                    parent_period,
                    True,
                )
            )
            method = expression_method(kwargs.get("expression"))
            if method is None:
                continue
            if method not in methods:
                raise ValueError("RC6_REQUEST_DATA: generated request expression method is missing")
            key = (method, symbol, period)
            if key not in visited:
                visited.add(key)
                walk(methods[method], symbol, period, depth + 1)

    # Child methods run only in the enclosing request context. Scanning them as
    # chart-level functions would incorrectly inherit chart metadata and reject
    # legal nested lower-TF calls. Unresolved inherited fields stay unresolved.
    for name, method in methods.items():
        if name not in child_names:
            walk(method, context["instrument_id"], to_pine_timeframe(context["timeframe"]))
    if not methods:
        walk(tree, context["instrument_id"], to_pine_timeframe(context["timeframe"]))
    return tuple(requirements)


def validate_static_request_sources(
    provider, requirements: tuple[RequestRequirement, ...], context: Mapping
) -> None:
    from pinelib.errors import PineRuntimeError

    for requirement in requirements:
        parent = (
            requirement.parent_timeframe
            if requirement.context_known
            else to_pine_timeframe(context["timeframe"])
        )
        chart = None if parent is None else TimeframeContext.parse(parent)
        if requirement.timeframe is not None and requirement.kind == "security_lower_tf":
            requested = TimeframeContext.parse(requirement.timeframe)
            if requested.seconds is None or (chart is not None and chart.seconds is None):
                raise ValueError(
                    "RC6_REQUEST_DATA: lower timeframe requests require fixed-duration intervals"
                )
            if chart is None and requirement.ignore_invalid_timeframe is not False:
                # Whether the interval is invalid and ignored depends on the
                # dynamic enclosing timeframe. Do not demand unused data early.
                continue
            if chart is not None and requested.seconds > chart.seconds:
                if requirement.ignore_invalid_timeframe is not False:
                    # A dynamic ignore flag is a runtime decision, never guessed.
                    continue
                raise ValueError(
                    "RC6_REQUEST_DATA: requested lower timeframe exceeds chart timeframe"
                )
        if requirement.symbol is None or requirement.timeframe is None:
            continue
        try:
            provider.source(requirement.symbol, requirement.timeframe)
        except PineRuntimeError as exc:
            raise ValueError(
                f"RC6_REQUEST_DATA: no admitted dataset for {requirement.symbol!r} "
                f"at {requirement.timeframe!r} (generated line {requirement.generated_line})"
            ) from exc
