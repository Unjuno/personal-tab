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
    page.evaluate('window.__held=false')
    page.wait_for_function('window.__testState.idle')
    pw.expect(page.locator('#editor')).to_have_value('')
    pw.expect(page.locator('#ghost')).to_have_text('')
