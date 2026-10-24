"""Open discovery is a read permission, never a license or new spending authority."""
import pytest

from jarvis_lite import ai, autonomous_research as ar, evidence, policy, research
from jarvis_lite.policy import PolicyError
from test_autonomous_research_v15 import setup, Search, Pages, model, TEXT
from test_ai_v14 import db, ADMIN, REASON
from test_integration_v13 import running, login, post


def open_policy(tx, *, activate=True, **changes):
    ar.configure(tx, {'domain_access_mode':'open_public_web','open_public_web_reviewed':True,
                     'reason':REASON, **changes}, ADMIN)
    config=ar._config(tx)
    if activate:
        ar.activate(tx, {'statement':ar.OPEN_ACTIVATE,'policy_revision':config['revision'],'reason':REASON}, ADMIN)
    return ar._config(tx)


def test_legacy_mode_default_and_strict_mode_validation(db):
    with db.transaction() as tx:
        assert ar._config(tx)['domain_access_mode']=='allowlist'
        # Persisted pre-v1.5.1 policy receives default without rewriting data.
        tx.put('autonomous_research_policy','global',{'allowed_domains':['example.com']})
        assert ar._config(tx)['domain_access_mode']=='allowlist'
        for invalid in ('OPEN_PUBLIC_WEB',None,[],True):
            with pytest.raises(PolicyError):
                ar.configure(tx, {'domain_access_mode':invalid,'reason':REASON},ADMIN)
        with pytest.raises(PolicyError):
            ar.configure(tx, {'domain_access_mode':'open_public_web','reason':REASON}, {'role':'operator'})


def test_open_activation_requires_review_distinct_statement_and_current_revision(db, monkeypatch):
    with db.transaction() as tx:
        setup(tx,monkeypatch)
        config=open_policy(tx, activate=False, open_public_web_reviewed=False, allowed_domains=[])
        with pytest.raises(PolicyError):
            ar.activate(tx, {'statement':ar.OPEN_ACTIVATE,'policy_revision':config['revision'],'reason':REASON}, ADMIN)
        assert not ar._config(tx)['enabled']
        config=open_policy(tx,activate=False,allowed_domains=[])
        with pytest.raises(PolicyError) as error:
            ar.activate(tx, {'statement':ar.ACTIVATE,'policy_revision':config['revision'],'reason':REASON}, ADMIN)
        assert error.value.code=='research_activation_invalid'
        ar.activate(tx, {'statement':ar.OPEN_ACTIVATE,'policy_revision':config['revision'],'reason':REASON},ADMIN)
        assert ar.require_ready(tx)['domain_access_mode']=='open_public_web'
        assert ar.status(tx)['activation_statement']==ar.OPEN_ACTIVATE
        old=config['revision']
        ar.configure(tx, {'prohibited_domains':['blocked.org'],'reason':REASON},ADMIN)
        assert not ar._config(tx)['enabled']
        with pytest.raises(PolicyError) as error:
            ar.activate(tx, {'statement':ar.OPEN_ACTIVATE,'policy_revision':old,'reason':REASON},ADMIN)
        assert error.value.code=='research_policy_changed'
        assert any(a['action']=='research.policy_configured' for a in tx.audits())


@pytest.mark.parametrize('mode', ['allowlist','open_public_web'])
def test_review_scope_is_separate_from_discovery_and_cannot_be_forged_by_page(db,monkeypatch,mode):
    with db.transaction() as tx:
        mission=setup(tx,monkeypatch)
        config=open_policy(tx) if mode=='open_public_web' else ar._config(tx)
        page=Pages().fetch('https://new.example.net/report',config)
        page.update(terms_allowed=True,license_reviewed=True,license='CC0',terms_reviewed=True)
        evaluation=evidence.evaluate_source(page,config,now=tx.now,goal=mission['goal'])
        assert evaluation['status']==('REVIEW_REQUIRED' if mode=='open_public_web' else 'REJECTED')
        assert evaluation['license_reviewed'] is evaluation['terms_reviewed'] is False
        assert evaluation['legal_review_reference'] is None
        assert 'source_legal_review_required' in evaluation['reasons']
        run={'mission_id':mission['id'],'policy_revision':config['revision']}
        registered=ar._register(tx,run,evaluation)
        assert not tx.get('research_source',registered['id'])
        claims=[{'text':TEXT,'kind':'fact','importance':'commercial','subject':'småföretag','predicate':'använder','value':'AI','unit':'',
                 'citations':[{'source_id':registered['id'],'snippet':TEXT}]}]
        reviewed=evidence.review_claims(claims,[registered],now=tx.now)
        assert all(c['status']!='VERIFIED' for c in reviewed['claims'])
        assert not tx.list('lead') and not tx.list('payment')


