"""Explicit kernels; execution supplies each call's private state context."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable

from packages.contracts import IndicatorDeclaration
from packages.runtime.operations.numeric import cast_float, cast_int, maximum, minimum, missing, replace_missing, rounded
from packages.runtime.series.basic import ChangeState, CrossState, SMAState


@dataclass(frozen=True)
class PlotEvent:
    value: int | float | None
    title: str


@dataclass(frozen=True)
class Kernel:
    parameters: tuple[str, ...]
    effects: tuple[str, ...]
    function: Callable[[tuple, object], object]
    aliases: tuple[tuple[str, ...], ...] = ()


def absolute(arguments, context):
    return None if arguments[0] is None else abs(arguments[0])


def indicator(arguments, context):
    return IndicatorDeclaration(*arguments)


def plot(arguments, context):
    return PlotEvent(*arguments)


def ema(arguments, context):
    return context.ema().update(*arguments)


def legacy_ema(arguments, context):
    return context.ema(sma_seed=True).update(*arguments)


def sma(arguments, context):
    return context.state("sma", lambda: SMAState(context.budget)).update(*arguments)


def change(arguments, context):
    return context.state("change", lambda: ChangeState(context.budget)).update(*arguments, context.language_version)


def crossing(direction):
    def update(arguments, context):
        return context.state("cross", CrossState).update(*arguments, direction)
    return update


KERNELS = MappingProxyType({
    "numeric.abs": Kernel(("number",), (), absolute, (("x",),)),
    "harness.indicator": Kernel(("title", "shorttitle", "overlay"), ("declaration",), indicator),
    "visual.plot": Kernel(("series", "title"), ("emits_plot",), plot),
    "series.ema": Kernel(("source", "length"), ("reads_history", "updates_state"), ema),
    "series.ema.sma_seed": Kernel(("source", "length"), ("reads_history", "updates_state"), legacy_ema, (("x", "y"),)),
    "numeric.na": Kernel(("x",), (), missing),
    "numeric.nz": Kernel(("source", "replacement"), (), replace_missing, (("x", "y"),)),
    "numeric.int": Kernel(("x",), (), cast_int),
    "numeric.float": Kernel(("x",), (), cast_float),
    "numeric.round": Kernel(("number",), (), rounded, (("x",), ("number", "precision"))),
    "numeric.min": Kernel(("number0", "number1"), (), minimum,
        tuple(tuple(f"number{i}" for i in range(n)) for n in range(3, 33))),
    "numeric.max": Kernel(("number0", "number1"), (), maximum,
        tuple(tuple(f"number{i}" for i in range(n)) for n in range(3, 33))),
    "series.sma": Kernel(("source", "length"), ("reads_history", "updates_state"), sma, (("x", "y"),)),
    "series.change": Kernel(("source", "length"), ("reads_history", "updates_state"), change, (("x", "y"),)),
    "series.cross": Kernel(("source1", "source2"), ("reads_history", "updates_state"), crossing("both"), (("x", "y"),)),
    "series.crossover": Kernel(("source1", "source2"), ("reads_history", "updates_state"), crossing("over"), (("x", "y"),)),
    "series.crossunder": Kernel(("source1", "source2"), ("reads_history", "updates_state"), crossing("under"), (("x", "y"),)),
})
