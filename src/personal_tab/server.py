#!/usr/bin/env python3
"""Opt-in local CPU editor; asynchronous statistical learning by default.

No network model service or automatic disk persistence. HTTP binds to loopback
unless explicitly running inside a container with a loopback-published port.
"""
from __future__ import annotations

import argparse
import json
import secrets
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from personal_tab.session import Session

TOKEN = secrets.token_urlsafe(32)
STATE = None  # Never start a learner as an import side effect (spawn safety).
LOCK = threading.Lock()
ROOT = Path(__file__).resolve().parent / 'web'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Never log user text, tokens or request bodies.

    def send(self, status, payload, mime='application/json; charset=utf-8'):
        body = (payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)).encode('utf8')
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def local_request(self):
        port = self.server.server_address[1]
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        origins = {None, *(f'http://{host}' for host in hosts)}
        return self.headers.get('Host') in hosts and self.headers.get('Origin') in origins

    def authorized(self):
        token = self.headers.get('X-Tab-Token', '')
        return self.local_request() and token.isascii() and secrets.compare_digest(token, TOKEN)

    def do_GET(self):
        if not self.local_request():
            self.send(403, {'error': 'forbidden'}); return
        if self.path == '/healthz':
            self.send(200, {'status': 'ok', 'device': 'cpu', 'storage': 'memory'})
        elif self.path == '/':
            html = (ROOT / 'live.html').read_text(encoding='utf-8').replace('__SESSION_TOKEN__', TOKEN)
            self.send(200, html, 'text/html; charset=utf-8')
        elif self.path == '/trace' and self.authorized():
            with LOCK:
                payload = STATE.trace.export()
            self.send(200, payload)
        elif self.path == '/memory' and self.authorized():
            with LOCK:
                payload = STATE.export_memory() if hasattr(STATE, 'export_memory') else {'error': 'requires async mode'}
            self.send(200, payload)
        elif self.path == '/status' and self.authorized():
            with LOCK:
                payload = {'learning_mode': self.server.learning_mode, 'device': 'cpu'}
                if hasattr(STATE, 'worker'):
                    payload['worker'] = STATE.worker.stats()
                    payload['epoch'] = STATE.epoch
            self.send(200, payload)
        else:
            self.send(403, {'error': 'forbidden'})

    def do_POST(self):
        global STATE
        if not self.authorized():
            self.send(403, {'error': 'forbidden'}); return
        if self.path not in {'/event', '/reset', '/suggestion', '/memory'}:
            self.send(404, {'error': 'not found'}); return
        try:
            self.connection.settimeout(5)
            n = int(self.headers.get('Content-Length', '0'))
            if not 0 <= n <= 1_000_000:
                raise ValueError('request too large')
            event = json.loads(self.rfile.read(n) or b'{}')
            if not isinstance(event, dict):
                raise ValueError('request must be an object')
            with LOCK:
                if self.path == '/reset':
                    if hasattr(STATE, 'reset'):
                        STATE.reset()
                    else:
                        STATE = Session()
                    result = {'reset': True}
                elif self.path == '/memory':
                    if not hasattr(STATE, 'restore_memory') or event.get('allowed') is not True:
                        raise ValueError('explicit import consent and async mode required')
                    STATE.restore_memory(event.get('memory'))
                    result = {'restored': True, 'text': STATE.trace.texts.get('document', ''),
                              'version': STATE.trace.versions.get('document', 0)}
                elif self.path == '/suggestion':
                    if not hasattr(STATE, 'suggest'):
                        raise ValueError('polling requires asynchronous mode')
                    doc = event.get('doc_id')
                    if not isinstance(doc, str) or len(doc) > 128:
                        raise ValueError('invalid doc_id')
                    version = event.get('version')
                    if version is not None and type(version) is not int:
                        raise ValueError('invalid version')
                    result = STATE.suggest(doc_id=doc, version=version,
                                           cursor=event.get('cursor'), mode=event.get('mode', 'terms'))
                else:
                    result = STATE.apply(event)
            self.send(200, result)
        except (ValueError, KeyError, TypeError) as exc:
            self.send(400, {'error': str(exc)})
        except TimeoutError:
            self.send(408, {'error': 'request timeout'})


def main():
    global STATE
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--host', choices=['127.0.0.1', '0.0.0.0'], default='127.0.0.1')
    parser.add_argument('--container-mode', action='store_true',
                        help='Allow wildcard bind inside a loopback-published container only')
    parser.add_argument('--learning', choices=['async', 'sync'], default='async',
                        help='CPU background worker by default; sync is a diagnostic baseline')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    if args.host == '0.0.0.0' and not args.container_mode:
        parser.error('Wildcard binding requires --container-mode and loopback-only port publishing')
    # Bind before spawning, so an occupied port does not leak a worker.
    with ThreadingHTTPServer((args.host, args.port), Handler) as server:
        server.daemon_threads = True
        server.learning_mode = args.learning
        if args.learning == 'async':
            from personal_tab.async_memory import AsyncSession
            STATE = AsyncSession()
        else:
            STATE = Session()
        def stop(*_):
            raise KeyboardInterrupt
        old_term = signal.signal(signal.SIGTERM, stop)
        print(f'Local research demo: http://127.0.0.1:{args.port} — CPU, {args.learning}, in-memory only', flush=True)
        try:
            server.serve_forever(poll_interval=.1)
        except KeyboardInterrupt:
            pass
        finally:
            signal.signal(signal.SIGTERM, old_term)
            if hasattr(STATE, 'close'):
                STATE.close()


if __name__ == '__main__':
    main()
