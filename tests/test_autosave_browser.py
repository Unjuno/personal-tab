"""Actual WebCrypto and IndexedDB in Chromium, with a deterministic in-process HTTP transport.

This is an encrypted-browser-storage test, not a physical Japanese IME test.
"""
import json
import os
from pathlib import Path
import shutil
import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_encrypted_browser_autosave_roundtrip_and_wrong_passphrase():
    pw = pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as p:
        exe = p.chromium.executable_path if Path(p.chromium.executable_path).exists() else shutil.which('chromium')
        if not exe:
            pytest.skip('Chromium not installed')
        browser = p.chromium.launch(executable_path=exe, headless=True, args=['--no-sandbox'])
        context = browser.new_context()
        try:
            state = {'text': '', 'version': 0, 'sequence': 0, 'origins': []}
            html = (ROOT / 'src/personal_tab/web/live.html').read_text().replace('__SESSION_TOKEN__', 'synthetic-test-tab-token')

            def router(route):
                req = route.request
                path = req.url.removeprefix('https://tab.example.test')
                if path == '/':
                    route.fulfill(status=200, content_type='text/html', body=html)
                    return
                if path == '/status':
                    route.fulfill(json={'can_edit': True, 'device': 'cpu', 'learning_mode': 'async'})
                    return
                payload = req.post_data_json if req.method == 'POST' else {}
                if path == '/lease':
                    result = {'active': True, 'text': state['text'], 'version': state['version'],
                              'sequence': state['sequence']}
                elif path == '/event':
                    assert payload['sequence'] == state['sequence'] + 1
                    source = list(state['text'])
                    start, end = payload['start'], payload['end']
                    state['text'] = ''.join(source[:start]) + payload['text'] + ''.join(source[end:])
                    state['origins'] = state['origins'][:start] + [payload['origin']] * len(payload['text']) + state['origins'][end:]
                    state['version'] += 1
                    state['sequence'] += 1
                    result = {'recorded': True, 'version': state['version'],
                              'sequence': state['sequence'], 'cursor': len(state['text']),
                              'suggestion': '', 'learning_pending': False,
                              'typed_training_chars': len(state['text']), 'unique_words': 0}
                elif path == '/memory' and req.method == 'GET':
                    spans = []
                    for i, origin in enumerate(state['origins']):
                        if spans and spans[-1]['origin'] == origin:
                            spans[-1]['end'] = i + 1
                        else:
                            spans.append({'start': i, 'end': i + 1, 'origin': origin})
                    result = {'schema': 'personal-tab-memory/v1', 'documents': [
                        {'id': 'document', 'text': state['text'], 'spans': spans}] if state['text'] else []}
                elif path == '/memory' and req.method == 'POST':
                    doc = next((d for d in payload['memory']['documents'] if d['id'] == 'document'), None)
                    state['text'] = doc['text'] if doc else ''
                    state['origins'] = [x['origin'] for span in doc['spans']
                                        for x in [span] for _ in range(span['end'] - span['start'])] if doc else []
                    state['version'] = 1 if doc else 0
                    state['sequence'] = 0
                    result = {'restored': True, 'text': state['text'], 'version': state['version']}
                elif path == '/suggestion':
                    result = {'suggestion': '', 'version': state['version'],
                              'sequence': state['sequence'], 'cursor': len(state['text']),
                              'learning_pending': False, 'typed_training_chars': len(state['text']), 'unique_words': 0}
                elif path == '/reset':
                    state.update(text='', origins=[], version=0, sequence=0)
                    result = {'reset': True}
                elif path == '/release':
                    result = {'released': True}
                else:
                    route.fulfill(status=404, json={'error': 'unknown route'})
                    return
                route.fulfill(status=200, json=result)

            context.route('https://tab.example.test/**', router)
            page = context.new_page()
            try:
                page.goto('https://tab.example.test/')
            except pw.Error as exc:
                if 'ERR_BLOCKED_BY_ADMINISTRATOR' in str(exc) and not os.environ.get('PERSONAL_TAB_REQUIRE_E2E'):
                    pytest.skip('Managed Chromium blocks navigation; encrypted browser E2E runs in CI')
                raise
            page.wait_for_function('leaseReady === true')
            page.locator('#consent').check()
            page.locator('#autosave-passphrase').fill('correct-horse-durable-test-password')
            page.locator('#enable-autosave').click()
            page.get_by_text('暗号化自動保存を開始しました。').wait_for(timeout=12000)
            value = 'この文章は暗号化して復元します。'
            page.locator('#editor').fill(value)
            page.wait_for_function('window.__testState.idle')
            page.get_by_text('暗号化して保存済み', exact=False).wait_for(timeout=12000)
            record_info = page.evaluate('''async () => {
                const record=await backupOp('get');
                return {hasRecord:!!record, schema:record.schema, size:record.ciphertext.length,
                        content:JSON.stringify(record)};
            }''')
            assert record_info['hasRecord'] and record_info['schema'] == 'personal-tab-encrypted-v1'
            assert value not in record_info['content']
            assert 'correct-horse-durable-test-password' not in record_info['content']

            # Simulate process restart without wiping the browser's encrypted storage.
            state.update(text='', origins=[], version=0, sequence=0)
            page.reload()
            page.wait_for_function('leaseReady === true')
            page.locator('#consent').check()
            page.locator('#autosave-passphrase').fill('wrong-password-00000')
            page.locator('#enable-autosave').click()
            page.get_by_text('有効化・復元に失敗しました。', exact=False).wait_for(timeout=12000)
            assert page.locator('#editor').input_value() == ''
            page.locator('#autosave-passphrase').fill('correct-horse-durable-test-password')
            page.locator('#enable-autosave').click()
            page.get_by_text('暗号化記憶を復元しました。', exact=False).wait_for(timeout=12000)
            assert page.locator('#editor').input_value() == value
            assert state['text'] == value

            page.on('dialog', lambda d: d.accept())
            page.locator('#remove-autosave').click()
            page.get_by_text('暗号化保存を削除しました。').wait_for(timeout=12000)
            assert page.evaluate('async()=>await backupOp("get")') is None
        finally:
            context.close()
            browser.close()
