"""Artifact-defined historical arrays of closed lower candles, never live ticks."""
from packages.contracts.optional_symbols import has_symbol_option
from .model import ValueType,TupleType
from dataclasses import dataclass

@dataclass(frozen=True)
class ClosedWindow:
    """Internal bounded prefix view; Pine receives copies owned by its array heap."""
    rows: list
    stop: int

    def snapshot(self):return tuple(self.rows[:self.stop])

    def arrays(self,typ):
        rows=self.snapshot()
        return tuple(tuple(row[i] for row in rows) for i in range(len(typ.elements))) if isinstance(typ,TupleType) else rows

OPERATION='context.lower_tf'
PARAMETERS=('symbol','timeframe','expression','ignore_invalid_symbol','currency','ignore_invalid_timeframe','calc_bars_count')
DEFAULT_COUNT=100000
MAX_COUNT=10000000

def policy(version):
    return {'available':version>=5,'parameters':list(PARAMETERS),'scope':'unconditional_global',
        'results':['int','float','bool'],'tuple_limit':16,'contexts':'pinned_snapshots',
        'timeframes':'strictly_lower_divisible_utc_minutes','availability':'closed_child_within_primary',
        'missing':'empty_array_no_fill','history_window_default':DEFAULT_COUNT,'history_window_max':MAX_COUNT,
        'default_basis':'explicit_OpenPine_window_not_claimed_as_unresolved_Pine_default',
        'expression_state':'private_per_call_site_last_N_closed_rows_at_execution_end','array_identity':'fresh_per_written_call_per_calculation',
        'currency':'native_only','ignore_invalid_symbol':'false_only_offline_binding_required',
        'external_execution_verified':False}

def result_type(typ):
    if isinstance(typ,TupleType):return TupleType(tuple(result_type(t) for t in typ.elements))
    if typ.base not in ('int','float','bool'):raise ValueError('Lower-timeframe expressions require int, float or bool components.')
    return ValueType('array.'+typ.base,'series')

def requestable(primary,requested):
    return (type(primary) is str and type(requested) is str and primary.isdigit() and requested.isdigit()
        and 0<int(requested)<int(primary)<=1440 and int(primary)%int(requested)==0)

def validate_skipped_timeframe(node,primary,requested):
    if not requestable(primary,requested) and not options(node)[0]:
        raise ValueError("Lower requests require a smaller divisible UTC minute interval.")


def options(node):
    from .model import LiteralIR
    args=node.arguments
    if len(args)!=len(PARAMETERS):raise ValueError('Lower-timeframe parameter count differs.')
    for i in ((3,4,5,6) if node.overload_id.endswith('.prepared') else (0,1,3,4,5,6)):
        if not isinstance(args[i].expression,LiteralIR):raise ValueError('Lower-timeframe settings must be literal constants.')
    values=[a.expression.value if isinstance(a.expression,LiteralIR) else None for a in args]
    if type(values[3]) is not bool or values[3] and not has_symbol_option(node):raise ValueError('Offline lower requests require ignore_invalid_symbol=false and a pinned binding.')
    from .fx import code
    if values[4] != '' and ('fx' not in node.overload_id.split('.') or values[4] != '@chart' and not code(values[4])):
        raise ValueError('Invalid lower-timeframe currency.')
    if type(values[5]) is not bool:raise ValueError('ignore_invalid_timeframe must be a literal bool.')
    if type(values[6]) is not int or not 1<=values[6]<=MAX_COUNT:raise ValueError('calc_bars_count must be a literal integer in 1..10000000.')
    return values[5],values[6]

def io_policy(program,memory_mb,context_count,magnifier=False,settings=None,*,primary=None,resolutions=()):
    """Account for separate history windows as actual concurrent readers."""
    slots=2*(context_count+1)+2
    if program is not None:
        from .context_ir import request_flags
        from .prepared_requests import resolved_requests
        skipped={r.call_site_id for r in resolutions if r.resolution=="unknown_symbol"}
        groups=set()
        for node in resolved_requests(program,settings,primary=primary):
            if node.call_site_id in skipped:continue
            pair=tuple(a.expression.value for a in node.arguments[:2])
            mode=('lower',options(node)[1]) if node.operation==OPERATION else ('security',request_flags(node,program.identity.language_version)[1])
            groups.add((*pair,*mode))
        slots=max(slots,3+len(groups))
    from .memory import job_memory
    return job_memory(memory_mb,slots+bool(magnifier))

def io_budget(program,memory_mb,context_count,magnifier=False,settings=None,*,primary=None,resolutions=()):
    return io_policy(program,memory_mb,context_count,magnifier,settings,primary=primary,resolutions=resolutions)['per_io_mb']
