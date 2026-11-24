"""Closed, versioned scalar ABI and bounded kernels shared by all evaluators.

This is an artifact-defined subset. Unicode means Python Unicode code points,
not grapheme clusters or UTF-16 offsets; external execution is not certified.
"""
from dataclasses import dataclass
from decimal import Decimal, localcontext, ROUND_HALF_UP
from types import MappingProxyType
import math
import re

MAX_STRING=4096
MAX_WORK=262144
PROFILE='openpine.scalar-functions.1'
STRING_SEARCH=('lower','upper','startswith','endswith','pos')
STRING_SEARCH_OPERATIONS=frozenset('scalar.'+name for name in STRING_SEARCH)
RUNTIME_STRING_OPERATIONS=STRING_SEARCH_OPERATIONS | {'scalar.trim','scalar.repeat','scalar.replace','scalar.tonumber'}
_DECIMAL_TEXT=re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?')

class ScalarError(ValueError):pass

@dataclass
class ScalarBudget:
    remaining:int=MAX_WORK
    def charge(self,cost):
        if type(cost) is not int or cost<0 or cost>self.remaining:raise ScalarError('Scalar work budget exceeded.')
        self.remaining-=cost
    def output(self,size):
        if not 0<=size<=MAX_STRING:raise ScalarError('Scalar string exceeds 4096 code points before allocation.')
        self.charge(size)

def concat(left,right,budget):
    budget.output(len(left)+len(right));return left+right

def apis(version):
    def row(kind,name,parameters,result,rule='scalar_simple',defaults=()):
        return {'id':f'openpine.scalar.v{version}.{kind}','name':name,'operation':'scalar.'+kind,
            'parameters':[{'name':n,'types':list(t),'qualifier':'series'} for n,t in parameters],
            'result':result,'result_rule':rule,'defaults':list(defaults),'effects':[],'named':version>=3}
    numeric=('int','float');value=numeric if version<4 else (*numeric,'bool','string')
    name='tostring' if version<5 else 'str.tostring';arg='x' if version<5 else 'value';fmt='y' if version<5 else 'format'
    rows=[row('tostring',name,[(arg,value)],'string'),
        row('tostring.format',name,[(arg,numeric),(fmt,('string',))],'string')]
    if version>=4:
        rows.append(row('tonumber','tonumber' if version==4 else 'str.tonumber',
                        [('x' if version==4 else 'string',('string',))],'float','fixed_series' if version==4 else 'derived'))
        rows.extend([row('length','str.length',[('string',('string',))],'int','scalar_const_simple'),
            row('replace_all','str.replace_all',[(n,('string',)) for n in ('source','target','replacement')],'string')])
    if version>=5:
        text=('string',)
        rows.extend([
            row('trim','str.trim',[('source',text)],'string','derived'),
            row('repeat','str.repeat',[('source',text),('repeat',('int',)),('separator',text)],'string','derived',('',)),
            row('replace','str.replace',[('source',text),('target',text),('replacement',text),('occurrence',('int',))],
                'string','scalar_const_simple',(0,)),
        ])
        for kind in STRING_SEARCH:
            parameters=[('source',('string',))]
            result='string'
            if kind not in ('lower','upper'):
                parameters.append(('str',('string',)))
                result='int' if kind=='pos' else 'bool'
            rows.append(row(kind,'str.'+kind,parameters,result,'scalar_const_simple'))
        rows.extend([row('substring','str.substring',[('source',('string',)),('begin_pos',('int',))],'string','scalar_const_simple'),
            row('substring.end','str.substring',[('source',('string',)),('begin_pos',('int',)),('end_pos',('int',))],'string','scalar_const_simple'),
            row('contains','str.contains',[('source',('string',)),('str',('string',))],'bool','scalar_const_simple'),
            row('in_seconds','timeframe.in_seconds',[('timeframe',('string',))],'int',defaults=('',)),
            row('from_seconds','timeframe.from_seconds',[('seconds',('int',))],'string')])
    return rows

def policy(version):
    return {'profile_id':PROFILE,'language_version':version,'max_string_code_points':MAX_STRING,'max_work':MAX_WORK,
        'work_scope':'one_target_evaluation_or_one_runtime_bar_shared_by_nested_calls',
        'string_indices':'unicode_code_points_zero_based_end_exclusive_strict_bounds',
        'empty_replace_target':'insert_at_all_code_point_boundaries','matching':'case_sensitive_no_normalization',
        'format':'one_integer_placeholder_optional_1_to_10_decimal_placeholders_zero_then_hash_HALF_UP',
        'default_format':'#.##########','na':'tostring_NaN_other_kernels_propagate_except_bool_v6_false',
        'timeframe_units':'1_5_10_15_30_45S_minutes1_1440_days1_365_weeks1_52_months1_12',
        'month_seconds':2628003,'from_seconds':'ceil_duration_smaller_unit_for_rounded_ties_larger_unit_for_exact_ties_clamp12M',
        'default_timeframe':'bound_primary_period_reference','request_intervals':'existing_canonical_minutes_or_single_D_W_M',
        'external_equivalence':False}

