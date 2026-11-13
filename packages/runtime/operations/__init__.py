"""Explicit kernels; execution supplies each call's private state context."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable

from packages.contracts import IndicatorDeclaration
from packages.runtime.operations.numeric import cast_float, cast_int, maximum, minimum, missing, replace_missing, rounded, absolute, numeric_kernel
from packages.runtime.series.basic import ChangeState, CrossState, SMAState
from packages.runtime.series.technical import ATRState, ExtremumState, RMAState, RSIState, true_range
from packages.runtime.series.composite import MomentsState,MACDState,BBState
from packages.runtime.series.channel_indicators import kernel as channel_kernel
from packages.runtime.series.trend_indicators import kernel as trend_kernel
from packages.contracts.trend_indicators import OPERATIONS as TRENDS, SAR, MARKET as TREND_MARKET, EFFECTS as TREND_EFFECTS, parameters as trend_parameters
from packages.contracts.channel_indicators import BOLLINGER, KELTNER, EFFECTS as CHANNEL_EFFECTS, MARKET as CHANNEL_MARKET


@dataclass(frozen=True)
class PlotEvent:
    value: int | float | None
    title: str
    presentation: dict | None = None
    result_handle: str | None = None
    output_type: object = None


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

def color_kernel(operation):
    def run(arguments,context):
        from packages.contracts.colors import invoke,validate_presentation,plot_style,plot_line_style
        try:
            if operation=='colors.plot':
                v=context.language_version
                suffix=context.overload_id.partition('.plot')[2]
                params=dict(zip(_PLOT_PARAMETERS[v,suffix],arguments,strict=True))
                value,title,color,width,style=(params[k] for k in ('series','title','color','linewidth','style'))
                if not plot_style(style):
                    raise ValueError('Invalid plot style.')
                if 'transp' in params:
                    t=params['transp']
                    if type(t) is not int or not 0<=t<=100:raise ValueError('Plot transparency requires an integer in 0..100.')
                    if color is not None and color.endswith('FF'):color=invoke('colors.new',(color,t))
                appearance={'color':color,'linewidth':width,'style':style.removeprefix('plot_style.')}
                if 'offset' in params:
                    appearance.update(offset=params['offset'],show_last=params.get('show_last'))
                if 'histbase' in params:
                    line=params.get('linestyle','plot_line_style.solid')
                    if not plot_line_style(line):
                        raise ValueError('Invalid plot line style.')
                    appearance.update(histbase=params['histbase'],join=params['join'],trackprice=params['trackprice'],
                                      linestyle=line.rsplit('.',1)[1])
                if params.get('force_overlay') is True:appearance['force_overlay']=True
                presentation=validate_presentation(appearance,v)
                return PlotEvent(value,title,presentation)
            return invoke(operation,arguments)
        except ValueError as error:raise KernelError('COLOR_RANGE',str(error),'rule.colors') from error
    return run


def ema(arguments, context):
    return context.ema().update(*arguments)


def legacy_ema(arguments, context):
    return context.ema(sma_seed=True).update(*arguments)


def sma(arguments, context):
    return context.state("sma", lambda: SMAState(context.budget,context.history_capacity)).update(*arguments)


def change(arguments, context):
    return context.state("change", lambda: ChangeState(context.budget,context.history_capacity)).update(*arguments, context.language_version)


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
from packages.runtime.series.window_indicators import kernel as indicator_window_kernel
from packages.contracts.window_indicators import EFFECTS as INDICATOR_EFFECTS, NAMES as INDICATOR_NAMES, parameters as indicator_parameters
_INDICATOR_WINDOWS = {
    'indicator_window.' + name: Kernel(
        indicator_parameters(name, 6), INDICATOR_EFFECTS, indicator_window_kernel('indicator_window.' + name),
        tuple(dict.fromkeys(indicator_parameters(name, version) for version in (1, 3))))
    for name in INDICATOR_NAMES
}
from packages.contracts.colors import apis as color_apis
from packages.contracts.visual_display import ABI_SUFFIX as DISPLAY_SUFFIX
_PLOT_PARAMETERS={(v,a['id'].partition('.plot')[2]):tuple(p['name'] for p in a['parameters'])
                  for v in range(1,7) for a in color_apis(v) if a['name']=='plot' and not a['id'].endswith(DISPLAY_SUFFIX)}
_COLOR_KERNELS={}
for _v in range(1,7):
    for _a in color_apis(_v):
        _op=_a['operation'];_names=tuple(p['name'] for p in _a['parameters'])
        if _op in _COLOR_KERNELS:
            _old=_COLOR_KERNELS[_op]
            _COLOR_KERNELS[_op]=Kernel(_old.parameters,_old.effects,_old.function,(*_old.aliases,_names))
        else:_COLOR_KERNELS[_op]=Kernel(_names,tuple(_a['effects']),color_kernel(_op))
KERNELS = MappingProxyType({
    **_COLOR_KERNELS,
    **{op: Kernel((*TREND_MARKET[op], *(p[0] for p in trend_parameters(op, 6))),
                  TREND_EFFECTS, trend_kernel(op), ((*TREND_MARKET[op], 'x', 'y', 'z'),) if op == SAR else ())
       for op in TRENDS},
    **{op: Kernel(('series', 'length', 'mult'), CHANNEL_EFFECTS, channel_kernel(op)) for op in BOLLINGER},
    **{op: Kernel((*CHANNEL_MARKET, 'series', 'length', 'mult', 'useTrueRange'), CHANNEL_EFFECTS,
                  channel_kernel(op), ((*CHANNEL_MARKET, 'series', 'length', 'mult'),)) for op in KELTNER},
    **_INDICATOR_WINDOWS,
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


def band_kernel(operation):
    from packages.contracts.bands import FILL_MASKS
    from packages.contracts import gradient_fill

    plot = color_kernel('colors.plot') if operation=='bands.plot' else None

    def run(arguments,context):
        from packages.contracts.bands import presentation
        from packages.contracts.colors import invoke
        from packages.contracts import ValueType
        try:
            if operation=='bands.nz':return arguments[1] if arguments[0] is None else arguments[0]
            if plot is not None:
                event=plot(arguments,context)
                meta={**event.presentation,'kind':'plot','references':(),'fillgaps':False}
                return PlotEvent(event.value,event.title,meta,'plot|'+context.call_site_id)
            if operation=='bands.hline':
                value,title,color,style,width=arguments
                meta=dict(color=color,linewidth=width,style=style.removeprefix('hline_style.'),kind='hline',references=(),fillgaps=False)
                return PlotEvent(value,title,presentation(meta,context.language_version),'hline|'+context.call_site_id)
            a,b=arguments[:2]
            kind=FILL_MASKS[operation]
            from packages.contracts.bands import handle
            if not handle(a,kind+'_id') or not handle(b,kind+'_id'):raise ValueError('Fill endpoints have different or invalid types.')
            refs=(a.split('|',1)[1],b.split('|',1)[1])
            if operation in gradient_fill.OPERATIONS:
                title,gaps=arguments[-2:]
                sample=dict(zip(gradient_fill.FIELDS,arguments[2:6],strict=True))
                meta=dict(kind='fill',style=gradient_fill.STYLE,references=refs,fillgaps=gaps,mask=kind,gradient=sample)
                gradient_fill.event(meta,operation,context.language_version)
                return PlotEvent(None,title,meta,output_type=ValueType('float','series'))
            color,title,*tail=arguments[2:]
            gaps=tail.pop(0) if context.language_version>=4 else False
            if context.language_version<6:
                t=tail.pop(0)
                if type(t) is not int or not 0<=t<=100:raise ValueError('Fill transparency requires int 0..100.')
                if color is not None and color.endswith('FF'):color=invoke('colors.new',(color,t))
            meta=dict(color=color,linewidth=1,style='fill',kind='fill',references=refs,fillgaps=gaps)
            if tail and tail[0] is not None:
                meta['show_last']=tail[0]
            return PlotEvent(None,title,presentation(meta,context.language_version),output_type=ValueType('float','series'))
        except ValueError as error:raise KernelError('VISUAL_LAYER',str(error),'rule.visual-bands') from error
    return run

from packages.contracts.bands import apis as band_apis
_BAND_KERNELS={}
for _v in range(1,7):
    for _a in band_apis(_v):
        _op=_a['operation'];_names=tuple(p['name'] for p in _a['parameters'])
        if _op in _BAND_KERNELS:
            _k=_BAND_KERNELS[_op]
            if _names!=_k.parameters and _names not in _k.aliases:_BAND_KERNELS[_op]=Kernel(_k.parameters,_k.effects,_k.function,(*_k.aliases,_names))
        else:_BAND_KERNELS[_op]=Kernel(_names,tuple(_a['effects']),band_kernel(_op))
KERNELS=MappingProxyType({**KERNELS,**_BAND_KERNELS})

from packages.contracts.markers import apis as marker_apis
from .markers import kernel as marker_kernel
from packages.contracts.visual_ohlc import apis as ohlc_apis
from .visual_ohlc import kernel as ohlc_kernel
_MARKER_KERNELS={}
for _v in range(1,7):
    for _a in (*marker_apis(_v), *ohlc_apis(_v)):
        _op=_a['operation'];_names=tuple(p['name'] for p in _a['parameters'])
        if _op in _MARKER_KERNELS:
            _k=_MARKER_KERNELS[_op]
            if _names!=_k.parameters and _names not in _k.aliases:_MARKER_KERNELS[_op]=Kernel(_k.parameters,_k.effects,_k.function,(*_k.aliases,_names))
        else:
            _fn = ohlc_kernel(_a['name'], PlotEvent, KernelError) if _op.startswith('ohlc.') else marker_kernel(_op, PlotEvent, KernelError)
            _MARKER_KERNELS[_op] = Kernel(_names, tuple(_a['effects']), _fn)
from packages.contracts.boxes import OPERATIONS as BOX_OPERATIONS
from packages.contracts.box_borders import OPERATIONS as BOX_BORDER_OPERATIONS, POINT_CONSTRUCTOR as STYLED_POINT_BOX
from packages.contracts.box_text import OPERATIONS as BOX_TEXT_OPERATIONS, POINT_CONSTRUCTOR as TEXT_POINT_BOX
from packages.contracts.tables import OPERATIONS as TABLE_OPERATIONS
from packages.contracts.drawing_xloc import OPERATIONS as XLOC_OPERATIONS
from packages.contracts.drawing_xloc_mutations import OPERATIONS as XLOC_ACCESS, effects as xloc_access_effects
from packages.contracts.drawings import PLURALS
from packages.contracts.drawing_objects import COPY_KINDS
from packages.contracts.drawing_properties import OPERATIONS as DRAWING_PROPERTIES, effects as property_effects
from packages.contracts.labels import OPERATIONS as LABEL_OPERATIONS
from packages.contracts.label_styles import OPERATIONS as LABEL_STYLE_OPERATIONS, POINT_CONSTRUCTOR as STYLED_POINT_LABEL
from packages.contracts.label_text import OPERATIONS as LABEL_TEXT_OPERATIONS, POINT_CONSTRUCTOR as TEXT_POINT_LABEL
from packages.contracts.label_yloc import OPERATIONS as LABEL_YLOC_OPERATIONS, POINT_CONSTRUCTOR as VERTICAL_POINT_LABEL
from packages.contracts.linefills import OPERATIONS as LINEFILL_OPERATIONS, effects as linefill_effects
from packages.contracts.lines import OPERATIONS as LINE_OPERATIONS
from packages.contracts.line_extensions import OPERATIONS as EXTENSION_OPERATIONS, effects as extension_effects
from packages.contracts.line_styles import OPERATIONS as LINE_STYLE_OPERATIONS, POINT_CONSTRUCTORS as STYLED_POINT_LINES

def drawing_kernel(operation, kind):
    def run(arguments, context):
        return getattr(context, kind).apply(operation, arguments)
    return run

KERNELS=MappingProxyType({**KERNELS, **_MARKER_KERNELS, **{
    op: Kernel(tuple(n for n, _ in parameters), ('draws_' + kind,), drawing_kernel(op, kind))
    for kind, operations in (('labels', LABEL_OPERATIONS), ('lines', LINE_OPERATIONS),
                             ('boxes', BOX_OPERATIONS), ('tables', TABLE_OPERATIONS))
    for op, parameters in operations.items()
}, **{
    op: Kernel(('id',), ('draws_' + PLURALS[kind],), drawing_kernel(op, PLURALS[kind]))
    for op, kind in COPY_KINDS.items()
}, **{
    op: Kernel(tuple(n for n, _ in parameters), property_effects(op),
               drawing_kernel(op, PLURALS[op.partition('.')[0]]))
    for op, parameters in DRAWING_PROPERTIES.items()
}, **{
    op: Kernel(tuple(n for n, _ in parameters), extension_effects(op), drawing_kernel(op, 'lines'))
    for op, parameters in EXTENSION_OPERATIONS.items()
}, **{
    op: Kernel(tuple(n for n, _ in parameters), linefill_effects(op), drawing_kernel(op, 'lines'))
    for op, parameters in LINEFILL_OPERATIONS.items()
}, **{
    op: Kernel(tuple(n for n, _ in parameters), ('draws_' + kind,), drawing_kernel(op, kind))
    for op, parameters in XLOC_OPERATIONS.items() for kind in (PLURALS[op.split('.')[0]],)
}, **{
    op: Kernel(tuple(n for n, _ in parameters), xloc_access_effects(op), drawing_kernel(op, PLURALS[op.split('.')[0]]))
    for op, parameters in XLOC_ACCESS.items()
}, **{
    op: Kernel(tuple(n for n, _ in parameters), ('draws_' + kind,), drawing_kernel(op, kind))
    for kind, operations, point_constructors in (('lines', LINE_STYLE_OPERATIONS, STYLED_POINT_LINES),
                                    ('boxes', BOX_BORDER_OPERATIONS, (STYLED_POINT_BOX,)),
                                    ('boxes', BOX_TEXT_OPERATIONS, (TEXT_POINT_BOX,)),
                                    ('labels', LABEL_STYLE_OPERATIONS, (STYLED_POINT_LABEL,)),
                                    ('labels', LABEL_YLOC_OPERATIONS, (VERTICAL_POINT_LABEL,)),
                                    ('labels', LABEL_TEXT_OPERATIONS, (TEXT_POINT_LABEL,)))
    for op, parameters in operations.items() if op not in point_constructors
}})
