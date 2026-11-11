"""Closed heap codec. Rebuild reference counts from checked durable roots."""

from packages.contracts.arrays import ELEMENTS, array_element, MAX_LENGTH, MAX_OBJECTS, MAX_CELLS, MAX_STRING_BYTES
from packages.runtime.arrays import ArrayHeap, ArrayObject, ArrayRef,ObjectRef,RecordObject,is_reference, string_bytes
from packages.contracts.objects import is_object
from packages.contracts.drawings import JOURNALED_BASES, valid_id
from packages.contracts.drawing_objects import drawing_array_version, is_drawing_array
from packages.contracts.arrays import is_array
from packages.contracts.maps import is_map,parts as map_parts,base as map_base
from .maps import MapRef,MapObject,token,sync as sync_map
from .matrices import MatrixRef,MatrixObject,shape,sync as sync_matrix
from packages.contracts.matrices import is_matrix,element as matrix_element
ELEMENTS=(*ELEMENTS,'color')


def require(condition):
    if not condition:raise ValueError('Invalid array heap checkpoint.')


def encode_reference(value):
    if type(value) is MatrixRef:return {'matrix_id':value.object_id}
    if type(value) is MapRef:return {'map_id':value.object_id}
    return {'array_id': value.object_id} if type(value) is ArrayRef else {'object_id':value.object_id} if type(value) is ObjectRef else value


def object_row(key,obj,items=None):
    from .array_slice import SliceObject
    if type(obj) is MatrixObject:
        packed=is_object(obj.element)
        return {'id':key,'element':obj.element,'rows':obj.rows,'columns':obj.columns,
            'refs' if packed else 'items':[v.object_id if v is not None else None for v in obj.items] if packed else list(obj.items)}
    if type(obj) is MapObject:
        packed=is_object(obj.element)
        return {'id':key,'map_key':obj.key_type,'element':obj.element,
            'refs' if packed else 'items':[v.object_id if packed and i%2 and v is not None else v for i,v in enumerate(obj.items)]}
    if type(obj) is SliceObject:
        return {'id':key,'element':obj.element,'parent_id':obj.parent.object_id,'start':obj.start,'end':obj.end}
    items=obj.items if items is None else items
    return ({'id':key,'type_id':obj.element,'fields':[encode_reference(v) for v in items],'initial':[encode_reference(v) for v in obj.initial_items]} if type(obj) is RecordObject
        else {'id':key,'element':obj.element,'refs':[v.object_id if v is not None else None for v in items]} if is_object(obj.element)
        else {'id':key,'element':obj.element,'items':list(items)})


def export_heap(heap,*,streaming=False):
    from packages.contracts.checkpoint_stream import RecordSequence
    keys=sorted(heap.objects)
    def row(i):
        key=keys[i];obj=heap.objects[key]
        return object_row(key,obj)
    return {**({'schema_version':'openpine.array-heap.5'} if heap.allow_graphs else {'schema_version':'openpine.array-heap.4'} if heap.allow_matrices else {'schema_version':'openpine.array-heap.3'} if heap.allow_maps else {'schema_version':'openpine.array-heap.2'} if heap.allow_views else {}),'next_id':heap.next_id,'cells':heap.cells,'string_bytes':heap.bytes,
        **({'origins':[object_row(k,o) for k,o in sorted(heap.origins.items())]} if heap.allow_graphs else {}),
        'objects':RecordSequence(len(keys),row) if streaming else [row(i) for i in range(len(keys))]}


