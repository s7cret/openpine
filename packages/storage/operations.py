"""Durable request receipts, committed atomically with research composition."""
import re,time
from packages.contracts import Diagnostic,OpenPineError
from packages.contracts.wire import canonical,loads,digest

TABLES={'experiment':('experiments','experiment_id'),'search':('searches','search_id'),'walkforward':('walkforwards','walkforward_id')}
def validate(kind,key):
    if kind not in TABLES or type(key) is not str or re.fullmatch('[0-9a-f]{32}',key) is None:raise ValueError('Use a research kind and a 32 character lowercase hex request_id.')
def reject(code,message):raise OpenPineError(Diagnostic('INPUT_INVALID',code,message))

class OperationRepository:
    def __init__(self,db):self.db=db
    def get(self,kind,key,request=None,*,allow_deleted=False):
        # A deletion publishes its marker and removes the target under this lock.
        with self.db.lock:return self._get(kind,key,request,allow_deleted=allow_deleted)
    def _get(self,kind,key,request=None,*,allow_deleted=False):
        validate(kind,key);rows=self.db.execute('SELECT receipt,receipt_sha256,request_sha256,resource_id,created_at,deleted FROM research_operations WHERE kind=? AND request_id=?',(kind,key))
        if not rows:return None
        value=loads(rows[0][0])
        expected={'schema_version','kind','request_id','request_sha256','resource_id',kind+'_id','status','created_at'}
        if type(value) is not dict or value.keys()!=expected or digest(value)!=rows[0][1] or value['kind']!=kind or value['request_id']!=key or value['schema_version']!='openpine.research-operation.1' or value['status']!='accepted' or (value['request_sha256'],value['resource_id'],value['created_at'])!=rows[0][2:5] or value[kind+'_id']!=value['resource_id']:raise ValueError('Research operation receipt checksum differs.')
        if re.fullmatch('[0-9a-f]{32}',value['resource_id']) is None or re.fullmatch('[0-9a-f]{64}',value['request_sha256']) is None:raise ValueError('Invalid receipt identity.')
        if request is not None and value['request_sha256']!=digest(request):reject('IDEMPOTENCY_CONFLICT','This request_id belongs to another research request.')
        table,field=TABLES[kind]
        exists=bool(self.db.execute(f'SELECT 1 FROM {table} WHERE {field}=?',(value['resource_id'],)));deleted=rows[0][5]
        if exists==bool(deleted):raise ValueError('Research operation target differs from its deletion marker.')
        if not allow_deleted and deleted:
            reject('OPERATION_GONE','The accepted research was deleted; this request_id remains reserved.')
        return value
    def put(self,kind,key,request,identifier):
        validate(kind,key)
        if self.db.connection.get_autocommit():raise ValueError('Publish the operation and research in one transaction.')
        value={'schema_version':'openpine.research-operation.1','kind':kind,'request_id':key,'request_sha256':digest(request),
            'resource_id':identifier,kind+'_id':identifier,'status':'accepted','created_at':time.time()}
        self.db.execute('INSERT INTO research_operations(kind,request_id,request_sha256,resource_id,created_at,receipt,receipt_sha256) VALUES(?,?,?,?,?,?,?)',(kind,key,value['request_sha256'],identifier,value['created_at'],canonical(value),digest(value)))
        return value
    def mark_deleted(self,kind,identifier):
        if kind not in TABLES or self.db.connection.get_autocommit():raise ValueError('Research deletion and its tombstone require one transaction.')
        self.db.execute('UPDATE research_operations SET deleted=1 WHERE kind=? AND resource_id=?',(kind,identifier))
    def audit(self):
        for kind,key in self.db.execute('SELECT kind,request_id FROM research_operations'):self.get(kind,key,allow_deleted=True)
