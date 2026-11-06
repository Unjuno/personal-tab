"""Regression contracts for causal background learning and snapshot promotion."""
import os
import time
import pytest
from personal_tab.async_memory import AsyncSession, MemoryBuilder, SnapshotWorker

TEXT='コンピュータ。コンピュータ。コンピ'

def event(seq=1, text=TEXT, **extra):
    result=dict(doc_id='d', sequence=seq, kind='insert', start=0, end=0,
                text=text, origin='typed', allowed=True)
    result.update(extra)
    return result

def job(request=1, epoch=0, text=TEXT, origin='typed'):
    return dict(request_id=request, epoch=epoch,
                documents=(('d',text,tuple([origin]*len(text)),request),))

def wait_until(predicate, timeout=5):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        result=predicate()
        if result:return result
        time.sleep(.005)
    pytest.fail('condition not reached before timeout')

def settled(s, doc='d'):
    return wait_until(lambda: (r if not (r:=s.suggest(doc_id=doc))['learning_pending'] else None))

def test_incremental_builder_matches_rebuilt_counts_and_origins():
    b=MemoryBuilder();first=b.learn(job(text='コンピュータ。コンピ'))
    assert first['model'].long.seen==len('コンピュータ。コンピ')
    result=b.learn(job(request=2))
    fresh=MemoryBuilder().learn(job(request=2))
    assert result['model'].long.table==fresh['model'].long.table
    assert result['terms'].words==fresh['terms'].words
    assert result['update_kind']=='append'
    assert result['model'].long.seen==len(TEXT)

def test_builder_never_learns_generated_or_pasted_targets():
    for origin in ['model','paste']:
        result=MemoryBuilder().learn(job(origin=origin))
        assert result['model'].long.seen==0
        assert not result['terms'].words

def test_worker_coalesces_pending_jobs_and_uses_separate_process():
    w=SnapshotWorker(delay=.15)
    try:
        w.submit(job(1))
        wait_until(lambda:w.stats()['in_flight']==1)
        for n in range(2,22):w.submit(job(n, text=TEXT+str(n)))
        wait_until(lambda:w.stats()['completed_id']==21)
        r=w.poll()
        assert r['request_id']==21
        assert r['pid']!=os.getpid()
        assert r['model'].long.seen==len(TEXT+'21')
        assert w.stats()['coalesced']>=18
        assert w.stats()['pending_count']<=1
        assert w.stats()['completed']<=3
    finally:w.close()
    assert not w.alive
    with pytest.raises(RuntimeError):w.submit(job(30))

def test_async_session_learns_without_future_text_and_deduplicates():
    with AsyncSession() as s:
        r=s.apply(event())
        assert r['recorded'] and r['version']==1
        result=settled(s)
        assert result['suggestion']=='ュータ'
        assert result['typed_training_chars']==len(TEXT)
        assert s.apply(event())['duplicate']
        assert s.trace.texts['d']==TEXT
        with pytest.raises(ValueError):s.apply(event(3))

def test_input_and_prediction_do_not_wait_for_slow_training():
    with AsyncSession(worker_delay=.3) as s:
        s.apply(event());assert settled(s)['suggestion']=='ュータ'
        s.apply(event(2,'ュ',start=len(TEXT),end=len(TEXT)))
        wait_until(lambda:s.worker.stats()['in_flight']!=0)
        start=time.monotonic()
        r=s.apply(event(3,'ー',start=len(TEXT)+1,end=len(TEXT)+1))
        assert time.monotonic()-start<.15
        assert r['learning_pending']
        assert s.worker.stats()['in_flight']!=0
        assert s.trace.texts['d']==TEXT+'ュー'
        settled(s)
        assert s.snapshot['model'].long.seen==len(TEXT)+2

