"""Bounded loopback reads and explicitly keyed research submissions."""
from http.client import HTTPConnection,HTTPException
from pathlib import Path
import hashlib,re,time,uuid
from urllib.parse import urlsplit
from packages.contracts.wire import canonical,loads,read_json,atomic_json,digest
from packages.contracts.transport import read_only_post
COLLECTIONS={'experiment':'experiments','search':'searches','walkforward':'walkforwards'}

class LocalAPIError(ValueError):
    def __init__(self,status,value):
        self.status=status;self.response=value
        super().__init__(f'Local API {status}: '+str(value))

def busy_read(status,value):
    ds=value.get('diagnostics') if type(value) is dict else None
    return status==503 and bool(ds) and all(type(d) is dict and d.get('code')=='COORDINATOR_BUSY' for d in ds) and value.get('admission',{}).get('retryable',True) is True

class LocalClient:
    def __init__(self,url,*,retry_attempts=8,deadline_seconds=120,on_wait=None):
        parsed=urlsplit(url)
        if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost') or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:raise ValueError('Use an explicit http://127.0.0.1:PORT loopback server URL.')
        if type(retry_attempts) is not int or not 1<=retry_attempts<=8 or type(deadline_seconds) not in (int,float) or not 0<deadline_seconds<=120:raise ValueError('Invalid bounded retry policy.')
        self.host,self.port=parsed.hostname,parsed.port or 80;self.token=None
        self.retry_attempts=retry_attempts;self.deadline_seconds=deadline_seconds;self.on_wait=on_wait
    def call(self,path,payload=None,*,request_id=None):
        if not path.startswith('/api/') or '\r' in path or '\n' in path:raise ValueError('Invalid local API path.')
        keyed=request_id is not None
        if keyed and (path not in {'/api/'+v for v in COLLECTIONS.values()} or payload is None or type(request_id) is not str or re.fullmatch('[0-9a-f]{32}',request_id) is None):raise ValueError('A request_id is supported only on research creation routes.')
        deadline=time.monotonic()+self.deadline_seconds
        if payload is not None and self.token is None:self.token=self.call('/api/config')['token']
        raw=canonical(payload) if payload is not None else None
        if raw and len(raw)>16*1024*1024:raise ValueError('Request exceeds the 16 MiB local API limit.')
        headers={'Content-Type':'application/json','X-OpenPine-Token':self.token} if raw is not None else {}
        if keyed:headers['X-OpenPine-Request']=request_id
        if payload=={} and re.fullmatch('/api/(experiments|searches|walkforwards)/[0-9a-f]{32}/(pause|resume|cancel)',path):headers['X-OpenPine-Control-View']='progress'
        safe=payload is None or keyed or read_only_post(path);last=None
        for attempt in range(self.retry_attempts):
            remaining=deadline-time.monotonic()
            if remaining<=0:break
            connection=HTTPConnection(self.host,self.port,timeout=remaining);delay=min(.8,.05*2**attempt)
            try:
                connection.request('POST' if payload is not None else 'GET',path,body=raw,headers=headers)
                response=connection.getresponse();body=response.read(32*1024*1024+1)
                if len(body)>32*1024*1024:raise ValueError('Local API response exceeds 32 MiB.')
                try:value=loads(body,max_bytes=32*1024*1024)
                except ValueError as error:raise LocalAPIError(response.status,{'status':'rejected','diagnostics':[{'code':'HTTP_PROTOCOL','message':'Local API returned invalid JSON.'}]}) from error
                if response.status<300:return value
                if type(value) is not dict:value={'status':'rejected','diagnostics':[{'code':'HTTP_PROTOCOL','message':'Local API returned an invalid error envelope.'}]}
                last=LocalAPIError(response.status,value)
                if not safe or not busy_read(response.status,value):raise last
                advertised=response.getheader('Retry-After')
                if advertised and re.fullmatch(r'\d+(?:\.\d+)?',advertised):delay=min(2.,float(advertised))
            except (HTTPException,ConnectionError,TimeoutError,OSError) as error:
                last=LocalAPIError(0,{'status':'rejected','diagnostics':[{'category':'TRANSPORT_ERROR','code':'RESPONSE_UNCERTAIN' if payload is not None else 'LOCAL_TRANSPORT','message':str(error)}],**({'request_id':request_id} if keyed else {})})
                if not safe:raise last from error
            finally:connection.close()
            remaining=deadline-time.monotonic()
            if attempt+1>=self.retry_attempts or remaining<=0:break
            if self.on_wait:self.on_wait({'attempt':attempt+1,'delay_seconds':min(delay,remaining),'request_id':request_id})
            time.sleep(min(delay,remaining))
        if last is not None:raise last
        raise LocalAPIError(0,{'status':'rejected','diagnostics':[{'code':'HTTP_DEADLINE','message':'Local API request deadline expired.'}]})

