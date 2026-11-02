"""Select rollback fields before following edges; carry is an identity property."""
from collections import Counter
from .arrays import ArrayObject,ArrayRef,ObjectRef,RecordObject,is_reference,string_bytes
from .maps import MapObject,MapRef
from .matrices import MatrixObject,MatrixRef
from .array_slice import SliceObject
from .reference_graph import children,referenced,external_counts
from .array_checkpoints import object_row,encode_reference
from .object_intrabar import masks
from .history_memory import Allocation

def walk(objects,values,field_ids,types,origins,select,charge=None):
    pending=[];scheduled={}
    def enqueue(key,carry):
        if key not in objects:raise ValueError('Missing intrabar graph backing object.')
        if type(objects[key]) is RecordObject:carry=True
        if key in scheduled and (scheduled[key] or not carry):return
        scheduled[key]=carry;pending.append((key,carry))
    for v in values.values():
        if is_reference(v):enqueue(v.object_id,True)
    for key in field_ids:enqueue(key,True)
    modes={};entries={}
    while pending:
        key,carry=pending.pop()
        if not carry and scheduled[key]:continue
        if key not in objects:raise ValueError('Missing intrabar graph backing object.')
        if type(objects[key]) is RecordObject:carry=True
        if key in modes and (modes[key] or not carry):continue
        modes[key]=carry;obj=select(key,carry);entries[key]=obj
        if charge:charge(len(obj.items)+len(getattr(obj,'initial_items',()))+1)
        if type(obj) is RecordObject:
            for i,v in enumerate(obj.items):
                if is_reference(v):enqueue(v.object_id,i in types[obj.element])
            for v in obj.initial_items:
                if is_reference(v):enqueue(v.object_id,False)
        else:
            for v in children(obj):
                if v is not None:enqueue(v.object_id,carry)
        if key in origins:
            for v in children(origins[key]):
                if v is not None:enqueue(v.object_id,False)
    return entries,modes

def remap_object(obj,ref):
    if type(obj) is RecordObject:
        items=[ref(v) for v in obj.items];initial=tuple(ref(v) for v in obj.initial_items)
        return RecordObject(obj.element,items,sum(map(string_bytes,items)),initial)
    if type(obj) is MatrixObject:return MatrixObject(obj.element,[ref(v) for v in obj.items],obj.string_bytes,obj.rows,obj.columns)
    if type(obj) is MapObject:return MapObject(obj.element,[ref(v) if i%2 else v for i,v in enumerate(obj.items)],obj.string_bytes,obj.key_type,obj.positions.copy())
    if type(obj) is SliceObject:return SliceObject(obj.element,ref(obj.parent),obj.start,obj.end)
    return ArrayObject(obj.element,[ref(v) for v in obj.items],obj.string_bytes)

