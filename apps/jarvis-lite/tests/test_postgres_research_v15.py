"""Search spending races use independent engines and only an owned random DB."""
import os
from concurrent.futures import ThreadPoolExecutor
import pytest
from jarvis_lite import autonomous_research as ar, policy, budget
from jarvis_lite.store import Database
from test_postgres import postgres_url
from test_autonomous_research_v15 import setup, Search, ADMIN, REASON

pytestmark = pytest.mark.skipif(not os.getenv('JARVIS_TEST_DATABASE_URL'), reason='Requires isolated PostgreSQL fixture')


def test_search_calendar_budget_serializes_concurrent_reservations(postgres_url, monkeypatch):
    engines = [Database(postgres_url) for _ in range(8)]
    try:
        with engines[0].transaction() as tx:
            policy.initialize(tx)
            budget.set_fx_rate(tx, currency='USD', sek_per_unit='12', source=REASON, observed_at=tx.now, actor=ADMIN)
            mission = setup(tx, monkeypatch)
            ar.configure(tx, {'monthly_limit_minor': 12, 'daily_limit_minor': 12, 'mission_limit_minor': 12, 'reason': REASON}, ADMIN)
            config = ar._config(tx)
            ar.activate(tx, {'statement': ar.ACTIVATE, 'policy_revision': config['revision'], 'reason': REASON}, ADMIN)
        def request(index):
            with engines[index].transaction() as tx:
                tx.agent_context = {'mission_id': mission['id'], 'task_id': 'fixture-task', 'role': 'lead_research'}
                run = {'id': 'race-' + str(index), 'mission_id': mission['id'], 'policy_revision': config['revision']}
                try:
                    return ar._search(tx, run, mission, 'svensk AI fråga ' + str(index), ADMIN, Search())['status']
                except policy.PolicyError as error:
                    return error.code
        with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(request,range(8)))
        assert results.count('COMPLETED') == 1, results
        assert results.count('search_budget_exhausted') == 7, results
        with engines[0].transaction() as tx:
            assert ar.costs(tx)['monthly_used_minor'] == 12
            assert len(tx.list('budget_entry')) == 1
    finally:
        for engine in engines:engine.close()
