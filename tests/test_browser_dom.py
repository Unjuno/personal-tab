"""DOM-only Chromium tests with explicit fake transport.

These validate UI race/keyboard logic, not browser->HTTP connectivity or native IME.
Real socket + worker integration is tested separately in test_async_http.py.
"""
from pathlib import Path
import shutil
import pytest

ROOT=Path(__file__).resolve().parents[1]
FAKE=r"""
window.__held=false;window.__events=[];window.__text='';window.__version=0;
window.fetch=async (path,options={})=>{
 const e=options.body?JSON.parse(options.body):{};
 if(path==='/event'){
  window.__events.push(e);
  while(window.__held)await new Promise(r=>setTimeout(r,10));
  const old=[...window.__text];window.__text=old.slice(0,e.start).join('')+e.text+old.slice(e.end).join('');window.__version++;
 }
 if(path==='/memory' && options.method==='POST'){
  const doc=e.memory?.documents?.find(item=>item.id==='document');
  window.__text=doc?.text||'';window.__version=doc?1:0;
  return {ok:true,json:async()=>({restored:true,text:window.__text,version:window.__version})};
 }
 if(path==='/reset'){window.__text='';window.__version=0;return {ok:true,json:async()=>({reset:true})};}
 const value={suggestion:e.mode==='statistical'?'':window.__text.endsWith('コンピ')?'ュータ':'',cursor:[...window.__text].length,version:window.__version,sequence:e.sequence,recorded:true,typed_training_chars:[...window.__text].length,unique_words:1,learning_pending:false,processing_ms:0.1};
 return {ok:true,json:async()=>value};
};
"""

@pytest.fixture
def page():
    pw=pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as p:
        exe=p.chromium.executable_path if Path(p.chromium.executable_path).exists() else shutil.which('chromium')
        if not exe:pytest.skip('Chromium not installed')
        browser=p.chromium.launch(executable_path=exe,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1100,'height':850})
            page.evaluate(FAKE)
            page.set_content((ROOT/'src/personal_tab/web/live.html').read_text().replace('__SESSION_TOKEN__','test-only-token'))
            page.locator('#consent').check();page.locator('#editor').focus()
            yield page,pw
        finally:browser.close()

def test_dom_ghost_tab_ime_and_escape(page):
    page,pw=page
    page.keyboard.insert_text('コンピュータ。コンピュータ。コンピ')
    pw.expect(page.locator('#ghost')).to_have_text('ュータ')
    page.locator('#editor').press('Tab')
    pw.expect(page.locator('#editor')).to_have_value('コンピュータ。コンピュータ。コンピュータ')
    page.wait_for_function('window.__events.length===2')
    assert page.evaluate('window.__events[1].origin')=='model'
    page.keyboard.insert_text('。コンピ')
    pw.expect(page.locator('#ghost')).to_have_text('ュータ')
    page.dispatch_event('#editor','compositionstart')
    pw.expect(page.locator('#ghost')).to_have_text('')
    page.dispatch_event('#editor','compositionend')
    page.locator('#editor').press('Escape')
    page.wait_for_timeout(100)
    pw.expect(page.locator('#ghost')).to_have_text('')

def test_dom_old_mode_response_cannot_create_a_candidate(page):
    page,pw=page
    page.evaluate('window.__held=true')
    page.keyboard.insert_text('コンピュータ。コンピュータ。コンピ')
    page.locator('#mode').select_option('statistical')
    page.evaluate('window.__held=false')
    page.wait_for_function('window.__testState.idle')
    page.wait_for_timeout(100)
    assert page.evaluate('suggestion')==''

def test_dom_caret_move_before_response_suppresses_candidate(page):
    page,pw=page
    page.evaluate('window.__held=true')
    page.keyboard.insert_text('コンピュータ。コンピュータ。コンピ')
    page.locator('#editor').press('Home')
    page.evaluate('window.__held=false')
    page.wait_for_function('window.__testState.idle')
    assert page.evaluate('suggestion')==''

def test_dom_reset_ignores_old_inflight_response(page):
    page,pw=page
    page.evaluate('window.__held=true')
    page.keyboard.insert_text('コンピュータ。コンピュータ。コンピ')
    page.locator('#reset').click()
    page.locator('#confirm-reset').click()
    page.evaluate('window.__held=false')
    page.wait_for_function('window.__testState.idle')
    pw.expect(page.locator('#editor')).to_have_value('')
    pw.expect(page.locator('#ghost')).to_have_text('')

def test_consent_pause_resume_during_inflight_edit_recovers_ack_and_candidate(page):
    page,pw=page
    page.evaluate('window.__held=true')
    page.keyboard.insert_text('コンピュータ。コンピュータ。コンピ')
    page.wait_for_function('window.__events.length === 1')
    page.locator('#consent').uncheck()
    page.locator('#consent').check()
    page.evaluate('window.__held=false')
    page.wait_for_function('window.__testState.idle')
    page.wait_for_function('window.__testState.ackSequence === window.__testState.sequence',timeout=5000)
    pw.expect(page.locator('#ghost')).to_have_text('ュータ',timeout=5000)

