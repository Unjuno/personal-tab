"""Causal, CPU-only personalized completion research engine.

No reference suffix is accepted by the predictor API. Characters are Python
Unicode code points, not IME key strokes or grapheme clusters. Counts implement
interpolated variable-order Markov prediction (PPM-inspired, not canonical PPM).
"""
from __future__ import annotations
from collections import Counter, deque
from dataclasses import dataclass
import copy
import hashlib
import json
import math
import pickle
import time

UNICODE_SIZE = 0x110000

@dataclass
class Forecast:
    # Each component contributes coefficient * count(character).
    components: list[tuple[Counter, float, tuple[str, ...]]]
    uniform_mass: float

    def prob(self, char: str) -> float:
        return self.uniform_mass / UNICODE_SIZE + sum(c.get(char, 0) * w for c, w, _ in self.components)

    def top(self, n: int = 1) -> list[tuple[str, float]]:
        candidates = set()
        for _, _, chars in self.components:
            candidates.update(chars)
        return sorted(((c, self.prob(c)) for c in candidates), key=lambda z: (-z[1], z[0]))[:n]

    def total_mass(self) -> float:
        return self.uniform_mass + sum(sum(c.values()) * w for c, w, _ in self.components)

    @staticmethod
    def mix(forecasts: list[Forecast], weights: list[float]) -> Forecast:
        if len(forecasts) != len(weights) or abs(sum(weights) - 1) > 1e-8:
            raise ValueError('Mixture weights must be normalized and aligned.')
        return Forecast([(c,w*a,chars) for f,a in zip(forecasts,weights) for c,w,chars in f.components],
                        sum(a*f.uniform_mass for a,f in zip(weights,forecasts)))

class NGram:
    def __init__(self, order: int = 6, alpha: float = 0.5, window: int | None = None):
        if order < 0 or alpha <= 0 or (window is not None and window < 1):
            raise ValueError('Invalid order, escape weight or window.')
        self.order, self.alpha, self.window = order, alpha, window
        self.table: dict[str, Counter] = {}
        self.totals: dict[str, int] = {}
        self.queue: deque[tuple[str,str]] = deque()
        self.top_cache: dict[str, tuple[str,...]] = {}
        self.seen = 0

    def _adjust(self, context: str, char: str, delta: int) -> None:
        for k in range(min(self.order, len(context)) + 1):
            key=context[-k:] if k else ''
            if key not in self.table:
                if delta < 0: raise AssertionError('negative unseen transition')
                self.table[key]=Counter(); self.totals[key]=0
            counter=self.table[key]
            counter[char] += delta
            self.totals[key] += delta
            if counter[char] == 0: del counter[char]
            if not counter:
                del self.table[key]; del self.totals[key]
            # Unigram top-list is an approximate candidate-search cache, not
            # an approximation to the probabilities used in log-loss.
            if k or self.seen % 64 == 0:
                self.top_cache.pop(key,None)

    def observe(self, context: str, char: str) -> None:
        if len(char) != 1: raise ValueError('observe takes one Unicode code point')
        context=context[-self.order:] if self.order else ''
        self._adjust(context,char,1)
        self.seen += 1
        if self.window is not None:
            self.queue.append((context,char))
            if len(self.queue)>self.window:
                old_context,old_char=self.queue.popleft()
                self._adjust(old_context,old_char,-1)

    def forecast(self, context: str) -> Forecast:
        components=[]; remaining=1.0
        for k in range(min(self.order,len(context)),-1,-1):
            key=context[-k:] if k else ''
            counter=self.table.get(key)
            if not counter: continue
            total=self.totals[key]
            escape=self.alpha*len(counter)
            denominator=total+escape
            coefficient=remaining/denominator
            if key not in self.top_cache:
                self.top_cache[key]=tuple(c for c,_ in counter.most_common(12))
            components.append((counter,coefficient,self.top_cache[key]))
            remaining*=escape/denominator
        return Forecast(components,remaining)

    def fit(self, documents: list[str]) -> None:
        for doc in documents:
            context=''
            for char in doc:
                self.observe(context,char)
                context=(context+char)[-self.order:] if self.order else ''

