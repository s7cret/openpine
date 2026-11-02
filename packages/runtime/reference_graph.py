"""Typed heap edges and iterative reachability, including nominal cycles."""
from collections import Counter
from packages.contracts.objects import is_object

def children(obj):
    from .arrays import ArrayObject,RecordObject,is_reference
    from .array_slice import SliceObject
    from .maps import MapObject,MapValues
    from .matrices import MatrixObject
    if type(obj) is MatrixObject:return obj.items if is_object(obj.element) else ()
    if type(obj) is MapObject:return MapValues(obj.items) if is_object(obj.element) else ()
    if type(obj) is SliceObject:return (obj.parent,)
    if type(obj) is RecordObject:
        return tuple(v for data in (obj.items,obj.initial_items) for v in data if is_reference(v))
    return obj.items if type(obj) is ArrayObject and is_object(obj.element) else ()

def external_counts(heap,persistent):
    """Keep history references, replace persistent roots, and exclude old edges."""
    from .arrays import is_reference
    counts=Counter(heap.references)
    for ref,n in heap.roots.items():counts[ref.object_id]-=n
    for obj in heap.objects.values():
        for ref in children(obj):
            if ref is not None:counts[ref.object_id]-=1
    for obj in heap.origins.values():
        for ref in children(obj):
            if ref is not None:counts[ref.object_id]-=1
    if any(n<0 for n in counts.values()):raise ValueError('Invalid confirmed graph reference counts.')
    for ref in persistent.values():
        if is_reference(ref):counts[ref.object_id]+=1
    return counts

def referenced(objects,external,*,charge=None,origins=None):
    """Count each live array's slots once, independently of aliases to that array."""
    counts=Counter({k:n for k,n in external.items() if n});pending=list(counts);seen=set(pending)
    while pending:
        key=pending.pop()
        if key not in objects:raise ValueError('Dangling graph reference.')
        edges=children(objects[key])
        if charge:charge(len(edges)+1)
        for ref in edges:
            if ref is not None:
                counts[ref.object_id]+=1
                if ref.object_id not in seen:seen.add(ref.object_id);pending.append(ref.object_id)
        if origins and key in origins:
            edges=children(origins[key])
            if charge:charge(len(edges)+1)
            for ref in edges:
                if ref is not None:
                    counts[ref.object_id]+=1
                    if ref.object_id not in seen:seen.add(ref.object_id);pending.append(ref.object_id)
    return counts

def collect(heap,persistent,evaluation,node):
    """Preflight a complete graph pass before publishing any collection."""
    from .history_memory import Allocation
    scratch=Allocation(heap.account) if heap.account else None
    try:
        if scratch:scratch.reserve(6*len(heap.objects),'reference graph GC scratch','index')
        for obj in heap.objects.values():heap.charge(len(children(obj))+1,evaluation,node)
        for obj in heap.origins.values():heap.charge(len(children(obj))+1,evaluation,node)
        external=external_counts(heap,persistent)
        if evaluation.is_preview:
            for key in heap.intrabar_dirty:
                if key in heap.objects:external[key]+=1
        counts=referenced(heap.objects,external,charge=lambda n:heap.charge(n,evaluation,node),origins=heap.origins)
        # Dirty-preview roots are temporary liveness, not durable root counts.
        if evaluation.is_preview:
            for key in heap.intrabar_dirty:
                if key in counts:counts[key]-=1
        dead=heap.objects.keys()-counts.keys()
        for key in dead:
            from .graph_baselines import discard
            discard(heap,key,edges=False)
            obj=heap.objects.pop(key);heap.cells-=len(obj.items);heap.bytes-=obj.string_bytes
            allocation=heap.allocations.pop(key,None)
            if allocation:allocation.close()
            heap.borrowed.pop(key,None);heap.owned.discard(key);heap.initial_records.discard(key)
        heap.references=dict(counts);heap.garbage={key for key,n in counts.items() if n==0}
        from .arrays import is_reference
        heap.roots=Counter(v for v in persistent.values() if is_reference(v))
    finally:
        if scratch:scratch.close()
