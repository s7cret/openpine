"""Budgeted ordering and probing on the existing shared array heap."""
from functools import cmp_to_key
from packages.contracts.array_order import SORT,SEARCH,field_layout,valid_order
from packages.contracts.objects import is_object
from .history_memory import Allocation
from .array_operations import replace_buffer

def check_cancel(evaluation,node):
    if evaluation.cancelled and evaluation.cancelled():evaluation.work.error('CANCELLED','Execution cancelled before publishing array ordering.',node,'EXECUTION_CANCELLED')

def field_index(heap,node):
    if not is_object(node.element):return None
    return field_layout(node.element,node.arguments[2].expression.value,tuple(heap.object_types.values()))[0]

def key(heap,value,index,node):
    return heap.record(value,node).items[index] if index is not None else value

def compare(heap,evaluation,node,left,right):
    heap.charge(1+(max(len(left),len(right)) if type(left) is str and type(right) is str else 0),evaluation,node)
    # Missing and empty strings form one explicit sorting bucket. Stability
    # preserves their original order, including for descending sorts.
    a=left is None or left=='';b=right is None or right==''
    if a or b:return int(a)-int(b)
    return (left>right)-(left<right)

def sorted_indices(heap,evaluation,node,obj,index,descending=False,defined_only=False):
    n=len(obj.items);heap.charge(n,evaluation,node)
    keys=[key(heap,v,index,node) for v in obj.items]
    positions=[i for i in range(n) if not defined_only or keys[i] is not None]
    def cmp(a,b):return (-1 if descending else 1)*compare(heap,evaluation,node,keys[a],keys[b])
    positions.sort(key=cmp_to_key(cmp))
    return positions

def evaluate(heap,evaluation,node,args,reference,obj):
    op=node.operation;n=len(obj.items);index=field_index(heap,node)
    if op in SEARCH:
        target=args[1]
        if target is None:heap.error('ARRAY_SEARCH_NA','Binary search target must be defined.',node)
        if not n:return -1
        def probe(i):
            value=key(heap,obj.items[i],index,node)
            if value is None:heap.error('ARRAY_SEARCH_NA','Binary search requires defined keys at each probe.',node)
            return compare(heap,evaluation,node,value,target)
        lo,hi=0,n
        # The caller supplies ascending keys. Do not replace this logarithmic
        # probe with an unrequested sort or a linear precondition scan.
        while lo<hi:
            mid=(lo+hi)//2;c=probe(mid)
            if op=='binary_search' and c==0:return mid
            if c<0 or op=='binary_search_rightmost' and c==0:lo=mid+1
            else:hi=mid
        if op=='binary_search':return -1
        if op=='binary_search_leftmost':
            return lo if lo<n and probe(lo)==0 else max(0,lo-1)
        return lo-1 if lo and probe(lo-1)==0 else lo
    descending=False
    if op in SORT:
        order=args[1]
        if heap.version==4:
            if type(order) is not bool:heap.error('ARRAY_ORDER','V4 sort order must be a defined bool.',node)
            descending=not order
        else:
            if not valid_order(order):heap.error('ARRAY_ORDER','Sort order must be a closed sort_order token.',node)
            descending=order=='descending'
    else:
        nth=args[1]
        if type(nth) is not int or nth<0:heap.error('ARRAY_RANK','Rank must be a defined nonnegative integer.',node)
        descending=op=='max'
    temporary=Allocation(heap.account) if heap.account is not None else None
    try:
        if temporary:
            # Keys, index objects, key wrappers and TimSort merge workspace are
            # charged before constructing any of the corresponding buffers.
            temporary.reserve(4*n,heap.owner+' ordering scratch','pair')
            temporary.reserve(n,heap.owner+' ordering indices','index')
        if op=='sort':
            # The ordinary transaction reserves final slots and protects COW
            # ownership/reference counts before any comparison can fail.
            heap.charge(n*(3 if is_object(obj.element) else 1),evaluation,node)
            def build():return [obj.items[i] for i in sorted_indices(heap,evaluation,node,obj,index,descending)]
            replace_buffer(heap,reference,obj,n,obj.string_bytes,evaluation,node,build)
            return None
        if op=='sort_indices':
            heap.capacity(len(heap.objects)+1,heap.cells+n,n,heap.bytes,node)
            if temporary:temporary.reserve(n,heap.owner+' sorted output','array_cell');temporary.reserve(1,heap.owner+' sorted output','array_object')
            values=sorted_indices(heap,evaluation,node,obj,index,descending)
            check_cancel(evaluation,node)
            if temporary:temporary.close();temporary=None
            return heap.create('int',values,0,evaluation,node)
        positions=sorted_indices(heap,evaluation,node,obj,None,descending,True)
        if not positions:return None
        if nth>=len(positions):heap.error('ARRAY_RANK','Rank exceeds the defined elements of this array.',node)
        return obj.items[positions[nth]]
    finally:
        if temporary:temporary.close()
