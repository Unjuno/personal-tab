"""Enforce a single server-side writer lease across independently opened tabs."""
import json
from pathlib import Path
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
def client():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    proc = subprocess.Popen([sys.executable, '-S', str(ROOT / 'serve_demo.py'), '--port', str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    base = f'http://127.0.0.1:{port}'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        for _ in range(200):
            if proc.poll() is not None:
                pytest.fail(proc.stderr.read().decode())
            try:
                opener.open(base, timeout=.25).read()
                break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(.025)
        else:
            pytest.fail('server failed to start')

        def tab():
            html = opener.open(base, timeout=2).read().decode()
            return re.search(r"const TOKEN='([^']+)'", html).group(1)

        def req(token, path, data=None):
            payload = None if data is None else json.dumps(data, ensure_ascii=False).encode('utf8')
            request = urllib.request.Request(base + path, data=payload, headers={
                'X-Tab-Token': token, 'Content-Type': 'application/json'})
            try:
                with opener.open(request, timeout=5) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as error:
                return error.code, json.loads(error.read())

        yield tab, req
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        proc.stderr.close()


def test_tabs_have_distinct_tokens_and_second_tab_cannot_edit(client):
    tab, req = client
    first, second = tab(), tab()
    assert first != second
    status, claim = req(first, '/lease', {'action': 'claim'})
    assert status == 200 and claim['active']
    assert req(second, '/lease', {'action': 'claim'})[0] == 409
    edit = dict(doc_id='document', sequence=1, allowed=True, kind='insert',
                start=0, end=0, text='保護した文章。', origin='typed')
    assert req(second, '/event', edit)[0] == 409
    assert req(first, '/event', edit)[0] == 200
    assert req(second, '/memory')[0] == 409
    assert req(second, '/reset', {})[0] == 409
    assert req(first, '/release', {})[0] == 200
    status, claim = req(second, '/lease', {'action': 'claim'})
    assert status == 200 and claim['active']
    assert claim['text'] == '保護した文章。' and claim['sequence'] == 1
    assert req(first, '/event', dict(edit, sequence=2, start=6, end=6, text='x'))[0] == 403


def test_browser_refresh_new_token_can_claim_after_release(client):
    tab, req = client
    first = tab()
    assert req(first, '/lease', {'action': 'claim'})[0] == 200
    assert req(first, '/release', {}) == (200, {'released': True})
    second = tab()
    assert req(second, '/lease', {'action': 'claim'})[0] == 200


def test_released_capability_is_revoked_even_before_new_tab_claims(client):
    tab, req = client
    first = tab()
    assert req(first, '/lease', {'action': 'claim'})[0] == 200
    assert req(first, '/release', {})[0] == 200
    assert req(first, '/status')[0] == 403
