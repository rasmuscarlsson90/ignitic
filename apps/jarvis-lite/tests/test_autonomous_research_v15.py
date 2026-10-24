"""Offline policy, claim support, budget and durable autonomous loop regressions."""
import hashlib
import json
import time

import pytest

from jarvis_lite import autonomous_research as ar, evidence, policy, research, ai
from jarvis_lite.policy import PolicyError
from test_ai_v14 import db, ADMIN, REASON

TEXT = 'Svenska småföretag använder AI för kundservice enligt undersökningen.'


def setup(tx, monkeypatch):
    for name in ('JARVIS_ALLOW_AUTONOMOUS_RESEARCH', 'JARVIS_ALLOW_RESEARCH_NETWORK'):
        monkeypatch.setenv(name, 'true')
    monkeypatch.setenv('BRAVE_SEARCH_API_KEY', 'synthetic-search-test-only')
    ar.configure(tx, {'allowed_domains': ['alpha.example', 'beta.example'], 'terms_reviewed': True,
        'terms_reference': REASON, 'provider_terms_reference': 'https://brave.com/terms/',
        'query_price_usd': '0.005', 'pricing_evidence': 'https://brave.com/search/api/',
        'pricing_observed_at': tx.now, 'min_domain_interval_seconds': 1, 'reason': REASON}, ADMIN)
    cfg = ar.status(tx)['policy']
    ar.activate(tx, {'statement': ar.ACTIVATE, 'policy_revision': cfg['revision'], 'reason': REASON}, ADMIN)
    mission = tx.create('business_mission', {'goal': 'Analysera svenska småföretag AI kundservice', 'research_mode': 'autonomous',
        'status': 'RUNNING', 'deadline_at': tx.now + 3600, 'max_cost_minor': 2000, 'source_ids': []})
    tx.agent_context = {'mission_id': mission['id'], 'task_id': 'fixture-task', 'role': 'lead_research'}
    return mission


class Search:
    calls = 0
    def search(self, query, **kwargs):
        self.calls += 1
        return {'results': [{'url': 'https://alpha.example/report'}, {'url': 'https://beta.example/report'}]}


class Pages:
    def fetch(self, url, config, **kwargs):
        # Distinct content hashes: syndication is not independent corroboration.
        text = TEXT + (' Metod A.' if 'alpha' in url else ' Metod B.')
        return {'canonical_url': url, 'requested_url': url, 'title': 'AI småföretag kundservice',
            'text': text, 'content_hash': hashlib.sha256(text.encode()).hexdigest(),
            'retrieved_at': time.time(), 'published_at': time.time() - 3600,
            'retrieval_status': 'SUCCESS', 'robots_allowed': True, 'terms_allowed': True, 'content_type': 'text/html'}


def model(tx, **kwargs):
    if kwargs['response_contract'] == 'web_research_plan':
        output = {'decision': 'search', 'queries': ['svenska småföretag AI kundservice undersökning'], 'information_gaps': []}
    else:
        ids = [item['source_id'] for item in tx.list('web_source_evaluation') if item['status'] == 'ACCEPTED']
        output = {'claims': [{'text': TEXT, 'subject': 'småföretag', 'predicate': 'använder', 'value': 'AI', 'unit': '',
            'kind': 'fact', 'importance': 'commercial', 'citations': [{'source_id': sid, 'snippet': TEXT} for sid in ids]}], 'information_gaps': []}
    return {'status': 'COMPLETED', 'simulated': False, 'output': {'text': json.dumps(output)}}