def research_options(parser):
    parser.add_argument('--request-id',help='stable 32-character hex key for research creation')
    parser.add_argument('--operation-file',type=Path,help='durable client submission receipt, reused after lost responses')
    parser.add_argument('--new-request',action='store_true',help='start a new submission after the previous one was accepted')

def client_rejection(error,fallback):
    if isinstance(error,LocalAPIError):return error.response
    if hasattr(error,'diagnostic'):
        from dataclasses import asdict
        return {'status':'rejected','diagnostics':[asdict(error.diagnostic)],**({'admission':error.admission} if hasattr(error,'admission') else {})}
    return {'status':'rejected','diagnostics':[{'code':fallback,'message':str(error)}]}

def operation_path(args,kind):
    return Path(getattr(args,'operation_file',None) or (str(args.output)+'.operation.json' if args.output else Path('.runtime/client-operations')/(kind+'-'+digest({'server':args.server,'request':str(args.request.resolve())})+'.json')))

def submission_intent(args,kind):
    return digest({'server':args.server,'kind':kind,'files':{name:{'path':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()} for name in ('request','source','bars') if (path:=getattr(args,name)) is not None}})

class Submission:
    def __init__(self,args,kind,payload,client):
        self.payload=payload;self.kind=kind;self.client=client
        self.path=operation_path(args,kind)
        if self.path.resolve() in {p.resolve() for p in (args.request,args.source,args.bars,args.output) if p}:raise ValueError('Operation file must not overwrite request, source, bars or output.')
        from packages.storage.lease import Lease
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lease=Lease(self.path.with_name(self.path.name+'.lock'))
        try:self._initialize(args,payload,client)
        except BaseException:self.lease.close();raise
    def _initialize(self,args,payload,client):
        old=read_json(self.path) if self.path.exists() else None;explicit=getattr(args,'request_id',None);fresh=getattr(args,'new_request',False)
        intent=getattr(args,'client_intent',None)
        if intent and old and old.get('intent_sha256')==intent and not fresh and (not explicit or explicit==old.get('request_id')):payload=old['payload'];self.payload=payload
        fingerprint=digest({'server':f'{client.host}:{client.port}','kind':self.kind,'payload':payload})
        if old and old.get('status')=='pending' and (old.get('fingerprint')!=fingerprint or fresh or explicit and explicit!=old.get('request_id')):raise ValueError('Recover the pending operation first, or use another --operation-file.')
        reuse=old and old.get('fingerprint')==fingerprint and not fresh and (not explicit or explicit==old.get('request_id'))
        key=old['request_id'] if reuse else explicit or uuid.uuid4().hex
        if type(key) is not str or re.fullmatch('[0-9a-f]{32}',key) is None:raise ValueError('Invalid request_id.')
        self.value={'schema_version':'openpine.client-operation.1','kind':self.kind,'request_id':key,'fingerprint':fingerprint,'intent_sha256':intent,'payload':payload,'status':'pending','resource_id':None}
        atomic_json(self.path,self.value)
    def submit(self):
        try:return self._submit()
        except LocalAPIError as error:
            error.response={**error.response,'request_id':self.value['request_id'],'operation_file':str(self.path)}
            if error.status in (409,410):self.value['status']='conflict' if error.status==409 else 'deleted';atomic_json(self.path,self.value)
            raise
        finally:self.lease.close()
    def _submit(self):
        key=self.value['request_id'];receipt=None
        try:receipt=self.client.call(f'/api/operations/{self.kind}/{key}')
        except LocalAPIError as error:
            if error.status!=404:raise
        if receipt is not None and receipt['request_sha256']!=digest(self.payload):raise ValueError('The saved request_id belongs to different settings.')
        if receipt is None:
            try:receipt=self.client.call('/api/'+COLLECTIONS[self.kind],self.payload,request_id=key)
            except LocalAPIError as error:
                if error.status in (400,403,409,410):
                    try:self.client.call(f'/api/operations/{self.kind}/{key}')
                    except LocalAPIError as lookup_error:
                        if lookup_error.status==404:self.value.update(status='rejected');atomic_json(self.path,self.value)
                raise
        self.value.update(status='accepted',resource_id=receipt['resource_id']);atomic_json(self.path,self.value)
        return receipt

