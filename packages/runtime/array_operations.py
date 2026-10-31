"""Transactional buffer changes on the existing shared array heap."""
from packages.contracts.array_operations import SEARCH
from packages.contracts.objects import is_object
from .arrays import ArrayObject,ObjectRef,string_bytes
from .history_memory import Allocation

def position(heap,value,size,node,*,insert=False):
    if type(value) is not int:heap.error('ARRAY_INDEX','Array index must be a defined integer.',node)
    if value<0 and heap.version==6:value+=size
    if not 0<=value<size+int(insert):heap.error('ARRAY_INDEX','Array index is out of bounds.',node)
    return value

def replace_buffer(heap,reference,obj,size,byte_count,evaluation,node,build):
    """Preflight temporary ownership before constructing or publishing new slots."""
    from .array_slice import ArrayWindow,replace
    if type(obj) is ArrayWindow:return replace(heap,reference,obj,size,byte_count,evaluation,node,build)
    delta=size-len(obj.items);bytes_delta=byte_count-obj.string_bytes
    heap.capacity(len(heap.objects),heap.cells+delta,size,heap.bytes+bytes_delta,node)
    temporary=Allocation(heap.account) if heap.account is not None else None
    try:
        if temporary:
            label=heap.owner+' / '+node.operation+' staging'
            temporary.reserve(1,label,'array_object')
            temporary.reserve(size,label,'reference' if is_object(obj.element) else 'array_cell')
            temporary.reserve(4*byte_count,label,'byte')
        items=build()
        if is_object(obj.element):
            for value in items:
                if value is not None:
                    if type(value) is not ObjectRef or value.type_id!=obj.element:heap.error('ARRAY_REFERENCE','Array element nominal type differs.',node)
                    heap.record(value,node)
        if evaluation.cancelled and evaluation.cancelled():
            evaluation.work.error('CANCELLED','Execution cancelled before publishing array slots.',node,'EXECUTION_CANCELLED')
        # No further large allocation follows this release. The successful
        # staging reservation already covered the old and new buffers together.
        if temporary:temporary.close();temporary=None
        heap.sync(reference.object_id,size,byte_count,element=obj.element)
        if is_object(obj.element):
            for value in items:heap.retain(value)
            for value in obj.items:heap.release(value)
        heap.objects[reference.object_id]=ArrayObject(obj.element,items,byte_count)
        heap.owned.add(reference.object_id);heap.borrowed.pop(reference.object_id,None)
        heap.cells+=delta;heap.bytes+=bytes_delta
    finally:
        if temporary:temporary.close()

def evaluate(heap,evaluation,node,args,reference,obj):
    op=node.operation;n=len(obj.items)
    if op in SEARCH:
        needle=args[1];found=-1
        for i in (range(n-1,-1,-1) if op=='lastindexof' else range(n)):
            heap.charge(1,evaluation,node)
            value=obj.items[i]
            if (value is needle if value is None or needle is None else value==needle):found=i;break
        return found>=0 if op=='includes' else found
    if op=='remove':
        i=position(heap,args[1],n,node);result=obj.items[i];size=n-1
        heap.charge((n+size)*(3 if is_object(obj.element) else 1),evaluation,node)
        def build():
            values=obj.items.copy();values.pop(i);return values
        replace_buffer(heap,reference,obj,size,obj.string_bytes-string_bytes(result),evaluation,node,build)
        return result
    if op=='insert':
        i=position(heap,args[1],n,node,insert=True);size=n+1
        heap.capacity(len(heap.objects),heap.cells+1,size,heap.bytes,node)
        heap.charge((n+size)*(3 if is_object(obj.element) else 1),evaluation,node)
        def build():
            values=obj.items.copy();values.insert(i,args[2]);return values
        replace_buffer(heap,reference,obj,size,obj.string_bytes+string_bytes(args[2]),evaluation,node,build)
    elif op=='reverse':
        if n<2:return None
        heap.charge(2*n*(3 if is_object(obj.element) else 1),evaluation,node)
        replace_buffer(heap,reference,obj,n,obj.string_bytes,evaluation,node,lambda:obj.items[::-1])
    elif op=='fill':
        start,end=args[2],n if args[3] is None else args[3]
        if type(start) is not int or type(end) is not int or not 0<=start<=end<=n:
            heap.error('ARRAY_RANGE','Fill requires defined nonnegative bounds 0 <= from <= to <= size.',node)
        if start==end:return None
        heap.charge(2*n*(3 if is_object(obj.element) else 1),evaluation,node)
        byte_count=obj.string_bytes+sum(string_bytes(args[1])-string_bytes(obj.items[i]) for i in range(start,end))
        def build():
            values=obj.items.copy()
            for i in range(start,end):values[i]=args[1]
            return values
        replace_buffer(heap,reference,obj,n,byte_count,evaluation,node,build)
    elif op=='concat':
        other=heap.object(args[1],node)
        if other.element!=obj.element:heap.error('ARRAY_REFERENCE','Concat requires equal element types.',node)
        if not other.items:return reference
        size=n+len(other.items)
        heap.capacity(len(heap.objects),heap.cells+len(other.items),size,heap.bytes,node)
        heap.charge((n+size)*(3 if is_object(obj.element) else 1),evaluation,node)
        replace_buffer(heap,reference,obj,size,obj.string_bytes+other.string_bytes,evaluation,node,lambda:obj.items+other.items)
        return reference
    return None
