"""Rebuildable disk index for verified broker identities; bounded independently of RAM."""
import apsw
from packages.contracts.wire import digest
from .disk import guard

FIELDS={'order_id':'order','fill_id':'fill','entry_fill_id':'fill','exit_fill_id':'fill','lot_id':'fill','trade_id':'trade'}
class IdIndex:
    def __init__(self,path,programs,*,sites=None,max_bytes=64*1024*1024):
        path.parent.mkdir(parents=True,exist_ok=True);guard(path.parent,1024*1024)
        self.db=apsw.Connection(str(path));self.programs=programs;self.limit=max_bytes;self.insertions=0
        self.db.execute('DROP TABLE IF EXISTS identities');self.db.execute('CREATE TABLE identities(side INTEGER,id TEXT,kind TEXT,ordinal INTEGER,PRIMARY KEY(side,id)) WITHOUT ROWID')
        self.counters=[{'order':0,'fill':0,'trade':0} for _ in programs]
        self.sites=[{r['call_site_id']:['written_site',i,r['operation']] for i,r in enumerate(side)} for side in sites] if sites is not None else None
        self.db.execute('BEGIN')
    def close(self):
        if not self.db.get_autocommit():self.db.execute('COMMIT')
        self.db.close()
    def token(self,side,kind,value):
        found=list(self.db.execute('SELECT kind,ordinal FROM identities WHERE side=? AND id=?',(side,value)))
        if found:
            if found[0][0]!=kind:raise ValueError('Broker identity is reused across different domains.')
            return ['broker_id',*found[0]]
        first=self.counters[side][kind]
        ordinal=next((i for i in range(max(0,first-16),first+17) if digest({'program':self.programs[side],'kind':kind,'sequence':i})==value),None)
        if ordinal is None:raise ValueError('Broker reference cannot be verified against its program and sequence.')
        self.db.execute('INSERT INTO identities VALUES(?,?,?,?)',(side,value,kind,ordinal));self.counters[side][kind]=max(first,ordinal+1);self.insertions+=1
        if self.insertions%1000==0:
            self.db.execute('COMMIT');guard(__import__('pathlib').Path(self.db.filename).parent,1024*1024)
            if self.db.pragma('page_count')*self.db.pragma('page_size')>self.limit:raise OverflowError('Comparison identity index reached its 64 MiB limit.')
            self.db.execute('BEGIN')
        return ['broker_id',kind,ordinal]
    def normalize(self,side,value,key=None):
        if type(value) is dict:return {k:self.normalize(side,v,k) for k,v in value.items()}
        if type(value) is list:return [self.normalize(side,v,key) for v in value]
        if key=='call_site_id' and type(value) is str and self.sites is not None:
            if value not in self.sites[side]:raise ValueError('Broker call site is absent from the pinned IR.')
            return self.sites[side][value]
        if key in FIELDS and type(value) is str:return self.token(side,FIELDS[key],value)
        if key=='target_id' and type(value) is str:
            found=list(self.db.execute('SELECT kind,ordinal FROM identities WHERE side=? AND id=?',(side,value)))
            if found:return ['broker_id',*found[0]]
        return value
