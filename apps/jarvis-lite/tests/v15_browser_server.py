import os,sys,socket,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
TEMP=tempfile.TemporaryDirectory(prefix="jarvis-v15-acceptance-")
sys.path.insert(0,str(ROOT/'tests'))
import pytest
from fastapi.testclient import TestClient
from jarvis_lite import auth
from jarvis_lite.store import Database
from jarvis_lite.app import create_app
from test_integration_v13 import ADMIN,PASSWORD
from test_acceptance_v15 import test_complete_http_chain_adapter_costs_pack_usage_and_no_fabrication, test_http_insufficient_evidence_never_delegates_invented_demand
from test_open_research_v151 import test_http_real_ai_adapter_open_unreviewed_sources_cannot_reach_sales
os.environ.update(JARVIS_ENV='test',JARVIS_SESSION_SECRET='synthetic-v15-browser-session',KILL_SWITCH='false',JARVIS_DATABASE_URL='sqlite:///'+TEMP.name+'/acceptance.sqlite')
db=Database(os.environ['JARVIS_DATABASE_URL']);patch=pytest.MonkeyPatch()
with db.transaction() as tx:
 for role in ('admin','viewer'):
  if not any(u['email']==role+'@example.com' for u in tx.list('user')):auth.create_user(tx,role+'@example.com',PASSWORD,role,actor=ADMIN,bootstrap=role=='admin')
with TestClient(create_app(db,worker_enabled=False)) as client:
    scenario = test_http_insufficient_evidence_never_delegates_invented_demand if os.getenv('JARVIS_BROWSER_GAPS') == 'true' else test_complete_http_chain_adapter_costs_pack_usage_and_no_fabrication
    if os.getenv('JARVIS_BROWSER_OPEN') == 'true':scenario=test_http_real_ai_adapter_open_unreviewed_sources_cannot_reach_sales
    scenario((client,db),patch)
for key in ('JARVIS_ALLOW_PAID_AI','JARVIS_ALLOW_AUTONOMOUS_RESEARCH','JARVIS_ALLOW_RESEARCH_NETWORK','JARVIS_ALLOW_LIVE','JARVIS_ALLOW_LIVE_CONTACT','JARVIS_ALLOW_LIVE_PAYMENTS'):os.environ[key]='false'
# Never allow the served acceptance app to reach a real external provider.
original=socket.socket.connect
def local(sock,address):
 if isinstance(address,tuple) and address[0] not in ('127.0.0.1','::1','localhost'):raise RuntimeError('external network forbidden')
 return original(sock,address)
socket.socket.connect=local
import uvicorn
uvicorn.run(create_app(db,worker_enabled=False),host='127.0.0.1',port=8027,log_level='warning')
