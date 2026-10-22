"""Walk-forward admission and control over the local application owner."""
import re
from urllib.parse import parse_qs, urlsplit


def get(handler, path):
    if not path.startswith('/api/walkforwards'):return False
    query = parse_qs(urlsplit(handler.path).query, strict_parsing=True)
    service = handler.server.walkforwards
    if path == '/api/walkforwards':
        if query:raise ValueError('Study list takes no query fields.')
        handler.send(200, service.list())
    elif match := re.fullmatch('/api/walkforwards/([0-9a-f]{32})/report', path):
        if query:raise ValueError('Report takes no query fields.')
        from apps.openpine.research_reports import ResearchReports
        handler.send(200, ResearchReports(handler.server.jobs).create('walkforward', match[1]))
    elif match := re.fullmatch('/api/walkforwards/([0-9a-f]{32})', path):
        if set(query)-{'start','limit'} or any(len(v)!=1 for v in query.values()):raise ValueError('Invalid study page.')
        handler.send(200, service.status(match[1], start=int(query.get('start',[0])[0]), limit=int(query.get('limit',[50])[0])))
    else:return False
    return True


def post(handler, path, payload):
    if path == '/api/walkforwards':handler.send(201, handler.server.walkforwards.create(payload))
    elif match := re.fullmatch('/api/walkforwards/([0-9a-f]{32})/(pause|resume|cancel)', path):
        if payload != {}:raise ValueError('Study controls use the saved composition.')
        handler.send(200, handler.server.walkforwards.control(match[1], match[2]))
    else:return False
    return True
