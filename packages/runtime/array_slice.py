"""Shared positional windows; only root arrays own slots and record edges."""
from dataclasses import dataclass,field
from .arrays import ArrayRef,ArrayObject,string_bytes
from .history_memory import Allocation
from packages.contracts.objects import is_object

@dataclass(frozen=True)
class SliceObject:
    element: str
    parent: ArrayRef
    start: int
    end: int
    items: tuple = field(default=(),init=False)
    string_bytes: int = field(default=0,init=False)

class WindowItems:
    """Read-only access, with no materialization on size/get/search/iteration."""
    def __init__(self,items,start,end):self.source=items;self.start=start;self.end=end
    def __len__(self):return self.end-self.start
    def __getitem__(self,i):
        if isinstance(i,slice):return [self[j] for j in range(*i.indices(len(self)))]
        if i<0:i+=len(self)
        if not 0<=i<len(self):raise IndexError(i)
        return self.source[self.start+i]
    def __iter__(self):
        for i in range(self.start,self.end):yield self.source[i]
    def copy(self):return list(self)
    def __add__(self,other):return [*self,*other]
    def __radd__(self,other):return [*other,*self]

class ArrayWindow:
    def __init__(self,view,root):
        self.view=view;self.element=view.element;self.items=WindowItems(root.items,view.start,view.end)
    @property
    def string_bytes(self):
        # Enum members are closed string tokens in the existing heap codec.
        from packages.contracts.enums import is_enum
        return sum(map(string_bytes,self.items)) if self.element=='string' or is_enum(self.element) else 0

def window(heap,view,node):
    root=heap.objects.get(view.parent.object_id)
    if type(root) is not ArrayObject or root.element!=view.element:
        heap.error('ARRAY_REFERENCE','Slice parent is not a matching root array.',node)
    if not 0<=view.start<=view.end<=len(root.items):
        heap.error('ARRAY_SLICE_BOUNDS','Slice window exceeds the current parent array.',node)
    return ArrayWindow(view,root)

def sync_view(heap,key):
    if heap.account is None:return
    from packages.contracts.memory import charge
    allocation=heap.allocations.get(key);label=heap.owner+' / slice '+str(key)
    total=charge('array_object')+charge('reference')+charge('index',2)
    heap.account.check(max(0,total-(allocation.bytes[0] if allocation else 0)),label,'array')
    if allocation is None:allocation=heap.allocations[key]=Allocation(heap.account);allocation.label=label
    allocation.resize(1,allocation.label,'array_object');allocation.resize(1,allocation.label,'reference');allocation.resize(2,allocation.label,'index')

def create(heap,reference,obj,start,end,evaluation,node):
    if not heap.allow_views:heap.error('ARRAY_SLICE_PROFILE','Shared windows are not admitted for this program.',node)
    if type(start) is not int or type(end) is not int or not 0<=start<end<=len(obj.items):
        heap.error('ARRAY_RANGE','Slice requires defined nonnegative bounds 0 <= from < to <= size.',node)
    heap.capacity(len(heap.objects)+1,heap.cells,end-start,heap.bytes,node)
    if heap.next_id>=2**63:heap.error('ARRAY_STORAGE','Array identity space exhausted.',node)
    if type(obj) is ArrayWindow:parent=obj.view.parent;start+=obj.view.start;end+=obj.view.start
    else:parent=reference
    view=SliceObject(obj.element,parent,start,end)
    heap.charge(1,evaluation,node)
    if evaluation.cancelled and evaluation.cancelled():evaluation.work.error('CANCELLED','Execution cancelled before publishing slice.',node,'EXECUTION_CANCELLED')
    key=heap.next_id;sync_view(heap,key)
    heap.next_id+=1;heap.objects[key]=view;heap.references[key]=0;heap.garbage.add(key);heap.owned.add(key);heap.retain(parent)
    if heap.allow_graphs:
        from .graph_baselines import remember
        remember(heap,key,view)
    return ArrayRef(key,obj.element)

def replace(heap,reference,obj,size,byte_count,evaluation,node,build):
    """Stage the window and full COW root, then publish both records together."""
    from .array_operations import replace_buffer
    view=obj.view;root=heap.objects[view.parent.object_id];n=len(root.items)
    delta=size-len(obj.items);total=n+delta;total_bytes=root.string_bytes-obj.string_bytes+byte_count
    heap.capacity(len(heap.objects),heap.cells+delta,total,heap.bytes-root.string_bytes+total_bytes,node)
    heap.charge((n+total)*(3 if is_object(root.element) else 1),evaluation,node)
    temporary=Allocation(heap.account) if heap.account else None
    try:
        if temporary:
            label=heap.owner+' slice staging'
            temporary.reserve(1,label,'array_object');temporary.reserve(size,label,'reference' if is_object(root.element) else 'array_cell');temporary.reserve(4*byte_count,label,'byte')
            # The view's COW metadata and root replacement must both be admitted.
            temporary.reserve(1,label,'array_object');temporary.reserve(1,label,'reference');temporary.reserve(2,label,'index')
        new_view=SliceObject(view.element,view.parent,view.start,view.start+size)
        def root_build():
            items=build()
            from itertools import chain,islice
            return list(chain(islice(root.items,view.start),items,islice(root.items,view.end,None)))
        # replace_buffer reserves the complete root while our window reservation
        # remains live. No callbacks or fallible work follows the root commit.
        replace_buffer(heap,view.parent,root,total,total_bytes,evaluation,node,root_build)
        if temporary:temporary.close();temporary=None
        sync_view(heap,reference.object_id)
        heap.objects[reference.object_id]=new_view;heap.owned.add(reference.object_id);heap.borrowed.pop(reference.object_id,None)
    finally:
        if temporary:temporary.close()

def mutate(heap,evaluation,node,args,reference,obj):
    op=node.operation;n=len(obj.items);result=None
    if op=='set':
        i=heap.index(args[1],obj,node);size=n;byte_count=obj.string_bytes-string_bytes(obj.items[i])+string_bytes(args[2])
        def build():
            values=obj.items.copy();values[i]=args[2];return values
    elif op in ('push','unshift'):
        size=n+1;byte_count=obj.string_bytes+string_bytes(args[1])
        def build():
            values=obj.items.copy();values.insert(n if op=='push' else 0,args[1]);return values
    elif op in ('pop','shift'):
        if not n:heap.error('ARRAY_EMPTY','Cannot remove an element from an empty array.',node)
        i=n-1 if op=='pop' else 0;result=obj.items[i];size=n-1;byte_count=obj.string_bytes-string_bytes(result)
        def build():
            values=obj.items.copy();values.pop(i);return values
    else:
        size=0;byte_count=0
        def build():return []
    heap.charge(2*n,evaluation,node)
    replace(heap,reference,obj,size,byte_count,evaluation,node,build)
    return result
