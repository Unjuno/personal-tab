import pytest
from personal_tab.session import Session

def event(seq,text,start=0,end=0,kind='insert',origin='typed',allowed=True):
    return dict(doc_id='d',sequence=seq,kind=kind,start=start,end=end,text=text,origin=origin,allowed=allowed)

def test_session_sequence_and_idempotence():
    s=Session();a=s.apply(event(1,'統計解析。'))
    assert s.trace.texts['d']=='統計解析。'
    s.apply(event(1,'統計解析。'))
    assert s.trace.texts['d']=='統計解析。'
    with pytest.raises(ValueError):s.apply(event(3,'X',5,5))

def test_session_delete_rebuild_removes_original():
    s=Session();s.apply(event(1,'コンピュータ。コンピュータ。'))
    assert s.terms.suggest('コンピ')=='ュータ'
    n=len(s.trace.texts['d'])
    s.apply(event(2,'',0,n,kind='delete'))
    assert s.terms.suggest('コンピ')==''
    assert s.model.long.seen==0

def test_session_model_text_is_context_not_target():
    s=Session();s.apply(event(1,'コンピュータ。',origin='model'))
    assert s.model.long.seen==0
    assert s.terms.suggest('コンピ')==''
    s.apply(event(2,'本人。',7,7))
    assert s.model.long.seen==3

def test_session_denied_event_not_recorded():
    s=Session();s.apply(event(1,'秘密',allowed=False))
    assert not s.trace.events and not s.trace.texts

def test_invalid_mode_rejected_without_mutation():
    s=Session();e=event(1,'invalid');e['mode']='unknown'
    with pytest.raises(ValueError):s.apply(e)
    assert s.trace.texts=={} and s.sequences=={}

def test_invalid_unicode_rejected_before_trace():
    s=Session()
    with pytest.raises(ValueError):s.apply(event(1,'\ud800'))
    assert s.trace.texts=={}

def test_trace_records_sequence_and_generation_not_visibility():
    s=Session();r=s.apply(event(1,'コンピュータ。コンピュータ。コンピ'))
    records=s.trace.events
    assert records[0]['sequence']==1 and records[0]['received_monotonic_ns']>0
    assert records[-1]['kind']=='suggestion_generated'
    assert records[-1]['text']=='ュータ'
    assert 'displayed' not in records[-1]
