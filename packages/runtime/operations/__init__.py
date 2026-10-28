"""Explicit kernels; execution supplies each call's private state context."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable

from packages.contracts import IndicatorDeclaration
from packages.runtime.operations.numeric import cast_float, cast_int, maximum, minimum, missing, replace_missing, rounded, absolute, numeric_kernel
from packages.runtime.series.basic import ChangeState, CrossState, SMAState
from packages.runtime.series.technical import ATRState, ExtremumState, RMAState, RSIState, true_range
from packages.runtime.series.composite import MomentsState,MACDState,BBState


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


def technical(kind, factory):
    def update(arguments, context):
        return context.state(kind, factory).update(*arguments)
    return update


def extrema(highest):
    def update(arguments, context):
        return context.state("highest" if highest else "lowest",
            lambda: ExtremumState(context.budget, highest)).update(*arguments)
    return update


def tr(arguments, context):
    return true_range(*arguments)


def moments(root=False,population=False):
    def update(arguments,context):
        source,length=arguments[:2];biased=True if population else arguments[2]
        state=context.state('moments',lambda:MomentsState(context.budget))
        state.update(source,length)
        return state.variance(biased,root=root)
    return update


def macd(arguments,context):
    return context.state('macd',lambda:MACDState(context.budget,context.language_version)).update(*arguments)


def bb(arguments,context):
    return context.state('bb',lambda:BBState(context.budget)).update(*arguments)


from packages.runtime.environment import calendar_kernel,timestamp
from packages.contracts.environment import CALENDAR

from packages.runtime.series.signals import kernel as signal_kernel
from packages.contracts.signals import signal_apis
_SIGNAL_KERNELS={}
for _a in signal_apis(6):
    _names=tuple(_a["implicit"])+tuple(p["name"] for p in _a["parameters"])
    _op=_a["operation"]
    if _op in _SIGNAL_KERNELS:
        _old=_SIGNAL_KERNELS[_op];_SIGNAL_KERNELS[_op]=Kernel(_old.parameters,_old.effects,_old.function,(*_old.aliases,_names))
    else:_SIGNAL_KERNELS[_op]=Kernel(_names,tuple(_a["effects"]),signal_kernel(_op.split(".")[1]))

from packages.contracts.scalar_functions import apis as scalar_apis,ScalarError
from packages.contracts.scalar_registry import invoke as scalar_invoke
from packages.runtime.series import KernelError

def scalar_kernel(operation):
    def run(arguments,context):
        try:return scalar_invoke(operation,arguments,context.scalar_budget,context.language_version)
        except ScalarError as error:raise KernelError("SCALAR_LIMIT",str(error),"rule.scalar") from error
    return run
_SCALAR_KERNELS={}
for _a in scalar_apis(6):
    _op=_a["operation"];_names=tuple(p["name"] for p in _a["parameters"])
    _aliases=tuple(tuple(p["name"] for p in b["parameters"]) for v in range(1,6) for b in scalar_apis(v) if b["operation"]==_op)
    _SCALAR_KERNELS[_op]=Kernel(_names,(),scalar_kernel(_op),_aliases)

from packages.contracts.numeric_functions import apis as numeric_apis
_NEW_NUMERIC_KERNELS={}
for _a in numeric_apis(6):
    _op=_a["operation"];_names=tuple(_a["parameters"])
    _aliases=tuple(tuple(b["parameters"]) for v in range(1,6) for b in numeric_apis(v) if b["operation"]==_op)
    _NEW_NUMERIC_KERNELS[_op]=Kernel(_names,(),numeric_kernel(_op),_aliases)

from packages.runtime.series.dynamic_windows import kernel as window_kernel
from packages.contracts.dynamic_windows import OPERATIONS as WINDOW_OPERATIONS
_DYNAMIC_WINDOW_KERNELS={}
for _op in WINDOW_OPERATIONS:
    _old=({'window.sum':'signals.sum','window.wma':'signals.wma'}.get(_op) or 'series.'+_op.split('.',1)[1])
    if _old in _SIGNAL_KERNELS:
        _k=_SIGNAL_KERNELS[_old];_names=_k.parameters;_aliases=_k.aliases
    elif _op in ('window.variance','window.stdev'):_names=('source','length','biased');_aliases=()
    elif _op.endswith('.population'):_names=('source','length');_aliases=(('x','y'),)
    else:_names=('source','length');_aliases=(('length',),)
    _DYNAMIC_WINDOW_KERNELS[_op]=Kernel(_names,('reads_history','updates_state'),window_kernel(_op),_aliases)

from packages.runtime.series.dynamic_signals import kernel as signal_window_kernel
from packages.contracts.dynamic_signals import NAMES as SIGNAL_WINDOW_NAMES
KERNELS = MappingProxyType({
    **{'swindow.'+n:Kernel(_SIGNAL_KERNELS['signals.'+n].parameters,('reads_history','updates_state'),signal_window_kernel('swindow.'+n),_SIGNAL_KERNELS['signals.'+n].aliases) for n in SIGNAL_WINDOW_NAMES},
    **_DYNAMIC_WINDOW_KERNELS,
    **_NEW_NUMERIC_KERNELS,
    **_SCALAR_KERNELS,
    **_SIGNAL_KERNELS,
    **{'environment.'+name:Kernel(('time',),(),calendar_kernel(name)) for name in CALENDAR},
    'environment.timestamp5':Kernel(('year','month','day','hour','minute'),(),timestamp),
    'environment.timestamp6':Kernel(('year','month','day','hour','minute','second'),(),timestamp),
    'series.variance':Kernel(('source','length','biased'),('reads_history','updates_state'),moments()),
    'series.stdev':Kernel(('source','length','biased'),('reads_history','updates_state'),moments(root=True)),
    'series.variance.population':Kernel(('source','length'),('reads_history','updates_state'),moments(population=True),(('x','y'),)),
    'series.stdev.population':Kernel(('source','length'),('reads_history','updates_state'),moments(root=True,population=True),(('x','y'),)),
    'series.macd':Kernel(('source','fastlen','slowlen','siglen'),('reads_history','updates_state'),macd),
    'series.bb':Kernel(('series','length','mult'),('reads_history','updates_state'),bb),
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
    "series.rma": Kernel(("source", "length"), ("reads_history", "updates_state"), technical("rma", RMAState), (("x", "y"),)),
    "series.tr": Kernel(("high", "low", "previous_close", "handle_na"), ("reads_history",), tr, (("high", "low", "previous_close", "false"),)),
    "series.atr": Kernel(("high", "low", "previous_close", "length"), ("reads_history", "updates_state"), technical("atr", ATRState)),
    "series.rsi": Kernel(("source", "length"), ("reads_history", "updates_state"), technical("rsi", RSIState), (("x", "y"),)),
    "series.highest": Kernel(("source", "length"), ("reads_history", "updates_state"), extrema(True), (("high", "length"),)),
    "series.lowest": Kernel(("source", "length"), ("reads_history", "updates_state"), extrema(False), (("low", "length"),)),
})