def test_durable_loop_autoapproves_sources_without_manual_urls_and_cites_exact_claim(db, monkeypatch):
    monkeypatch.setattr(ai, 'task', model)
    search = Search()
    with db.transaction() as tx:
        mission = setup(tx, monkeypatch)
    for _ in range(12):
        with db.transaction() as tx:
            tx.agent_context = {'mission_id': mission['id'], 'task_id': 'fixture-task', 'role': 'lead_research'}
            result = ar.advance(tx, mission, ADMIN, 'step', search_adapter=search, web_adapter=Pages())
        if result['status'] != 'RESEARCH_CONTINUE':
            break
    assert result['status'] == 'EVIDENCE_READY', result
    with db.transaction() as tx:
        pack = ar.guard_current_pack(tx, mission)
        assert evidence.validate_pack(pack)
        assert len(pack['verified_claim_ids']) == 1
        assert len(research.documents(tx, result['source_ids'])) == 2
        assert search.calls == 1
        assert ar.costs(tx)['monthly_used_minor'] == 12
        assert ar.costs(tx)['billed_cost_minor'] is None
        before = len(tx.list('budget_entry'))
        tx.agent_context = {'mission_id': mission['id'], 'task_id': 'fixture-task', 'role': 'lead_research'}
        assert ar.advance(tx, mission, ADMIN, 'replayed')['status'] == 'EVIDENCE_READY'
        assert len(tx.list('budget_entry')) == before
        claim = pack['claims'][0]
        with pytest.raises(PolicyError, match='Affärspåståendet'):
            evidence.guard_downstream(pack, [claim['id']], statements=[{'claim_id': claim['id'], 'text': 'Marknaden efterfrågar automatiska betalningar'}])
        assert not tx.list('lead') and not tx.list('payment')


@pytest.mark.parametrize('bad', [{'monthly_limit_minor': 10001}, {'max_concurrent_calls': 2}, {'allowed_domains': ['localhost']}, {'allowed_domains': ['127.0.0.1']}, {'allowed_content_types': ['text/javascript']}, {'max_queries_per_mission': 4}])
def test_policy_hard_bounds(db, bad):
    with db.transaction() as tx, pytest.raises(PolicyError):
        ar.configure(tx, {**bad, 'reason': REASON}, ADMIN)


def test_search_disabled_by_default_and_admin_only(db):
    with db.transaction() as tx:
        assert not ar.status(tx)['readiness']['ready']
        with pytest.raises(PolicyError):
            ar.configure(tx, {'reason': REASON}, {'role': 'operator'})
        assert not tx.list('external_intent')


def test_budget_cap_counts_old_pending_liabilities(db, monkeypatch):
    with db.transaction() as tx:
        mission = setup(tx, monkeypatch)
        tx.create('budget_entry', {'vendor': 'brave_search', 'state': 'RESERVED', 'reserved_sek_minor': 10000,
            'mission_id': mission['id'], 'created_at': tx.now - 90 * 86400})
        with pytest.raises(PolicyError, match='budget'):
            ar._guard_limits(tx, mission['id'], 1)


def test_claim_support_rejects_fabrication_stale_and_spoofing(db, monkeypatch):
    with db.transaction() as tx:
        mission = setup(tx, monkeypatch)
        cfg = ar._config(tx)
        sources = [evidence.evaluate_source(Pages().fetch('https://' + host + '/report', cfg), cfg,
            now=tx.now, goal=mission['goal']) for host in ('alpha.example', 'beta.example')]
        claim = {'text': TEXT, 'subject': 'småföretag', 'predicate': 'använder', 'value': 'AI', 'unit': '',
            'kind': 'fact', 'importance': 'commercial', 'citations': [{'source_id': s['id'], 'snippet': TEXT} for s in sources]}
        assert evidence.review_claims([claim], sources, now=tx.now)['claims'][0]['status'] == 'VERIFIED'
        assert evidence.review_claims([{**claim, 'text': 'Alla företag behöver köpa AI-tjänster nu'}], sources)['claims'][0]['status'] == 'REJECTED'
        assert evidence.review_claims([{**claim, 'citations': claim['citations'][:1]}], sources)['claims'][0]['status'] == 'UNVERIFIED'
        spoof = Pages().fetch('https://alpha.example.attacker.example/report', cfg)
        assert evidence.evaluate_source(spoof, cfg, now=tx.now)['status'] == 'REJECTED'
        stale = {**Pages().fetch('https://alpha.example/report', cfg), 'published_at': tx.now - 4000*86400}
        assert evidence.evaluate_source(stale, cfg, now=tx.now)['status'] == 'REJECTED'


