"""Closed HTTP interface to independent bounded diagnostic reruns."""
import re
from urllib.parse import urlsplit,parse_qs

def get(handler,path):
    service=handler.server.traces
    if match:=re.fullmatch('/api/traces/([0-9a-f]{32})(/(events|source))?',path):
        query=parse_qs(urlsplit(handler.path).query,strict_parsing=True)
        if any(len(v)!=1 for v in query.values()) or query.keys()-{'start','limit'}:raise ValueError('Invalid trace page.')
        if match[3]=='source':
            if query:raise ValueError('Trace source accepts no query.')
            handler.send(200,service.source(match[1]))
        elif match[3]=='events':handler.send(200,service.page(match[1],int(query.get('start',['0'])[0]),int(query.get('limit',['200'])[0])))
        elif query:raise ValueError('Trace status accepts no query.')
        else:handler.send(200,service.status(match[1]))
    else:return False
    return True

def post(handler,path,payload):
    service=handler.server.traces
    if path=='/api/traces':handler.send(201,service.create(payload))
    elif match:=re.fullmatch('/api/traces/([0-9a-f]{32})/(pause|resume|cancel)',path):
        if payload!={}:raise ValueError('Trace control accepts an empty object.')
        handler.send(202,service.control(match[1],match[2]))
    else:return False
    return True
