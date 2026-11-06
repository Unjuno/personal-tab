"""In-memory authorized-editor session; live demo, not production persistence.

Append-only typed input is incrementally learned. Replacements/deletions rebuild
from canonical active text and origin labels, so stale transitions are removed.
Rebuild is synchronous in this research server (not browser UI thread); large
stores require a versioned worker/atomic snapshot design before production use.
"""
from __future__ import annotations
import time
from personal_tab.core import StreamModel,TraceStore
from personal_tab.term_model import TermMemory

class Session:
    def __init__(self):
        self.trace=TraceStore();self.sequences={};self.active='';self._new_models()
    def _new_models(self):
        self.model=StreamModel(mode='pooled',order=4,alpha=1.)
        self.terms=TermMemory(minimum_prefix=3,minimum_count=2,max_length=4)
    def _observe(self,c,origin):
        if origin=='typed':
            self.model.observe(c);self.terms.observe(c)
        else:
            # Non-user text can provide current context, but is NOT a target
            # for count updates; discard mixed-origin partial term candidates.
            self.model.context=(self.model.context+c)[-self.model.order:]
            self.terms.pending='';self.terms.kind=None
    def rebuild(self):
        self._new_models()
        for doc,text in self.trace.texts.items():
            self.model.begin_doc(doc);self.terms.begin_doc(doc)
            for c,origin in zip(text,self.trace.origins[doc]):self._observe(c,origin)
        self.active=''
    def apply(self,event):
        if event.get('allowed',False) is not True:return {'suggestion':'','recorded':False}
        mode=event.get('mode','terms')
        if mode not in {'terms','statistical'}:raise ValueError('invalid mode')
        raw_text=event.get('text','')
        if not isinstance(raw_text,str):raise ValueError('text must be a string')
        raw_text.encode('utf-8',errors='strict')
        doc=str(event['doc_id']);seq=int(event['sequence']);last=self.sequences.get(doc,0)
        if seq<=last:return {'duplicate':True,'version':self.trace.versions.get(doc,0),'suggestion':''}
        if seq!=last+1:raise ValueError('out-of-order event; resend missing event')
        kind=event['kind'];old=self.trace.texts.get(doc,'');text=str(event.get('text',''))
        start=int(event.get('start',0));end=int(event.get('end',0));origin=event.get('origin','typed')
        new_length=len(old)-(end-start)+(0 if kind=='delete' else len(text))
        if new_length>100_000:raise ValueError('demo limit: 100000 code points')
        t=time.perf_counter_ns()
        self.trace.apply(doc,kind,start=start,end=end,text=text,origin=origin)
        self.sequences[doc]=seq
        if self.trace.events:
            self.trace.events[-1].update(sequence=seq,received_monotonic_ns=time.monotonic_ns())
            if isinstance(event.get('input_mono_ms'),(int,float)):
                self.trace.events[-1]['client_input_mono_ms']=event['input_mono_ms']
        if kind=='composition':return {'suggestion':'','version':self.trace.versions.get(doc,0),'recorded':True}
        if kind=='insert' and start==end==len(old) and (self.active in ('',doc)):
            if self.active!=doc:
                self.model.begin_doc(doc);self.terms.begin_doc(doc)
                # On first activation, an existing prefix comes from a rebuild.
                self.model.context=old[-self.model.order:]
            for c in text:self._observe(c,origin)
        else:self.rebuild()
        self.active=doc
        current=self.trace.texts.get(doc,'');cursor=min(len(current),start+len(text))
        context=current[:cursor]
        self.model.context=context[-self.model.order:]
        self.model.doc_id=doc;self.terms.doc=doc
        mode=event.get('mode','terms')
        if mode=='terms':p=self.terms.suggest(context)
        elif mode=='statistical':
            p,products=self.model.generate(context,max_chars=4)
            if len(p)<4 or products[-1]<.6:p=''
        else:raise ValueError('mode must be terms or statistical')
        if p:
            self.trace.events.append({'doc_id':doc,'kind':'suggestion_generated','version':self.trace.versions.get(doc,0),'sequence':seq,'text':p,'mode':mode,'cursor':cursor,'received_monotonic_ns':time.monotonic_ns()})
        return {'suggestion':p,'version':self.trace.versions.get(doc,0),'recorded':True,
                'cursor':cursor,'sequence':seq,'processing_ms':(time.perf_counter_ns()-t)/1e6,
                'typed_training_chars':self.model.long.seen,'unique_words':len(self.terms.words)}