def contracts(version):
    from .model import CallContract,ValueType
    from .wire import digest
    return {a['id']:CallContract('symbol.'+a['id'],'overload.'+a['id'],'assertion.'+a['id'],digest(a),a['operation'],
        tuple('parameter.'+a['id']+'.'+p['name'] for p in a['parameters']),tuple(tuple(p['types']) for p in a['parameters']),
        tuple(p['qualifier'] for p in a['parameters']),None,(),ValueType(a['result']),a['result_rule']) for a in apis(version)}

def validate_call(node,version):
    c=next((c for c in contracts(version).values() if c.overload_id==node.overload_id),None)
    if c is None or any(getattr(node,n)!=getattr(c,n) for n in ('operation','symbol_id','assertion_id','signature_sha256','effects')):
        raise ScalarError('Scalar call differs from the closed version-specific registry.')
    if tuple(a.parameter_id for a in node.arguments)!=c.parameter_ids or sorted(node.evaluation_order)!=list(range(len(node.arguments))):
        raise ScalarError('Scalar argument binding differs.')
    for a,types,limit in zip(node.arguments,c.parameter_types,c.qualifier_limits,strict=True):
        if a.expression.value_type.base not in types:raise ScalarError('Scalar argument type differs.')
        if a.origin=='default':
            from .model import LiteralIR
            if node.operation in ('scalar.repeat','scalar.replace'):
                default='' if node.operation=='scalar.repeat' else 0
                if (a.parameter_id!=c.parameter_ids[-1] or not isinstance(a.expression,LiteralIR)
                        or a.expression.value_type.qualifier!='const' or a.expression.value!=default):
                    raise ScalarError('String call default differs from its registered literal.')
                continue
            from .model import ReferenceIR
            from .environment import PERIOD_SLOTS
            if (node.operation!='scalar.in_seconds' or not isinstance(a.expression,ReferenceIR)
                    or a.expression.contract.slot not in PERIOD_SLOTS):
                raise ScalarError('Scalar default must be the bound primary timeframe reference.')
    from .model import ValueType
    qualifiers=('const','input','simple','series')
    q=max((a.expression.value_type.qualifier for a in node.arguments),key=qualifiers.index)
    if c.result_rule=='fixed_series':q='series'
    elif c.result_rule!='derived' and (q=='input' or q=='const' and c.result_rule=='scalar_simple'):q='simple'
    if node.value_type!=ValueType(c.result_type.base,q):raise ScalarError('Scalar result type differs from the registry.')
    return c

def _number(value,pattern,budget):
    if type(pattern) is not str or not re.fullmatch(r'[#0](?:\.0*#*|\.0+)?',pattern) or pattern.endswith('.') or len(pattern)>12:
        raise ScalarError('Supported numeric format: # or 0, optionally followed by 1..10 decimal 0/# placeholders (zeros first).')
    fraction=pattern.partition('.')[2]
    if len(fraction)>10:raise ScalarError('Numeric format exceeds ten decimal places.')
    return format_decimal(value, len(fraction), fraction.count('0'), budget)


def format_decimal(value, places, required, budget, *, percent=False):
    if value is None:return 'NaN'
    if type(value) not in (int,float) or type(value) is float and not math.isfinite(value):raise ScalarError('Numeric formatting requires a finite scalar.')
    # Finite doubles have at most 309 integer digits; bound before Decimal/format.
    budget.output(312+places+2*percent)
    with localcontext() as ctx:
        ctx.prec=340
        decimal=Decimal(str(value))
        if percent:
            decimal=decimal.scaleb(2)
        result=format(decimal.quantize(Decimal(1).scaleb(-places),rounding=ROUND_HALF_UP),'f')
    if places:
        whole,_,tail=result.partition('.');tail=tail.rstrip('0')
        tail=tail+'0'*max(0,required-len(tail));result=whole+('.'+tail if tail else '')
    if result.startswith('-') and Decimal(result)==0:result=result[1:]
    return result

