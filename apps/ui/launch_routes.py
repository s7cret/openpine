"""HTTP transport for common preparation and saved launch inspection."""
import re
from urllib.parse import urlsplit,parse_qs


def get(handler,path):
    service=handler.server.launches
    if path=='/api/launches':handler.send(200,service.list())
    elif path in ('/api/launches/requirements','/api/launches/snapshots'):
        query=parse_qs(urlsplit(handler.path).query,strict_parsing=True)
        if any(len(v)!=1 for v in query.values()):raise ValueError('Duplicate preparation query.')
        if path.endswith('requirements'):
            if set(query)!={'input_ref'}:raise ValueError('Provide the compiled input reference.')
            handler.send(200,service.requirements(query['input_ref'][0]))
        else:
            if set(query)-{'offset','limit'}:raise ValueError('Invalid snapshot page query.')
            handler.send(200,service.snapshots(int(query.get('offset',['0'])[0]),int(query.get('limit',['50'])[0])))
    elif match:=re.fullmatch('/api/launches/([0-9a-f]{32})',path):handler.send(200,service.status(match[1]))
    elif match:=re.fullmatch('/api/launches/([0-9a-f]{32})/passport',path):
        if urlsplit(handler.path).query:raise ValueError('Saved passport accepts no query fields.')
        handler.send(200,service.inspection(match[1]))
    elif match:=re.fullmatch('/api/launches/([0-9a-f]{32})/(source|sources)',path):handler.send(200,getattr(service,match[2])(match[1]))
    elif match:=re.fullmatch('/api/jobs/([0-9a-f]{32})/configuration',path):handler.send(200,service.configuration(match[1]))
    else:return False
    return True


def post(handler,path,payload):
    service=handler.server.launches
    if path=='/api/launches/requirements':
        if (type(payload) is not dict or not {'input_ref','inputs'}<=payload.keys()<={'input_ref','inputs','primary_context','explain','mode_options'}
                or type(payload.get('explain',False)) is not bool or 'mode_options' in payload and not payload.get('explain')):raise ValueError('Requirements need input_ref, overrides, optional primary metadata and explicit explanation options.')
        handler.send(200,service.requirements(payload['input_ref'],payload['inputs'],primary_context=payload.get('primary_context'),explain=payload.get('explain',False),mode_options=payload.get('mode_options')))
    elif path=='/api/launches':handler.send(201,service.create(payload))
    elif path=='/api/launches/history-spec':handler.send(200,service.history_spec(payload))
    elif match:=re.fullmatch('/api/launches/([0-9a-f]{32})/research-base',path):
        if type(payload) is not dict or payload.keys()!={'snapshots'}:raise ValueError('Provide explicit research catalogue snapshots.')
        handler.send(200,service.research_base(match[1],payload['snapshots']))
    elif match:=re.fullmatch('/api/launches/([0-9a-f]{32})/(start|cancel|resume)',path):
        if type(payload) is not dict or payload:raise ValueError('Preparation action takes no extra fields.')
        handler.send(202,getattr(service,match[2])(match[1]))
    else:return False
    return True