def research_command(args,kind):
    client=LocalClient(args.server);path='/api/'+COLLECTIONS[kind];identifier=args.id;result=None;submission=None
    if args.request:
        args.client_intent=submission_intent(args,kind);saved_path=operation_path(args,kind);old=read_json(saved_path) if saved_path.exists() else None
        frozen=old and old.get('intent_sha256')==args.client_intent and not getattr(args,'new_request',False) and (not getattr(args,'request_id',None) or args.request_id==old.get('request_id'))
        if old and old.get('status')=='pending' and not frozen:raise ValueError('The pending operation has different source/data/settings; recover it or use another --operation-file.')
        payload=old['payload'] if frozen else read_json(args.request)
        if args.source and not frozen:
            response=client.call('/api/compile',{'source':args.source.read_text(encoding='utf-8'),'source_name':args.source.name})
            if response.get('status')!='compiled':raise ValueError(str(response))
            payload['input_ref']=response['input_ref']
        if args.bars and not frozen:payload['snapshot_id']=client.call('/api/snapshots',{'provider':'upload','raw':args.bars.read_text(encoding='utf-8')})['sha256']
        submission=Submission(args,kind,payload,client);result=submission.submit();identifier=result[kind+'_id']
    item=path+'/'+str(identifier)
    if args.action=='list':return client.call(path),0
    if args.action=='status':return client.call(item+f'?start={args.start}&limit={args.limit}'),0
    if args.action=='report':return client.call(item+'/report'),0
    if args.action.startswith('delete-'):
        action='preview' if args.action=='delete-preview' else 'delete'
        return client.call(f'/api/research/{kind}/{identifier}/{action}',{} if action=='preview' else {'preview_id':args.preview_id}),0
    if args.action=='rerun':return client.call(item+'/rerun',{'ordinal':args.ordinal,'phase':args.phase}),0
    if args.action in ('pause','resume','cancel'):return client.call(item+'/'+args.action,{}),0
    if args.action=='run':
        client.call(item+'/resume',{})
        try:
            while True:
                result=client.call(item+'/progress')
                if result['status'] not in ('running','selecting','checking_holdout','pausing'):break
                time.sleep(.2)
        except KeyboardInterrupt:result=client.call(item+'/pause',{})
        result=client.call(item+f'?start={args.start}&limit={args.limit}')
        if submission:result={**result,'request_id':submission.value['request_id'],'operation_file':str(submission.path)}
        return result,0 if result['status'] in ('succeeded','paused') else 2
    return result,0
