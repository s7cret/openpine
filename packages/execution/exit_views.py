"""Byte-bounded projection of the extended exit journal for chart clients."""
from packages.contracts.wire import canonical

CHART_EVENT_BYTES=2*1024*1024
EXPORT_EVENT_BYTES=8*1024*1024

def export_events(events):
    result=[];size=2
    try:
        for event in events:
            size+=len(canonical(event))+1
            if size>EXPORT_EVENT_BYTES:return None
            result.append(event)
    finally:
        if hasattr(events,'close'):events.close()
    return result

def chart_events(events):
    visible=[];size=2;truncated=False
    try:
        for event in events:
            if event['kind']=='mark':
                mark=event['value'];value={k:mark[k] for k in ('protections','entries','lots','position_size','position_avg_price','open_lots','order_groups')}
                value['exit_states']=[{k:o[k] for k in ('order_id','exit_id','from_entry','targets')} for o in mark['exit_states']]
                if 'economics' in mark:value['economics']=mark['economics']
                if 'risk' in mark:value['risk']=mark['risk']
                event={**event,'value':value}
            cost=len(canonical(event))+1
            if len(visible)>=5000 or size+cost>CHART_EVENT_BYTES:
                truncated=True;break
            visible.append(event);size+=cost
    finally:
        if hasattr(events,'close'):events.close()
    return visible,truncated,size
