"""Bounded metadata routes, independent of expensive result projection."""
import re
from urllib.parse import urlsplit
from apps.openpine.research_operations import lookup
from apps.openpine.research_progress import progress

KINDS={'experiments':'experiment','searches':'search','walkforwards':'walkforward'}
def get(handler,path):
    if match:=re.fullmatch('/api/operations/(experiment|search|walkforward)/([0-9a-f]{32})',path):
        if urlsplit(handler.path).query:raise ValueError('Operation lookup takes no query fields.')
        handler.send(200,lookup(handler.server.jobs,*match.groups()));return True
    if match:=re.fullmatch('/api/(experiments|searches|walkforwards)/([0-9a-f]{32})/progress',path):
        if urlsplit(handler.path).query:raise ValueError('Progress takes no query fields.')
        handler.send(200,progress(handler.server.jobs,KINDS[match[1]],match[2]));return True
    return False

def create(handler,kind,payload):
    service=getattr(handler.server,{'experiment':'experiments','search':'searches','walkforward':'walkforwards'}[kind])
    return service.create(payload,request_id=handler.headers.get('X-OpenPine-Request'))

def control(handler,kind,identifier,action):
    service=getattr(handler.server,{'experiment':'experiments','search':'searches','walkforward':'walkforwards'}[kind])
    view=getattr(handler,'headers',{}).get('X-OpenPine-Control-View')
    if view not in (None,'progress'):raise ValueError('Unknown research control representation.')
    if view is None:return service.control(identifier,action)
    with handler.server.jobs.limiter.slot('metadata'):
        service.control(identifier,action,include_status=False)
        return progress(handler.server.jobs,kind,identifier)
