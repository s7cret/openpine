"""Bounded row-major matrices; shared IDs, graph ownership and staged writes."""
from dataclasses import dataclass
from packages.contracts.matrices import MAX_DIMENSION
from packages.contracts.arrays import MAX_LENGTH
from packages.contracts.objects import is_object
from .arrays import ArrayObject,ObjectRef,string_bytes
from .history_memory import Allocation

@dataclass(frozen=True)
class MatrixRef:
    object_id:int
    element:str

@dataclass
class MatrixObject(ArrayObject):
    rows:int
    columns:int

def shape(rows,columns):return type(rows) is int and type(columns) is int and 0<=rows<=MAX_DIMENSION and 0<=columns<=MAX_DIMENSION and rows*columns<=MAX_LENGTH
def missing(t,v):return False if t=='bool' and v==6 else None

def object(heap,ref,node):
    if ref is None:heap.error('MATRIX_NA','Cannot access a missing matrix.',node)
    obj=heap.objects.get(getattr(ref,'object_id',None))
    if not heap.allow_matrices or type(ref) is not MatrixRef or type(obj) is not MatrixObject or obj.element!=ref.element:heap.error('MATRIX_REFERENCE','Matrix reference/type does not belong to this evaluation.',node)
    return obj

def reserve(a,t,n,bytes_,label,*,matrix=True):
    a.reserve(1,label,'array_object');a.reserve(n,label,'reference' if is_object(t) else 'array_cell')
    if matrix:a.reserve(2,label,'reference')
    a.reserve(4*bytes_,label,'byte')

def sync(heap,key,t,n,bytes_):
    if heap.account is None:return
    from packages.contracts.memory import charge
    kind='reference' if is_object(t) else 'array_cell';label=heap.owner+' / matrix '+str(key)
    total=charge('array_object')+charge(kind,n)+charge('reference',2)+4*bytes_
    a=heap.allocations.get(key);heap.account.check(max(0,total-(a.bytes[0] if a else 0)),label,'array')
    if a is None:a=heap.allocations[key]=Allocation(heap.account);a.label=label
    a.resize(1,a.label,'array_object');a.resize(0 if is_object(t) else n,a.label,'array_cell')
    a.resize(2+(n if is_object(t) else 0),a.label,'reference');a.resize(4*bytes_,a.label,'byte')

