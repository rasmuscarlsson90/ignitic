import os,subprocess,time,json,urllib.request
from playwright.sync_api import sync_playwright,expect
from pathlib import Path
root=str(Path(__file__).resolve().parents[1])
env=dict(os.environ);env['PYTHONPATH']=root
child=subprocess.Popen([root+'/.venv/bin/python',root+'/tests/v15_browser_server.py'],env=env,cwd=root,stdout=open('/tmp/v15-acceptance-browser.log','w'),stderr=subprocess.STDOUT)
checks=[];errors=[];external=[]
try:
 for _ in range(100):
  try:urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:8027/health',timeout=1);break
  except Exception:time.sleep(.2)
 with sync_playwright() as p:
  browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
  page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)))
  def guard(route):
   if not route.request.url.startswith('http://127.0.0.1:8027/'):external.append(route.request.url);route.abort()
   else:route.continue_()
  page.route('**/*',guard)
  page.goto('http://127.0.0.1:8027/research/autonomy');page.fill('#login-email','admin@example.com');page.fill('#login-password','Synthetic-v13-integration-password-42');page.click('#login-submit');expect(page.locator('#last-updated')).to_contain_text('Uppdaterad',timeout=30000)
  expect(page.locator('#autonomy-runs')).to_contain_text('svenska småföretag AI kundservice');checks.append('actual search queries')
  expect(page.locator('#autonomy-evidence')).to_contain_text('Svenska småföretag använder AI');expect(page.locator('#autonomy-evidence')).to_contain_text('Användning i agentteamet');checks.append('literal fact and recorded agent usage')
  expect(page.locator('#autonomy-costs')).to_contain_text('0,12');expect(page.locator('#autonomy-costs')).to_contain_text('1,08');checks.append('separate real recorded search and AI test costs')
  expect(page.locator('#autonomy-sources')).to_contain_text('alpha.example');checks.append('source provenance')
  expect(page.locator('#autonomy-sources')).to_contain_text('outside.example');page.select_option('#autonomy-source-filter','rejected');expect(page.locator('#autonomy-sources')).to_contain_text('administratörsgodkända');checks.append('rejected discovery and Swedish policy reason');page.select_option('#autonomy-source-filter','approved');expect(page.locator('#autonomy-sources')).to_contain_text('alpha.example');checks.append('automatically approved source filter')
  page.locator('#autonomy-policy-form').locator('xpath=ancestor::details[1]/summary').click();expect(page.locator('#autonomy-policy-save')).to_be_enabled();checks.append('admin reviewed policy controls')
  state=page.request.get('http://127.0.0.1:8027/api/v1.5/overview').json();mission=state['agents']['missions'][0]
  page.goto('http://127.0.0.1:8027/missions/'+mission['id']);expect(page.locator('.mission-web-research')).to_contain_text('Faktaunderlag');checks.append('mission shared evidence')
  page.set_viewport_size({'width':390,'height':844});page.screenshot(path='/tmp/v15-acceptance-mobile.png',full_page=True);checks.append('mobile rendering')
  viewer=browser.new_page();viewer.route('**/*',guard);viewer.goto('http://127.0.0.1:8027/research/autonomy');viewer.fill('#login-email','viewer@example.com');viewer.fill('#login-password','Synthetic-v13-integration-password-42');viewer.click('#login-submit');expect(viewer.locator('#last-updated')).to_contain_text('Uppdaterad',timeout=30000)
  expect(viewer.locator('#autonomy-policy-save')).to_be_disabled();checks.append('viewer cannot authorize policy')
  page.set_viewport_size({'width':1440,'height':1000});page.goto('http://127.0.0.1:8027/research/autonomy');expect(page.locator('#last-updated')).to_contain_text('Uppdaterad',timeout=30000)
  page.locator('#autonomy-policy-form').locator('xpath=ancestor::details[1]/summary').click();page.fill('#autonomy-policy-reason','Granskat avgränsad testpolicy utan verkliga sökanrop')
  with page.expect_response(lambda r:r.url.endswith('/api/research/autonomy/policy') and r.request.method=='POST') as saved:page.click('#autonomy-policy-save')
  assert saved.value.status==200,saved.value.text();expect(page.locator('#autonomy-state')).to_have_text('Avstängd');checks.append('actual browser policy save revokes activation')
  assert not errors,errors;assert not external,external
  browser.close()
 print(json.dumps({'result':'PASS','checks':checks,'errors':errors,'external':external}))
finally:
 child.terminate();child.wait(timeout=10)
