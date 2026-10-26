"""Application boundary for optional idempotent creation and receipt lookup."""
from packages.storage.operations import OperationRepository,validate,reject

class Creation:
    def __init__(self,manager,kind,payload,request_id):
        self.manager=manager;self.kind=kind;self.payload=payload;self.key=request_id;self.repository=OperationRepository(manager.db)
        if request_id is not None:validate(kind,request_id)
    def existing(self):
        if self.key is None:return None
        with self.manager.limiter.slot('metadata'):return self.repository.get(self.kind,self.key,self.payload)
    def publish(self,identifier):
        return self.repository.put(self.kind,self.key,self.payload,identifier) if self.key is not None else None
    def result(self,service,identifier):return self.existing() if self.key is not None else service.status(identifier)

def lookup(manager,kind,key):
    with manager.limiter.slot('metadata'):
        result=OperationRepository(manager.db).get(kind,key)
        if result is None:reject('OPERATION_NOT_FOUND','No accepted operation has this request_id.')
        return result
