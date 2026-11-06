"""Container-facing HTTP regressions; actual sockets, no web mocking."""
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]


def test_explicit_container_bind_and_guards():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    proc = subprocess.Popen([sys.executable, '-S', str(ROOT/'serve_demo.py'),
                             '--port', str(port), '--host', '0.0.0.0', '--container-mode'],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = f'http://127.0.0.1:{port}'
    def get(path, **headers):
        try:
            with client.open(urllib.request.Request(base+path, headers=headers), timeout=2) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()
    try:
        for _ in range(60):
            if proc.poll() is not None:
                assert False, proc.stderr.read().decode()
            try:
                status, text = get('/healthz')
                break
            except urllib.error.URLError:
                time.sleep(.03)
        else:
            assert False, 'server did not start'
        assert status == 200
        assert json.loads(text) == {'status':'ok', 'device':'cpu', 'storage':'memory'}
        status, page = get('/')
        assert status == 200 and '__SESSION_TOKEN__' not in page
        assert get('/', Host=f'attacker.example:{port}')[0] == 403
        assert get('/', Origin='https://attacker.example')[0] == 403
        assert get('/trace')[0] == 403
        assert get('/trace', **{'X-Tab-Token':'é'})[0] == 403
        token = re.search(r"const TOKEN='([^']+)'", page).group(1)
        assert get('/trace', **{'X-Tab-Token':token})[0] == 200
    finally:
        proc.terminate()
        proc.communicate(timeout=5)


def test_wildcard_bind_requires_explicit_container_mode():
    p = subprocess.run([sys.executable, '-S', str(ROOT/'serve_demo.py'), '--host', '0.0.0.0'],
                       capture_output=True, text=True, timeout=5)
    assert p.returncode != 0
    assert '--container-mode' in p.stderr
