"""Explicit kernels; execution supplies each call's private state context."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable

from packages.contracts import IndicatorDeclaration


@dataclass(frozen=True)
class PlotEvent:
    value: int | float | None
    title: str


@dataclass(frozen=True)
class Kernel:
    parameters: tuple[str, ...]
    effects: tuple[str, ...]
    function: Callable[[tuple, object], object]


def absolute(arguments, context):
    return None if arguments[0] is None else abs(arguments[0])


def indicator(arguments, context):
    return IndicatorDeclaration(*arguments)


def plot(arguments, context):
    return PlotEvent(*arguments)


def ema(arguments, context):
    return context.ema().update(*arguments)


KERNELS = MappingProxyType({
    "numeric.abs": Kernel(("number",), (), absolute),
    "harness.indicator": Kernel(("title", "shorttitle", "overlay"), ("declaration",), indicator),
    "visual.plot": Kernel(("series", "title"), ("emits_plot",), plot),
    "series.ema": Kernel(("source", "length"), ("reads_history", "updates_state"), ema),
})
