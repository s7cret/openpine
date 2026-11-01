"""Typed ordered maps on the shared finite reference heap."""
from dataclasses import dataclass
import hashlib
from packages.contracts.maps import MAX_PAIRS,base
from packages.contracts.objects import is_object
from packages.contracts import ValueType
from .arrays import ArrayObject,ObjectRef,string_bytes
from .history_memory import Allocation

@dataclass(frozen=True)
class MapRef:
    object_id:int
    key_type:str
    element:str

@dataclass
class MapObject(ArrayObject):
    key_type:str
    positions:dict

class MapValues:
    """Non-owning graph edge view; GC does not materialize value buffers."""
    def __init__(self,items):self.items=items
    def __len__(self):return len(self.items)//2
    def __iter__(self):
        for i in range(1,len(self.items),2):yield self.items[i]

def token(value):
    # Typed maps have one key family. Canonical missing/zero keys and a digest
    # prevent attacker-chosen integer hashes from producing quadratic lookups.
    raw=b'n' if value is None else ('f'+(0.0 if value==0 else value).hex()).encode() if type(value) is float else ('b'+str(value)).encode() if type(value) is bool else ('i'+str(value)).encode() if type(value) is int else b's'+value.encode('utf-8')
    return hashlib.sha256(raw).digest(),raw

def object(heap,ref,node):
    if ref is None:heap.error('MAP_NA','Cannot access a missing map.',node)
    obj=heap.objects.get(getattr(ref,'object_id',None))
    if not heap.allow_maps or type(ref) is not MapRef or type(obj) is not MapObject or (obj.key_type,obj.element)!=(ref.key_type,ref.element):heap.error('MAP_REFERENCE','Map reference/type does not belong to this evaluation.',node)
    return obj

def reserve(allocation,size,bytes_,label):
    allocation.reserve(1,label,'array_object')
    allocation.reserve(size,label,'array_cell')
    allocation.reserve(size*2,label,'reference')
    allocation.reserve(size*80+4*bytes_,label,'byte')

def sync(heap,key,size,bytes_):
    if heap.account is None:return
    from packages.contracts.memory import charge
    total=charge('array_object')+charge('array_cell',size)+charge('reference',size*2)+size*80+4*bytes_
    allocation=heap.allocations.get(key);label=heap.owner+' / map '+str(key)
    heap.account.check(max(0,total-(allocation.bytes[0] if allocation else 0)),label,'array')
    if allocation is None:allocation=heap.allocations[key]=Allocation(heap.account);allocation.label=label
    allocation.resize(1,allocation.label,'array_object');allocation.resize(size,allocation.label,'array_cell')
    allocation.resize(size*2,allocation.label,'reference');allocation.resize(size*80+4*bytes_,allocation.label,'byte')

