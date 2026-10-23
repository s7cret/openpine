"""Closed scalar-product types and the artifact-defined composite profile."""
from .model import ValueType, TupleType

QUALIFIERS = ('const', 'input', 'simple', 'series')
MAX_ITEMS = 16


def uses_composite(value):
    from dataclasses import fields, is_dataclass
    from .model import TupleIR, UnpackIR, CallIR
    if isinstance(value,(TupleIR,UnpackIR,TupleType)):return True
    if isinstance(value,CallIR) and value.operation in COMPOSITE_OPERATIONS:return True
    if is_dataclass(value):return any(uses_composite(getattr(value,f.name)) for f in fields(value))
    return type(value) is tuple and any(uses_composite(e) for e in value)


def qualified(value_type, qualifier):
    if isinstance(value_type, TupleType):
        return TupleType(tuple(ValueType(e.base, qualifier) for e in value_type.elements))
    return ValueType(value_type.base, qualifier)


def composite_policy(version):
    return {'language_version':version, 'arity':[2,MAX_ITEMS], 'nested':False,
        'bindings':'new_scalar_declarations_and_underscore_discard',
        'evaluation':'once_in_source_order', 'qualifier':'strongest_shared_dependency',
        'tuple_storage':'ephemeral_results_only', 'length':'simple_int_1_100000',
        'window':'last_length_defined_samples', 'warmup':'full_length',
        'source_na':'skip_and_hold', 'biased_default':True,
        'unbiased_singleton':'na', 'variance':'exact_binary_window_sums',
        'macd_ema_seed':'sma' if version<5 else 'first_defined',
        'macd_signal':'ema_of_defined_macd', 'bb':'sma_plus_minus_mult_population_stdev',
        'macd_na':'fast_slow_hold_signal_consumes_defined_difference',
        'request_result':'scalar_numeric_or_flat_numeric_tuple',
        'memory_cells':1000000, 'external_execution_verified':False}


def composite_apis(version):
    prefix='' if version<5 else 'ta.'
    source,length=('x','y') if version<3 else ('source','length')
    def p(name,base,qualifier='series',default=None):
        return {'name':name,'types':[base],'qualifier':qualifier,'default':default}
    def api(name,params,result,operation=None):
        return {'id':f'openpine.composite.v{version}.{name}','name':prefix+name,
            'operation':operation or 'series.'+name, 'parameters':params,
            'result':result,'named':version>=3,'effects':['reads_history','updates_state']}
    result=[]
    for name in ('variance','stdev'):
        params=[p(source,'float'),p(length,'int','simple')]
        if version>=5: params.append(p('biased','bool','series',True))
        result.append(api(name,params,['float'],'series.'+name+('.population' if version<5 else '')))
    result.append(api('macd',[p('source','float'),p('fastlen','int','simple'),
        p('slowlen','int','simple'),p('siglen','int','simple')],['float']*3))
    result.append(api('bb',[p('series','float'),p('length','int','simple'),
        p('mult','float','simple')],['float']*3))
    return result


COMPOSITE_OPERATIONS=frozenset(('series.variance','series.stdev','series.variance.population',
    'series.stdev.population','series.macd','series.bb'))