def test_open_redirect_via_unreviewed_domain_cannot_launder_final_reviewed_origin(db,monkeypatch):
    with db.transaction() as tx:
        mission=setup(tx,monkeypatch);config=open_policy(tx)
        page=Pages().fetch('https://alpha.example.com/report',config)
        page['redirects']=[{'from':'https://new.example.net/start','to':page['canonical_url'],'status':302}]
        evaluation=evidence.evaluate_source(page,config,now=tx.now,goal=mission['goal'])
        assert evaluation['license_reviewed'] is False and evaluation['status']=='REVIEW_REQUIRED'


def test_open_loop_reads_unlisted_pages_but_never_promotes_them_or_invents_verified_evidence(db,monkeypatch):
    monkeypatch.setattr(ai,'task',model)
    search=Search();reads=[]
    class TrackedPages(Pages):
        def fetch(self,url,config,**kwargs):
            reads.append(url)
            return super().fetch(url,config,**kwargs)
    with db.transaction() as tx:
        mission=setup(tx,monkeypatch);open_policy(tx,allowed_domains=[])
    for _ in range(18):
        with db.transaction() as tx:
            tx.agent_context={'mission_id':mission['id'],'task_id':'fixture-task','role':'lead_research'}
            result=ar.advance(tx,mission,ADMIN,'open-loop',search_adapter=search,web_adapter=TrackedPages())
        if result['status']!='RESEARCH_CONTINUE':break
    assert result['status']=='REVIEW_REQUIRED' and result['code']=='research_evidence_insufficient'
    assert set(reads)=={'https://alpha.example.com/report','https://beta.example.org/report'}
    with db.transaction() as tx:
        assert all(d['status']=='DISCOVERED' for d in tx.list('web_discovery'))
        assert all(e['status']=='REVIEW_REQUIRED' and not e['license_reviewed'] for e in tx.list('web_source_evaluation'))
        assert not tx.list('research_source') and not tx.list('research_document')
        assert ar.get_pack(tx,mission['id'])['verified_claim_ids']==[]
        assert ar.costs(tx)['monthly_used_minor']==12 and search.calls==1


@pytest.mark.parametrize('scope', ['monthly','daily','mission','global','mission_authority'])
def test_open_search_cannot_bypass_any_spending_cap(db,monkeypatch,scope):
    search=Search()
    with db.transaction() as tx:
        mission=setup(tx,monkeypatch)
        changes={}
        if scope=='monthly':changes={'monthly_limit_minor':0,'daily_limit_minor':0,'mission_limit_minor':0}
        elif scope=='daily':changes={'daily_limit_minor':0,'mission_limit_minor':0}
        elif scope=='mission':changes={'mission_limit_minor':0}
        config=open_policy(tx,**changes)
        if scope=='global':
            settings=policy.get_settings(tx);settings['daily_budget_minor']=0;tx.put('settings','global',settings)
        elif scope=='mission_authority':
            mission['max_cost_minor']=0;tx.put('business_mission',mission['id'],mission)
        run={'id':'open-budget','mission_id':mission['id'],'policy_revision':config['revision']}
        with pytest.raises(PolicyError):ar._search(tx,run,mission,'svensk AI marknad',ADMIN,search)
        assert search.calls==0 and not tx.list('budget_entry')


@pytest.mark.parametrize('flag', ['JARVIS_ALLOW_RESEARCH_NETWORK','JARVIS_ALLOW_AUTONOMOUS_RESEARCH'])
def test_open_mode_does_not_bypass_runtime_network_switches(db,monkeypatch,flag):
    with db.transaction() as tx:
        setup(tx,monkeypatch);open_policy(tx,allowed_domains=[])
        monkeypatch.setenv(flag,'false')
        with pytest.raises(PolicyError) as error:ar.require_ready(tx)
        assert error.value.code=='research_runtime_disabled'


