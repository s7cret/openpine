"""Closed comparison and reviewed diagnostic lifecycle HTTP endpoints."""
import re
from urllib.parse import urlsplit,parse_qs

def query(handler,allowed):
    result=parse_qs(urlsplit(handler.path).query,strict_parsing=True,keep_blank_values=True)
    if result.keys()-set(allowed) or any(len(v)!=1 for v in result.values()):raise ValueError('Invalid comparison query.')
    return {k:v[0] for k,v in result.items()}

def page(handler):
    q=query(handler,('start','limit'));return int(q.get('start',0)),int(q.get('limit',50))

def get(handler,path):
    service=handler.server.comparisons;retention=handler.server.diagnostic_retention
    if path=='/api/comparisons':value=service.list(*page(handler))
    elif path=='/api/traces':value=retention.list_traces(*page(handler))
    elif path=='/api/diagnostics/retention':query(handler,());value=retention.policy()
    elif match:=re.fullmatch('/api/diagnostics/(trace|comparison)/candidates',path):value=retention.candidates(match[1],*page(handler))
    elif match:=re.fullmatch('/api/comparisons/([0-9a-f]{32})(/(explanation|chart))?',path):
        if match[3]=='chart':
            from apps.openpine.comparison_chart import view
            q=query(handler,('side','start','limit'));value=view(service,match[1],int(q.get('side',0)),int(q.get('start',0)),int(q.get('limit',100)))
        elif match[3]=='explanation':
            from apps.openpine.comparison_trace import explanation
            q=query(handler,('side','start','limit'));value=explanation(service,match[1],int(q.get('side',0)),int(q.get('start',0)),int(q.get('limit',100)))
        else:query(handler,());value=service.status(match[1])
    else:return False
    handler.send(200,value);return True

def post(handler,path,payload):
    service=handler.server.comparisons;retention=handler.server.diagnostic_retention
    if not path.startswith(('/api/comparisons','/api/diagnostics/')):return False
    query(handler,())
    if path=='/api/comparisons':handler.send(201,service.create(payload));return True
    if path=='/api/diagnostics/retention':handler.send(200,retention.policy(payload));return True
    if match:=re.fullmatch('/api/comparisons/([0-9a-f]{32})/(pause|resume|cancel|trace)',path):
        if payload!={}:raise ValueError('Comparison controls accept an empty object.')
        if match[2]=='trace':
            from apps.openpine.comparison_trace import create
            value=create(service,match[1])
        else:value=service.control(match[1],match[2])
        handler.send(202,value);return True
    if match:=re.fullmatch('/api/diagnostics/(trace|comparison)/([0-9a-f]{32})/(delete-preview|delete-apply)',path):
        if match[3]=='delete-preview':
            if payload!={}:raise ValueError('Deletion preview accepts an empty object.')
            value=retention.preview(match[1],match[2])
        else:
            if type(payload) is not dict or payload.keys()!={'preview_id'}:raise ValueError('Provide the reviewed preview_id.')
            value=retention.delete(match[1],match[2],payload['preview_id'])
        handler.send(200,value);return True
    return False
