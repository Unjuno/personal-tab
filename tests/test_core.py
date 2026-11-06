import json
import pytest
from personal_tab.core import NGram, StreamModel, TraceStore

def test_observation_changes_future_not_past():
    m=NGram(order=4)
    m.observe('あ','い')
    assert m.seen == 1
    assert m.forecast('あ').top()[0][0] == 'い'

def test_future_suffix_never_given_to_model():
    a=NGram(order=4); b=NGram(order=4)
    for i,c in enumerate('文章補完は'):
        a.observe('文章補完は'[:i], c); b.observe('文章補完は'[:i], c)
    assert a.forecast('文章補完は').top() == b.forecast('文章補完は').top()

def test_forecast_distribution_normalizes():
    m=NGram(order=3)
    for i,c in enumerate('ababcab'):
        m.observe('ababcab'[:i], c)
    p=m.forecast('ab')
    assert abs(p.total_mass()-1) < 1e-10
    assert p.prob('未') > 0

def test_window_expires_old_count():
    m=NGram(order=2,window=2)
    m.observe('x','a'); m.observe('x','b'); m.observe('x','b')
    assert m.forecast('x').top()[0][0]=='b'
    assert m.table['x'].get('a',0)==0

def test_generation_does_not_learn_own_predictions():
    e=StreamModel(mode='pooled',order=4)
    e.begin_doc('a')
    for c in '本研究では有効性を検証する。本研究では有効性を検証する。': e.observe(c)
    before=e.fingerprint()
    e.generate('本研究では',8)
    assert e.fingerprint()==before

def test_predict_then_observe_and_empty_vocab():
    e=StreamModel(mode='adaptive',order=4)
    e.begin_doc('x')
    assert e.seen==0
    e.forecast('新')
    assert e.seen==0
    e.observe('新')
    assert e.seen==1

def test_trace_permission_composition_and_model_origin():
    s=TraceStore()
    s.apply('d','insert',start=0,end=0,text='機密',allowed=False)
    assert s.texts.get('d','')==''
    s.apply('d','composition',text='へんかん')
    assert s.texts.get('d','')==''
    s.apply('d','insert',start=0,end=0,text='候補',origin='model')
    assert s.training_documents()==[]
    s.apply('u','insert',start=0,end=0,text='本人')
    assert s.training_documents()==['本人']

def test_trace_edit_no_duplicate_learning_and_stale_response():
    s=TraceStore()
    s.apply('d','insert',start=0,end=0,text='古い文。')
    old=s.versions['d']
    s.apply('d','replace',start=0,end=2,text='新しい')
    assert s.texts['d']=='新しい文。'
    assert s.training_documents()==['新しい文。']
    assert not s.is_current('d',old)
    s.apply('d','delete_document')
    assert s.training_documents()==[]
    assert '古い' not in json.dumps(s.export(redact_deleted=True),ensure_ascii=False)

def test_prediction_state_reproducible():
    a=StreamModel(mode='adaptive',order=4)
    b=StreamModel(mode='adaptive',order=4)
    for e in (a,b):
        e.begin_doc('d')
        for c in '同じ入力を順に読む。': e.observe(c)
    assert a.generate('同じ',5)==b.generate('同じ',5)
