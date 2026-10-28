"""Bounded diagnosis and previewed replacement runs; upgrades stay offline."""
import re
from urllib.parse import parse_qs, urlsplit

from apps.openpine.diagnostics import doctor, compatibility, diagnostic_package, Compatibility
from apps.openpine.restarts import preview, apply


def get(handler,path):
    manager=handler.server.jobs
    if path not in ('/api/maintenance/doctor','/api/maintenance/compatibility','/api/maintenance/diagnostics'):
        match=re.fullmatch(r'/api/jobs/([0-9a-f]{32})/diagnosis',path)
        if not match:return False
        with manager.limiter.slot('read'),manager.lock:
            row=Compatibility(manager.db,manager.objects).job(match[1])
        handler.send(200,row);return True
    query=parse_qs(urlsplit(handler.path).query,strict_parsing=True)
    allowed={'kind','start','limit'} if path.endswith('/compatibility') else set()
    if set(query)-allowed or any(len(v)!=1 for v in query.values()):raise ValueError('Invalid maintenance parameters.')
    with manager.limiter.slot('prepare'):
        if path.endswith('/doctor'):value=doctor(manager.directory)
        elif path.endswith('/diagnostics'):value=diagnostic_package(manager.directory)
        else:value=compatibility(manager.directory,kind=query.get('kind',['jobs'])[0],start=int(query.get('start',[0])[0]),limit=int(query.get('limit',[20])[0]))
    handler.send(200,value);return True


def post(handler,path,payload):
    match=re.fullmatch(r'/api/jobs/([0-9a-f]{32})/restart/(preview|apply)',path)
    if not match:return False
    if type(payload) is not dict:raise ValueError('Restart requires an object.')
    if match[2]=='preview':
        if payload:raise ValueError('Restart preview has no parameters.')
        value=preview(handler.server.jobs,match[1]);status=200
    else:
        if payload.keys()!={'preview_id','request_id'}:raise ValueError('Restart apply requires preview and request identities.')
        value=apply(handler.server.jobs,match[1],**payload);status=202
    handler.send(status,value);return True
