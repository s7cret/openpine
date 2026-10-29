"""Independent immutable replay prefixes share blocks instead of duplicating row indexes."""
from packages.contracts.wire import loads,digest
from packages.contracts.realtime import MarketEvent
from .traces import get,put
from .columnar import connection

SCHEMA='openpine.frozen-replay-tape.1'
def tape(repo,key):
    cached=getattr(repo,'_frozen_tape',None)
    if cached and cached[0]==key:return cached[1]
    ref=get(repo.db,'trace:replay-tape:'+key)
    if ref is None:return None
    result=repo.objects.get(ref,max_bytes=1024*1024)
    if result['schema_version']!=SCHEMA:raise ValueError('Invalid frozen tape schema.')
    row=repo.row(key)
    if (result['config_ref'],result['events'],result['input_chain'])!=(row[0],row[3],row[4]):raise ValueError('Frozen tape identity differs from its replay.')
    cursor=0;chain=repo.config(key)['identity']
    for part in result['parts']:
        if part['first']!=cursor or not 1<=part['rows']<=10000 or part['before']!=chain:raise ValueError('Frozen tape block boundaries differ.')
        cursor+=part['rows'];chain=part['after']
    if cursor!=result['events'] or chain!=result['input_chain']:raise ValueError('Frozen tape does not cover its accepted prefix.')
    repo._frozen_tape=(key,result);return result

def freeze(repo,key):
    prior=tape(repo,key)
    if prior:return get(repo.db,'trace:replay-tape:'+key)
    row=repo.row(key);parts=[];cursor=0;chain=repo.config(key)['identity']
    for first,count,ref in repo.db.execute('SELECT first_sequence,rows,object_id FROM replay_parts WHERE replay_id=? ORDER BY first_sequence',(key,)):
        if first!=cursor:raise ValueError('Input tape has a missing block.')
        after=repo.db.execute('SELECT chain FROM replay_events WHERE replay_id=? AND sequence=?',(key,first+count-1))[0][0]
        parts.append({'object_id':ref,'format':'parquet','first':first,'rows':count,'before':chain,'after':after});cursor+=count;chain=after
    while cursor<row[3]:
        chunk=repo.db.execute('SELECT sequence,payload,event_sha,chain FROM replay_events WHERE replay_id=? AND sequence>=? AND sequence<? ORDER BY sequence LIMIT 1024',(key,cursor,row[3]))
        if not chunk:raise ValueError('Frozen replay has no durable tail.')
        first=cursor;before=chain;events=[]
        for seq,raw,sha,after in chunk:
            if seq!=cursor or raw is None:raise ValueError('Missing frozen tail event.')
            payload=loads(raw);chain=digest({'previous':chain,'event':payload})
            if digest(payload)!=sha or chain!=after:raise ValueError('Frozen tail chain differs.')
            events.append(payload);cursor+=1
        parts.append({'object_id':repo.objects.put(events),'format':'json','first':first,'rows':len(events),'before':before,'after':chain})
    if chain!=row[4]:raise ValueError('Frozen tape final chain differs.')
    return repo.objects.put({'schema_version':SCHEMA,'config_ref':row[0],'events':row[3],'input_chain':chain,'parts':parts})

def part_events(repo,part,start=None,stop=None):
    if part['format']=='json':
        for payload in repo.objects.get(part['object_id'],max_bytes=16*1024*1024):
            event=MarketEvent.from_payload(payload)
            if (start is None or start<=event.sequence) and (stop is None or event.sequence<stop):yield event
    elif part['format']=='parquet':
        with connection(threads=1,memory_mb=32) as db:
            path=str(repo.objects.verify(part['object_id']))
            reader=db.execute('SELECT sequence,event_json FROM read_parquet(?) ORDER BY sequence',(path,)) if start is None and stop is None else db.execute('SELECT sequence,event_json FROM read_parquet(?) WHERE sequence>=? AND sequence<? ORDER BY sequence',(path,start,stop))
            while batch:=reader.fetchmany(1024):
                for seq,raw in batch:
                    event=MarketEvent.from_payload(loads(raw))
                    if event.sequence!=seq:raise ValueError('Frozen tape row cursor differs.')
                    yield event
    else:raise ValueError('Unknown frozen tape format.')

def verified_chains(repo,part):
    cached=getattr(repo,'_frozen_part',None)
    if cached and cached[0]==part:return cached[1]
    chain=part['before'];chains=[]
    for event in part_events(repo,part):
        if event.sequence!=part['first']+len(chains):raise ValueError('Frozen tape sequence differs.')
        chain=digest({'previous':chain,'event':event.payload()});chains.append(chain)
        if len(chains)>part['rows']:raise ValueError('Frozen tape block has extra rows.')
    if len(chains)!=part['rows'] or chain!=part['after']:raise ValueError('Frozen tape block checksum chain differs.')
    repo._frozen_part=(dict(part),chains);return chains

def events(repo,key,start,stop):
    saved=tape(repo,key)
    if not 0<=start<=stop<=saved['events']:raise ValueError('Frozen tape read exceeds its prefix.')
    cursor=start
    for part in saved['parts']:
        if part['first']+part['rows']<=start or part['first']>=stop:continue
        verified_chains(repo,part)
        for event in part_events(repo,part,start,stop):
            if start<=event.sequence<stop:
                if event.sequence!=cursor:raise ValueError('Frozen tape read skipped a row.')
                yield event;cursor+=1
    if cursor!=stop:raise ValueError('Frozen tape ended before its pinned cursor.')

def chain_at(repo,key,sequence):
    saved=tape(repo,key)
    for part in saved['parts']:
        if part['first']<=sequence<part['first']+part['rows']:return verified_chains(repo,part)[sequence-part['first']]
    raise ValueError('Missing frozen prefix chain.')

def references(db,objects,mark):
    for (raw,) in db.execute("SELECT value FROM settings WHERE name LIKE 'trace:replay-tape:%'"):
        ref=loads(raw);mark(ref);saved=objects.get(ref,max_bytes=1024*1024)
        if saved['schema_version']!=SCHEMA:raise ValueError('Invalid frozen tape root.')
        for part in saved['parts']:mark(part['object_id'])