def test_deletion_and_reset_never_promote_old_training_result():
    with AsyncSession(worker_delay=.15) as s:
        s.apply(event());settled(s)
        assert s.suggest(doc_id='d')['suggestion']=='ュータ'
        s.apply(event(2,'。',start=len(TEXT),end=len(TEXT)))
        wait_until(lambda:s.worker.stats()['in_flight']!=0)
        s.apply(event(3,'コンピ',kind='replace',start=0,end=len(TEXT)+1))
        assert s.suggest(doc_id='d')['suggestion']==''
        assert settled(s)['suggestion']==''
        s.reset()
        assert not s.trace.events and not s.trace.texts
        s.apply(event(text='コンピ'))
        assert settled(s)['suggestion']==''
        assert s.snapshot['model'].long.seen==3

def test_unknown_stale_and_ime_requests_are_safe():
    with AsyncSession() as s:
        denied=event();denied['allowed']=False
        assert not s.apply(denied)['recorded']
        assert not s.trace.events
        s.apply(event(text='未確定',kind='composition'))
        assert not s.trace.texts
        assert '未確定' not in str(s.trace.events)
        s.apply(event(2,text=TEXT));settled(s)
        assert s.suggest(doc_id='d',version=0)['stale']
        assert s.suggest(doc_id='d',version=0)['suggestion']==''
        assert s.suggest(doc_id='unknown')['suggestion']==''

def test_future_suffix_cannot_affect_same_prefix_prediction():
    with AsyncSession() as a, AsyncSession() as b:
        for s in [a,b]:s.apply(event());settled(s)
        assert a.suggest(doc_id='d')['suggestion']==b.suggest(doc_id='d')['suggestion']
        assert a.snapshot['model'].fingerprint()==b.snapshot['model'].fingerprint()

def test_invalid_events_do_not_mutate_state():
    with AsyncSession() as s:
        for changes in [dict(kind='invalid'),dict(mode='invalid'),dict(start=-1),
                        dict(sequence=True),dict(text='\ud800'),dict(origin='invalid')]:
            e=event();e.update(changes)
            with pytest.raises((ValueError,TypeError)):s.apply(e)
            assert s.trace.texts=={} and s.sequences=={}
        assert not s.worker.stats()['submitted']

def test_worker_failure_recovers_without_losing_canonical_text():
    with AsyncSession() as s:
        s.apply(event());settled(s)
        old_pid=s.worker.stats()['pid']
        s.worker._process.terminate();s.worker._process.join(timeout=2)
        s.apply(event(2,'。',start=len(TEXT),end=len(TEXT)))
        result=settled(s)
        assert result['learning_error'] is None
        assert result['learning_phase']=='ready'
        assert s.worker.stats()['pid']!=old_pid
        assert s.trace.texts['d']==TEXT+'。'


def test_explicit_json_memory_roundtrip_learns_only_typed_content():
    with AsyncSession() as original:
        original.apply(event());settled(original)
        original.apply(event(2,'モデル専用。',start=len(TEXT),end=len(TEXT),origin='model'))
        settled(original)
        memory=original.export_memory()
    with AsyncSession() as resumed:
        resumed.restore_memory(memory)
        settled(resumed)
        assert resumed.snapshot['model'].long.seen==len(TEXT)
        assert resumed.snapshot['terms'].suggest('コンピ')=='ュータ'
        assert not resumed.snapshot['terms'].suggest('モデル専')
        assert resumed.trace.texts['d']==TEXT+'モデル専用。'


def test_invalid_memory_import_is_atomic():
    with AsyncSession() as s:
        s.apply(event());settled(s)
        before=s.trace.export()
        memory=s.export_memory()
        memory['documents'][0]['spans'][0]['end']+=100
        with pytest.raises(ValueError):s.restore_memory(memory)
        assert s.trace.export()==before
        assert s.suggest(doc_id='d')['suggestion']=='ュータ'


def test_repeated_poll_does_not_learn_model_output():
    with AsyncSession() as s:
        s.apply(event());settled(s)
        count=s.snapshot['model'].long.seen
        for _ in range(20):assert s.suggest(doc_id='d')['suggestion']=='ュータ'
        assert s.snapshot['model'].long.seen==count
        assert len([e for e in s.trace.events if e['kind']=='suggestion_generated'])==1


