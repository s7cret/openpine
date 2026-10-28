"""Versioned, bounded JSON tree blocks in the existing immutable ObjectStore.

Only this checkpoint API interprets references. Ordinary JSON/IR objects never
become object graphs. The decoded primitive tree is still materialized before
the runtime's separate atomic restore; this is not a lazy runtime state.
"""
import json,math
from packages.contracts.wire import canonical
from .objects import identifier

SCHEMA='openpine.checkpoint-tree.1'
BLOCK=65536
MAX_RAW=256*1024**2
DEFAULT_LIMIT=128*1024**2

def _events(value,depth=0):
    if depth>128:raise ValueError('Checkpoint tree is too deep.')
    if type(value) in (list,tuple):
        yield ['l',len(value)]
        for child in value:yield from _events(child,depth+1)
    elif type(value) is dict:
        if any(type(key) is not str for key in value):raise ValueError('Checkpoint keys must be strings.')
        yield ['d',len(value)]
        for key in sorted(value):
            yield from _events(key,depth+1);yield from _events(value[key],depth+1)
    elif type(value) is str:
        yield ['s',len(value)]
        for start in range(0,len(value),4096):yield ['p',value[start:start+4096]]
    elif value is None or type(value) in (int,float,bool):yield ['v',value]
    else:raise ValueError('Non-primitive checkpoint value.')

def put(objects,value):
    # Retain the legacy representation for small records, including existing
    # tooling which inspects small runtime_ref/output_ref objects directly.
    size=0
    for fragment in json.JSONEncoder(ensure_ascii=False,allow_nan=False).iterencode(value):
        size+=len(fragment.encode('utf-8'))
        if size>BLOCK:break
    if size<=BLOCK:return objects.put(value)
    parts=[];raw=[];size=2;tokens=total=0
    def flush():
        nonlocal raw,size,total
        data=b'['+b','.join(raw)+b']';total+=len(data)
        if total>MAX_RAW:raise ValueError('Checkpoint tree exceeds its encoded limit.')
        # put() uses canonical JSON and the same content-addressed publication.
        sha=objects.put(json.loads(data))
        parts.append(dict(object_id=sha,bytes=objects.path(sha).stat().st_size))
        raw=[];size=2
    for event in _events(value):
        encoded=canonical(event)
        if len(encoded)+2>BLOCK:raise ValueError('Checkpoint scalar exceeds block limit.')
        if size+len(encoded)+bool(raw)>BLOCK:flush()
        raw.append(encoded);size+=len(encoded)+1;tokens+=1
    if raw:flush()
    return objects.put(dict(schema_version=SCHEMA,parts=parts,tokens=tokens,encoded_bytes=total))

def _root(objects,sha,limit):
    path=objects.verify(sha);size=path.stat().st_size
    if size*64>limit:raise ValueError('Legacy checkpoint exceeds safe decode budget; rewrite with block codec.')
    return objects.get(sha,max_bytes=min(MAX_RAW,limit//64))

def _parts(root):
    if (root.keys()!={'schema_version','parts','tokens','encoded_bytes'} or root['schema_version']!=SCHEMA
            or type(root['parts']) is not list or not 1<=len(root['parts'])<=8192
            or type(root['tokens']) is not int or not 1<=root['tokens']<=16_000_000
            or type(root['encoded_bytes']) is not int or not 1<=root['encoded_bytes']<=MAX_RAW):
        raise ValueError('Invalid checkpoint block manifest.')
    total=0
    for part in root['parts']:
        if type(part) is not dict or part.keys()!={'object_id','bytes'} or type(part['bytes']) is not int or not 1<=part['bytes']<=BLOCK:
            raise ValueError('Invalid checkpoint block reference.')
        identifier(part['object_id']);total+=part['bytes']
    if total!=root['encoded_bytes']:raise ValueError('Checkpoint block size mismatch.')
    return root['parts']

def references(objects,sha,mark):
    root=_root(objects,sha,DEFAULT_LIMIT)
    if type(root) is dict and root.get('schema_version')==SCHEMA:
        for part in _parts(root):
            mark(part['object_id'])
            if objects.path(part['object_id']).stat().st_size!=part['bytes']:raise ValueError('Checkpoint block size mismatch.')

def get(objects,sha,*,limit_bytes=DEFAULT_LIMIT):
    if type(limit_bytes) is not int or limit_bytes<4*BLOCK:raise ValueError('Invalid checkpoint decode budget.')
    root=_root(objects,sha,limit_bytes)
    if type(root) is not dict or root.get('schema_version')!=SCHEMA:return root
    parts=_parts(root);frames=[];result=[];used=objects.path(sha).stat().st_size*64;tokens=0;temporary_live=0
    def charge(n,temporary=None):
        nonlocal used
        if temporary is None:temporary=temporary_live
        if used+n+temporary>limit_bytes:raise ValueError('Checkpoint decoded memory budget exceeded.')
        used+=n
    def accept(value):
        nonlocal used
        while frames:
            kind,container,left,key=frames[-1]
            if kind=='s':raise ValueError('Checkpoint string token expected.')
            if kind=='d':
                if key is None:
                    if type(value) is not str or value in container:raise ValueError('Duplicate/non-string checkpoint key.')
                    frames[-1][3]=value;frames[-1][2]-=1;return
                charge(72);container[key]=value;frames[-1][3]=None
            else:charge(16);container.append(value)
            frames[-1][2]-=1
            if frames[-1][2]:return
            frames.pop();value=container
        if result:raise ValueError('Multiple checkpoint roots.')
        result.append(value)
    for part in parts:
        path=objects.verify(part['object_id'])
        if path.stat().st_size!=part['bytes']:raise ValueError('Checkpoint block size mismatch.')
        temporary=part['bytes']*64
        temporary_live=temporary
        charge(0,temporary)
        events=objects.get(part['object_id'],max_bytes=BLOCK)
        if type(events) is not list or not events:raise ValueError('Empty checkpoint block.')
        for event in events:
            tokens+=1
            if type(event) is not list or len(event)!=2:raise ValueError('Invalid checkpoint token.')
            kind,value=event
            if frames and frames[-1][0]=='s':
                frame=frames[-1]
                if kind!='p' or type(value) is not str or not 1<=len(value)<=min(4096,frame[2]):raise ValueError('Invalid checkpoint string part.')
                charge(256+4*len(value),temporary);frame[1].append(value);frame[2]-=len(value)
                if not frame[2]:
                    charge(4*sum(map(len,frame[1])),temporary)
                    text=''.join(frame[1]);used-=sum(256+4*len(p) for p in frame[1]);frames.pop();accept(text)
                continue
            if kind in ('l','d','s'):
                if type(value) is not int or not 0<=value<=16_000_000 or len(frames)>=128:raise ValueError('Invalid checkpoint container.')
                charge(256,temporary)
                container={} if kind=='d' else []
                if value:frames.append([kind,container,value*(2 if kind=='d' else 1),None])
                else:accept('' if kind=='s' else container)
            elif kind=='v':
                if value is not None and type(value) not in (int,float,bool):raise ValueError('Invalid checkpoint scalar.')
                if type(value) is int and value.bit_length()>4300 or type(value) is float and not math.isfinite(value):raise ValueError('Invalid checkpoint number.')
                charge(96+(value.bit_length()//8 if type(value) is int else 0),temporary);accept(value)
            else:raise ValueError('Unknown checkpoint token.')
        del events
    if frames or len(result)!=1 or tokens!=root['tokens']:raise ValueError('Incomplete checkpoint tree.')
    return result[0]