def test_explicit_interaction_states_follow_ime_offer_and_dismiss(page):
    page,pw=page
    assert page.locator('body').get_attribute('data-interaction-phase')=='ready'
    page.evaluate('window.__held=true')
    page.keyboard.insert_text('コンピュータ。コンピュータ。コンピ')
    pw.expect(page.locator('body')).to_have_attribute('data-interaction-phase','syncing')
    page.dispatch_event('#editor','compositionstart')
    pw.expect(page.locator('body')).to_have_attribute('data-interaction-phase','composing')
    page.dispatch_event('#editor','compositionend')
    page.evaluate('window.__held=false')
    pw.expect(page.locator('#ghost')).to_have_text('ュータ',timeout=5000)
    pw.expect(page.locator('body')).to_have_attribute('data-interaction-phase','offered')
    page.locator('#editor').press('Escape')
    pw.expect(page.locator('body')).to_have_attribute('data-interaction-phase','dismissed')
    pw.expect(page.locator('#ghost')).to_have_text('')


def test_stale_version_cannot_offer_ghost_even_if_server_omits_stale_flag(page):
    page,pw=page
    page.keyboard.insert_text('コンピュータ。コンピュータ。コンピ')
    pw.expect(page.locator('#ghost')).to_have_text('ュータ')
    page.evaluate('''() => {
      const oldFetch = window.fetch;
      window.fetch = async (...args) => {
        const reply = await oldFetch(...args);
        if (args[0] !== '/suggestion') return reply;
        const payload = await reply.json();
        payload.version -= 1;
        return {ok:true, json:async()=>payload};
      };
      clear();
      cancelPoll();
      pollSuggestions(generation,sequence,pollSerial);
    }''')
    page.wait_for_timeout(180)
    pw.expect(page.locator('#ghost')).to_have_text('')

def test_reset_and_cancel_are_explicit_without_data_loss(page):
    page,pw=page
    page.keyboard.insert_text('覚えておきたい文章。')
    pw.expect(page.locator('#editor')).to_have_value('覚えておきたい文章。')
    page.locator('#reset').click()
    pw.expect(page.locator('#editor')).to_have_value('覚えておきたい文章。')
    pw.expect(page.locator('#reset-confirm')).to_be_visible()
    page.locator('#cancel-reset').click()
    pw.expect(page.locator('#editor')).to_have_value('覚えておきたい文章。')
    page.locator('#reset').click()
    page.locator('#confirm-reset').click()
    pw.expect(page.locator('#editor')).to_have_value('')
    pw.expect(page.locator('#reset-confirm')).to_be_hidden()


def test_import_requires_confirmation_before_replacing_existing_text(page):
    page,pw=page
    page.keyboard.insert_text('保持している文章。')
    page.wait_for_function('window.__testState.idle')
    file={'name':'memory.json','mimeType':'application/json','buffer':b'{"schema":"personal-tab-memory/v1","documents":[]}'}
    page.locator('#load-memory').set_input_files(file)
    pw.expect(page.locator('#import-confirm')).to_be_visible()
    pw.expect(page.locator('#editor')).to_have_value('保持している文章。')
    page.locator('#cancel-import').click()
    pw.expect(page.locator('#import-confirm')).to_be_hidden()
    pw.expect(page.locator('#editor')).to_have_value('保持している文章。')

def test_confirmed_import_updates_editor_and_restarts_state_machine(page):
    page,pw=page
    page.keyboard.insert_text('古い文章。')
    page.wait_for_function('window.__testState.idle')
    blob='{"schema":"personal-tab-memory/v1","documents":[{"id":"document","text":"新しい文章。","spans":[{"start":0,"end":6,"origin":"typed"}]}]}'.encode('utf8')
    page.locator('#load-memory').set_input_files({'name':'memory.json','mimeType':'application/json','buffer':blob})
    pw.expect(page.locator('#editor')).to_have_value('古い文章。')
    page.locator('#confirm-import').click()
    pw.expect(page.locator('#editor')).to_have_value('新しい文章。')
    pw.expect(page.locator('body')).not_to_have_attribute('data-interaction-phase','mutating')
    pw.expect(page.locator('#import-confirm')).to_be_hidden()

def test_syncing_state_never_displays_a_stale_offered_candidate(page):
    page,pw=page
    page.keyboard.insert_text('コンピュータ。コンピュータ。コンピ')
    pw.expect(page.locator('#ghost')).to_have_text('ュータ')
    page.evaluate('pendingCount++;paint()')
    pw.expect(page.locator('body')).to_have_attribute('data-interaction-phase','syncing')
    pw.expect(page.locator('#ghost')).to_have_text('')
    page.evaluate('pendingCount--;paint()')
    pw.expect(page.locator('#ghost')).to_have_text('ュータ')

def test_learning_worker_failure_is_visible_but_does_not_disable_canonical_editing(page):
    page,pw=page
    page.keyboard.insert_text('コンピュータ。コンピュータ。コンピ')
    pw.expect(page.locator('#ghost')).to_have_text('ュータ')
    page.evaluate('''() => {
      const oldFetch=window.fetch;
      window.fetch=async (...args) => {
        const reply=await oldFetch(...args);
        if(args[0]!=='/suggestion')return reply;
        const value=await reply.json();value.learning_error='learning worker unavailable';
        return {ok:true,json:async()=>value};
      };
      clear();cancelPoll();pollSuggestions(generation,sequence,pollSerial);
    }''')
    pw.expect(page.locator('body')).to_have_attribute('data-interaction-phase','degraded',timeout=5000)
    pw.expect(page.locator('#editor')).to_be_enabled()
    pw.expect(page.locator('#ghost')).to_have_text('')
