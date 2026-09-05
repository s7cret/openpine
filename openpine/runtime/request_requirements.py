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
    requirements = []
    for node in ast.walk(tree):
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
            symbol = context["instrument_id"]
        if period == "":
            period = to_pine_timeframe(context["timeframe"])
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
            )
        )
    return tuple(requirements)


def validate_static_request_sources(
    provider, requirements: tuple[RequestRequirement, ...], context: Mapping
) -> None:
    from pinelib.errors import PineRuntimeError

    chart = TimeframeContext.parse(to_pine_timeframe(context["timeframe"]))
    for requirement in requirements:
        if requirement.timeframe is not None and requirement.kind == "security_lower_tf":
            requested = TimeframeContext.parse(requirement.timeframe)
            if requested.seconds is None or chart.seconds is None:
                raise ValueError(
                    "RC6_REQUEST_DATA: lower timeframe requests require fixed-duration intervals"
                )
            if requested.seconds > chart.seconds:
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
