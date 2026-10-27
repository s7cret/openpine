"""Closed-child event causality and a frozen, finite warmup prefix."""
import copy
from .lower_tf import OPERATION

PROFILE='openpine.lower-events.1'
def policy():
    return {'profile_id':PROFILE,'versions':[5,6],'visibility':'received_closed_children_in_primary_interval',
        'expression':'shared_private_typed_context','warmup':'first_pinned_count_closes_last_N_evaluated_then_continue',
        'window':'fixed_at_start_never_rebased_on_new_arrivals','late_default':'reject',
        'late_override':'drop_from_committed_arrays_advance_private_context_with_metadata',
        'preview':'only_existing_primary_calculation_phases','retention':'uncommitted_parent_windows_only',
        'max_pending_cells':1000000,'external_equivalence':False}

def configuration(program,contexts,primary_id,value,*,settings=None):
    from .prepared_requests import resolved_requests
    primary=next((c for c in contexts if c.context_id==primary_id),None)
    nodes=[n for n in resolved_requests(program,settings,primary=primary) if n.operation==OPERATION]
    if not nodes:
        if value is not None:raise ValueError('Lower event settings require a lower-timeframe request.')
        return None
    v=copy.deepcopy(value) if value is not None else {'schema_version':PROFILE,'late':'reject','warmup':{}}
    if (type(v) is not dict or v.keys()!={'schema_version','late','warmup'} or v['schema_version']!=PROFILE
            or v['late'] not in ('reject','drop_with_metadata') or type(v['warmup']) is not dict):raise ValueError('Invalid lower event configuration.')
    available={(c.symbol,c.timeframe):c.context_id for c in contexts};used=set()
    for node in nodes:
        pair=tuple(a.expression.value for a in node.arguments[:2])
        if pair not in available:raise ValueError('Lower request context is absent from replay.')
        used.add(available[pair])
    if not v['warmup'].keys()<=used:raise ValueError('Warmup contains a non-lower context.')
    for cid,row in v['warmup'].items():
        if (type(row) is not dict or row.keys()!={'count','end_ms'} or type(row['count']) is not int
                or not 0<=row['count']<=10000000 or type(row['end_ms']) is not int
                or not 0<=row['end_ms']<253402214400000):raise ValueError('Invalid frozen lower warmup prefix.')
    return v
