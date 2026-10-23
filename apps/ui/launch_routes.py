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
    elif match:=re.fullmatch('/api/launches/([0-9a-f]{32})/(source|sources)',path):handler.send(200,getattr(service,match[2])(match[1]))
    elif match:=re.fullmatch('/api/jobs/([0-9a-f]{32})/configuration',path):handler.send(200,service.configuration(match[1]))
    else:return False
    return True


def post(handler,path,payload):
    service=handler.server.launches
    if path=='/api/launches':handler.send(201,service.create(payload))
    elif path=='/api/launches/history-spec':handler.send(200,service.history_spec(payload))
    elif match:=re.fullmatch('/api/launches/([0-9a-f]{32})/(start|cancel|resume)',path):
        if type(payload) is not dict or payload:raise ValueError('Preparation action takes no extra fields.')
        handler.send(202,getattr(service,match[2])(match[1]))
    else:return False
    return True