def test_settled_async_matches_synchronous_reference_for_edits():
    from personal_tab.session import Session
    reference=Session()
    with AsyncSession() as s:
        edits=[event(),event(2,'ュータ。',start=len(TEXT),end=len(TEXT),origin='model'),
               event(3,'解析結果。',start=0,end=7,kind='replace'),
               event(4,'',start=0,end=5,kind='delete'),
               event(5,'貼付け内容。',start=0,end=0,origin='paste')]
        for ev in edits:
            reference.apply(ev);s.apply(ev);settled(s)
            assert s.trace.texts==reference.trace.texts
            assert s.snapshot['model'].long.table==reference.model.long.table
            assert s.snapshot['terms'].words==reference.terms.words


def test_trace_limits_reject_without_mutation():
    with AsyncSession() as s:
        s.MAX_TRACE_CHARS=2
        with pytest.raises(ValueError):s.apply(event(text='あいう'))
        assert not s.trace.texts and not s.sequences

def test_explicit_learning_phase_is_coherent_across_delayed_training_and_rebuild():
    with AsyncSession(worker_delay=.2) as s:
        assert s.suggest(doc_id='d')['learning_phase']=='empty'
        pending=s.apply(event())
        assert pending['learning_phase'] in {'queued','training'}
        ready=settled(s)
        assert ready['learning_phase']=='ready'
        edit=s.apply(event(2,'新',kind='replace',start=0,end=1))
        assert edit['learning_phase']=='rebuilding'
        assert not edit['suggestion']
        assert settled(s)['learning_phase']=='ready'
        s.worker._process.terminate()
        s.worker._process.join(timeout=2)
        assert s.suggest(doc_id='d')['learning_phase'] in {'recovering','updating','queued','ready'}
        wait_until(lambda:s.worker.stats()['restarts']>=1)
        assert settled(s)['learning_phase']=='ready'


def test_worker_crash_restarts_and_replays_full_latest_snapshot():
    with AsyncSession() as session:
        assert session.apply(event())['recorded']
        settled(session)
        old_pid = session.worker.stats()['pid']
        # Actual process crash, not a mock exception.
        session.worker._process.kill()
        session.worker._process.join(timeout=2)
        # Foreground input remains accepted and the latest canonical text is replayed.
        session.apply(dict(event(seq=2, text='。コンピ'), kind='insert', start=len(TEXT), end=len(TEXT)))
        result = wait_until(lambda: (r if not (r := session.suggest(doc_id='d'))['learning_pending'] else None),
                            timeout=7)
        assert not result['learning_error']
        assert session.worker.stats()['pid'] != old_pid
        assert session.worker.stats()['restarts'] >= 1
        assert result['typed_training_chars'] == len(TEXT + '。コンピ')


def test_worker_restart_budget_is_bounded_and_reports_terminal_error():
    with AsyncSession() as s:
        s.worker.MAX_RESTARTS=1
        s.apply(event());settled(s)
        for _ in range(2):
            s.worker._process.kill()
            s.worker._process.join(timeout=2)
            if _==0:
                wait_until(lambda:s.worker.stats()['restarts']==1 and s.worker.alive)
                settled(s)
        wait_until(lambda:s.worker.stats()['error'] is not None)
        r=s.suggest(doc_id='d')
        assert r['learning_phase']=='failed'
        assert r['learning_error']
        assert s.trace.texts['d']==TEXT


def test_crash_before_reset_cannot_restore_old_training_material():
    with AsyncSession(worker_delay=.12) as s:
        s.apply(event())
        settled(s)
        s.worker._process.kill()
        s.worker._process.join(timeout=2)
        s.reset()
        s.apply(event(text='コンピ'))
        wait_until(lambda: not s.suggest(doc_id='d')['learning_pending'], timeout=8)
        r=s.suggest(doc_id='d')
        assert r['typed_training_chars']==len('コンピ')
        assert r['suggestion']==''
        assert 'コンピュータ' not in str(s.trace.export())