def restore_heap(value, version, account=None, heap=None,*,allow_views=False,allow_maps=False,allow_matrices=False,allow_graphs=False):
    from packages.runtime.engine import value_matches
    from packages.contracts import ValueType
    allow_views=heap.allow_views if heap is not None else allow_views
    allow_maps=heap.allow_maps if heap is not None else allow_maps
    allow_matrices=heap.allow_matrices if heap is not None else allow_matrices
    allow_graphs=heap.allow_graphs if heap is not None else allow_graphs
    require(type(value) is dict and value.keys() == {'next_id', 'cells', 'string_bytes', 'objects',*(['schema_version'] if allow_views or allow_maps or allow_matrices or allow_graphs else []),*(['origins'] if allow_graphs else [])})
    if allow_graphs:require(value['schema_version']=='openpine.array-heap.5' and version>=5)
    elif allow_matrices:require(value['schema_version']=='openpine.array-heap.4' and version>=5)
    elif allow_maps:require(value['schema_version']=='openpine.array-heap.3' and version>=5)
    elif allow_views:require(value['schema_version']=='openpine.array-heap.2' and version>=4)
    require(type(value['next_id']) is int and 1 <= value['next_id'] < 2**63)
    from packages.contracts.checkpoint_stream import RecordSequence
    require(isinstance(value['objects'],(list,RecordSequence)) and len(value['objects']) <= MAX_OBJECTS)
    heap = heap if heap is not None else ArrayHeap(version,account,allow_views=allow_views,allow_maps=allow_maps,allow_matrices=allow_matrices,allow_graphs=allow_graphs);heap.owner='arrays restore';heap.next_id = value['next_id']
    previous = 0;compact=set()
    for row in value['objects']:
        if type(row) is dict and row.keys() in ({'id','element','rows','columns','items'},{'id','element','rows','columns','refs'}):
            packed='refs' in row;key,t,r,c=(row[k] for k in ('id','element','rows','columns'));items=row['refs' if packed else 'items']
            require(allow_matrices and type(key) is int and previous<key<heap.next_id);previous=key
            require(is_matrix('matrix.'+t) if type(t) is str else False)
            require(shape(r,c) and type(items) is list and len(items)==r*c and packed==is_object(t))
            if packed:require(t in heap.object_types and all(v is None or type(v) is int for v in items));compact.add(key)
            else:require(all(value_matches(v,ValueType(t),version,missing_string=True) for v in items))
            byte_count=sum(map(string_bytes,items));heap.cells+=len(items);heap.bytes+=byte_count
            require(heap.cells<=MAX_CELLS and heap.bytes<=MAX_STRING_BYTES)
            sync_matrix(heap,key,t,len(items),byte_count);heap.objects[key]=MatrixObject(t,items.copy(),byte_count,r,c)
            heap.references[key]=0;heap.garbage.add(key);heap.owned.add(key)
            continue
        if type(row) is dict and row.keys() in ({'id','map_key','element','items'},{'id','map_key','element','refs'}):
            packed='refs' in row
            key,key_type,element=row['id'],row['map_key'],row['element'];items=row['refs' if packed else 'items']
            require(allow_maps and type(key) is int and previous<key<heap.next_id);previous=key
            require(type(key_type) is str and type(element) is str)
            require(is_map(map_base(key_type,element)) and type(items) is list and len(items)%2==0 and len(items)<=MAX_LENGTH)
            require(packed==is_object(element))
            if packed:
                require(element in heap.object_types and all(v is None or type(v) is int for v in items[1::2]))
                compact.add(key)
            positions={}
            for i in range(0,len(items),2):
                require(value_matches(items[i],ValueType(key_type),version,missing_string=True))
                digest=token(items[i]);require(digest not in positions);positions[digest]=i
                if not is_object(element):require(value_matches(items[i+1],ValueType(element),version,missing_string=True))
            byte_count=sum(map(string_bytes,items));heap.cells+=len(items);heap.bytes+=byte_count
            require(heap.cells<=MAX_CELLS and heap.bytes<=MAX_STRING_BYTES)
            sync_map(heap,key,len(items),byte_count)
            heap.objects[key]=MapObject(element,items.copy(),byte_count,key_type,positions)
            heap.references[key]=0;heap.garbage.add(key);heap.owned.add(key)
            continue
        if type(row) is dict and row.keys()=={'id','element','parent_id','start','end'}:
            from .array_slice import SliceObject,sync_view
            key,element,parent,start,end=(row[k] for k in ('id','element','parent_id','start','end'))
            require(allow_views and type(key) is int and previous<key<heap.next_id);previous=key
            require(type(parent) is int and parent<key and type(element) is str and type(start) is int and type(end) is int and 0<=start<=end<=MAX_LENGTH)
            root=heap.objects.get(parent)
            require(type(root) is ArrayObject and root.element==element)
            # Invalidated windows are durable too: access fails until the parent
            # grows sufficiently. A restore must not silently clamp the range.
            sync_view(heap,key);heap.objects[key]=SliceObject(element,ArrayRef(parent,element),start,end)
            heap.references[key]=0;heap.garbage.add(key);heap.owned.add(key)
            continue
        if type(row) is dict and row.keys()=={'id','type_id','fields','initial'}:
            key,type_id,items,initial=row['id'],row['type_id'],row['fields'],row['initial']
            require(type(key) is int and previous<key<heap.next_id);previous=key
            contract=heap.object_types.get(type_id)
            require(contract is not None and type(items) is list and type(initial) is list and len(items)==len(contract.fields) and len(initial) in (0,len(items)))
            from packages.contracts.objects import is_reference as reference_type
            for data in (items,initial):
                for item,f in zip(data,contract.fields,strict=bool(data)):
                    if reference_type(f.base):require(allow_graphs and (item is None or type(item) is dict))
                    else:require(value_matches(item,ValueType(f.base),version,missing_string=True))
            byte_count=sum(map(string_bytes,items));heap.cells+=len(items);heap.bytes+=byte_count
            require(heap.cells<=MAX_CELLS and heap.bytes<=MAX_STRING_BYTES)
            heap.sync(key,len(items),byte_count,tuple(initial),record_type=type_id);heap.objects[key]=RecordObject(type_id,items.copy(),byte_count,tuple(initial))
            if initial:heap.initial_records.add(key)
            heap.references[key]=0;heap.garbage.add(key);heap.owned.add(key)
            continue
        require(type(row) is dict and row.keys() in ({'id','element','items'},{'id','element','refs'}))
        packed='refs' in row
        key, element, items = row['id'], row['element'], row['refs' if packed else 'items']
        require(type(key) is int and previous < key < heap.next_id);previous = key
        from packages.contracts.enums import is_enum
        drawing = element in JOURNALED_BASES
        require(type(element) is str and (element in ELEMENTS or element in heap.object_types or is_enum(element) or drawing)
                and type(items) is list and len(items) <= MAX_LENGTH)
        if drawing:
            require(not packed and version >= drawing_array_version(element)
                    and all(item is None or valid_id(item) for item in items))
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
        if type(obj) is RecordObject and allow_graphs:
            fields=heap.object_types[obj.element].fields
            def unpack(items):return [decode_reference(v,f.base,heap) if reference_type(f.base) else v for v,f in zip(items,fields,strict=bool(items))]
            obj.items=unpack(obj.items);obj.initial_items=tuple(unpack(obj.initial_items))
        if type(obj) is MapObject and is_object(obj.element):
            obj.items=[decode_reference({'object_id':v} if v is not None else None,obj.element,heap) if i%2 else v for i,v in enumerate(obj.items)]
        if type(obj) in (ArrayObject,MatrixObject) and is_object(obj.element):
            obj.items=[decode_reference({'object_id':v} if v is not None and key in compact else v,obj.element,heap) for v in obj.items]
    if allow_graphs:
        origins=value['origins'];require(type(origins) is list and len(origins)<=len(heap.objects))
        ids=[r.get('id') if type(r) is dict else None for r in origins]
        require(all(type(k) is int for k in ids) and ids==sorted(set(ids)) and set(ids)<=heap.objects.keys())
        require(all(type(heap.objects[k]) is not RecordObject for k in ids))
        if origins:
            replacements=dict(zip(ids,origins,strict=True))
            rows=[replacements.get(r['id'],r) for r in value['objects']]
            def items(r):return r.get('refs',r.get('items',r.get('fields',())))
            raw={'schema_version':'openpine.array-heap.5','next_id':heap.next_id,'objects':rows,'origins':[],
                'cells':sum(len(items(r)) for r in rows),'string_bytes':sum(string_bytes(v) for r in rows for v in items(r))}
            staged=ArrayHeap(version,heap.account,tuple(heap.object_types.values()),allow_views=allow_views,allow_maps=allow_maps,allow_matrices=allow_matrices,allow_graphs=True)
            try:
                restore_heap(raw,version,heap=staged)
                from .array_slice import SliceObject
                for key in ids:
                    obj=staged.objects[key];actual=heap.objects[key]
                    require(type(obj) is type(actual) and obj.element==actual.element)
                    if type(obj) is MapObject:require(obj.key_type==actual.key_type)
                    if type(obj) is SliceObject:require((obj.parent,obj.start,obj.end)==(actual.parent,actual.start,actual.end))
                    heap.origins[key]=obj
                    if key in staged.allocations:heap.origin_allocations[key]=staged.allocations.pop(key)
            finally:staged.close()
    return heap


