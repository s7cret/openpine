"""Checked one-level array-to-record edges over the existing reference heap."""
from collections import Counter
from packages.contracts.objects import is_object

def children(obj):
    from .arrays import ArrayObject
    return obj.items if type(obj) is ArrayObject and is_object(obj.element) else ()

def external_counts(heap,persistent):
    """Keep history references, replace persistent roots, and exclude old edges."""
    from .arrays import is_reference
    counts=Counter(heap.references)
    for ref,n in heap.roots.items():counts[ref.object_id]-=n
    for obj in heap.objects.values():
        for ref in children(obj):
            if ref is not None:counts[ref.object_id]-=1
    if any(n<0 for n in counts.values()):raise ValueError('Invalid confirmed graph reference counts.')
    for ref in persistent.values():
        if is_reference(ref):counts[ref.object_id]+=1
    return counts

def referenced(objects,external):
    """Count each live array's slots once, independently of aliases to that array."""
    counts=Counter({k:n for k,n in external.items() if n});pending=list(counts);seen=set()
    while pending:
        key=pending.pop()
        if key in seen:continue
        if key not in objects:raise ValueError('Dangling graph reference.')
        seen.add(key)
        for ref in children(objects[key]):
            if ref is not None:
                counts[ref.object_id]+=1;pending.append(ref.object_id)
    return counts
