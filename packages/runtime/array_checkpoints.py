"""Closed heap codec. Rebuild reference counts from checked durable roots."""

from packages.contracts.arrays import ELEMENTS, ARRAY_BASES, MAX_LENGTH, MAX_OBJECTS, MAX_CELLS, MAX_STRING_BYTES
from packages.runtime.arrays import ArrayHeap, ArrayObject, ArrayRef,ObjectRef,RecordObject,is_reference, string_bytes
from packages.contracts.objects import is_object
from packages.contracts.arrays import is_array


def require(condition):
    if not condition:raise ValueError('Invalid array heap checkpoint.')


def encode_reference(value):
    return {'array_id': value.object_id} if type(value) is ArrayRef else {'object_id':value.object_id} if type(value) is ObjectRef else value

def object_row(key,obj,items=None):
    items=obj.items if items is None else items
    return ({'id':key,'type_id':obj.element,'fields':list(items),'initial':list(obj.initial_items)} if type(obj) is RecordObject
        else {'id':key,'element':obj.element,'refs':[v.object_id if v is not None else None for v in items]} if is_object(obj.element)
        else {'id':key,'element':obj.element,'items':list(items)})


def export_heap(heap,*,streaming=False):
    from packages.contracts.checkpoint_stream import RecordSequence
    keys=sorted(heap.objects)
    def row(i):
        key=keys[i];obj=heap.objects[key]
        return object_row(key,obj)
    return {'next_id':heap.next_id,'cells':heap.cells,'string_bytes':heap.bytes,
        'objects':RecordSequence(len(keys),row) if streaming else [row(i) for i in range(len(keys))]}


def restore_heap(value, version, account=None, heap=None):
    from packages.runtime.engine import value_matches
    from packages.contracts import ValueType
    require(type(value) is dict and value.keys() == {'next_id', 'cells', 'string_bytes', 'objects'})
    require(type(value['next_id']) is int and 1 <= value['next_id'] < 2**63)
    from packages.contracts.checkpoint_stream import RecordSequence
    require(isinstance(value['objects'],(list,RecordSequence)) and len(value['objects']) <= MAX_OBJECTS)
    heap = heap if heap is not None else ArrayHeap(version,account);heap.owner='arrays restore';heap.next_id = value['next_id']
    previous = 0;compact=set()
    for row in value['objects']:
        if type(row) is dict and row.keys()=={'id','type_id','fields','initial'}:
            key,type_id,items,initial=row['id'],row['type_id'],row['fields'],row['initial']
            require(type(key) is int and previous<key<heap.next_id);previous=key
            contract=heap.object_types.get(type_id)
            require(contract is not None and type(items) is list and type(initial) is list and len(items)==len(contract.fields) and len(initial) in (0,len(items)))
            for data in (items,initial):require(all(value_matches(item,ValueType(f.base),version,missing_string=True) for item,f in zip(data,contract.fields,strict=bool(data))))
            byte_count=sum(map(string_bytes,items));heap.cells+=len(items);heap.bytes+=byte_count
            require(heap.cells<=MAX_CELLS and heap.bytes<=MAX_STRING_BYTES)
            heap.sync(key,len(items),byte_count,tuple(initial));heap.objects[key]=RecordObject(type_id,items.copy(),byte_count,tuple(initial))
            if initial:heap.initial_records.add(key)
            heap.references[key]=0;heap.garbage.add(key);heap.owned.add(key)
            continue
        require(type(row) is dict and row.keys() in ({'id','element','items'},{'id','element','refs'}))
        packed='refs' in row
        key, element, items = row['id'], row['element'], row['refs' if packed else 'items']
        require(type(key) is int and previous < key < heap.next_id);previous = key
        from packages.contracts.enums import is_enum
        require(type(element) is str and (element in ELEMENTS or element in heap.object_types or is_enum(element)) and type(items) is list and len(items) <= MAX_LENGTH)
        if packed:
            require(element in heap.object_types and all(v is None or type(v) is int for v in items));compact.add(key)
        if element in ELEMENTS or is_enum(element):require(all(value_matches(item, ValueType(element), version, missing_string=True) for item in items))
        byte_count = sum(map(string_bytes, items))
        heap.cells += len(items);heap.bytes += byte_count
        require(heap.cells <= MAX_CELLS and heap.bytes <= MAX_STRING_BYTES)
        heap.sync(key,len(items),byte_count,element=element)
        # Own all restored buffers; caller-owned JSON cannot mutate live state.
        heap.objects[key] = ArrayObject(element, items.copy(), byte_count)
        heap.references[key] = 0;heap.garbage.add(key);heap.owned.add(key)
        del row,items
    require(type(value['cells']) is int and value['cells'] == heap.cells)
    require(type(value['string_bytes']) is int and value['string_bytes'] == heap.bytes)
    for key,obj in heap.objects.items():
        if type(obj) is ArrayObject and is_object(obj.element):
            obj.items=[decode_reference({'object_id':v} if v is not None and key in compact else v,obj.element,heap) for v in obj.items]
    return heap


def decode_reference(value, kind, heap):
    require(is_array(kind) or is_object(kind))
    if value is None:return None
    if is_object(kind):
        require(type(value) is dict and value.keys()=={'object_id'} and type(value['object_id']) is int)
        key=value['object_id'];require(key in heap.objects and type(heap.objects[key]) is RecordObject and heap.objects[key].element==kind)
        return ObjectRef(key,kind)
    require(type(value) is dict and value.keys() == {'array_id'} and type(value['array_id']) is int)
    key = value['array_id']
    require(key in heap.objects and type(heap.objects[key]) is ArrayObject and heap.objects[key].element == kind.removeprefix('array.'))
    return ArrayRef(key, heap.objects[key].element)


def root_heap(heap, persistent, histories):
    from collections import Counter
    heap.roots = Counter(v for v in persistent.values() if is_reference(v))
    from .reference_graph import children
    for obj in heap.objects.values():
        for reference in children(obj):heap.retain(reference)
    for reference, count in heap.roots.items():
        for _ in range(count):heap.retain(reference)
    for ring in histories.values():
        for value in ring.items:heap.retain(value)
    require(not heap.garbage)