def test_unknown_search_cost_cannot_be_reissued(db, monkeypatch):
    from jarvis_lite import search_provider
    class Uncertain:
        calls = 0
        def search(self, *args, **kwargs):
            self.calls += 1
            raise search_provider.SearchUnknown('search_timeout')
    adapter = Uncertain()
    with db.transaction() as tx:
        mission = setup(tx, monkeypatch)
        run = {'id': 'unknown-run', 'mission_id': mission['id'], 'policy_revision': ar._config(tx)['revision']}
        first = ar._search(tx, run, mission, 'svensk AI marknad', ADMIN, adapter)
        second = ar._search(tx, run, mission, 'svensk AI marknad', ADMIN, adapter)
        assert first['status'] == second['status'] == 'UNKNOWN'
        assert adapter.calls == 1
        assert tx.list('budget_entry')[-1]['state'] == 'RESERVED'


def test_v15_http_policy_auth_and_no_credential_exposure(running):
    from test_integration_v13 import login
    client, database = running
    assert client.get('/api/v1.5/overview').status_code == 401
    headers = login(client, 'viewer')
    response = client.get('/api/v1.5/overview')
    assert response.status_code == 200
    assert not response.json()['autonomous_research']['readiness']['ready']
    assert client.post('/api/research/autonomy/policy', json={'reason': REASON}, headers=headers).status_code == 403
    headers = login(client)
    assert client.post('/api/research/autonomy/policy', json={'reason': REASON}).status_code == 403
    saved = client.post('/api/research/autonomy/policy', json={'reason': REASON}, headers=headers)
    assert saved.status_code == 200
    assert 'BRAVE_SEARCH_API_KEY' not in saved.text
    with database.transaction() as tx:
        assert not tx.list('external_intent')


from test_integration_v13 import running


@pytest.mark.parametrize('supported', [False, True])
def test_sales_cannot_substitute_unproven_market_demand(db, monkeypatch, supported):
    from jarvis_lite import intelligence
    test_durable_loop_autoapproves_sources_without_manual_urls_and_cites_exact_claim(db, monkeypatch)
    with db.transaction() as tx:
        mission = tx.list('business_mission')[0]
        pack = ar.get_pack(tx, mission['id'])
        mission.update(evidence_pack_id=pack['id'], source_ids=[s['source_id'] for s in pack['sources']])
        report = tx.create('research_report', {'mission_id': mission['id'], 'status': 'COMPLETED',
            'citations': research.document_citations(tx, mission['source_ids'])})
        mission['research_report_id'] = report['id']
        tx.put('business_mission', mission['id'], mission)
        claim = pack['claims'][0]
        proposal = {'title': 'Hypotes om intern AI-assistans', 'target_group': 'Föreslagen målgrupp, behöver valideras',
            'problem': claim['text'] if supported else 'Alla småföretag efterfrågar vår AI-tjänst', 'solution': 'Hypotes om oskickade AI-utkast',
            'price_estimate_minor': 500000, 'cost_estimate_minor': 100000, 'competition': 'Ej verifierad',
            'risks': ['Efterfrågan behöver valideras'], 'source_ids': mission['source_ids'],
            'next_steps': ['Granska hypotesen internt'], 'assumptions': ['Priser är obevisade estimat'],
            'classification': 'HYPOTHESIS', 'claim_ids': [claim['id']], 'factual_claims': [{'claim_id': claim['id'], 'text': claim['text']}]}
        monkeypatch.setattr(intelligence, '_call', lambda *args, **kwargs: (
            {'id': 'offline-sales-fixture'}, {'summary': 'Interna hypoteser, inte verifierad efterfrågan', 'opportunities': [proposal]}, 'COMPLETED'))
        result = intelligence.prepare_strategy(tx, mission, ADMIN, 'sales-proof')
        assert result['status'] == ('COMPLETED' if supported else 'REVIEW_REQUIRED')
        if not supported:
            assert result['issues'] == ['unsupported_or_unbounded_opportunity']
            assert result['opportunities'] == []
