"""Persistence, month boundary and permission-fence acceptance, no live network."""
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from jarvis_lite import autonomous_research as ar, policy
from jarvis_lite.store import Database, Transaction
from test_ai_v14 import db, ADMIN, REASON
from test_autonomous_research_v15 import setup, Search, test_durable_loop_autoapproves_sources_without_manual_urls_and_cites_exact_claim as seed_pack


def test_stockholm_calendar_month_rollover_retains_unknown_liability(db, monkeypatch):
    zone=ZoneInfo('Europe/Stockholm')
    boundary=datetime(2026,11,1,tzinfo=zone).timestamp()
    with db.transaction() as tx:
        setup(tx,monkeypatch)
        tx.create('budget_entry',{'vendor':'brave_search','state':'SETTLED','actual_sek_minor':70,'created_at':boundary-1})
        tx.create('budget_entry',{'vendor':'brave_search','state':'RESERVED','reserved_sek_minor':30,'created_at':boundary-86400})
        tx.now=boundary-.1
        assert ar.costs(tx)['monthly_used_minor']==100
        tx.now=boundary
        assert ar.costs(tx)['calendar_month']=='2026-11'
        assert ar.costs(tx)['monthly_used_minor']==30
        assert ar.costs(tx)['monthly_reserved_minor']==30


def test_restart_does_not_resend_unknown_search(db,monkeypatch):
    from jarvis_lite.search_provider import SearchUnknown
    class Timeout:
        calls=0
        def search(self,*args,**kwargs):self.calls+=1;raise SearchUnknown('timeout')
    adapter=Timeout()
    with db.transaction() as tx:
        mission=setup(tx,monkeypatch)
        run={'id':'restart','mission_id':mission['id'],'policy_revision':ar._config(tx)['revision']}
        assert ar._search(tx,run,mission,'svensk AI',ADMIN,adapter)['status']=='UNKNOWN'
        retained=tx.list('budget_entry')
    reopened=Database(db.url)
    try:
        with reopened.transaction() as tx:
            tx.agent_context={'mission_id':mission['id'],'task_id':'fixture-task','role':'lead_research'}
            assert ar._search(tx,run,mission,'svensk AI',ADMIN,adapter)['status']=='UNKNOWN'
            assert tx.list('budget_entry')==retained
        assert adapter.calls==1
    finally:reopened.close()


def test_emergency_stop_between_intent_and_dispatch_releases_without_call(db,monkeypatch):
    with db.transaction() as tx:
        mission=setup(tx,monkeypatch)
        run={'id':'stopped','mission_id':mission['id'],'policy_revision':ar._config(tx)['revision']}
        original=Transaction.checkpoint
        def stopped(current):
            original(current)
            policy.set_kill_switch(current,True,REASON,ADMIN)
        monkeypatch.setattr(Transaction,'checkpoint',stopped)
        adapter=Search()
        assert ar._search(tx,run,mission,'svensk AI',ADMIN,adapter)['status']=='BLOCKED'
        assert adapter.calls==0
        assert tx.list('budget_entry')[0]['state']=='RELEASED'


def test_revoked_policy_rejects_reuse_preserves_original_evidence(db,monkeypatch):
    seed_pack(db,monkeypatch)
    with db.transaction() as tx:
        mission=tx.list('business_mission')[0]
        pack=ar.get_pack(tx,mission['id'])
        sources=tx.list('research_source')
        ar.activate(tx,{'statement':ar.DISABLE,'policy_revision':ar._config(tx)['revision'],'reason':REASON},ADMIN)
        with pytest.raises(policy.PolicyError):ar.guard_current_pack(tx,mission)
        assert tx.get('evidence_pack',pack['id'])==pack
        assert tx.list('research_source')==sources


def test_restart_after_search_retains_phase_and_never_repeats_paid_query(db,monkeypatch):
    from jarvis_lite import ai
    from test_autonomous_research_v15 import model, Pages
    monkeypatch.setattr(ai,'task',model)
    adapter=Search()
    with db.transaction() as tx:
        mission=setup(tx,monkeypatch)
        ar.advance(tx,mission,ADMIN,'plan',search_adapter=adapter,web_adapter=Pages())
        ar.advance(tx,mission,ADMIN,'search',search_adapter=adapter,web_adapter=Pages())
        before=tx.list('budget_entry')
    reopened=Database(db.url)
    try:
        for _ in range(8):
            with reopened.transaction() as tx:
                tx.agent_context={'mission_id':mission['id'],'task_id':'fixture-task','role':'lead_research'}
                result=ar.advance(tx,mission,ADMIN,'resume',search_adapter=adapter,web_adapter=Pages())
            if result['status']=='EVIDENCE_READY':break
        assert result['status']=='EVIDENCE_READY'
        assert adapter.calls==1
        with reopened.transaction() as tx:assert tx.list('budget_entry')==before
    finally:reopened.close()
