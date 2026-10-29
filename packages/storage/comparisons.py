"""Publication-bound comparison records in the existing catalogue, schema 16."""
import time
from .traces import get,put
from .diagnostics import identifier,gone,list_records
from packages.contracts.wire import loads,digest

class ComparisonRepository:
    def __init__(self,db,objects):self.db,self.objects=db,objects
    def get(self,key):
        identifier(key);value=get(self.db,'comparison:config:'+key)
        if value is None:raise ValueError('Comparison was deleted.' if gone(self.db,'comparison',key) else 'Unknown comparison.')
        return value
    def state(self,key):self.get(key);return get(self.db,'comparison:state:'+key,{'status':'queued','cursor':{'channel':'rows','index':0}})
    def save(self,key,state):self.get(key);put(self.db,'comparison:state:'+key,state)
    def create(self,key,config):
        identifier(key)
        if gone(self.db,'comparison',key):raise ValueError('Deleted comparison request ids remain reserved.')
        old=get(self.db,'comparison:config:'+key)
        if old is not None:
            if old['request']!=config['request']:raise ValueError('Comparison id was reused with different inputs.')
            return False
        put(self.db,'comparison:config:'+key,{**config,'created_at':time.time()});return True
    def list(self,start=0,limit=50):return list_records(self.db,'comparison',start,limit)

def references(db,objects,mark):
    for (raw,) in db.execute("SELECT value FROM settings WHERE name LIKE 'comparison:config:%'"):
        config=loads(raw)
        for descriptor in config['sources']:
            mark(descriptor['side_ref']);side=objects.get(descriptor['side_ref'],max_bytes=32*1024*1024)
            if digest(side)!=descriptor['binding_sha256']:raise ValueError('Comparison side checksum differs.')
            # The owning source row is retained; additionally pin its exact old publication.
            if side.get('result_ref'):mark(side['result_ref'])
            if side.get('config_ref'):mark(side['config_ref'])
            publication=side['publication']
            for part in publication.get('series',[]):mark(part['object_id'])
            for part in (publication.get('simulation') or {}).get('journal',{}).get('parts',[]):mark(part['object_id'])
            if side.get('tape_ref'):
                mark(side['tape_ref']);tape=objects.get(side['tape_ref'],max_bytes=1024*1024)
                for part in tape['parts']:mark(part['object_id'])
    for (raw,) in db.execute("SELECT value FROM settings WHERE name LIKE 'comparison:report:%'"):
        mark(loads(raw))
