"""Immutable bounded JSON chunks; small diagnostic indexes use the existing catalogue."""
from packages.contracts.trace import SCHEMA,options
from packages.contracts.wire import canonical,loads,digest,atomic_json,read_json

def get(db,name,default=None):
    rows=db.execute('SELECT value FROM settings WHERE name=?',(name,))
    return loads(rows[0][0]) if rows else default
def put(db,name,value):
    db.execute('INSERT INTO settings(name,value) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value',(name,canonical(value)))

class Writer:
    def __init__(self,objects,configuration,*,previous=None,attempt_id='inline'):
        self.objects=objects;self.configuration=configuration;self.policy=options(configuration['options'])
        self.attempt_id=attempt_id
        self.order_sites={}
        self.cached=None;self.last_cursor=None
        self.parts=[];self.pending=[];self.pending_bytes=0;self.count=0;self.bytes=0;self.stopped=False;self.reason=None;self.error=None;self.invocations=0;self.branches=0
        if previous:
            prior=objects.get(previous,max_bytes=256*1024)
            if prior['configuration']!=configuration:raise ValueError('Trace checkpoint belongs to another configuration.')
            self.parts=list(prior['parts']);self.count=prior['events'];self.bytes=prior['bytes'];self.stopped=prior['truncated'];self.reason=prior['reason']
            self.invocations=prior['invocations'];self.branches=prior['branches']
            self.order_sites=dict(prior.get('order_sites',{}))
            self.last_cursor=prior.get('captured_through')
    def emit(self,row):
        if self.stopped:return
        item={'sequence':self.count,'attempt_id':self.attempt_id,**row};size=len(canonical(item))
        if size>16384:self.stopped=True;self.reason='event_size_limit';return
        if self.count>=self.policy['max_events'] or self.bytes+size>self.policy['max_bytes']:
            self.stopped=True;self.reason='event_limit' if self.count>=self.policy['max_events'] else 'byte_limit';return
        self.pending.append(item);self.pending_bytes+=size;self.count+=1;self.bytes+=size
        self.last_cursor={'primary_bar':row.get('primary_bar'),'event_sequence':row.get('event',{}).get('sequence')}
        if self.pending_bytes>=32768:self.flush()
    def flush(self):
        if not self.pending:return
        try:
            ref=self.objects.put(self.pending)
            self.parts.append({'object_id':ref,'start':self.pending[0]['sequence'],'events':len(self.pending)})
        except OSError as error:
            self.count-=len(self.pending);self.bytes-=self.pending_bytes
            self.stopped=True;self.reason='storage_error';self.error=type(error).__name__
        self.pending=[];self.pending_bytes=0
    def save(self,**boundary):
        self.flush()
        status=boundary.get('status');attempt=boundary.get('attempt_id',self.attempt_id)
        key=(self.count,self.bytes,self.invocations,self.branches,self.stopped,self.reason,status,attempt)
        if self.cached and self.cached[0]==key:return self.cached[1]
        ref=self.objects.put({'schema_version':SCHEMA,'configuration':self.configuration,'parts':self.parts,
            'events':self.count,'bytes':self.bytes,'truncated':self.stopped,'reason':self.reason,'storage_error':self.error,
            'invocations':self.invocations,'branches':self.branches,'order_sites':self.order_sites,
            'captured_through':self.last_cursor,'attempt_id':attempt,'status':status})
        self.cached=(key,ref);return ref

def rows(objects,ref,start=0,limit=200):
    if type(start) is not int or start<0 or type(limit) is not int or not 1<=limit<=500:raise ValueError('Invalid trace page.')
    manifest=objects.get(ref,max_bytes=256*1024)
    if manifest['schema_version']!=SCHEMA:raise ValueError('Invalid trace manifest.')
    found=[];cursor=0
    for part in manifest['parts']:
        if part['start']!=cursor:raise ValueError('Trace parts are not consecutive.')
        cursor+=part['events']
        if cursor<=start or part['start']>=start+limit:continue
        chunk=objects.get(part['object_id'],max_bytes=65536)
        if len(chunk)!=part['events'] or any(r['sequence']!=part['start']+i for i,r in enumerate(chunk)):raise ValueError('Trace sequence differs from its chunk.')
        found.extend(r for r in chunk if start<=r['sequence']<start+limit)
    if cursor!=manifest['events']:raise ValueError('Trace event count differs.')
    return {'manifest':{k:v for k,v in manifest.items() if k!='parts'},'events':found,'next':start+len(found),'total':cursor}

def references(db,objects,mark):
    for name,raw in db.execute("SELECT name,value FROM settings WHERE name LIKE 'trace:result:%' OR name LIKE 'trace:checkpoint:%' OR name LIKE 'trace:replay-result:%'"):
        data=loads(raw);ref=data['trace_ref'] if type(data) is dict else data
        mark(ref)
        manifest=objects.get(ref,max_bytes=256*1024)
        if manifest.get('schema_version')!=SCHEMA:continue
        for part in manifest['parts']:mark(part['object_id'])

def capture(manager,job_id,path,*,checkpoint=None):
    config=get(manager.db,'trace:config:'+job_id)
    if config is None:return
    file=path/('trace-checkpoint.json' if checkpoint else 'trace-final.json')
    if not file.is_file():
        put(manager.db,'trace:warning:'+job_id,{'status':'trace_unavailable','error':'no_durable_manifest'})
        return
    note=read_json(file,max_bytes=4096)
    if checkpoint and note.get('checkpoint_id')!=checkpoint:return
    manifest=manager.objects.get(note['trace_ref'],max_bytes=256*1024)
    if manifest['configuration']!=config or manifest['attempt_id']!=path.name:raise ValueError('Stale trace publication.')
    name='trace:checkpoint:'+job_id if checkpoint else 'trace:result:'+job_id
    with manager.db.transaction():
        put(manager.db,name,{'checkpoint_id':checkpoint,'trace_ref':note['trace_ref']} if checkpoint else note['trace_ref'])
        manager.db.execute('DELETE FROM settings WHERE name=?',('trace:warning:'+job_id,))

class TraceStore:
    def __init__(self,db,objects,lifecycle):self.db=db;self.objects=objects;self.lifecycle=lifecycle
    def input(self,job_id,path,checkpoint):
        config=get(self.db,'trace:config:'+job_id)
        if config is not None:
            saved=get(self.db,'trace:checkpoint:'+job_id) if checkpoint else None
            previous=saved['trace_ref'] if saved and saved['checkpoint_id']==checkpoint else None
            atomic_json(path/'trace.json',{'configuration':config,'previous':previous,'prefix_missing':bool(checkpoint and not previous)})
    def capture(self,job_id,path,checkpoint=None):
        try:capture(self,job_id,path,checkpoint=checkpoint)
        except (OSError,ValueError,KeyError,TypeError) as error:
            put(self.db,'trace:warning:'+job_id,{'status':'invalid_trace','error':type(error).__name__})