def publish(e,node,ref,rows,columns,bytes_,build,*,out_size=None,out_bytes=0):
    heap=e.heap;t=node.element;old=object(heap,ref,node) if ref is not None else None
    if not shape(rows,columns):heap.error('MATRIX_SHAPE','Matrix dimensions/product exceed the admitted bounds.',node)
    n=rows*columns;old_n=len(old.items) if old else 0;old_bytes=old.string_bytes if old else 0
    extra=int(ref is None)+int(out_size is not None)
    heap.capacity(len(heap.objects)+extra,heap.cells-old_n+n+(out_size or 0),max(n,out_size or 0),heap.bytes-old_bytes+bytes_+out_bytes,node)
    if heap.next_id+extra>=2**63:heap.error('MATRIX_STORAGE','Reference identity space exhausted.',node)
    heap.charge(1+old_n+n*2+(out_size or 0)+(bytes_+out_bytes+old_bytes)//64,e,node)
    scratch=Allocation(heap.account) if heap.account is not None else None
    try:
        if scratch:
            reserve(scratch,t,n,bytes_,heap.owner+' / matrix staging')
            if out_size is not None:reserve(scratch,t,out_size,out_bytes,heap.owner+' / removed vector staging',matrix=False)
        staged=build();items,removed=staged if out_size is not None else (staged,None)
        if len(items)!=n or out_size is not None and len(removed)!=out_size:heap.error('MATRIX_SHAPE','Staged shape differs.',node)
        if is_object(t):
            for buffer in (items,removed or ()):
                for v in buffer:
                    if v is not None:
                        if type(v) is not ObjectRef or v.type_id!=t:heap.error('MATRIX_REFERENCE','Foreign nominal value.',node)
                        heap.record(v,node)
        if e.cancelled and e.cancelled():e.work.error('CANCELLED','Cancelled before publishing matrix.',node,'EXECUTION_CANCELLED')
        # Reserving the entire target and output while the old buffer is live
        # proves both final allocations fit before either can become visible.
        if heap.allow_graphs and ref is not None:
            from .graph_baselines import protect
            protect(heap,ref.object_id)
        if scratch:scratch.close();scratch=None
        key=heap.next_id if ref is None else ref.object_id
        sync(heap,key,t,n,bytes_)
        if is_object(t):
            for v in items:heap.retain(v)
            if old:
                for v in old.items:heap.release(v)
        heap.objects[key]=MatrixObject(t,items,bytes_,rows,columns)
        heap.owned.add(key);heap.borrowed.pop(key,None);heap.cells+=n-old_n;heap.bytes+=bytes_-old_bytes
        if ref is None:
            heap.next_id+=1;heap.references[key]=0;heap.garbage.add(key);ref=MatrixRef(key,t)
            if heap.allow_graphs:
                from .graph_baselines import remember
                remember(heap,key,heap.objects[key])
        if out_size is not None:return heap.create(t,removed,out_bytes,e,node)
        return ref
    finally:
        if scratch:scratch.close()

def array(e,node,t,n,bytes_,build):
    heap=e.heap;heap.charge(n+bytes_//64+1,e,node)
    heap.capacity(len(heap.objects)+1,heap.cells+n,n,heap.bytes+bytes_,node)
    if heap.next_id+1>=2**63:heap.error('MATRIX_STORAGE','Reference identity space exhausted.',node)
    scratch=Allocation(heap.account) if heap.account is not None else None
    try:
        if scratch:reserve(scratch,t,n,bytes_,heap.owner+' / matrix vector staging',matrix=False)
        items=build()
        if e.cancelled and e.cancelled():e.work.error('CANCELLED','Cancelled before publishing matrix vector.',node,'EXECUTION_CANCELLED')
        if scratch:scratch.close();scratch=None
        return heap.create(t,items,bytes_,e,node)
    finally:
        if scratch:scratch.close()

def index(heap,value,size,node,*,insertion=False):
    if type(value) is not int or not 0<=value<size+int(insertion):heap.error('MATRIX_INDEX','Matrix index must be defined, nonnegative and in bounds.',node)
    return value

def row_array(e,node,obj,row):
    index(e.heap,row,obj.rows,node);start=row*obj.columns;stop=start+obj.columns
    e.heap.charge(obj.columns+1,e,node)
    bytes_=sum(string_bytes(obj.items[i]) for i in range(start,stop))
    return array(e,node,obj.element,obj.columns,bytes_,lambda:obj.items[start:stop])

def evaluate(e,node):
    heap=e.heap;heap.owner=e.storage_owner+' / matrices / '+str(e.context_id);args=[None]*len(node.arguments)
    for i in node.evaluation_order:args[i]=e.evaluate(node.arguments[i].expression)
    op=node.operation;t=node.element
    if op=='is_na':return args[0] is None
    if op=='new':
        r,c,v=args
        if not shape(r,c):heap.error('MATRIX_SHAPE','Matrix dimensions must be defined, nonnegative and bounded.',node)
        return publish(e,node,None,r,c,r*c*string_bytes(v),lambda:[v]*(r*c))
    ref=args[0];obj=object(heap,ref,node);r,c=obj.rows,obj.columns;n=len(obj.items)
    defaults={a.parameter_id for a in node.arguments if a.origin=='default'}
    if op in ('rows','columns','elements_count'):return r if op=='rows' else c if op=='columns' else n
    if op in ('get','set'):
        at=index(heap,args[1],r,node)*c+index(heap,args[2],c,node)
        if op=='get':return obj.items[at]
        v=args[3];bytes_=obj.string_bytes-string_bytes(obj.items[at])+string_bytes(v)
        def build():
            items=obj.items.copy();items[at]=v;return items
        publish(e,node,ref,r,c,bytes_,build);return None
    if op=='copy':return publish(e,node,None,r,c,obj.string_bytes,obj.items.copy)
    if op=='transpose':
        heap.charge(r+c+1,e,node)
        return publish(e,node,None,c,r,obj.string_bytes,lambda:[obj.items[i*c+j] for j in range(c) for i in range(r)])
    if op=='reshape':
        nr,nc=args[1:]
        if not shape(nr,nc) or nr*nc!=n:heap.error('MATRIX_SHAPE','Reshape must preserve element count.',node)
        publish(e,node,ref,nr,nc,obj.string_bytes,obj.items.copy);return None
    if op=='row':return row_array(e,node,obj,args[1])
    if op=='col':
        col=index(heap,args[1],c,node);heap.charge(r+1,e,node);bytes_=sum(string_bytes(obj.items[i*c+col]) for i in range(r))
        return array(e,node,t,r,bytes_,lambda:[obj.items[i*c+col] for i in range(r)])
    if op=='fill':
        v,fr,tr,fc,tc=args[1:];tr=r if 'to_row' in defaults else tr;tc=c if 'to_column' in defaults else tc
        if any(type(x) is not int for x in (fr,tr,fc,tc)) or not 0<=fr<=tr<=r or not 0<=fc<=tc<=c:heap.error('MATRIX_INDEX','Invalid half-open fill rectangle.',node)
        if fr==tr or fc==tc:return None
        heap.charge((tr-fr)*(tc-fc)+1,e,node)
        removed=sum(string_bytes(obj.items[i*c+j]) for i in range(fr,tr) for j in range(fc,tc))
        bytes_=obj.string_bytes-removed+(tr-fr)*(tc-fc)*string_bytes(v)
        def build():
            items=obj.items.copy()
            for i in range(fr,tr):
                for j in range(fc,tc):items[i*c+j]=v
            return items
        publish(e,node,ref,r,c,bytes_,build);return None
    if op in ('swap_rows','swap_columns'):
        byrow=op=='swap_rows';a=index(heap,args[1],r if byrow else c,node);b=index(heap,args[2],r if byrow else c,node)
        def build():
            items=obj.items.copy()
            for i in range(c if byrow else r):
                x,y=(a*c+i,b*c+i) if byrow else (i*c+a,i*c+b);items[x],items[y]=items[y],items[x]
            return items
        publish(e,node,ref,r,c,obj.string_bytes,build);return None
    if op in ('add_row','add_col'):
        byrow=op=='add_row';name='row' if byrow else 'column';at=(r if byrow else c) if name in defaults else args[1]
        index(heap,at,r if byrow else c,node,insertion=True)
        source=None if 'array_id' in defaults else heap.object(args[2],node)
        if source is not None and source.element!=t:heap.error('MATRIX_TYPE','Inserted array element type differs.',node)
        width=c if byrow else r
        if r==c==0 and source is not None:width=len(source.items)
        if source is not None and len(source.items)!=width:heap.error('MATRIX_ARRAY_SIZE','Inserted array size differs from matrix extent.',node)
        nr,nc=(r+1,width) if byrow else (width,c+1)
        v=missing(t,heap.version);extra_bytes=source.string_bytes if source is not None else width*string_bytes(v)
        heap.charge(width+1,e,node)
        def build():
            values=list(source.items) if source is not None else [v]*width
            if byrow:return obj.items[:at*c]+values+obj.items[at*c:]
            return [values[i] if j==at else obj.items[i*c+j-int(j>at)] for i in range(nr) for j in range(nc)]
        publish(e,node,ref,nr,nc,obj.string_bytes+extra_bytes,build);return None
    if op in ('remove_row','remove_col'):
        byrow=op=='remove_row';name='row' if byrow else 'column';at=(r-1 if byrow else c-1) if name in defaults else args[1]
        index(heap,at,r if byrow else c,node);width=c if byrow else r
        heap.charge(n+1,e,node)
        removed_bytes=sum(string_bytes(obj.items[at*c+j] if byrow else obj.items[j*c+at]) for j in range(width))
        def build():
            if byrow:return obj.items[:at*c]+obj.items[(at+1)*c:],obj.items[at*c:(at+1)*c]
            return [v for i,v in enumerate(obj.items) if i%c!=at],[obj.items[i*c+at] for i in range(r)]
        return publish(e,node,ref,r-1 if byrow else r,c if byrow else c-1,obj.string_bytes-removed_bytes,build,out_size=width,out_bytes=removed_bytes)
    heap.error('MATRIX_OPERATION','Unknown matrix operation.',node)
