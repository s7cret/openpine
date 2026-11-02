"""Constructor collection buffers retained only by the new graph profile."""
from .arrays import ArrayObject,RecordObject
from .history_memory import Allocation
from .reference_graph import children

def remember(heap,key,obj):
    if heap.allow_graphs and type(obj) is not RecordObject:
        heap.origins[key]=obj
        for ref in children(obj):heap.retain(ref)

def reserve(heap,obj):
    if heap.account is None:return None
    a=Allocation(heap.account);label=heap.owner+' constructor collection baseline'
    try:
        from .maps import MapObject,reserve as map_reserve
        from .matrices import MatrixObject,reserve as matrix_reserve
        from .array_slice import SliceObject
        if type(obj) is MapObject:map_reserve(a,len(obj.items),obj.string_bytes,label)
        elif type(obj) is MatrixObject:matrix_reserve(a,obj.element,len(obj.items),obj.string_bytes,label)
        else:
            a.reserve(1,label,'array_object')
            a.reserve(len(children(obj)),label,'reference')
            a.reserve(max(0,len(obj.items)-len(children(obj))),label,'array_cell')
            a.reserve(4*obj.string_bytes,label,'byte')
            if type(obj) is SliceObject:a.reserve(2,label,'index')
        return a
    except BaseException:a.close();raise

def protect(heap,key):
    if not heap.allow_graphs or key not in heap.origins or key in heap.origin_allocations or key in heap.borrowed_origins:return
    a=reserve(heap,heap.origins[key])
    if a:heap.origin_allocations[key]=a

def discard(heap,key,*,edges=True):
    obj=heap.origins.pop(key,None)
    if obj is not None and edges:
        for ref in children(obj):heap.release(ref)
    a=heap.origin_allocations.pop(key,None)
    if a:a.close()
    heap.borrowed_origins.pop(key,None)