def decode_reference(value, kind, heap):
    require(is_array(kind) and not is_drawing_array(kind) or is_object(kind) or is_map(kind) or is_matrix(kind))
    if value is None:return None
    if is_matrix(kind):
        require(heap.allow_matrices and type(value) is dict and value.keys()=={'matrix_id'} and type(value['matrix_id']) is int)
        key=value['matrix_id'];obj=heap.objects.get(key)
        require(type(obj) is MatrixObject and obj.element==matrix_element(kind))
        return MatrixRef(key,obj.element)
    if is_map(kind):
        require(heap.allow_maps and type(value) is dict and value.keys()=={'map_id'} and type(value['map_id']) is int)
        key=value['map_id'];obj=heap.objects.get(key)
        require(type(obj) is MapObject and (obj.key_type,obj.element)==map_parts(kind))
        return MapRef(key,obj.key_type,obj.element)
    if is_object(kind):
        require(type(value) is dict and value.keys()=={'object_id'} and type(value['object_id']) is int)
        key=value['object_id'];require(key in heap.objects and type(heap.objects[key]) is RecordObject and heap.objects[key].element==kind)
        return ObjectRef(key,kind)
    require(type(value) is dict and value.keys() == {'array_id'} and type(value['array_id']) is int)
    key = value['array_id']
    from .array_slice import SliceObject
    require(key in heap.objects and (type(heap.objects[key]) is ArrayObject or heap.allow_views and type(heap.objects[key]) is SliceObject) and heap.objects[key].element == array_element(kind))
    return ArrayRef(key, heap.objects[key].element)


def root_heap(heap, persistent, histories):
    from collections import Counter
    heap.roots = Counter(v for v in persistent.values() if is_reference(v))
    from .reference_graph import children
    for obj in heap.objects.values():
        for reference in children(obj):heap.retain(reference)
    for obj in heap.origins.values():
        for reference in children(obj):heap.retain(reference)
    for reference, count in heap.roots.items():
        for _ in range(count):heap.retain(reference)
    for ring in histories.values():
        for value in ring.items:heap.retain(value)
    if heap.allow_graphs:
        from .reference_graph import referenced
        external=Counter(v.object_id for v in persistent.values() if is_reference(v))
        for ring in histories.values():external.update(v.object_id for v in ring.items if is_reference(v))
        require(referenced(heap.objects,external,origins=heap.origins).keys()==heap.objects.keys())
    require(not heap.garbage)
