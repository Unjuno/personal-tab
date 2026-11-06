"""Causal lightweight terminology completion baseline; no future token boundaries."""
from __future__ import annotations
from collections import defaultdict
import math,pickle,time,json,gzip,itertools


def script(c):
    if '\u4e00'<=c<='\u9fff' or c in '々ヶ':return 'han'
    if '\u30a1'<=c<='\u30fa' or c=='ー':return 'katakana'
    if c.isascii() and c.isalpha():return 'latin'
    return None

class TermMemory:
    def __init__(self,minimum_prefix=2,minimum_count=1,max_length=8,frozen=False):
        self.minimum_prefix=minimum_prefix;self.minimum_count=minimum_count
        self.max_length=max_length;self.frozen=frozen;self.seen=0;self.pending='';self.kind=None;self.doc=''
        self.words={};self.index=defaultdict(set);self._fitting=False

    def _commit(self):
        w=self.pending
        if not 4<=len(w)<=24 or (self.frozen and not self._fitting):return
        n,_,_=self.words.get(w,(0,0,''));self.words[w]=(n+1,self.seen,self.doc)
        for k in range(2,min(6,len(w)-2)+1):self.index[w[:k]].add(w)

    def observe(self,char):
        if len(char)!=1:raise ValueError('one character')
        k=script(char)
        if k!=self.kind or k is None:
            self._commit();self.pending='';self.kind=k
        if k:self.pending+=char
        self.seen+=1

    def begin_doc(self,title):
        self.pending='';self.kind=None;self.doc=title

    def fit(self,docs):
        self._fitting=True
        for i,t in enumerate(docs):
            self.begin_doc(f'train:{i}')
            for c in t:self.observe(c)
            self._commit() # explicit imported-document end is observable
        self._fitting=False;self.begin_doc('')

    def suggest(self,context=None):
        if context is None:prefix=self.pending
        else:
            prefix='';last=script(context[-1]) if context else None
            if last:
                for c in reversed(context[-6:]):
                    if script(c)!=last:break
                    prefix=c+prefix
        candidates={}
        for k in range(self.minimum_prefix,min(6,len(prefix))+1):
            for word in sorted(self.index.get(prefix[-k:],())):
                n,stamp,doc=self.words[word]
                if n<self.minimum_count:continue
                text=word[k:k+self.max_length]
                if len(text)<2:continue
                # Each occurrence is counted once; multiple prefix matches do
                # not multiply support as in the older pre-indexed baseline.
                score=2*k+math.log1p(n)+.5*(doc==self.doc)+.1/(1+(self.seen-stamp)/5000)
                candidates[text]=max(candidates.get(text,-1e9),score)
        return min(candidates,key=lambda c:(-candidates[c],c)) if candidates else ''


def replay_terms(m,docs):
    import numpy as np
    records=[];times=[];up=[]
    for title,text in docs:
        m.begin_doc(title);rows=[]
        for pos,c in enumerate(text):
            t=time.perf_counter_ns();p=m.suggest();times.append((time.perf_counter_ns()-t)/1e6)
            rows.append({'pos':pos,'prediction':p,'products':[1.]*len(p),'next_prob':1.,'next_correct':False,'weights':None})
            t=time.perf_counter_ns();m.observe(c);up.append((time.perf_counter_ns()-t)/1e6)
        records.append({'title':title,'text':text,'rows':rows})
    return records,{'query_p50_ms':float(np.median(times)),'query_p95_ms':float(np.percentile(times,95)),'update_p95_ms':float(np.percentile(up,95)),
                    'state_pickle_bytes':len(pickle.dumps(m)),'unique_words':len(m.words),'query_samples':len(times)}


def run():
    from experiments.benchmark import load,score,dump,OUT,digest_docs
    train,val=load('train'),load('validation');gate={'threshold':1,'min_len':2,'max_len':8};rows=[]
    for prefix,count,length in itertools.product([2,3],[1,2,3],[4,8]):
        cfg=dict(minimum_prefix=prefix,minimum_count=count,max_length=length)
        m=TermMemory(**cfg);m.fit([t for _,t in train]);rec,perf=replay_terms(m,val);s=score(rec,gate)
        rows.append({'config':cfg,'validation':s})
    eligible=[r for r in rows if r['validation']['shown']>=20 and (r['validation']['precision'] or 0)>=.55]
    best=max(eligible or rows,key=lambda r:r['validation']['utility1'])
    lock={'selected':best,'grid':rows,'gate':gate,'train_sha256':digest_docs(train),'validation_sha256':digest_docs(val),'quality_constraint_met':bool(eligible)}
    dump(OUT/'terms_locked.json',lock)
    docs=load('test_in_domain')+load('test_shift');res={}
    for frozen,name in [(True,'terms_fixed'),(False,'terms_online')]:
        m=TermMemory(**best['config'],frozen=frozen);m.fit([t for _,t in train]);rec,perf=replay_terms(m,docs);s=score(rec,gate)
        s['next_accuracy']=None;s['bits_per_char']=None # term model is NOT a normalized LM
        with gzip.open(OUT/(name+'_test_records.json.gz'),'wt',encoding='utf8') as f:json.dump(rec,f,ensure_ascii=False)
        res[name]={'config':best['config'],'gate':gate,'metrics':s,'performance':perf}
        print(name,best['config'],{k:s[k] for k in ['shown','hit','saved','precision','saving_rate']},flush=True)
    dump(OUT/'terms_results.json',{'test_sha256':digest_docs(docs),'results':res})
if __name__=='__main__':run()
