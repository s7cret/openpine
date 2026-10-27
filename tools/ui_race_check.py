"""Serve an isolated in-memory research UI race fixture; no catalog is opened."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):pass

    def do_GET(self):
        paths = {'/': (ROOT/'tests/ui/research-races.html', 'text/html; charset=utf-8'),
                 '/preparation': (ROOT/'tests/ui/preparation-races.html', 'text/html; charset=utf-8'),
                 '/network.js': (ROOT/'apps/ui/static/network.js', 'text/javascript; charset=utf-8'),
                 '/research.js': (ROOT/'apps/ui/static/research.js', 'text/javascript; charset=utf-8')}
        if self.path not in paths:self.send_error(404);return
        path, mime = paths[self.path]
        raw = path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers();self.wfile.write(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args(argv)
    with ThreadingHTTPServer(('127.0.0.1', args.port), Handler) as server:
        print(f'Research UI race fixture: http://127.0.0.1:{server.server_port}', flush=True)
        try:server.serve_forever(poll_interval=.1)
        except KeyboardInterrupt:pass
    return 0


if __name__ == '__main__':raise SystemExit(main())