def test_http_open_policy_admin_csrf_and_activation_revision(running,monkeypatch):
    from test_acceptance_v15 import prepare
    client,database=running
    headers,_=prepare(client,database,monkeypatch)
    body={'domain_access_mode':'open_public_web','allowed_domains':[],
          'open_public_web_reviewed':True,'reason':REASON}
    assert client.post('/api/research/autonomy/policy',json=body).status_code==403
    state=post(client,'/api/research/autonomy/policy',headers,body)
    assert state['policy']['enabled'] is False
    assert state['activation_statement']==ar.OPEN_ACTIVATE
    activation={'statement':ar.ACTIVATE,'policy_revision':state['policy']['revision'],'reason':REASON}
    assert client.post('/api/research/autonomy/activation',headers=headers,json=activation).status_code==422
    activation['statement']=ar.OPEN_ACTIVATE
    assert post(client,'/api/research/autonomy/activation',headers,activation)['readiness']['ready'] is True
    with database.transaction() as tx:assert not tx.list('web_research_run')
    viewer=login(client,'viewer')
    assert client.post('/api/research/autonomy/policy',headers=viewer,json=body).status_code==403
    assert client.post('/api/research/autonomy/activation',headers=viewer,json=activation).status_code==403


def test_open_reviewed_scope_still_supports_verified_research_and_revocation(db,monkeypatch):
    monkeypatch.setattr(ai,'task',model)
    with db.transaction() as tx:
        mission=setup(tx,monkeypatch);open_policy(tx)
    for _ in range(18):
        with db.transaction() as tx:
            tx.agent_context={'mission_id':mission['id'],'task_id':'fixture-task','role':'lead_research'}
            result=ar.advance(tx,mission,ADMIN,'open-reviewed-loop',search_adapter=Search(),web_adapter=Pages())
        if result['status']!='RESEARCH_CONTINUE':break
    assert result['status']=='EVIDENCE_READY',result
    with db.transaction() as tx:
        pack=ar.guard_current_pack(tx,mission)
        assert pack['verified_claim_ids'] and len(research.documents(tx,result['source_ids']))==2
        source=tx.get('research_source',result['source_ids'][0])
        ar.configure(tx, {'domain_access_mode':'allowlist','reason':REASON},ADMIN)
        with pytest.raises(PolicyError):ar.source_allowed(tx,source,mission['id'])
        assert tx.get('evidence_pack',pack['id'])==pack


def test_http_real_ai_adapter_open_unreviewed_sources_cannot_reach_sales(running,monkeypatch):
    from test_acceptance_v15 import prepare
    client,database=running
    headers,calls=prepare(client,database,monkeypatch)
    state=post(client,'/api/research/autonomy/policy',headers,{'domain_access_mode':'open_public_web',
        'allowed_domains':[],'open_public_web_reviewed':True,'reason':REASON})
    post(client,'/api/research/autonomy/activation',headers,{'statement':ar.OPEN_ACTIVATE,
        'policy_revision':state['policy']['revision'],'reason':REASON})
    mission=post(client,'/api/missions',headers,{'goal':'Analysera svenska småföretag AI kundservice med offentliga källor',
        'research_mode':'autonomous','idempotency_key':'http-open-unreviewed'})
    for _ in range(24):
        job=post(client,'/api/jobs/run',headers,{})['job']
        assert job
        if job['status']=='paused':break
    detail=client.get('/api/missions/'+mission['id']).json()
    assert detail['mission']['status']=='REVIEW_REQUIRED'
    assert detail['mission']['blocker']['code']=='research_evidence_insufficient'
    assert not any(role=='sales' for role,_ in calls)
    auto=client.get('/api/research/autonomy').json()
    assert len(auto['sources'])==3 and all(s['status']=='REVIEW_REQUIRED' for s in auto['sources'])
    assert all(d['status']=='DISCOVERED' for d in auto['discoveries'])
    assert auto['costs']['booked_cost_minor']==12
    assert not auto['evidence_packs'][0]['verified_claim_ids']
    with database.transaction() as tx:
        for kind in ('research_source','research_document','opportunity_report','lead','payment','email_message'):
            assert not tx.list(kind),kind
