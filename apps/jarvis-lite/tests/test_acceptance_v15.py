"""HTTP/real OpenRouter adapter acceptance with strictly offline transports."""
import json
import httpx
from jarvis_lite import ai, budget, autonomous_research as ar, search_provider, web_retrieval
from test_integration_v13 import running, login, post, ADMIN, REASON
from test_autonomous_research_v15 import Pages, TEXT
from v15_acceptance_support import content_for


def prepare(client, db, monkeypatch, *, unsupported=False):
    headers = login(client)
    for name in ('JARVIS_ALLOW_AUTONOMOUS_RESEARCH','JARVIS_ALLOW_RESEARCH_NETWORK','JARVIS_ALLOW_PAID_AI'):
        monkeypatch.setenv(name,'true')
    monkeypatch.setenv('OPENROUTER_API_KEY','synthetic-ai-acceptance-key')
    monkeypatch.setenv('BRAVE_SEARCH_API_KEY','synthetic-search-acceptance-key')
    with db.transaction() as tx:
        budget.set_fx_rate(tx,currency='USD',sek_per_unit='12',source=REASON,observed_at=tx.now,actor=ADMIN)
        observed=tx.now
    post(client,'/api/ai/config',headers,{'enabled':True,'max_output_tokens':4096,'reason':REASON})
    post(client,'/api/research/autonomy/policy',headers,{'allowed_domains':['alpha.example.com','beta.example.org'],
        'terms_reviewed':True,'terms_reference':REASON,'provider_terms_reference':'https://brave.com/terms/',
        'query_price_usd':'0.005','pricing_evidence':'https://brave.com/search/api/',
        'pricing_observed_at':observed,'reason':REASON})
    revision=client.get('/api/research/autonomy').json()['policy']['revision']
    post(client,'/api/research/autonomy/activation',headers,{'statement':ar.ACTIVATE,'policy_revision':revision,'reason':REASON})
    calls=[]
    def provider(request):
        body=json.loads(request.content)
        contract=body['response_format']['json_schema']['name'].removeprefix('jarvis_')
        prompt=json.loads(json.loads(body['messages'][-1]['content'])['untrusted_content'])
        role={'ceo_plan_research':'ceo','ceo_summary':'ceo','web_research_plan':'lead_research',
              'web_research_claims':'lead_research','research_report':'lead_research','sales_strategy_evidence':'sales',
              'service_result_evidence':'delivery','finance_analysis':'finance','compliance_qa':'compliance'}[contract]
        calls.append((role,contract))
        if contract=='web_research_plan':
            content={'decision':'search','queries':['svenska småföretag AI kundservice'],'information_gaps':[]}
        elif contract=='web_research_claims':
            content={'claims':[{'text':TEXT,'subject':'småföretag','predicate':'använder','value':'AI','unit':'',
                'kind':'fact','importance':'commercial','citations':[{'source_id':s['source_id'],'snippet':TEXT} for s in prompt['sources']]}], 'information_gaps':[]}
        else:content=content_for(role,contract,prompt)
        if unsupported and contract == 'web_research_claims':
            content['claims'][0]['text'] = 'Alla småföretag vill köpa vår tjänst för 5000 kronor'
            content['information_gaps'] = ['Verifierad efterfrågan saknas']
        return httpx.Response(200,json={'id':'offline-'+str(len(calls)),'usage':{'cost':'0.001','prompt_tokens':100,'completion_tokens':100},
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'action':'recommend','text':content,'reason':REASON})}}]})
    original=ai.OpenRouterAdapter
    class OfflineAI(original):
        def __init__(self,key):super().__init__(key,transport=httpx.MockTransport(provider))
    search_original=search_provider.BraveSearchAdapter
    class OfflineSearch(search_original):
        def __init__(self,**kwargs):super().__init__(transport=httpx.MockTransport(lambda req:httpx.Response(200,headers={'content-type':'application/json'},json={
            'type':'search','web':{'results':[{'url':'https://alpha.example.com/report','title':'AI småföretag'},{'url':'https://beta.example.org/report','title':'AI småföretag'},
                              {'url':'https://outside.example.net/report','title':'Ej tillåten källa'}]}})),**kwargs)
    monkeypatch.setattr(ai,'OpenRouterAdapter',OfflineAI)
    monkeypatch.setattr(search_provider,'BraveSearchAdapter',OfflineSearch)
    monkeypatch.setattr(web_retrieval,'PublicWebAdapter',Pages)
    return headers,calls


def test_complete_http_chain_adapter_costs_pack_usage_and_no_fabrication(running,monkeypatch):
    client,db=running
    headers,calls=prepare(client,db,monkeypatch)
    body={'goal':'Analysera svenska småföretag AI kundservice med källor och ekonomiska osäkerheter',
          'research_mode':'autonomous','deliverable_kind':'business_plan','max_cost_minor':2000,'idempotency_key':'http-autonomy'}
    mission=post(client,'/api/missions',headers,body)
    assert post(client,'/api/missions',headers,body)['id']==mission['id']
    for _ in range(24):
        job=post(client,'/api/jobs/run',headers,{})['job']
        assert job and job['status']=='completed',json.dumps(job,ensure_ascii=False)
        detail=client.get('/api/missions/'+mission['id']).json()
        if detail['mission']['status']=='REVIEW_REQUIRED':break
    assert detail['mission']['completed_steps']==7,detail
    assert len(calls)==9,calls
    state=client.get('/api/v1.5/overview').json()
    auto=state['autonomous_research']
    assert auto['costs']['booked_cost_minor']==12
    assert auto['costs']['ai_cost_minor']==108
    assert auto['costs']['billed_cost_minor'] is None
    assert any(d['status']=='REJECTED' for d in auto['discoveries'])
    pack=auto['evidence_packs'][0]
    assert pack['verified_claim_ids']
    assert {u['role'] for u in auto['usage'] if u['pack_id']==pack['id']}=={'lead_research','sales','delivery','finance','compliance','ceo'}
    assert state['metrics']['verified_revenue_minor']==0
    with db.transaction() as tx:
        assert not tx.list('lead') and not tx.list('payment') and not tx.list('email_message')
        assert len(tx.list('ai_trace'))==9
        assert all(e['state']=='SETTLED' for e in tx.list('budget_entry'))


def test_http_insufficient_evidence_never_delegates_invented_demand(running,monkeypatch):
    client,db=running
    headers,calls=prepare(client,db,monkeypatch,unsupported=True)
    mission=post(client,'/api/missions',headers,{'goal':'Analysera svenska småföretag AI kundservice och verifiera efterfrågan',
        'research_mode':'autonomous','idempotency_key':'http-gaps'})
    for _ in range(24):
        job=post(client,'/api/jobs/run',headers,{})['job']
        assert job
        if job['status']=='paused':break
    detail=client.get('/api/missions/'+mission['id']).json()
    assert detail['mission']['status']=='REVIEW_REQUIRED'
    assert detail['mission']['blocker']['code']=='research_evidence_insufficient'
    assert not any(role=='sales' for role,_ in calls)
    state=client.get('/api/v1.5/overview').json()
    assert state['autonomous_research']['runs'][0]['gaps']
    assert state['metrics']['verified_revenue_minor']==0
    with db.transaction() as tx:
        assert not tx.list('opportunity_report') and not tx.list('lead')
