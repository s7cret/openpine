"""Authenticated loopback search routes over the same application service as CLI."""
import re
from urllib.parse import parse_qs,urlsplit


def get(handler,path):
    if not path.startswith('/api/searches'):return False
    service=handler.server.searches;query=parse_qs(urlsplit(handler.path).query,strict_parsing=True)
    if path=='/api/searches':
        if query:raise ValueError('Search list takes no fields.')
        handler.send(200,service.list())
    elif match:=re.fullmatch('/api/searches/([0-9a-f]{32})/report',path):
        if query:raise ValueError('Report takes no query fields.')
        from apps.openpine.research_reports import ResearchReports
        handler.send(200,ResearchReports(handler.server.jobs).create('search',match[1]))
    elif match:=re.fullmatch('/api/searches/([0-9a-f]{32})',path):
        if set(query)-{'start','limit'} or any(len(v)!=1 for v in query.values()):raise ValueError('Invalid search page.')
        handler.send(200,service.status(match[1],start=int(query.get('start',[0])[0]),limit=int(query.get('limit',[50])[0])))
    else:return False
    return True


def post(handler,path,payload):
    service=handler.server.searches
    if path=='/api/searches':
        from .operation_routes import create
        handler.send(201,create(handler,'search',payload))
    elif match:=re.fullmatch('/api/searches/([0-9a-f]{32})/(pause|resume|cancel)',path):
        if payload!={}:raise ValueError('Search controls use saved settings.')
        from .operation_routes import control
        handler.send(200,control(handler,'search',match[1],match[2]))
    elif match:=re.fullmatch('/api/searches/([0-9a-f]{32})/rerun',path):
        if type(payload) is not dict or payload.keys()!={'ordinal','phase'}:raise ValueError('Rerun requires ordinal and phase.')
        handler.send(201,service.rerun(match[1],payload['ordinal'],phase=payload['phase']))
    else:return False
    return True
