"""The three explicit operation kernels of the first execution slice."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable

from packages.contracts import IndicatorDeclaration


@dataclass(frozen=True)
class PlotEvent:
    value: int | float
    title: str


@dataclass(frozen=True)
class Kernel:
    parameters: tuple[str, ...]
    effects: tuple[str, ...]
    function: Callable[[tuple], object]


def absolute(arguments):
    return abs(arguments[0])


def indicator(arguments):
    return IndicatorDeclaration(*arguments)


def plot(arguments):
    return PlotEvent(*arguments)


KERNELS = MappingProxyType({
    "numeric.abs": Kernel(("number",), (), absolute),
    "harness.indicator": Kernel(("title", "shorttitle", "overlay"), ("declaration",), indicator),
    "visual.plot": Kernel(("series", "title"), ("emits_plot",), plot),
})
