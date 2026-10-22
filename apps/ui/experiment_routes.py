"""Finite experiments and schema-scoped presets over the shared application services."""
import re
from urllib.parse import parse_qs,urlsplit


def get(handler,path):
    service=handler.server.experiments
    query=parse_qs(urlsplit(handler.path).query,strict_parsing=True)
    if match:=re.fullmatch('/api/experiments/([0-9a-f]{32})/report',path):
        if query:raise ValueError('Report takes no query fields.')
        from apps.openpine.research_reports import ResearchReports
        handler.send(200,ResearchReports(handler.server.jobs).create('experiment',match[1]))
        return True
    if path=='/api/presets':
        if set(query)!={'input_ref'} or any(len(v)!=1 for v in query.values()):raise ValueError('Preset list requires one compiled input_ref.')
        handler.send(200,service.presets(query['input_ref'][0]))
    elif path=='/api/experiments':
        if query:raise ValueError('Experiment list takes no fields.')
        handler.send(200,service.list())
    elif match:=re.fullmatch('/api/experiments/([0-9a-f]{32})',path):
        if set(query)-{'start','limit'} or any(len(v)!=1 for v in query.values()):raise ValueError('Invalid experiment page.')
        handler.send(200,service.status(match[1],start=int(query.get('start',[0])[0]),limit=int(query.get('limit',[50])[0])))
    else:return False
    return True


def post(handler,path,payload):
    service=handler.server.experiments
    if path=='/api/presets':handler.send(201,service.save_preset(payload))
    elif path=='/api/experiments':handler.send(201,service.create(payload))
    elif match:=re.fullmatch('/api/experiments/([0-9a-f]{32})/(pause|resume|cancel)',path):
        if payload!={}:raise ValueError('Experiment control uses its frozen settings.')
        handler.send(200,service.control(match[1],match[2]))
    else:return False
    return True
