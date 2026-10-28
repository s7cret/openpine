"""Stream independent rings/kernels/heap objects through the bounded tree codec.

Only a small structural header and one primitive record coexist. Consumers build
native state privately, then keep their existing atomic validation/commit fence.
"""
from packages.contracts.checkpoint_stream import RecordMapping,RecordSequence
from packages.contracts.wire import canonical
from . import checkpoint_tree as tree
from .objects import identifier

SCHEMA='openpine.checkpoint-records.1'
MAX_RECORDS=100000

def put(objects,value,*,limit_bytes=tree.DEFAULT_LIMIT):
    def deferred(v):
        if isinstance(v,(RecordMapping,RecordSequence)):return True
        if type(v) is dict:return any(deferred(x) for x in v.values())
        if type(v) in (list,tuple):return any(deferred(x) for x in v)
        return False
    if not deferred(value):
        sha=tree.put(objects,value);tree.get(objects,sha,limit_bytes=limit_bytes);return sha
    refs=[]
    def record(value):
        sha=tree.put(objects,value)
        # Same decoder and pinned budget as restoration, before accepting root.
        tree.get(objects,sha,limit_bytes=limit_bytes//2)
        refs.append(sha)
        if len(refs)>MAX_RECORDS:raise ValueError('Too many checkpoint records.')
        return sha
    header_bytes=128
    def charge(n):
        nonlocal header_bytes
        header_bytes+=n
        if header_bytes*64>limit_bytes//2:raise ValueError('Checkpoint structural header exceeds restore budget.')
    def encode(v,depth=0):
        if depth>128:raise ValueError('Checkpoint header is too deep.')
        charge(16)
        if isinstance(v,RecordMapping):
            rows=[]
            for k in v:
                charge(len(canonical(k))+128);rows.append([k,record(v[k])])
            return ['m',rows]
        if isinstance(v,RecordSequence):
            rows=[]
            for i in range(len(v)):
                charge(96);rows.append(record(v[i]))
            return ['r',rows]
        if type(v) is dict:
            rows=[]
            for k,x in sorted(v.items()):
                charge(len(canonical(k))+16);rows.append([k,encode(x,depth+1)])
            return ['d',rows]
        if type(v) in (tuple,list):return ['l',[encode(x,depth+1) for x in v]]
        charge(len(canonical(v)))
        return ['v',v]
    root=dict(schema_version=SCHEMA,tree=encode(value))
    # Half remains for the decoded structural header while a record is loaded.
    if len(canonical(root))*64>limit_bytes//2:raise ValueError('Checkpoint structural header exceeds restore budget.')
    return objects.put(root)

def get(objects,sha,*,limit_bytes=tree.DEFAULT_LIMIT,streaming=True):
    path=objects.verify(sha)
    if path.stat().st_size*64>limit_bytes:raise ValueError('Checkpoint structural header exceeds restore budget.')
    root=objects.get(sha,max_bytes=limit_bytes//64)
    if type(root) is not dict or root.get('schema_version')!=SCHEMA:
        return tree.get(objects,sha,limit_bytes=limit_bytes)
    if path.stat().st_size*64>limit_bytes//2:raise ValueError('Checkpoint structural header exceeds restore budget.')
    if root.keys()!={'schema_version','tree'}:raise ValueError('Invalid checkpoint record header.')
    count=0
    def load(ref):return tree.get(objects,ref,limit_bytes=limit_bytes//2)
    def decode(node,depth=0):
        nonlocal count
        count+=1
        if count>MAX_RECORDS or depth>128 or type(node) is not list or len(node)!=2:raise ValueError('Invalid checkpoint record node.')
        kind,v=node
        if kind=='v':
            if v is not None and type(v) not in (str,int,float,bool):raise ValueError('Invalid checkpoint primitive.')
            return v
        if type(v) is not list:raise ValueError('Invalid checkpoint record container.')
        if kind=='l':return [decode(x,depth+1) for x in v]
        if kind=='r':
            for ref in v:identifier(ref)
            return RecordSequence(len(v),lambda i:load(v[i])) if streaming else [load(ref) for ref in v]
        if kind in ('d','m'):
            data={}
            for row in v:
                if type(row) is not list or len(row)!=2 or type(row[0]) is not str or row[0] in data:raise ValueError('Duplicate/invalid checkpoint key.')
                data[row[0]]=identifier(row[1]) if kind=='m' else decode(row[1],depth+1)
            return (RecordMapping(data,lambda key:load(data[key])) if streaming else {key:load(ref) for key,ref in data.items()}) if kind=='m' else data
        raise ValueError('Unknown checkpoint record kind.')
    return decode(root['tree'])

def references(objects,sha,mark):
    root=objects.get(sha)
    if type(root) is not dict or root.get('schema_version')!=SCHEMA:
        return tree.references(objects,sha,mark)
    # Validate the closed header before following any references.
    get(objects,sha)
    def walk(node):
        kind,v=node
        if kind in ('r','m'):
            for ref in (v if kind=='r' else [row[1] for row in v]):
                mark(ref);tree.references(objects,ref,mark)
        elif kind=='l':
            for child in v:walk(child)
        elif kind=='d':
            for _,child in v:walk(child)
    walk(root['tree'])
