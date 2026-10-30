"""Atomic carry of array edges and only explicitly intrabar record fields."""
from collections import Counter
from .arrays import ArrayRef,ObjectRef,ArrayObject,RecordObject,is_reference,string_bytes
from .array_checkpoints import restore_heap,decode_reference,encode_reference,object_row
from .reference_graph import children,referenced,external_counts
from .object_intrabar import masks,exact
from packages.contracts.objects import is_reference as reference_type

def capture(memory,current,base):
    bindings=memory.bindings(base);types=masks(base.heap)
    values={k:v for k,v in current.persistent.items() if k in bindings and bindings[k].mode=='varip'}
    field_ids={k for k,o in base.heap.objects.items() if type(o) is RecordObject and types[o.element]}
    objects={**base.heap.objects,**current.heap.objects}
    roots=Counter(v.object_id for v in values.values() if is_reference(v));roots.update(field_ids)
    retained=referenced(objects,roots);mapping={};next_id=base.heap.next_id
    for key in sorted(retained):
        mapping[key]=key if key in base.heap.objects else next_id
        if key not in base.heap.objects:next_id+=1
    previous={o['id']:o for o in memory.value['heap']['objects']} if memory.value else {}
    entries={}
    def remap(ref):return ObjectRef(mapping[ref.object_id],ref.type_id) if type(ref) is ObjectRef else ArrayRef(mapping[ref.object_id],ref.element) if type(ref) is ArrayRef else ref
    for key in sorted(retained):
        obj=objects[key];original=base.heap.objects.get(key)
        current.heap.charge(len(obj.items)+1,current,base.program.statements[0])
        if type(obj) is RecordObject:
            prior=previous.get(key)
            ordinary=original.items if original is not None else prior['fields'] if prior is not None else obj.initial_items or obj.items
            items=list(ordinary)
            for i in types[obj.element]:items[i]=obj.items[i]
            entries[mapping[key]]=RecordObject(obj.element,items,sum(map(string_bytes,items)),obj.initial_items if original is None else ())
        else:
            items=[remap(v) for v in obj.items] if children(obj) else obj.items.copy()
            entries[mapping[key]]=ArrayObject(obj.element,items,obj.string_bytes)
    allocation=memory.charge(base,list(entries.values()),values)
    try:
        value={'values':{k:encode_reference(remap(v)) for k,v in values.items()},'field_objects':sorted(field_ids),
            'heap':{'next_id':next_id,'cells':sum(len(o.items) for o in entries.values()),'string_bytes':sum(o.string_bytes for o in entries.values()),
                'objects':[object_row(k,entries[k]) for k in sorted(entries)]}}
    except BaseException:
        if allocation:allocation.close()
        raise
    if memory.allocation:memory.allocation.close()
    memory.value,memory.allocation=value,allocation
    if current.observer:
        import itertools
        for key,item in itertools.islice(values.items(),16):current.observer.record(current,'varip_carry',value=item,binding_id=key)

def decode(memory,base,heap):
    from .engine import value_matches
    value=memory.value;bindings=memory.bindings(base);types=masks(base.heap)
    if type(value) is not dict or value.keys()!={'values','heap','field_objects'} or type(value['values']) is not dict:raise ValueError('Invalid object-array intrabar state.')
    expected={k for k,o in base.heap.objects.items() if type(o) is RecordObject and types[o.element]}
    fields=value['field_objects']
    if type(fields) is not list or any(type(k) is not int for k in fields) or fields!=sorted(expected):raise ValueError('Intrabar field roots differ.')
    heap=restore_heap(value['heap'],base.program.identity.language_version,heap=heap);values={}
    for key,raw in value['values'].items():
        b=bindings.get(key)
        if b is None or b.mode!='varip':raise ValueError('Unexpected intrabar binding.')
        item=decode_reference(raw,b.value_type.base,heap) if reference_type(b.value_type.base) else raw
        if not value_matches(item,b.value_type,base.program.identity.language_version,missing_string=True):raise ValueError('Invalid intrabar value.')
        values[key]=item
    if not {k for k in base.persistent if bindings[k].mode=='varip'}<=values.keys():raise ValueError('Missing confirmed varip roots.')
    roots=Counter(v.object_id for v in values.values() if is_reference(v));roots.update(expected)
    if referenced(heap.objects,roots).keys()!=heap.objects.keys():raise ValueError('Unowned partial graph records.')
    new=sorted(heap.objects.keys()-base.heap.objects.keys())
    if new!=list(range(base.heap.next_id,base.heap.next_id+len(new))) or heap.next_id!=base.heap.next_id+len(new):raise ValueError('Intrabar allocation boundary differs.')
    for key,obj in heap.objects.items():
        original=base.heap.objects.get(key)
        if original is not None and (type(obj) is not type(original) or obj.element!=original.element):raise ValueError('Changed confirmed nominal layout.')
        if type(obj) is RecordObject:
            if original is None and len(obj.initial_items)!=len(obj.items):raise ValueError('Missing constructor baseline.')
            if original is not None and obj.initial_items!=original.initial_items:raise ValueError('Changed confirmed baseline.')
            ordinary=original.items if original is not None else obj.initial_items
            if any(not exact(obj.items[i],ordinary[i]) for i in range(len(obj.items)) if i not in types[obj.element]):raise ValueError('Ordinary field escaped rollback.')
    objects={**base.heap.objects,**heap.objects}
    refs=referenced(objects,external_counts(base.heap,{**base.persistent,**values}))
    from packages.contracts.arrays import MAX_OBJECTS,MAX_CELLS,MAX_STRING_BYTES
    live=[objects[k] for k in refs]
    if len(live)>MAX_OBJECTS or sum(len(o.items) for o in live)>MAX_CELLS or sum(o.string_bytes for o in live)>MAX_STRING_BYTES:raise ValueError('Combined graph exceeds shared quotas.')
    return values,heap

def apply(memory,base):
    values,partial=memory.decode(base);heap=base.heap;persistent={**base.persistent,**values}
    try:
        objects={**heap.objects,**partial.objects};refs=referenced(objects,external_counts(heap,persistent))
        for key,obj in partial.objects.items():
            old=heap.allocations.pop(key,None)
            if old:old.close()
            heap.borrowed.pop(key,None)
            if key in partial.allocations:heap.allocations[key]=partial.allocations.pop(key)
            heap.objects[key]=obj;heap.owned.add(key)
            if type(obj) is RecordObject:
                heap.intrabar_dirty.add(key)
                if obj.initial_items:heap.initial_records.add(key)
        for key in list(heap.objects):
            if key not in refs:
                old=heap.allocations.pop(key,None)
                if old:old.close()
                heap.borrowed.pop(key,None);del heap.objects[key];heap.owned.discard(key);heap.initial_records.discard(key)
        heap.references=dict(refs);heap.garbage=set();heap.roots=Counter(v for v in persistent.values() if is_reference(v))
        heap.next_id=partial.next_id;heap.cells=sum(len(o.items) for o in heap.objects.values());heap.bytes=sum(o.string_bytes for o in heap.objects.values())
        base.persistent=persistent
    finally:partial.close()