def capture(memory,current,base):
    bindings=memory.bindings(base);types=masks(base.heap)
    values={k:v for k,v in current.persistent.items() if k in bindings and bindings[k].mode=='varip'}
    fields={k for k,o in base.heap.objects.items() if type(o) is RecordObject and types[o.element]}
    objects={**base.heap.objects,**current.heap.objects};origins=current.heap.origins
    scratch=Allocation(base.heap.account) if base.heap.account else None
    allocation=None
    try:
        if scratch:
            scratch.reserve(8*len(objects),'intrabar graph traversal','index')
            scratch.reserve(2*sum(len(o.items) for o in objects.values() if type(o) is RecordObject),'intrabar graph field staging','reference')
        def select(key,carry):
            obj=objects[key];original=base.heap.objects.get(key)
            if type(obj) is not RecordObject:
                if carry:return obj
                result=original or origins.get(key)
                if result is None:raise ValueError('Missing constructor collection baseline.')
                return result
            ordinary=original.items if original is not None else obj.initial_items
            if len(ordinary)!=len(obj.items):raise ValueError('Missing constructor field baseline.')
            items=[obj.items[i] if i in types[obj.element] else v for i,v in enumerate(ordinary)]
            return RecordObject(obj.element,items,sum(map(string_bytes,items)),obj.initial_items if original is None else ())
        entries,_=walk(objects,values,fields,types,origins,select,lambda n:current.heap.charge(n,current,base.program.statements[0]))
        selected_origins={k:origins[k] for k in entries if k not in base.heap.objects and type(entries[k]) is not RecordObject}
        roots=Counter(v.object_id for v in values.values() if is_reference(v));roots.update(fields)
        retained=referenced(entries,roots,origins=selected_origins)
        entries={k:entries[k] for k in retained};selected_origins={k:o for k,o in selected_origins.items() if k in retained}
        mapping={};next_id=base.heap.next_id
        for key in sorted(entries):
            mapping[key]=key if key in base.heap.objects else next_id
            if key not in base.heap.objects:next_id+=1
        def ref(v):
            if not is_reference(v):return v
            key=mapping[v.object_id]
            return ObjectRef(key,v.type_id) if type(v) is ObjectRef else ArrayRef(key,v.element) if type(v) is ArrayRef else MatrixRef(key,v.element) if type(v) is MatrixRef else MapRef(key,v.key_type,v.element)
        allocation=memory.charge(base,[*entries.values(),*selected_origins.values()],values)
        output={mapping[k]:remap_object(o,ref) for k,o in entries.items()}
        initial={mapping[k]:remap_object(o,ref) for k,o in selected_origins.items()}
        value={'values':{k:encode_reference(ref(v)) for k,v in values.items()},'field_objects':sorted(fields),
            'heap':{'schema_version':'openpine.array-heap.5','next_id':next_id,'cells':sum(len(o.items) for o in output.values()),
                'string_bytes':sum(o.string_bytes for o in output.values()),'objects':[object_row(k,output[k]) for k in sorted(output)],
                'origins':[object_row(k,initial[k]) for k in sorted(initial)]}}
    except BaseException:
        if allocation:allocation.close()
        raise
    finally:
        if scratch:scratch.close()
    if memory.allocation:memory.allocation.close()
    memory.value,memory.allocation=value,allocation
    if current.observer:
        import itertools
        for k,v in itertools.islice(values.items(),16):current.observer.record(current,'varip_carry',value=v,binding_id=k)

def decode(memory,base,heap):
    from .object_array_intrabar import decode as decode_common
    from packages.contracts.wire import canonical
    values,heap=decode_common(memory,base,heap)
    expected={k for k,o in heap.objects.items() if k not in base.heap.objects and type(o) is not RecordObject}
    if heap.origins.keys()!=expected:raise ValueError('Constructor collection baseline inventory differs.')
    fields=memory.value['field_objects'];types=masks(base.heap)
    _,modes=walk(heap.objects,values,fields,types,heap.origins,lambda k,c:heap.objects[k])
    for key,carry in modes.items():
        obj=heap.objects[key]
        if carry or type(obj) is RecordObject:continue
        original=base.heap.objects.get(key,heap.origins.get(key))
        if original is None or canonical(object_row(key,obj))!=canonical(object_row(key,original)):
            raise ValueError('An ordinary collection escaped intrabar rollback.')
    return values,heap

def apply(memory,base):
    values,partial=memory.decode(base);heap=base.heap;persistent={**base.persistent,**values}
    try:
        objects={**heap.objects,**partial.objects};origins={**heap.origins,**partial.origins}
        refs=referenced(objects,external_counts(heap,persistent),origins=origins)
        for key,obj in partial.objects.items():
            old=heap.allocations.pop(key,None)
            if old:old.close()
            heap.borrowed.pop(key,None)
            if key in partial.allocations:heap.allocations[key]=partial.allocations.pop(key)
            heap.objects[key]=obj;heap.owned.add(key)
            if type(obj) is RecordObject:
                heap.intrabar_dirty.add(key)
                if obj.initial_items:heap.initial_records.add(key)
        from .graph_baselines import discard
        for key,obj in partial.origins.items():
            discard(heap,key,edges=False);heap.origins[key]=obj
            if key in partial.origin_allocations:heap.origin_allocations[key]=partial.origin_allocations.pop(key)
        for key in list(heap.objects):
            if key not in refs:
                old=heap.allocations.pop(key,None)
                if old:old.close()
                discard(heap,key,edges=False);heap.borrowed.pop(key,None);del heap.objects[key]
                heap.owned.discard(key);heap.initial_records.discard(key)
        heap.references=dict(refs);heap.garbage=set();heap.roots=Counter(v for v in persistent.values() if is_reference(v))
        heap.next_id=partial.next_id;heap.cells=sum(len(o.items) for o in heap.objects.values());heap.bytes=sum(o.string_bytes for o in heap.objects.values())
        base.persistent=persistent
    finally:partial.close()
