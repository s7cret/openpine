"""Market HTTP routes submit work and read bounded views; no network in handlers."""

import re
from urllib.parse import parse_qs, urlsplit

from packages.marketdata.timeframes import capabilities


def query(handler,names):
    fields=parse_qs(urlsplit(handler.path).query,strict_parsing=True,keep_blank_values=True)
    if set(fields)-set(names) or any(len(v)!=1 for v in fields.values()):raise ValueError('Invalid market query parameters.')
    return {k:v[0] for k,v in fields.items()}


def get(handler,path):
    server=handler.server
    if path=='/api/market/capabilities':value=capabilities()
    elif path=='/api/market/storage/segments':
        from apps.openpine.market_maintenance import MarketMaintenance
        value=MarketMaintenance(server.jobs,live=server.live).segments()
    elif path=='/api/market/instruments':
        params=query(handler,('q','limit'))
        value={'instruments':server.history.catalog.search(params.get('q',''),int(params.get('limit','100')))}
    elif path=='/api/market/history':value=server.history.repository.tasks()
    elif match:=re.fullmatch(r'/api/market/history/([0-9a-f]{32})',path):value=server.history.status(match[1])
    elif path=='/api/market/live':
        value=[{'session_id':s['session_id'],'feed_id':s['feed_id'],'desired':s['desired'],'status':s['status'],
            'created_at':s['created_at']} for s in server.live.repository.sessions()]
    elif match:=re.fullmatch(r'/api/market/live/([0-9a-f]{32})(/(chart|ledger|journal))?',path):
        if match[3]:
            params=query(handler,('start','limit'));start=int(params.get('start','0'));limit=int(params.get('limit','1000'))
            if match[3]=='journal':
                if not 0<=start or not 1<=limit<=1000:raise ValueError('Journal requires a nonnegative start and limit 1..1000.')
                state=server.live.repository.session(match[1])
                with server.jobs.limiter.slot('read'):
                    records=list(server.live.repository.records(state['feed_id'],start,limit))
                value={'records':records,'next':start+len(records),'total':server.live.repository.state(state['feed_id'])['accepted']}
            else:value=server.live.view(match[1],match[3],start,limit)
        else:value=server.live.status(match[1])
    else:return False
    handler.send(200,value);return True


def post(handler,path,payload):
    server=handler.server
    if path.startswith('/api/market/maintenance/'):
        from apps.openpine.market_maintenance import MarketMaintenance
        maintenance=MarketMaintenance(server.jobs,live=server.live)
        if path=='/api/market/maintenance/rotate':
            if payload!={}:raise ValueError('Rotation takes no fields.')
            value=maintenance.rotate()
        elif path in ('/api/market/maintenance/preview','/api/market/maintenance/apply'):
            names={'before_ms','grace_seconds'}|({'preview_id'} if path.endswith('/apply') else set())
            if type(payload) is not dict or payload.keys()!=names:raise ValueError('Invalid reviewed maintenance fields.')
            value=maintenance.apply(payload['before_ms'],payload['preview_id'],grace_seconds=payload['grace_seconds']) if path.endswith('/apply') else maintenance.preview(**payload)
        else:return False
        status=200
    elif path=='/api/market/history':value=server.history.submit(payload);status=202
    elif path=='/api/market/catalog/refresh':
        if payload!={}:raise ValueError('Catalogue refresh takes no fields.')
        value=server.history.refresh_catalog();status=202
    elif match:=re.fullmatch(r'/api/market/history/([0-9a-f]{32})/(cancel|resume)',path):
        if payload!={}:raise ValueError('Acquisition actions take no fields.')
        value=getattr(server.history,match[2])(match[1]);status=200
    elif path=='/api/market/live':value=server.live.create(payload);status=202
    elif match:=re.fullmatch(r'/api/market/live/([0-9a-f]{32})/(stop|resume)',path):
        if payload!={}:raise ValueError('Live actions take no fields.')
        value=getattr(server.live,match[2])(match[1]);status=200
    else:return False
    handler.send(status,value);return True