def _intervals():
    rows=[(n,f'{n}S',0) for n in (1,5,10,15,30,45)]
    for unit,base,maximum,rank in (('',60,1440,1),('D',86400,365,2),('W',604800,52,3),('M',2628003,12,4)):
        rows.extend((n*base,str(n)+unit,rank) for n in range(1,maximum+1))
    return tuple(rows)
_INTERVALS=_intervals()
_SECONDS=MappingProxyType({text:seconds for seconds,text,_ in _INTERVALS}|{'D':86400,'W':604800,'M':2628003})

def invoke(operation,arguments,budget=None,version=6):
    budget=ScalarBudget() if budget is None else budget
    if operation not in OPERATIONS:raise ScalarError('Unregistered scalar kernel.')
    budget.charge(1)
    for value in arguments:
        if type(value) is str:
            if len(value)>MAX_STRING:raise ScalarError('Scalar input string exceeds 4096 code points.')
            budget.charge(len(value))
    kind=operation.removeprefix('scalar.')
    if kind=='trim':
        source=arguments[0]
        return '' if source is None else source.strip()
    if kind.startswith('tostring'):
        value=arguments[0]
        if kind=='tostring.format':return _number(value,arguments[1],budget)
        if value is None:return 'NaN'
        if type(value) is str:return value
        if type(value) is bool:return 'true' if value else 'false'
        return _number(value,'#.##########',budget)
    if any(v is None for v in arguments):return False if kind in ('contains','startswith','endswith') and version==6 else None
    if kind=='tonumber':
        source=arguments[0]
        if _DECIMAL_TEXT.fullmatch(source) is None:return None
        number=float(source)
        return number if math.isfinite(number) else None
    if kind=='repeat':
        source,count,separator=arguments
        if count<0:raise ScalarError('String repeat count must be nonnegative.')
        size=len(source)*count+len(separator)*max(0,count-1)
        budget.output(size)
        if not size:return ''
        if count==1:return source
        return (source+separator)*(count-1)+source
    if kind=='replace':
        source,target,replacement,occurrence=arguments
        if occurrence<0:raise ScalarError('String occurrence must be nonnegative.')
        if target:
            if occurrence>=len(source)//len(target):return source
            budget.charge(len(source)*len(target))
            start=0
            for _ in range(occurrence+1):
                index=source.find(target,start)
                if index<0:return source
                start=index+len(target)
        else:
            if occurrence>len(source):return source
            index=occurrence
        budget.output(len(source)-len(target)+len(replacement))
        return source[:index]+replacement+source[index+len(target):]
    if kind in ('lower','upper'):
        source=arguments[0]
        convert=str.lower if kind=='lower' else str.upper
        # The default Unicode contextual case rule (final sigma) preserves length.
        budget.output(sum(len(convert(character)) for character in source))
        return convert(source)
    if kind=='startswith':return arguments[0].startswith(arguments[1])
    if kind=='endswith':return arguments[0].endswith(arguments[1])
    if kind=='pos':
        source,target=arguments
        budget.charge(len(source)*max(1,len(target)))
        position=source.find(target)
        return position if position>=0 else None
    if kind=='length':return len(arguments[0])
    if kind.startswith('substring'):
        source,start=arguments[:2];end=arguments[2] if len(arguments)==3 else len(source)
        if type(start) is not int or type(end) is not int or not 0<=start<=end<=len(source):raise ScalarError('Substring indices require 0 <= begin <= end <= length.')
        budget.output(end-start);return source[start:end]
    if kind in ('contains','replace_all'):
        source,target=arguments[:2];budget.charge(len(source)*max(1,len(target)))
        if kind=='contains':return target in source
        replacement=arguments[2];occurrences=source.count(target) if target else len(source)+1
        budget.output(len(source)+occurrences*(len(replacement)-len(target)))
        return source.replace(target,replacement)
    if kind=='in_seconds':
        try:return _SECONDS[arguments[0]]
        except KeyError as error:raise ScalarError('Timeframe is outside the admitted interval grammar.') from error
    if kind=='from_seconds':
        seconds=arguments[0]
        if type(seconds) is not int:raise ScalarError('Seconds require an integer.')
        if seconds>31622400:return '12M'
        budget.charge(len(_INTERVALS))
        candidates=(r for r in _INTERVALS if r[0]>=seconds)
        row=min(candidates,key=lambda r:(r[0],-r[2] if r[0]==seconds else r[2]),default=None)
        return '12M' if row is None else row[1]
    raise ScalarError('Unimplemented scalar kernel.')

OPERATIONS=frozenset(a['operation'] for a in apis(6))
