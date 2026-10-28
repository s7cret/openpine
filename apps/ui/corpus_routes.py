"""Closed built-in corpus API; never accepts filesystem paths from a browser."""
import re
from urllib.parse import urlsplit


def get(handler,path):
    if path not in ('/api/corpus','/api/corpus/runs') and not re.fullmatch(r'/api/corpus/runs/[0-9a-f]{32}',path):
        return False
    if urlsplit(handler.path).query:raise ValueError('Corpus endpoints have no query parameters.')
    service=handler.server.corpus
    if path=='/api/corpus':value=service.corpus.catalogue()
    elif path=='/api/corpus/runs':value=service.list()
    else:
        try:value=service.report(path.rsplit('/',1)[1])
        except KeyError:
            handler.send(404,{'status':'rejected','diagnostics':[{'code':'CORPUS_NOT_FOUND','message':'Unknown corpus run.'}]});return True
    handler.send(200,value);return True


def post(handler,path,payload):
    match=re.fullmatch(r'/api/corpus/runs/([0-9a-f]{32})/cancel',path)
    if match:
        if payload!={} or urlsplit(handler.path).query:raise ValueError('Corpus cancellation has no parameters.')
        handler.send(202,handler.server.corpus.cancel(match[1]));return True
    if path!='/api/corpus/runs':return False
    if urlsplit(handler.path).query:raise ValueError('Corpus endpoints have no query parameters.')
    handler.send(202,handler.server.corpus.create(payload));return True