class StreamModel:
    MODES={'frozen','pooled','recent','mix','adaptive'}
    def __init__(self, mode: str = 'pooled', order: int = 6, alpha: float = 0.5,
                 window: int = 512, eta: float = .05, share: float = .01,
                 base: NGram | None = None, recent_weight: float = .3):
        if mode not in self.MODES: raise ValueError(mode)
        if not 0 < share < 1 or eta <= 0 or not 0 <= recent_weight <= 1: raise ValueError('Invalid gate')
        self.mode,self.order,self.alpha,self.window=mode,order,alpha,window
        self.eta,self.share,self.recent_weight=eta,share,recent_weight
        self.base=base if base is not None else NGram(order,alpha)
        self.long=copy.deepcopy(self.base) if mode!='frozen' else None
        self.recent=NGram(order,alpha,window=window)
        self.weights=[.2,.5,.3]
        self.context='';self.doc_id='';self.seen=0

    def begin_doc(self, doc_id: str) -> None:
        self.context=''; self.doc_id=doc_id
        # Recent memory persists, but no cross-document transition is inserted.

    def experts(self, context: str) -> list[Forecast]:
        return [self.base.forecast(context),self.long.forecast(context),self.recent.forecast(context)]

    def forecast(self, context: str | None = None) -> Forecast:
        context=self.context if context is None else context
        if self.mode=='frozen': return self.base.forecast(context)
        if self.mode=='pooled': return self.long.forecast(context)
        if self.mode=='recent': return self.recent.forecast(context)
        if self.mode=='mix':
            w=self.recent_weight
            return Forecast.mix([self.long.forecast(context),self.recent.forecast(context)],[1-w,w])
        return Forecast.mix(self.experts(context),self.weights)

    def observe(self, char: str) -> None:
        if len(char)!=1: raise ValueError('one character required')
        if self.mode=='adaptive':
            fs=self.experts(self.context)
            logw=[math.log(w)+self.eta*math.log(max(f.prob(char),1e-30)) for w,f in zip(self.weights,fs)]
            maximum=max(logw); ws=[math.exp(x-maximum) for x in logw]; total=sum(ws)
            self.weights=[(1-self.share)*w/total+self.share/3 for w in ws]
        if self.mode!='frozen':
            self.long.observe(self.context,char)
            self.recent.observe(self.context,char)
        self.context=(self.context+char)[-self.order:]
        self.seen+=1

    def generate(self, context: str | None = None, max_chars: int = 8,
                 minimum_product: float = .005) -> tuple[str,list[float]]:
        context=self.context if context is None else context
        text='';products=[];product=1.0
        for _ in range(max_chars):
            top=self.forecast(context).top()
            if not top: break
            c,p=top[0]
            if c in '\r\n\x00' or (not c.isprintable()): break
            product*=p
            if product<minimum_product: break
            text+=c; products.append(product)
            context=(context+c)[-self.order:]
            if c in '。！？': break
        return text,products

    def fingerprint(self) -> str:
        # Candidate caches can change during reads; model counts/weights cannot.
        state=(self.seen,self.context,self.weights,
               self.base.table,self.long.table if self.long else None,self.recent.table)
        return hashlib.sha256(pickle.dumps(state,protocol=5)).hexdigest()

class TraceStore:
    """Opt-in editor event trace and canonical active text. No global keylogger.

    This prototype separates tracing from learning. On correction/deletion an
    active model must be rebuilt from training_documents(), rather than learning
    all old snapshots again. Rebuild must be outside the input UI thread.
    """
    def __init__(self):
        self.texts: dict[str,str]={};self.origins: dict[str,list[str]]={}
        self.versions: dict[str,int]={};self.events: list[dict]=[]
        self.deleted:set[str]=set()

    def apply(self, doc_id: str, kind: str, *, start: int = 0, end: int = 0,
              text: str = '', origin: str = 'typed', allowed: bool = True) -> None:
        if allowed is not True: return  # Only explicit boolean consent authorizes capture.
        if kind=='composition':
            self.events.append({'doc_id':doc_id,'kind':kind,'length':len(text)})
            return  # No raw composition text or training.
        if kind=='delete_document':
            self.texts.pop(doc_id,None);self.origins.pop(doc_id,None)
            self.deleted.add(doc_id)
            for e in self.events:
                if e.get('doc_id')==doc_id:
                    e.pop('text',None)
            self.versions[doc_id]=self.versions.get(doc_id,0)+1
            self.events.append({'doc_id':doc_id,'kind':kind,'version':self.versions[doc_id]})
            return
        if kind not in {'insert','replace','delete'}: raise ValueError(kind)
        old=self.texts.get(doc_id,''); origins=self.origins.get(doc_id,[])
        if not 0<=start<=end<=len(old): raise ValueError('invalid character offsets')
        if origin not in {'typed','model','paste'}: raise ValueError('invalid origin')
        if kind=='delete': text=''
        self.texts[doc_id]=old[:start]+text+old[end:]
        self.origins[doc_id]=origins[:start]+[origin]*len(text)+origins[end:]
        self.versions[doc_id]=self.versions.get(doc_id,0)+1
        self.deleted.discard(doc_id)
        self.events.append({'doc_id':doc_id,'kind':kind,'start':start,'end':end,
                            'text':text,'origin':origin,'version':self.versions[doc_id]})

    def training_documents(self) -> list[str]:
        result=[]
        for doc_id,text in self.texts.items():
            run=''
            for c,o in zip(text,self.origins[doc_id]):
                if o=='typed': run+=c
                elif run: result.append(run);run=''
            if run: result.append(run)
        return result

    def is_current(self, doc_id: str, version: int) -> bool:
        return doc_id in self.texts and self.versions.get(doc_id)==version

    def export(self, redact_deleted: bool = True) -> dict:
        return {'texts':copy.deepcopy(self.texts),'origins':copy.deepcopy(self.origins),
                'versions':self.versions.copy(),'events':copy.deepcopy(self.events)}
