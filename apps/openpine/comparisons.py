"""One bounded diagnostic controller over immutable result publications and shared readers."""
import threading,time,copy
from packages.contracts.wire import digest
from packages.storage.comparisons import ComparisonRepository
from packages.storage.traces import get,put
from packages.storage.diagnostics import identifier
from apps.openpine.comparison_contract import request,SCHEMA
from apps.openpine.comparison_sources import capture,read,comparable

class Comparisons:
    def __init__(self,manager,*,start=True):
        self.manager=manager;self.start=start;self.repository=ComparisonRepository(manager.db,manager.objects)
        if not hasattr(manager,'comparison_coordinator'):manager.comparison_coordinator={'threads':{},'stops':{},'lock':threading.RLock()}
        shared=manager.comparison_coordinator;self.threads=shared['threads'];self.stops=shared['stops'];self.lock=shared['lock']
    def close(self):
        with self.lock:
            for event in self.stops.values():event.set()
            threads=list(self.threads.values())
        for thread in threads:thread.join(timeout=15)
    def create(self,payload):
        payload=request(payload);key=payload['request_id'];m=self.manager
        with self.lock,m.limiter.slot('prepare'),m.lock,m.db.transaction():
            existing=get(m.db,'comparison:config:'+key)
            if existing:
                if existing['request']!=payload:raise ValueError('Comparison id was reused with different inputs.')
                return self.status(key)
            descriptors=[capture(m,payload[name]) for name in ('left','right')]
            self.repository.create(key,{'schema_version':SCHEMA,'request':payload,'sources':descriptors})
            sides=[read(m,d) for d in descriptors];compat=comparable(*sides)
            state={'status':'queued','desired':'running','cursor':{'channel':'rows','index':0},'checked':{'rows':0,'events':0,'statistics':0},
                'chains':{'left':digest(None),'right':digest(None)},'compatibility':compat,'first_difference':None}
            if compat['status']!='comparable' and payload['mode']=='compatible':state.update(status='not_comparable',desired='completed')
            self.repository.save(key,state)
            if state['status']=='not_comparable':self.publish(key,self.repository.get(key),state)
        if self.start and state['status']=='queued':self.launch(key)
        return self.status(key)
    def status(self,key):
        config=self.repository.get(key);state=self.repository.state(key)
        if state['status']=='running' and not (key in self.threads and self.threads[key].is_alive()):state={**state,'status':'interrupted'}
        report=get(self.manager.db,'comparison:report:'+key)
        return {'schema_version':'openpine.comparison-status.1','comparison_id':key,'configuration':config,'state':state,'report_ref':report,
            'report':self.manager.objects.get(report,max_bytes=256*1024) if report else None}
    def list(self,start=0,limit=50):
        page=self.repository.list(start,limit)
        page['items']=[{'id':r['id'],'created_at':r['configuration']['created_at'],'state':self.status(r['id'])['state']} for r in page['items']]
        return page
    def launch(self,key,*,background=True):
        with self.lock:
            for old in list(self.threads):
                if not self.threads[old].is_alive():self.threads.pop(old);self.stops.pop(old,None)
            if key in self.threads:return
            if len(self.threads)>=1:
                state=self.repository.state(key);state.update(status='waiting_resources',error='One streaming comparison may run at a time; resume later.');self.repository.save(key,state);return
            state=self.repository.state(key)
            if state['status'] in ('equal','data_equal','different','not_comparable'):raise ValueError('Comparison is complete.')
            self.manager.db.execute('DELETE FROM settings WHERE name=?',('comparison:report:'+key,))
            self.stops[key]=threading.Event()
            if background:
                thread=threading.Thread(target=self.run,args=(key,),daemon=True);self.threads[key]=thread;thread.start()
        if not background:self.run(key)
    def run(self,key):
        m=self.manager;config=self.repository.get(key);state=self.repository.state(key);state.pop('error',None);state.update(status='running',desired='running');self.repository.save(key,state)
        stop=self.stops.setdefault(key,threading.Event())
        try:
            with m.limiter.slot('prepare'),m.limiter.disk.slot(64*1024*1024,kind='comparison'):
                from apps.openpine.comparison_engine import compare
                sides=[read(m,d) for d in config['sources']]
                def save(value):
                    desired=self.repository.state(key).get('desired','running')
                    if desired!='running':stop.set()
                    self.repository.save(key,{**value,'desired':desired})
                state=compare(m,key,sides,state,save,stop.is_set)
        except InterruptedError:
            desired=self.repository.state(key).get('desired');state['status']=desired if desired in ('paused','cancelled') else 'interrupted'
        except OverflowError as error:state.update(status='limited',error=str(error))
        except Exception as error:
            from packages.execution.limits import CoordinatorBusy
            state.update(status='waiting_resources' if isinstance(error,CoordinatorBusy) else 'failed',error=str(error)[:1024])
        state['desired']=state['status'] if state['status'] not in ('running','queued') else 'running'
        with m.db.transaction():
            self.repository.save(key,state)
            if state['status'] in ('equal','data_equal','different','not_comparable','limited'):
                self.publish(key,config,state)
    def publish(self,key,config,state):
        from apps.openpine.compare_values import small
        metadata=[]
        for descriptor in config['sources']:
            side=read(self.manager,descriptor)
            metadata.append({**descriptor,**{k:small(side.get(k)) for k in ('program_sha256','artifact_ref','input_ref','snapshot_id','sequence','scope','model','settings','primary_context')},
                'identity':{k:v for k,v in side['identity'].items() if k!='compatibility_notes'}})
        report={'schema_version':SCHEMA,'comparison_id':key,'sources':config['sources'],**state,
            'pinned_metadata':metadata,
            'scope':'Pinned logical data; no claim of program or TradingView equivalence.','whole_program_equivalence_verified':False}
        put(self.manager.db,'comparison:report:'+key,self.manager.objects.put(report))
    def control(self,key,action):
        if action=='resume':self.launch(key);return self.status(key)
        if action not in ('pause','cancel'):raise ValueError('Unknown comparison control.')
        with self.lock:
            state=self.repository.state(key)
            if state['status'] in ('equal','data_equal','different','not_comparable'):raise ValueError('Comparison is complete.')
            desired='paused' if action=='pause' else 'cancelled';state['desired']=desired
            if key not in self.threads or not self.threads[key].is_alive():state['status']=desired
            self.repository.save(key,state)
            if key in self.stops:self.stops[key].set()
        return self.status(key)
