"""Closed heap codec. Rebuild reference counts from checked durable roots."""

from packages.contracts.arrays import ELEMENTS, ARRAY_BASES, MAX_LENGTH, MAX_OBJECTS, MAX_CELLS, MAX_STRING_BYTES
from packages.runtime.arrays import ArrayHeap, ArrayObject, ArrayRef, string_bytes


def require(condition):
    if not condition:raise ValueError('Invalid array heap checkpoint.')


def encode_reference(value):
    return {'array_id': value.object_id} if type(value) is ArrayRef else value


def export_heap(heap):
    return {'next_id': heap.next_id, 'cells': heap.cells, 'string_bytes': heap.bytes,
        'objects': [{'id': key, 'element': obj.element, 'items': list(obj.items)} for key, obj in sorted(heap.objects.items())]}


def restore_heap(value, version, account=None, heap=None):
    from packages.runtime.engine import value_matches
    from packages.contracts import ValueType
    require(type(value) is dict and value.keys() == {'next_id', 'cells', 'string_bytes', 'objects'})
    require(type(value['next_id']) is int and 1 <= value['next_id'] < 2**63)
    require(type(value['objects']) is list and len(value['objects']) <= MAX_OBJECTS)
    heap = heap if heap is not None else ArrayHeap(version,account);heap.owner='arrays restore';heap.next_id = value['next_id']
    previous = 0
    for row in value['objects']:
        require(type(row) is dict and row.keys() == {'id', 'element', 'items'})
        key, element, items = row['id'], row['element'], row['items']
        require(type(key) is int and previous < key < heap.next_id);previous = key
        require(type(element) is str and element in ELEMENTS and type(items) is list and len(items) <= MAX_LENGTH)
        require(all(value_matches(item, ValueType(element), version, missing_string=True) for item in items))
        byte_count = sum(map(string_bytes, items))
        heap.cells += len(items);heap.bytes += byte_count
        require(heap.cells <= MAX_CELLS and heap.bytes <= MAX_STRING_BYTES)
        heap.sync(key,len(items),byte_count)
        # Own all restored buffers; caller-owned JSON cannot mutate live state.
        heap.objects[key] = ArrayObject(element, items.copy(), byte_count)
        heap.references[key] = 0;heap.garbage.add(key);heap.owned.add(key)
    require(type(value['cells']) is int and value['cells'] == heap.cells)
    require(type(value['string_bytes']) is int and value['string_bytes'] == heap.bytes)
    return heap


def decode_reference(value, kind, heap):
    require(kind in ARRAY_BASES)
    if value is None:return None
    require(type(value) is dict and value.keys() == {'array_id'} and type(value['array_id']) is int)
    key = value['array_id']
    require(key in heap.objects and heap.objects[key].element == kind.removeprefix('array.'))
    return ArrayRef(key, heap.objects[key].element)


def root_heap(heap, persistent, histories):
    from collections import Counter
    heap.roots = Counter(v for v in persistent.values() if type(v) is ArrayRef)
    for reference, count in heap.roots.items():
        for _ in range(count):heap.retain(reference)
    for ring in histories.values():
        for value in ring.items:heap.retain(value)
    require(not heap.garbage)
