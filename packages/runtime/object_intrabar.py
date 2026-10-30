"""Carry only marked object fields over confirmed ordinary-field baselines."""
from collections import Counter
from packages.contracts import ValueType
from packages.contracts.arrays import MAX_OBJECTS,MAX_CELLS,MAX_STRING_BYTES
from packages.contracts.objects import is_reference as reference_type
from .arrays import ArrayRef,ObjectRef,RecordObject,is_reference,string_bytes
from .array_checkpoints import restore_heap,decode_reference,object_row

def masks(heap):
    return {key:tuple(i for i,f in enumerate(n.fields) if f.intrabar) for key,n in heap.object_types.items()}

def capture(memory,current,base):
    bindings=memory.bindings(base);types=masks(base.heap)
    values={k:v for k,v in current.persistent.items() if k in bindings and bindings[k].mode=='varip'}
    refs={v.object_id:v for v in values.values() if is_reference(v)}
    field_ids={key for key,obj in base.heap.objects.items() if type(obj) is RecordObject and types[obj.element]}
    prior={row['id']:row for row in memory.value['heap']['objects']} if memory.value else {}
    entries={};mapping={};next_id=base.heap.next_id
    for key in sorted(refs.keys()|field_ids):
        original=base.heap.objects.get(key);obj=current.heap.objects.get(key,original)
        if obj is None:raise ValueError('Intrabar object reference has no backing record.')
        target=key if original is not None else next_id
        if original is None:next_id+=1
        mapping[key]=target
        if type(obj) is RecordObject:
            previous=prior.get(key)
            ordinary=(original.items if original is not None else previous['fields'] if previous is not None
                else obj.initial_items or obj.items)
            items=list(ordinary)
            for index in types[obj.element]:items[index]=obj.items[index]
            entries[target]=RecordObject(obj.element,items,sum(map(string_bytes,items)),obj.initial_items if original is None else ())
        else:entries[target]=obj
    allocation=memory.charge(base,list(entries.values()),values)
    try:
        encoded={k:({'array_id':mapping[v.object_id]} if type(v) is ArrayRef else {'object_id':mapping[v.object_id]} if type(v) is ObjectRef else v) for k,v in values.items()}
        rows=[object_row(key,entries[key]) for key in sorted(entries)]
        value={'values':encoded,'field_objects':sorted(field_ids),'heap':{'next_id':next_id,
            'cells':sum(len(obj.items) for obj in entries.values()),'string_bytes':sum(obj.string_bytes for obj in entries.values()),'objects':rows}}
    except BaseException:
        if allocation:allocation.close()
        raise
    if memory.allocation:memory.allocation.close()
    memory.value,memory.allocation=value,allocation
    if current.observer:
        import itertools
        for key,item in itertools.islice(values.items(),16):current.observer.record(current,'varip_carry',value=item,binding_id=key)

def exact(a,b):return type(a) is type(b) and (a.hex()==b.hex() if type(a) is float else a==b)

def decode(memory,base,heap):
    from .engine import value_matches
    value=memory.value;bindings=memory.bindings(base);types=masks(base.heap)
    if type(value) is not dict or value.keys()!={'values','heap','field_objects'} or type(value['values']) is not dict:
        raise ValueError('Invalid object-aware intrabar state.')
    fields=value['field_objects']
    expected={key for key,obj in base.heap.objects.items() if type(obj) is RecordObject and types[obj.element]}
    if type(fields) is not list or any(type(i) is not int for i in fields) or fields!=sorted(expected):
        raise ValueError('Intrabar field roots differ from confirmed nominal objects.')
    heap=restore_heap(value['heap'],base.program.identity.language_version,heap=heap)
    values={}
    for key,raw in value['values'].items():
        binding=bindings.get(key)
        if binding is None or binding.mode!='varip':raise ValueError('Intrabar state contains a non-varip binding.')
        typ=binding.value_type
        item=decode_reference(raw,typ.base,heap) if reference_type(typ.base) else raw
        if not value_matches(item,typ,base.program.identity.language_version,missing_string=True):raise ValueError('Invalid intrabar root value.')
        values[key]=item
    if not {key for key in base.persistent if bindings[key].mode=='varip'}<=values.keys():raise ValueError('Intrabar state loses confirmed varip bindings.')
    used={v.object_id for v in values.values() if is_reference(v)}
    if used|expected!=heap.objects.keys():raise ValueError('Intrabar heap has missing or unowned records.')
    new=sorted(heap.objects.keys()-base.heap.objects.keys())
    if new!=list(range(base.heap.next_id,base.heap.next_id+len(new))) or heap.next_id!=base.heap.next_id+len(new):
        raise ValueError('Intrabar heap identities differ from the confirmed allocation boundary.')
    for key,obj in heap.objects.items():
        original=base.heap.objects.get(key)
        if original is not None and (type(obj) is not type(original) or obj.element!=original.element):raise ValueError('Intrabar record changes a confirmed nominal type.')
        if type(obj) is RecordObject:
            if original is not None and obj.initial_items!=original.initial_items:raise ValueError('Intrabar record changes confirmed allocation metadata.')
            ordinary=original.items if original is not None else obj.initial_items or obj.items
            if any(not exact(obj.items[i],ordinary[i]) for i in range(len(obj.items)) if i not in types[obj.element]):
                raise ValueError('An ordinary object field escaped intrabar rollback.')
    objects={**base.heap.objects,**heap.objects};references=base.heap.references.copy()
    roots=Counter(v for v in {**base.persistent,**values}.values() if is_reference(v))
    for ref,n in (base.heap.roots-roots).items():references[ref.object_id]-=n
    for ref,n in (roots-base.heap.roots).items():references[ref.object_id]=references.get(ref.object_id,0)+n
    retained=[obj for key,obj in objects.items() if references.get(key,0)>0]
    if len(retained)>MAX_OBJECTS or sum(len(o.items) for o in retained)>MAX_CELLS or sum(o.string_bytes for o in retained)>MAX_STRING_BYTES:
        raise ValueError('Combined confirmed and intrabar records exceed their shared budget.')
    return values,heap
