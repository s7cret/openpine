"""Explicit retention settings and deletion reviews; physical collection remains offline."""
import time
from packages.storage.traces import get,put
from packages.storage.diagnostics import list_records,preview_trace,delete_trace,identifier,retention
from packages.contracts.wire import digest

class DiagnosticRetention:
    def __init__(self,manager,traces,comparisons):self.manager,self.traces,self.comparisons=manager,traces,comparisons
    def policy(self,value=None):
        if value is not None:
            if type(value) is not dict or value.keys()!={'keep_days'} or value['keep_days'] is not None and (type(value['keep_days']) is not int or not 1<=value['keep_days']<=3650):raise ValueError('Provide keep_days: null or integer 1..3650.')
            put(self.manager.db,'diagnostic:retention',value)
        return get(self.manager.db,'diagnostic:retention',{'keep_days':None})
    def list_traces(self,start=0,limit=50):
        page=list_records(self.manager.db,'trace',start,limit);items=[]
        for record in page['items']:
            try:state=self.traces.status(record['id'])['job']['status']
            except (ValueError,KeyError):state='source_unavailable'
            items.append({'id':record['id'],'created_at':record['configuration'].get('created_at'),'status':state})
        return {**page,'items':items}
    def candidates(self,kind,start=0,limit=50):
        days=self.policy()['keep_days']
        if days is None:return {'items':[],'next':0,'total':0,'enabled':False}
        return {**retention(self.manager.db,kind,time.time()-days*86400,start,limit),'enabled':True}
    def preview(self,kind,key):
        identifier(key);m=self.manager
        with self.traces.lock,self.comparisons.lock,m.lock,m.db.transaction():
            if kind=='trace':
                if key in self.traces.active or key in self.traces.threads and self.traces.threads[key].is_alive():raise ValueError('Diagnostic worker is active.')
                return preview_trace(m,key)
            if kind!='comparison':raise ValueError('Unknown diagnostic kind.')
            config=self.comparisons.repository.get(key);state=self.comparisons.repository.state(key)
            if state['status'] in ('queued','running') or key in self.comparisons.threads and self.comparisons.threads[key].is_alive():raise ValueError('Stop the comparison before deletion.')
            value={'schema_version':'openpine.comparison-deletion.1','id':key,'fingerprint':digest({'config':config,'state':state}),
                'physical_deletion':'existing_offline_gc'}
            from packages.storage.lifecycle import reachable
            before=reachable(m.db,m.objects)
            m.db.execute('SAVEPOINT comparison_preview')
            try:
                for prefix in ('comparison:config:','comparison:state:','comparison:report:','comparison:traces:'):m.db.execute('DELETE FROM settings WHERE name=?',(prefix+key,))
                refs=before-reachable(m.db,m.objects)
            finally:m.db.execute('ROLLBACK TO comparison_preview');m.db.execute('RELEASE comparison_preview')
            value.update(objects_released=len(refs),bytes_released=sum(m.objects.path(ref).stat().st_size for ref in refs))
            return {**value,'preview_id':digest(value)}
    def delete(self,kind,key,expected):
        m=self.manager
        with self.traces.lock,self.comparisons.lock,m.lock,m.db.transaction():
            report=self.preview(kind,key)
            if report['preview_id']!=expected:raise ValueError('Deletion preview is stale.')
            if kind=='trace':result=delete_trace(m,key,expected)
            else:
                from packages.storage.comparison_scratch import clear
                clear(m.directory,key)
                for prefix in ('comparison:config:','comparison:state:','comparison:report:','comparison:traces:'):
                    m.db.execute('DELETE FROM settings WHERE name=?',(prefix+key,))
                put(m.db,'diagnostic:deleted:comparison:'+key,{'deleted_at':time.time(),'preview_id':expected});result={**report,'deleted':True}
            owner=self.traces if kind=='trace' else self.comparisons
            owner.threads.pop(key,None);owner.stops.pop(key,None)
            return result
