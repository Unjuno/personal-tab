"""Optional real Chromium -> real HTTP -> real spawned learning worker."""
import json
import os
from pathlib import Path
import re
import socket
import shutil
import subprocess
import sys
import time
import urllib.request

import pytest

ROOT=Path(__file__).resolve().parents[1]


def test_browser_real_async_tab_ime_reset_and_provenance():
    pw=pytest.importorskip('playwright.sync_api')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    process=subprocess.Popen([sys.executable,str(ROOT/'serve_demo.py'),'--port',str(port)],
                             stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        for _ in range(200):
            try:
                client.open(f'http://127.0.0.1:{port}/healthz',timeout=.2);break
            except OSError:time.sleep(.02)
        with pw.sync_playwright() as p:
            executable = p.chromium.executable_path if Path(p.chromium.executable_path).exists() else shutil.which('chromium')
            if not executable:
                pytest.skip('Chromium not installed; run playwright install chromium')
            browser=p.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
            try:
                page=browser.new_page(viewport={'width':1100,'height':850})
                try:
                    page.goto(f'http://127.0.0.1:{port}')
                except pw.Error as exc:
                    if 'ERR_BLOCKED_BY_ADMINISTRATOR' in str(exc) and not os.environ.get('PERSONAL_TAB_REQUIRE_E2E'):
                        pytest.skip('Managed Chromium policy blocks localhost; real browser HTTP E2E unverified')
                    raise
                page.locator('#consent').check()
                editor=page.locator('#editor');editor.focus()
                # Browser input insertion, not paste: Input.insertText preserves Unicode.
                page.keyboard.insert_text('コンピュータ。コンピュータ。コンピ')
                pw.expect(page.locator('#ghost')).to_have_text('ュータ',timeout=10000)
                editor.press('Tab')
                pw.expect(editor).to_have_value('コンピュータ。コンピュータ。コンピュータ')
                page.wait_for_function("window.__testState?.idle === true")
                trace=page.evaluate("async()=>{const t=await fetch('/trace',{headers:{'X-Tab-Token':TOKEN}});return t.json()}")
                assert any(e.get('origin')=='model' for e in trace['events'])
                # Real browser DOM composition events, not an OS-native IME.
                page.keyboard.insert_text('。コンピ')
                pw.expect(page.locator('#ghost')).to_have_text('ュータ',timeout=10000)
                page.dispatch_event('#editor','compositionstart')
                pw.expect(page.locator('#ghost')).to_have_text('')
                page.dispatch_event('#editor','compositionend')
                editor.press('Escape')
                time.sleep(.15)
                pw.expect(page.locator('#ghost')).to_have_text('')
                page.locator('#reset').click()
                page.locator('#confirm-reset').click()
                pw.expect(editor).to_have_value('')
                page.keyboard.insert_text('コンピ')
                page.wait_for_function("window.__testState?.idle === true")
                time.sleep(.3)
                pw.expect(page.locator('#ghost')).to_have_text('')
                # IME/keyboard evidence does not claim native IME or all browsers.
                assert not page.locator('#error').inner_text()
            finally:browser.close()
    finally:
        process.terminate();process.communicate(timeout=5)
