"""Bounded catalog and live/storage telemetry for the local market panel."""
from packages.contracts.wire import loads
from packages.storage.live import LiveRepository
from apps.openpine.market_maintenance import MarketMaintenance


def overview(manager,live,*,start=0,limit=50):
    if type(start) is not int or not 0<=start<=2**63-1 or type(limit) is not int or not 1<=limit<=100:raise ValueError('Catalog requires start >= 0 and limit 1..100.')
    with manager.limiter.slot('read'):
        db=manager.db;datasets=[]
        rows=db.execute('SELECT snapshot_id FROM datasets ORDER BY created_at DESC,snapshot_id LIMIT ? OFFSET ?',(limit,start))
        for (snapshot_id,) in rows:
            manifest=manager.market.manifest(snapshot_id);identity=manifest['identity'];parts={p['object_id'] for p in manifest['parts']}
            task=db.execute("SELECT state FROM data_tasks WHERE json_extract(CAST(state AS TEXT),'$.snapshot_id')=? ORDER BY created_at DESC LIMIT 1",(snapshot_id,))
            report=loads(task[0][0]).get('coverage') if task else None
            datasets.append({'snapshot_id':snapshot_id,'symbol':identity['symbol'],'timeframe':identity['timeframe'],
                'bars':identity['count'],'first_ms':identity['first_ms'],'last_ms':identity['last_ms'],
                'provider':manifest['provenance'].get('provider'),'object_bytes':sum(manager.objects.path(sha).stat().st_size for sha in parts),
                'coverage':report,'kind':'snapshot' if task else 'page_or_live_segment'})
        total=db.execute('SELECT COUNT(*) FROM datasets')[0][0];feeds=[];repository=LiveRepository(db,manager.objects)
        for identifier,config,raw in db.execute("SELECT feed_id,config,state FROM live_feeds ORDER BY json_extract(CAST(state AS TEXT),'$.updated_at') DESC LIMIT 100"):
            config=loads(config);state=loads(raw);subscribers=repository.sessions(identifier,active=True)
            feeds.append({'feed_id':identifier,'instrument':config['instrument'],'timeframe':config['timeframe'],
                'status':state.get('status'),'error':state.get('error'),'accepted':state.get('accepted',0),
                'last_received_at':state.get('last_received_at'),'reconnects':state.get('reconnects',0),'subscribers':len(subscribers),
                'max_pending_records':max((state.get('accepted',0)-repository.cursor(s,identifier) for s in subscribers),default=0)})
        objects=manager.objects.root
        if objects.is_symlink():raise ValueError('Unsafe storage object root.')
        count=0;size=0
        for path in objects.glob('[0-9a-f][0-9a-f]/*'):
            if path.is_file() and not path.is_symlink() and not path.parent.is_symlink():
                try:size+=path.stat().st_size;count+=1
                except FileNotFoundError:pass
        storage={'object_files':count,'object_bytes':size,
            'catalog_bytes':sum(p.stat().st_size for p in manager.directory.glob('catalog.sqlite3*') if p.is_file()),
            **MarketMaintenance(manager).segments()}
        return {'datasets':datasets,'total':total,'next':start+len(rows),'feeds':feeds,
            'feed_total':db.execute('SELECT COUNT(*) FROM live_feeds')[0][0],
            'active_sessions':db.execute("SELECT COUNT(*) FROM live_sessions WHERE desired='running'")[0][0],
            'storage':storage,'execution':live.execution_metrics()}
