"""Diagnostic indexes, tombstones and reviewed metadata deletion. Objects use shared GC."""
import time,re
from packages.contracts.wire import loads,digest
from .traces import get,put

def identifier(key):
    if type(key) is not str or not re.fullmatch('[0-9a-f]{32}',key):raise ValueError('Invalid diagnostic id.')
    return key

def retained(db):
    result=set()
    for (raw,) in db.execute("SELECT value FROM settings WHERE name LIKE 'comparison:config:%'"):
        config=loads(raw)
        result.update((s['kind'],s['id']) for s in config['sources'])
    return result

def protected(db,kind,key):return (kind,key) in retained(db)

def gone(db,kind,key):return get(db,f'diagnostic:deleted:{kind}:{key}') is not None

def list_records(db,kind,start=0,limit=50):
    if type(start) is not int or start<0 or type(limit) is not int or not 1<=limit<=100:raise ValueError('Invalid diagnostic page.')
    prefix='trace:config:' if kind=='trace' else 'comparison:config:'
    rows=db.execute('SELECT name,value FROM settings WHERE name LIKE ? ORDER BY name LIMIT ? OFFSET ?',(prefix+'%',limit,start))
    total=db.execute('SELECT count(*) FROM settings WHERE name LIKE ?',(prefix+'%',))[0][0]
    return {'items':[{'id':name[len(prefix):],'configuration':loads(raw)} for name,raw in rows],'total':total,'next':min(total,start+len(rows))}

def trace_config(db,key):
    identifier(key);config=get(db,'trace:config:'+key)
    if config is None:raise ValueError('Diagnostic was deleted.' if gone(db,'trace',key) else 'Unknown diagnostic run.')
    return config

def remove_trace(db,key,config):
    if protected(db,'job',key):raise ValueError('A comparison retains this diagnostic result.')
    if 'replay_id' in config:
        clone=config['replay_id']
        if protected(db,'replay',clone):raise ValueError('A comparison retains this replay.')
        for table in ('replay_events','replay_parts','replays'):db.execute('DELETE FROM '+table+' WHERE replay_id=?',(clone,))
        for prefix in ('trace:replay-config:','trace:replay-result:','trace:replay-tape:'):db.execute('DELETE FROM settings WHERE name=?',(prefix+clone,))
    else:
        if db.execute('SELECT 1 FROM experiment_variants WHERE job_id=?',(key,)):raise ValueError('Research retains this diagnostic job.')
        db.execute('DELETE FROM attempts WHERE job_id=?',(key,));db.execute('DELETE FROM jobs WHERE job_id=?',(key,))
        db.execute('UPDATE job_lineage SET deleted=1 WHERE job_id=?',(key,))
        db.execute('DELETE FROM results WHERE result_id NOT IN (SELECT result_id FROM jobs WHERE result_id IS NOT NULL)')
    for prefix in ('trace:config:','trace:checkpoint:','trace:result:','trace:warning:','trace:replay-state:'):
        db.execute('DELETE FROM settings WHERE name=?',(prefix+key,))

def state_fingerprint(db,key,config):
    if 'replay_id' in config:
        return get(db,'trace:replay-state:'+key,{}),db.execute('SELECT checkpoint_ref,applied FROM replays WHERE replay_id=?',(config['replay_id'],))
    rows=db.execute('SELECT state,result_id FROM jobs WHERE job_id=?',(key,))
    return (bytes(rows[0][0]).hex() if rows else None),get(db,'trace:result:'+key)

def preview_trace(manager,key):
    config=trace_config(manager.db,key);db=manager.db
    if 'replay_id' in config:
        state=get(db,'trace:replay-state:'+key,{}).get('status','queued')
    else:
        rows=db.execute('SELECT status FROM jobs WHERE job_id=?',(key,));state=rows[0][0] if rows else 'missing'
    if state in ('queued','running'):raise ValueError('Stop the diagnostic before deleting it.')
    fingerprint=digest({'config':config,'state':state_fingerprint(db,key,config)})
    from .lifecycle import reachable
    before=reachable(db,manager.objects)
    # Roll back the same metadata mutation that apply performs; no object is unlinked here.
    with db.lock:
        db.execute('SAVEPOINT diagnostic_preview')
        try:
            remove_trace(db,key,config);after=reachable(db,manager.objects)
        finally:db.execute('ROLLBACK TO diagnostic_preview');db.execute('RELEASE diagnostic_preview')
    refs=before-after
    value={'schema_version':'openpine.trace-deletion.1','id':key,'fingerprint':fingerprint,'objects_released':len(refs),
        'bytes_released':sum(manager.objects.path(ref).stat().st_size for ref in refs),'physical_deletion':'existing_offline_gc'}
    return {**value,'preview_id':digest(value)}

def delete_trace(manager,key,expected):
    db=manager.db
    with db.transaction():
        report=preview_trace(manager,key)
        if report['preview_id']!=expected:raise ValueError('Deletion preview is stale; request it again.')
        remove_trace(db,key,trace_config(db,key));put(db,'diagnostic:deleted:trace:'+key,{'deleted_at':time.time(),'preview_id':expected})
    return {**report,'deleted':True}

def retention(db,kind,before,start=0,limit=50):
    if type(before) not in (int,float) or not 0<=before<=time.time():raise ValueError('Retention cutoff must be a past Unix timestamp.')
    page=list_records(db,kind,start,limit)
    page['items']=[r for r in page['items'] if r['configuration'].get('created_at',float('inf'))<before]
    return page
