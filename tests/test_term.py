from personal_tab.term_model import TermMemory

def test_word_boundary_is_not_known_early():
    m=TermMemory(minimum_prefix=2)
    m.begin_doc('a')
    for c in 'アルゴリズム':m.observe(c)
    assert m.suggest('アル')==''
    m.observe('。')
    assert m.suggest('アル')=='ゴリズム'

def test_support_counts_occurrences_not_prefix_variants():
    m=TermMemory(minimum_prefix=2,minimum_count=2)
    m.fit(['コンピュータ。'])
    assert m.suggest('コンピ')==''
    m.fit(['コンピュータ。'])
    assert m.suggest('コンピ')=='ュータ'

def test_current_prefix_and_frozen_memory():
    m=TermMemory(minimum_prefix=2,frozen=True);m.fit(['統計解析。'])
    m.begin_doc('b')
    for c in '統計':m.observe(c)
    assert m.suggest()=='解析'
    for c in '理論。':m.observe(c)
    assert m.suggest('統計理')==''
