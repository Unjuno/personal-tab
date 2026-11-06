"""Real HTTP, real spawned learner. No mock inference or transport."""
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
import pytest

ROOT=Path(__file__).resolve().parents[1]
TEXT='コンピュータ。コンピュータ。コンピ'

@pytest.fixture
def endpoint():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    proc=subprocess.Popen([sys.executable,'-S',str(ROOT/'serve_demo.py'),'--port',str(port)],
                          stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base=f'http://127.0.0.1:{port}'
    try:
        for _ in range(200):
            if proc.poll() is not None:pytest.fail(proc.stderr.read().decode())
            try:
                html=client.open(base,timeout=.5).read().decode();break
            except (urllib.error.URLError,TimeoutError):time.sleep(.02)
        else:pytest.fail('server startup timeout')
        token=re.search(r"const TOKEN='([^']+)'",html).group(1)
        def req(path,body=None,auth=True):
            raw=None if body is None else json.dumps(body,ensure_ascii=False).encode()
            r=urllib.request.Request(base+path,data=raw,headers={
                'Content-Type':'application/json',**({'X-Tab-Token':token} if auth else {})})
            try:
                with client.open(r,timeout=5) as res:return res.status,json.loads(res.read())
            except urllib.error.HTTPError as e:return e.code,json.loads(e.read())
        yield req
    finally:
        proc.terminate();proc.wait(timeout=5)
        proc.stderr.close()

def event(seq=1,text=TEXT,**kw):
    return dict(doc_id='d',sequence=seq,kind='insert',start=0,end=0,text=text,origin='typed',allowed=True,**kw)

def ready(req,version=1):
    for _ in range(400):
        status,result=req('/suggestion',dict(doc_id='d',version=version))
        assert status==200,result
        if not result['learning_pending']:return result
        time.sleep(.01)
    pytest.fail('background learner timeout')

def test_default_is_async_and_real_learning_completes(endpoint):
    code,state=endpoint('/status')
    assert code==200 and state['learning_mode']=='async'
    code,result=endpoint('/event',event())
    assert code==200 and result['recorded']
    assert ready(endpoint)['suggestion']=='ュータ'
    assert endpoint('/event',event())[1]['duplicate']

def test_http_rejects_stale_candidate_and_invalid_payload(endpoint):
    endpoint('/event',event());ready(endpoint)
    code,r=endpoint('/suggestion',dict(doc_id='d',version=0))
    assert code==200 and r['stale'] and not r['suggestion']
    assert endpoint('/suggestion',dict(doc_id='d'),auth=False)[0]==403
    assert endpoint('/event',[])[0]==400
    assert endpoint('/suggestion',dict(doc_id='d',cursor=-1))[0]==400
    assert endpoint('/status',auth=False)[0]==403

def test_reset_never_recreates_old_memory(endpoint):
    endpoint('/event',event());ready(endpoint)
    assert endpoint('/reset',{})==(200,{'reset':True})
    endpoint('/event',event(text='コンピ'))
    result=ready(endpoint)
    assert not result['suggestion'] and result['typed_training_chars']==3
    trace=endpoint('/trace')[1]
    assert 'コンピュータ' not in json.dumps(trace,ensure_ascii=False)


def test_http_json_memory_roundtrip_and_consent(endpoint):
    endpoint('/event',event());ready(endpoint)
    code,memory=endpoint('/memory')
    assert code==200 and memory['schema']=='personal-tab-memory/v1'
    assert endpoint('/memory',{'memory':memory})[0]==400
    endpoint('/reset',{})
    assert endpoint('/memory',{'allowed':True,'memory':memory})[0]==200
    assert ready(endpoint)['suggestion']=='ュータ'
    malformed={'schema':'personal-tab-memory/v1','documents':[{'id':'d','text':'x','spans':[]}]}
    assert endpoint('/memory',{'allowed':True,'memory':malformed})[0]==400
    assert ready(endpoint)['suggestion']=='ュータ'