def publish(heap,evaluation,node,ref,obj,size,bytes_,build):
    old_size=len(obj.items) if obj is not None else 0;old_bytes=obj.string_bytes if obj is not None else 0
    if size%2 or size//2>MAX_PAIRS:heap.error('MAP_STORAGE','Map pair budget exceeded.',node)
    heap.capacity(len(heap.objects)+int(ref is None),heap.cells-old_size+size,size,heap.bytes-old_bytes+bytes_,node)
    if ref is None and heap.next_id>=2**63:heap.error('MAP_STORAGE','Reference identity space exhausted.',node)
    heap.charge(1+old_size+size*3+(bytes_+old_bytes)//64,evaluation,node)
    temporary=Allocation(heap.account) if heap.account is not None else None
    try:
        if temporary:reserve(temporary,size,bytes_,heap.owner+' / map staging')
        items=build();positions={token(items[i]):i for i in range(0,len(items),2)}
        if len(items)!=size or len(positions)*2!=size:heap.error('MAP_KEY','Duplicate or invalid staged map keys.',node)
        if is_object(node.element):
            for value in items[1::2]:
                if value is not None:
                    if type(value) is not ObjectRef or value.type_id!=node.element:heap.error('MAP_REFERENCE','Map value nominal type differs.',node)
                    heap.record(value,node)
        if evaluation.cancelled and evaluation.cancelled():evaluation.work.error('CANCELLED','Execution cancelled before publishing map.',node,'EXECUTION_CANCELLED')
        if temporary:temporary.close();temporary=None
        key=heap.next_id if ref is None else ref.object_id
        sync(heap,key,size,bytes_)
        if is_object(node.element):
            for value in items[1::2]:heap.retain(value)
            if obj is not None:
                for value in obj.items[1::2]:heap.release(value)
        heap.objects[key]=MapObject(node.element,items,bytes_,node.key_type,positions)
        heap.owned.add(key);heap.borrowed.pop(key,None)
        heap.cells+=size-old_size;heap.bytes+=bytes_-old_bytes
        if ref is None:
            heap.next_id+=1;heap.references[key]=0;heap.garbage.add(key)
            ref=MapRef(key,node.key_type,node.element)
        return ref
    finally:
        if temporary:temporary.close()

def missing(element,version):return False if element=='bool' and version==6 else None

def evaluate(evaluation,node):
    heap=evaluation.heap;heap.owner=evaluation.storage_owner+' / maps / '+str(evaluation.context_id)
    args=[None]*len(node.arguments)
    for i in node.evaluation_order:args[i]=evaluation.evaluate(node.arguments[i].expression)
    op=node.operation
    if op=='is_na':return args[0] is None
    if op=='new':return publish(heap,evaluation,node,None,None,0,0,lambda:[])
    ref=args[0];obj=object(heap,ref,node);n=len(obj.items)
    if op=='size':return n//2
    if op=='copy':return publish(heap,evaluation,node,None,None,n,obj.string_bytes,obj.items.copy)
    if op in ('keys','values'):
        offset=0 if op=='keys' else 1;element=node.key_type if offset==0 else node.element
        heap.charge(n+obj.string_bytes//64+1,evaluation,node)
        bytes_=sum(string_bytes(obj.items[i]) for i in range(offset,n,2))
        heap.capacity(len(heap.objects)+1,heap.cells+n//2,n//2,heap.bytes+bytes_,node)
        temporary=Allocation(heap.account) if heap.account is not None else None
        try:
            if temporary:
                temporary.reserve(1,heap.owner+' map array staging','array_object')
                temporary.reserve(n//2,heap.owner+' map array staging','reference' if is_object(element) else 'array_cell')
                temporary.reserve(4*bytes_,heap.owner+' map array staging','byte')
            values=obj.items[offset::2]
            if evaluation.cancelled and evaluation.cancelled():evaluation.work.error('CANCELLED','Execution cancelled before publishing map array.',node,'EXECUTION_CANCELLED')
            if temporary:temporary.close();temporary=None
            return heap.create(element,values,bytes_,evaluation,node)
        finally:
            if temporary:temporary.close()
    if op in ('get','contains','put','remove'):
        key=args[1];heap.charge(1+string_bytes(key)//64,evaluation,node)
        index=obj.positions.get(token(key));previous=missing(node.element,heap.version) if index is None else obj.items[index+1]
        if op=='get':return previous
        if op=='contains':return index is not None
        if op=='remove':
            if index is None:return previous
            size=n-2;bytes_=obj.string_bytes-string_bytes(obj.items[index])-string_bytes(previous)
            def build():return obj.items[:index]+obj.items[index+2:]
        else:
            size=n+2 if index is None else n
            bytes_=obj.string_bytes+string_bytes(args[2])+(string_bytes(key) if index is None else -string_bytes(obj.items[index+1]))
            def build():
                items=obj.items.copy()
                if index is None:items.extend((key,args[2]))
                else:items[index+1]=args[2]
                return items
        publish(heap,evaluation,node,ref,obj,size,bytes_,build)
        return previous
    if op=='clear':
        publish(heap,evaluation,node,ref,obj,0,0,lambda:[]);return None
    if op=='put_all':
        other=object(heap,args[1],node)
        if (other.key_type,other.element)!=(obj.key_type,obj.element):heap.error('MAP_TYPE','Map types must match.',node)
        # The plan and complete staged copy are both bounded and accounted;
        # self-merge reads the original slots, never partially published data.
        heap.charge(len(other.items)+other.string_bytes//64+1,evaluation,node)
        additions=sum(token(other.items[i]) not in obj.positions for i in range(0,len(other.items),2))
        size=n+2*additions;bytes_=obj.string_bytes
        for i in range(0,len(other.items),2):
            at=obj.positions.get(token(other.items[i]));bytes_+=string_bytes(other.items[i+1])
            bytes_+=string_bytes(other.items[i]) if at is None else -string_bytes(obj.items[at+1])
        def build():
            items=obj.items.copy();positions=obj.positions.copy()
            for i in range(0,len(other.items),2):
                key,value=other.items[i:i+2];digest=token(key);at=positions.get(digest)
                if at is None:positions[digest]=len(items);items.extend((key,value))
                else:items[at+1]=value
            return items
        publish(heap,evaluation,node,ref,obj,size,bytes_,build);return None
    heap.error('MAP_OPERATION','Unknown map operation.',node)
