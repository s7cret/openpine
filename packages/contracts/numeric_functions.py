"""Versioned finite numeric ABI and one implementation for runtime and targets."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_DOWN, ROUND_HALF_UP, localcontext
from functools import cache
from pathlib import Path
from types import MappingProxyType
import math
from .model import CallContract,ValueType
from .scalar_functions import ScalarError
from .wire import read_json,digest

ROOT=Path(__file__).resolve().parents[2]
NAMES=('floor','ceil','sign','sqrt','pow','log','log10','exp')
NEW_OPERATIONS=frozenset('numeric.'+name for name in NAMES)
LEGACY_OPERATIONS=frozenset('numeric.'+name for name in ('abs','round','min','max','int','float','na','nz'))
OPERATIONS=NEW_OPERATIONS|LEGACY_OPERATIONS

class NumericError(ScalarError):
    def __init__(self,code,message):self.code=code;super().__init__(message)

def apis(version):
    rows=[]
    for name in NAMES:
        parameters=('x','y') if name=='pow' and version<3 else ('base','exponent') if name=='pow' else ('x',) if version<5 else ('number',)
        rows.append({'id':f'openpine.numeric.v{version}.{name}','name':('math.' if version>=5 else '')+name,
            'operation':'numeric.'+name,'parameters':list(parameters),'types':['int','float'],
            'result':'int' if name in ('floor','ceil') and version>=3 else 'float',
            'result_rule':'derived','named':version>=3,'effects':[]})
    return rows

def policy(version):
    return {'profile_id':'openpine.numeric-functions.1','language_version':version,
        'floor_ceil_result':'float' if version<3 else 'int','qualifiers':'preserve_argument_max_const_input_simple_series',
        'legacy_abi':'unchanged_captured_contracts','round_ties':'toward_positive_infinity_decimal',
        'format_ties':'separate_existing_HALF_UP','domains':'sqrt_nonnegative_log_positive_pow_real',
        'invalid_domain':'numeric_na','zero_pow_zero':1.0,'nonfinite_output':'NUMERIC_OVERFLOW',
        'integer_range':'signed_int64','pow':'binary64_math_pow_never_python_unbounded_integer_power',
        'cost':dict(COSTS),'argument_work':1,'legacy_budget':'charged_only_in_new_numeric_programs',
        'work_scope':'existing_shared_scalar_budget_per_target_or_runtime_bar','external_equivalence':False}

COSTS=MappingProxyType({**{op:1 for op in LEGACY_OPERATIONS},**{'numeric.'+n:c for n,c in
    (('floor',2),('ceil',2),('sign',2),('sqrt',8),('pow',32),('log',32),('log10',32),('exp',32))}})

@cache
def new_contracts(version):
    return MappingProxyType({a['id']:CallContract('symbol.'+a['id'],'overload.'+a['id'],'assertion.'+a['id'],digest(a),a['operation'],
        tuple('parameter.'+a['id']+'.'+n for n in a['parameters']),tuple(tuple(a['types']) for _ in a['parameters']),
        tuple('series' for _ in a['parameters']),None,(),ValueType(a['result']),'derived') for a in apis(version)})

@cache
def legacy_document():return read_json(ROOT/'spec/numeric-legacy-abi.json')

@cache
def contracts(version):
    result={}
    for row in legacy_document()['versions'][str(version)]:
        item=dict(row);item['result_type']=ValueType(**item['result_type'])
        for key in ('parameter_ids','qualifier_limits','effects'):item[key]=tuple(item[key])
        item['parameter_types']=tuple(tuple(x) for x in item['parameter_types'])
        c=CallContract(**item);result[c.overload_id]=c
    result.update((c.overload_id,c) for c in new_contracts(version).values())
    return MappingProxyType(result)

def result_type(contract,types):
    order=('const','input','simple','series');q=max((t.qualifier for t in types),key=order.index)
    if contract.result_rule=='common':
        if len({t.base for t in types})!=1:raise NumericError('NUMERIC_ABI','Numeric common argument types differ.')
        return ValueType(types[0].base,q)
    if contract.result_rule=='derived':return ValueType(contract.result_type.base,q)
    if contract.result_rule=='fixed':return types[contract.result_parameter] if contract.result_parameter is not None else contract.result_type
    raise NumericError('NUMERIC_ABI','Unreviewed numeric result rule.')

def validate_call(node,version):
    c=contracts(version).get(node.overload_id)
    if c is None or c.operation not in OPERATIONS or any(getattr(node,k)!=getattr(c,k) for k in ('symbol_id','assertion_id','signature_sha256','operation','effects')):
        raise NumericError('NUMERIC_ABI','Numeric call differs from its closed version-specific ABI.')
    if tuple(a.parameter_id for a in node.arguments)!=c.parameter_ids:raise NumericError('NUMERIC_ABI','Numeric parameter identities differ.')
    indices=[a.source_index for a in node.arguments if a.origin=='source']
    if any(type(i) is not int for i in indices) or sorted(indices)!=list(range(len(indices))) or any(a.origin not in ('source','default') for a in node.arguments):raise NumericError('NUMERIC_ABI','Numeric source argument order differs.')
    order=tuple(sorted(range(len(node.arguments)),key=lambda i:(node.arguments[i].origin=='default',node.arguments[i].source_index if node.arguments[i].source_index is not None else i)))
    if any(type(i) is not int for i in node.evaluation_order) or node.evaluation_order!=order:raise NumericError('NUMERIC_ABI','Numeric evaluation order differs.')
    for index,(arg,types) in enumerate(zip(node.arguments,c.parameter_types,strict=True)):
        if arg.expression.value_type.base not in types:raise NumericError('NUMERIC_ABI','Numeric argument type differs.')
        if arg.origin=='default':
            from .model import LiteralIR
            if (c.operation!='numeric.nz' or index!=1 or not isinstance(arg.expression,LiteralIR) or arg.source_index is not None
                    or arg.expression.value_type.qualifier!='const'
                    or type(arg.expression.value) not in (int,float,bool) or arg.expression.value!=0):
                raise NumericError('NUMERIC_ABI','Numeric default differs from the preserved nz typed zero.')
    if node.value_type!=result_type(c,[a.expression.value_type for a in node.arguments]):raise NumericError('NUMERIC_ABI','Numeric result type differs.')
    return c

def invoke(operation,arguments,budget=None,version=6):
    if operation not in OPERATIONS:raise NumericError('NUMERIC_ABI','Unregistered numeric operation.')
    if budget is not None:budget.charge(COSTS[operation]+len(arguments))
    kind=operation.removeprefix('numeric.')
    if kind=='na':return arguments[0] is None
    if kind=='nz':return arguments[1] if arguments[0] is None else arguments[0]
    if kind=='round':
        source=arguments[0];precision=arguments[1] if len(arguments)==2 else 0
        if type(precision) is not int or not -15<=precision<=15:raise NumericError('ROUND_PRECISION','Round precision must be a defined int in -15..15.')
        if source is None:return None
        with localcontext() as ctx:
            ctx.prec=400;value=Decimal(str(source));rounded=value.quantize(Decimal(1).scaleb(-precision),rounding=ROUND_HALF_DOWN if value.is_signed() else ROUND_HALF_UP)
        return int(rounded) if len(arguments)==1 else float(rounded)
    if any(value is None for value in arguments):return None
    value=arguments[0]
    if kind=='abs':return abs(value)
    if kind=='int':return int(value)
    if kind=='float':return float(value)
    if kind=='min':return min(arguments)
    if kind=='max':return max(arguments)
    if any(type(v) not in (int,float) or type(v) is float and not math.isfinite(v) for v in arguments):raise NumericError('NUMERIC_RANGE','Numeric input must be finite.')
    try:
        if kind in ('floor','ceil'):
            result=(math.floor if kind=='floor' else math.ceil)(value)
            result=float(result) if version<3 else result
        elif kind=='sign':result=1.0 if value>0 else -1.0 if value<0 else 0.0
        elif kind=='sqrt':result=None if value<0 else math.sqrt(value)
        elif kind in ('log','log10'):result=None if value<=0 else (math.log if kind=='log' else math.log10)(value)
        elif kind=='exp':result=math.exp(value)
        elif kind=='pow':
            exponent=arguments[1]
            if value<0 and exponent!=math.trunc(exponent) or value==0 and exponent<0:return None
            # Preserve parity of exact integer exponents without ever creating
            # an unbounded integer power; magnitude remains binary64.
            result=math.pow(abs(value) if value<0 else value,exponent)
            if value<0 and math.trunc(exponent)%2:result=-result
        else:raise NumericError('NUMERIC_ABI','Numeric operation is missing.')
    except (OverflowError,ValueError) as error:raise NumericError('NUMERIC_OVERFLOW','Numeric result is outside the finite real range.') from error
    if type(result) is int and not -(2**63)<=result<2**63:raise NumericError('NUMERIC_RANGE','Numeric integer result exceeds int64.')
    if type(result) is float and not math.isfinite(result):raise NumericError('NUMERIC_OVERFLOW','Numeric result is outside the finite real range.')
    return result
