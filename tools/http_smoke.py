#!/usr/bin/env python3
"""Exercise real HTTP event learning. Uses synthetic text; no private input."""
import argparse
import json
import re
import time
import urllib.error
import urllib.request


def run(url):
    client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def req(path,body=None,headers=None):
        raw=None if body is None else json.dumps(body,ensure_ascii=False).encode()
        r=urllib.request.Request(url.rstrip('/')+path,data=raw,
                                 headers={'Content-Type':'application/json',**(headers or {})})
        try:
            with client.open(r,timeout=5) as response:return response.status,response.read().decode()
        except urllib.error.HTTPError as e:return e.code,e.read().decode()
    code,data=req('/healthz')
    assert code==200 and json.loads(data)['device']=='cpu'
    code,html=req('/')
    assert code==200
    match=re.search(r"const TOKEN='([^']+)'",html)
    assert match and match.group(1)!='__SESSION_TOKEN__'
    headers={'X-Tab-Token':match.group(1)}
    assert req('/trace')[0]==403
    assert req('/',headers={'Host':'attacker.invalid'})[0]==403
    assert req('/reset',{},headers)[0]==200
    event={'doc_id':'smoke','sequence':1,'kind':'insert','start':0,'end':0,
           'text':'コンピュータ。コンピュータ。コンピ','allowed':True,'origin':'typed'}
    code,result=req('/event',event,headers)
    assert code==200
    for _ in range(200):
        value=json.loads(result)
        if value.get('suggestion')=='ュータ':break
        time.sleep(.02)
        code,result=req('/suggestion',{'doc_id':'smoke','version':1},headers)
        assert code==200
    else:raise AssertionError('asynchronous learner did not produce the expected term')
    code,result=req('/event',event,headers)
    assert code==200 and json.loads(result)['duplicate'] is True
    assert req('/reset',{},headers)[0]==200
    print(json.dumps({'health':'PASS','authorization':'PASS','host_guard':'PASS',
                      'online_learning':'PASS','deduplication':'PASS','reset':'PASS'},ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:8765')
    run(p.parse_args().url)
