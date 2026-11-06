"""Real loopback HTTP tests (not browser/IME end-to-end tests)."""
from pathlib import Path
import json
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture
def endpoint():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen([sys.executable, '-S', str(ROOT/'serve_demo.py'), '--port', str(port), '--learning', 'sync'],
                               cwd=ROOT.parent, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    url = f'http://127.0.0.1:{port}'
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        for _ in range(100):
            if process.poll() is not None:
                pytest.fail(process.stderr.read().decode())
            try:
                with client.open(url, timeout=.5) as response:
                    html = response.read().decode()
                token = re.search(r"const TOKEN='([^']+)'", html).group(1)
                assert token != '__SESSION_TOKEN__'
                break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(.03)
        else:
            pytest.fail('Local server did not become ready')
        def request(path, payload=None, authorized=True, origin=None):
            headers = {'Content-Type':'application/json'}
            if authorized:
                headers['X-Tab-Token'] = token
            if origin:
                headers['Origin'] = origin
            data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
            req = urllib.request.Request(url+path, data=data, headers=headers)
            try:
                with client.open(req, timeout=3) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read())
        yield request
    finally:
        process.terminate()
        process.wait(timeout=5)
        if process.stderr:
            process.stderr.close()

def event(sequence=1, text='コンピュータ。コンピュータ。コンピ', **extra):
    return {'doc_id':'test', 'sequence':sequence, 'kind':'insert', 'start':0, 'end':0,
            'text':text, 'origin':'typed', 'allowed':True, **extra}

def test_http_rejects_unauthorized_requests(endpoint):
    assert endpoint('/event', event(), authorized=False)[0] == 403
    assert endpoint('/event', event(), origin='https://untrusted.example')[0] == 403
    assert endpoint('/trace', authorized=False)[0] == 403

def test_http_learns_and_deduplicates(endpoint):
    status, result = endpoint('/event', event())
    assert status == 200 and result['suggestion'] == 'ュータ'
    assert result['typed_training_chars'] == len(event()['text'])
    status, duplicate = endpoint('/event', event())
    assert status == 200 and duplicate['duplicate'] is True

def test_http_does_not_learn_without_permission(endpoint):
    status, result = endpoint('/event', event(allowed=False))
    assert status == 200 and result['recorded'] is False
    assert endpoint('/event', event())[1]['suggestion'] == 'ュータ'

def test_http_reset_clears_session(endpoint):
    assert endpoint('/event', event())[0] == 200
    assert endpoint('/reset', {}) == (200, {'reset':True})
    status, result = endpoint('/event', event(text='コンピ'))
    assert status == 200 and result['typed_training_chars'] == 3
    assert result['suggestion'] == ''
