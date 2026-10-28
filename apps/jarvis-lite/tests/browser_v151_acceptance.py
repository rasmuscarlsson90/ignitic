"""Run with uv run --with playwright python tests/browser_v151_acceptance.py."""
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright

ROOT=Path(__file__).resolve().parents[1]
env={**os.environ,'PYTHONPATH':str(ROOT),'JARVIS_BROWSER_OPEN':'true','JARVIS_BROWSER_GAPS':'false'}
checks=[];errors=[];external=[]
with open('/tmp/v151-browser.log','w') as log:
    child=subprocess.Popen([str(ROOT/'.venv/bin/python'),str(ROOT/'tests/v15_browser_server.py')],
        env=env,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            if child.poll() is not None:raise AssertionError('Fixture server exited; inspect /tmp/v151-browser.log')
            try:
                urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:8027/health',timeout=1)
                break
            except OSError:time.sleep(.2)
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1000})
            page.on('pageerror',lambda e:errors.append(str(e)))
            def guard(route):
                if not route.request.url.startswith('http://127.0.0.1:8027/'):
                    external.append(route.request.url);route.abort()
                else:route.continue_()
            page.route('**/*',guard)
            def login(tab,role):
                tab.goto('http://127.0.0.1:8027/research/autonomy')
                tab.fill('#login-email',role+'@example.com')
                tab.fill('#login-password','Synthetic-v13-integration-password-42');tab.click('#login-submit')
                expect(tab.locator('#last-updated')).to_contain_text('Uppdaterad',timeout=30000)
            login(page,'admin')
            expect(page.locator('#autonomy-summary')).to_contain_text('Öppen offentlig webb')
            expect(page.locator('#autonomy-sources')).to_contain_text('Villkor och licens är inte granskade')
            page.select_option('#autonomy-source-filter','pending')
            expect(page.locator('#autonomy-sources .discovered-source')).to_have_count(3)
            page.select_option('#autonomy-source-filter','approved')
            expect(page.locator('#autonomy-sources')).to_contain_text('Inga källor')
            checks.append('open discovery displayed as unverified; no false automatic approval')
            expect(page.locator('#autonomy-evidence')).to_contain_text('inga samstämmiga')
            expect(page.locator('#autonomy-costs')).to_contain_text('0,12')
            checks.append('insufficient shared evidence and separate bounded search budget')
            page.locator('#autonomy-policy-form').locator('xpath=ancestor::details[1]/summary').click()
            expect(page.locator('#autonomy-domain-access-mode')).to_have_value('open_public_web')
            page.select_option('#autonomy-domain-access-mode','allowlist')
            assert page.locator('#autonomy-allowed-domains').evaluate('(e)=>e.required')
            page.select_option('#autonomy-domain-access-mode','open_public_web')
            assert not page.locator('#autonomy-allowed-domains').evaluate('(e)=>e.required')
            assert page.locator('#autonomy-open-reviewed').evaluate('(e)=>e.required')
            page.uncheck('#autonomy-open-reviewed')
            assert not page.locator('#autonomy-open-reviewed').evaluate('(e)=>e.checkValidity()')
            page.check('#autonomy-open-reviewed')
            checks.append('mode selection enforces explicit review and correct empty-domain behavior')
            viewer=browser.new_page();viewer.route('**/*',guard);login(viewer,'viewer')
            expect(viewer.locator('#autonomy-domain-access-mode')).to_be_disabled()
            expect(viewer.locator('#autonomy-activate')).to_be_disabled()
            checks.append('viewer cannot change mode or authorize open access')
            page.fill('#autonomy-policy-reason','Explicit offline browser review of open public web quarantine')
            with page.expect_response(lambda r:r.url.endswith('/api/research/autonomy/policy') and r.request.method=='POST') as saved:
                page.click('#autonomy-policy-save')
            assert saved.value.status==200,saved.value.text()
            body=saved.value.json()
            assert body['policy']['domain_access_mode']=='open_public_web' and not body['policy']['enabled']
            expect(page.locator('#autonomy-state')).to_have_text('Avstängd')
            expect(page.locator('#autonomy-activation-label')).to_have_text('Skriv I APPROVE PAID OPEN PUBLIC WEB RESEARCH')
            checks.append('actual policy save disables previous approval and requires separate open activation')
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.screenshot(path='/tmp/v151-browser-mobile.png',full_page=True)
            checks.append('mobile layout has no horizontal overflow')
            assert not external,external
            assert not errors,errors
            browser.close()
        print(json.dumps({'result':'PASS','checks':checks,'errors':errors,'external':external}))
    finally:
        child.terminate();child.wait(timeout=10)
