"""Explicit loopback client: the running server remains the sole catalog owner."""
from http.client import HTTPConnection, HTTPException
import time
from urllib.parse import urlsplit
from packages.contracts.wire import canonical, loads, read_json


class LocalClient:
    def __init__(self, url):
        parsed = urlsplit(url)
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost')
                or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ValueError('Use an explicit http://127.0.0.1:PORT loopback server URL.')
        self.host, self.port = parsed.hostname, parsed.port or 80
        self.token = None

    def call(self, path, payload=None):
        if not path.startswith('/api/') or '\r' in path or '\n' in path:raise ValueError('Invalid local API path.')
        if payload is not None and self.token is None:self.token = self.call('/api/config')['token']
        raw = canonical(payload) if payload is not None else None
        if raw and len(raw) > 16*1024*1024:raise ValueError('Request exceeds the 16 MiB local API limit.')
        headers = {'Content-Type': 'application/json', 'X-OpenPine-Token': self.token} if raw is not None else {}
        connection = HTTPConnection(self.host, self.port, timeout=120)
        try:
            connection.request('POST' if payload is not None else 'GET', path, body=raw, headers=headers)
            response = connection.getresponse()
            body = response.read(32*1024*1024+1)
            if len(body) > 32*1024*1024:raise ValueError('Local API response exceeds 32 MiB.')
            value = loads(body, max_bytes=32*1024*1024)
            if response.status >= 300:raise ValueError(f'Local API {response.status}: '+str(value))
            return value
        except HTTPException as error:raise ValueError('Local HTTP protocol error: '+str(error)) from error
        finally:connection.close()


def research_command(args, kind):
    client = LocalClient(args.server)
    collection = {'search': 'searches', 'experiment': 'experiments', 'walkforward': 'walkforwards'}[kind]
    path = '/api/'+collection
    identifier = args.id
    result = None
    if args.request:
        payload = read_json(args.request)
        if args.source:
            response = client.call('/api/compile', {'source': args.source.read_text(encoding='utf-8'), 'source_name': args.source.name})
            if response.get('status') != 'compiled':raise ValueError(str(response))
            payload['input_ref'] = response['input_ref']
        if args.bars:
            payload['snapshot_id'] = client.call('/api/snapshots', {'provider': 'upload', 'raw': args.bars.read_text(encoding='utf-8')})['sha256']
        result = client.call(path, payload)
        identifier = result[kind+'_id']
    item = path+'/'+str(identifier)
    if args.action == 'list':return client.call(path), 0
    if args.action == 'status':return client.call(item+f'?start={args.start}&limit={args.limit}'), 0
    if args.action == 'report':return client.call(item+'/report'), 0
    if args.action.startswith('delete-'):
        action = 'preview' if args.action == 'delete-preview' else 'delete'
        return client.call(f'/api/research/{kind}/{identifier}/{action}', {} if action == 'preview' else {'preview_id': args.preview_id}), 0
    if args.action == 'rerun':return client.call(item+'/rerun', {'ordinal': args.ordinal, 'phase': args.phase}), 0
    if args.action in ('pause', 'resume', 'cancel'):return client.call(item+'/'+args.action, {}), 0
    if args.action == 'run':
        client.call(item+'/resume', {})
        try:
            while True:
                result = client.call(item+f'?start={args.start}&limit={args.limit}')
                if result['status'] not in ('running', 'selecting', 'checking_holdout'):break
                time.sleep(.2)
        except KeyboardInterrupt:result = client.call(item+'/pause', {})
        return result, 0 if result['status'] in ('succeeded', 'paused') else 2
    return result, 0
